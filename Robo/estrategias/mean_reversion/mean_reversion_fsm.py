from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import logging
import os
from typing import Any, Callable

logger = logging.getLogger(__name__)
try:
    from .mr_telemetry import log_mr_event
except Exception:  # pragma: no cover
    log_mr_event = None  # type: ignore[assignment]

"""Máquina de estados (FSM) e lógica de simulação para Mean Reversion."""


class MRState(str, Enum):
    IDLE = "Idle"
    REVERSAO_EM_FORMACAO = "ReversaoEmFormacao"
    EM_POSICAO = "EmPosicao"
    COOLDOWN = "Cooldown"


class MRTrigger(str, Enum):
    TICK = "tick"
    FUNDO_POTENCIAL = "fundo_potencial"
    ENTRADA_CONFIRMADA = "entrada_confirmada"
    SAIDA = "saida"
    TIMEOUT = "timeout"
    RESET = "reset"
    INVALIDACAO = "invalidacao"
    FILL = "fill"


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    timeframe: str
    now: datetime
    df: Any | None = None
    indicators: dict[str, Any] | None = None


@dataclass(frozen=True)
class PositionSnapshot:
    in_position: bool
    side: str | None = None
    entry_price: float | None = None
    size: float | None = None
    raw: Any | None = None


@dataclass(frozen=True)
class Transition:
    from_state: MRState
    to_state: MRState
    trigger: MRTrigger
    reason: str
    data: dict[str, Any]


@dataclass(frozen=True)
class FSMConfig:
    max_wait_reversao_minutes: int = 30
    cooldown_minutes: int = 10
    rsi_period: int = 14
    ema_period: int = 50
    vwap_period: int = 20
    # Opcional (não deve bloquear por padrão)
    require_below_ema_or_vwap: bool = False
    # Estrutural mínimo (padrão: ligado)
    require_momentum_fade: bool = True
    # Fundo válido: pivot_low deve ser o menor low em uma janela recente (sem lookahead).
    pivot_low_lookback: int = 5
    # Regime: bloquear entradas MR em tendência forte.
    require_mr_regime: bool = True
    # Se True, recalcula microRes/ATR/stop/limit durante a reversão (equivale a "modificar" a ordem).
    # Por padrão fica False para manter a stop-limit como prova de mercado fixa até fill/expiração/invalidação.
    update_stop_limit_each_bar: bool = False
    confirm_fill_via_exchange: bool = False
    min_bars: int = 40
    micro_res_n: int = 3
    atr_period: int = 14
    k_stop: float = 0.10
    k_limit: float = 0.05
    order_expiry_minutes: int = 30
    max_bars_reversao: int = 5
    k_inv: float = 0.20
    k_sl: float = 0.20


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ReversalSetup:
    pivot_pos: int
    pivot_low: float
    timestamp: Any
    rsi: float | None = None
    ema: float | None = None
    vwap: float | None = None
    meta: dict[str, Any] | None = None


@dataclass(frozen=True)
class StopLimitIntent:
    symbol: str
    side: str
    stop: float
    limit: float
    micro_res: float
    atr: float
    created_at: datetime
    expires_at: datetime
    pivot_pos: int
    active_from_pos: int
    computed_from_pos: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "stop": self.stop,
            "limit": self.limit,
            "micro_res": self.micro_res,
            "atr": self.atr,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "pivot_pos": self.pivot_pos,
            "active_from_pos": self.active_from_pos,
            "computed_from_pos": self.computed_from_pos,
        }


@dataclass(frozen=True)
class PositionPlan:
    side: str
    entry_price: float
    stop_loss: float
    atr: float
    pivot_low: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "side": self.side,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "atr": self.atr,
            "pivot_low": self.pivot_low,
        }


def _as_ohlcv_df(df: Any):
    try:
        import pandas as pd

        if df is None or not isinstance(df, pd.DataFrame):
            return None
        needed = {"open", "high", "low", "close", "volume"}
        if not needed.issubset(set(df.columns)):
            return None
        out = df.copy()
        for col in ("open", "high", "low", "close", "volume"):
            out[col] = out[col].astype(float)
        return out
    except Exception:
        return None


def _rsi(series, period: int):
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _ema(series, period: int):
    return series.ewm(span=period, adjust=False).mean()


def _vwap(df, period: int):
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = typical * df["volume"]
    return pv.rolling(window=period).sum() / df["volume"].rolling(window=period).sum()


def _extract_regime(indicators: dict[str, Any] | None) -> str | None:
    if not indicators:
        return None
    for key in ("regime", "market_regime", "regime_mercado", "tendencia_macro", "contexto_mercado"):
        value = indicators.get(key)
        if value:
            return str(value)
    return None


def _is_mr_regime(indicators: dict[str, Any] | None) -> bool:
    """Permite Mean Reversion se não parecer tendência forte/direcional."""
    regime = _extract_regime(indicators)
    if not regime:
        return True  # fallback permissivo: não bloqueia o armamento
    r = regime.strip().lower()
    strong_trend_tokens = (
        "tendencia forte",
        "trend strong",
        "tendencia_direcional",
        "direcional forte",
        "strong trend",
        "bull trend",
        "bear trend",
    )
    if any(tok in r for tok in strong_trend_tokens):
        return False
    ok_tokens = ("lateral", "range", "exaust", "consolida", "choppy")
    if any(tok in r for tok in ok_tokens):
        return True
    return True


def _atr(df, period: int):
    import pandas as pd

    high = df["high"]
    low = df["low"]
    close = df["close"]
    tr = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window=period).mean()


def compute_stop_limit_intent(
    *,
    market: MarketSnapshot,
    setup: ReversalSetup,
    cfg: FSMConfig,
    side: str = "buy",
    computed_from_pos: int,
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> StopLimitIntent | None:
    """Calcula microRes + stop/limit (sem lookahead) e retorna uma intenção de ordem.

    Regra de não-lookahead:
    - considera apenas candles fechados até `last_closed_pos = len(df) - 2`.

    Micro-resistência:
    - microRes = max(high dos últimos N candles desde o fundo)
    - aqui: janela começa no candle do fundo (`setup.pivot_pos`) e vai até o último candle fechado,
      limitada a N candles no total (N=cfg.micro_res_n).
    """
    df = _as_ohlcv_df(market.df)
    if df is None:
        return None

    computed_from_pos = int(computed_from_pos)
    pivot_pos = int(setup.pivot_pos)
    if computed_from_pos < pivot_pos:
        return None

    n = max(1, int(cfg.micro_res_n))
    end = computed_from_pos + 1
    start = max(pivot_pos, computed_from_pos - (n - 1))

    if end <= start:
        return None

    micro_res = float(df["high"].iloc[start:end].max())

    atr_s = _atr(df, period=int(cfg.atr_period))
    atr_v = float(atr_s.iloc[computed_from_pos])
    if atr_v != atr_v or atr_v <= 0:  # NaN / inválido
        return None

    stop = micro_res + atr_v * float(cfg.k_stop)
    limit = stop + atr_v * float(cfg.k_limit)

    created_at_final = created_at or market.now
    expires_at_final = expires_at or (created_at_final + timedelta(minutes=int(cfg.order_expiry_minutes)))
    active_from_pos = computed_from_pos + 1

    return StopLimitIntent(
        symbol=market.symbol,
        side=str(side).lower(),
        stop=float(stop),
        limit=float(limit),
        micro_res=micro_res,
        atr=atr_v,
        created_at=created_at_final,
        expires_at=expires_at_final,
        pivot_pos=pivot_pos,
        active_from_pos=active_from_pos,
        computed_from_pos=computed_from_pos,
    )


def _last_closed_pos(df) -> int:
    return len(df) - 2


def _candle_ohlc(df, pos: int) -> dict[str, float]:
    return {
        "open": float(df["open"].iloc[pos]),
        "high": float(df["high"].iloc[pos]),
        "low": float(df["low"].iloc[pos]),
        "close": float(df["close"].iloc[pos]),
    }


def evaluate_stop_limit_fill(
    *,
    df,
    candle_pos: int,
    intent: StopLimitIntent,
) -> tuple[bool, dict[str, Any]]:
    """Backtest conservador para BUY stop-limit com OHLC.

    Sem tick data, a sequência intrabar é desconhecida.
    - Se abre acima do limit: assume "pulou" (não preenche).
    - Fill se: high >= stop E low <= limit (houve negociação <= limit).
    - Entrada assumida: limit (pior caso).
    """
    ohlc = _candle_ohlc(df, candle_pos)
    if intent.side != "buy":
        return False, {"reason": "apenas_buy_implementado", "ohlc": ohlc}

    if ohlc["open"] > intent.limit:
        return False, {"reason": "gap_acima_do_limit", "ohlc": ohlc}

    if ohlc["high"] >= intent.stop and ohlc["low"] <= intent.limit:
        return True, {"reason": "fill_conservador", "entry_price": float(intent.limit), "ohlc": ohlc}

    return False, {"reason": "nao_preenchida", "ohlc": ohlc}


def detect_fundo_potencial_setup(market: MarketSnapshot, cfg: FSMConfig) -> ReversalSetup | None:
    """Detecção base de fundo (sem lookahead) para transição Idle -> ReversaoEmFormacao.

    Armazena apenas informações mínimas do "fundo" para futura confirmação:
    - índice (posição) do candle do fundo no DataFrame
    - fundoLow
    - timestamp

    Regra de não-lookahead:
    - usa somente o último candle FECHADO: `pivot_pos = len(df) - 2`
    """
    df = _as_ohlcv_df(market.df)
    if df is None:
        return None

    min_needed = max(
        int(cfg.min_bars),
        int(cfg.rsi_period) + 5,
        int(cfg.ema_period) + 5,
        int(cfg.vwap_period) + 5,
    )
    if len(df) < min_needed:
        return None

    pivot_pos = len(df) - 2  # último candle fechado
    if pivot_pos < 3:
        return None

    close = df["close"]
    rsi_s = _rsi(close, period=int(cfg.rsi_period))
    ema_s = _ema(close, period=int(cfg.ema_period))
    vwap_s = _vwap(df, period=int(cfg.vwap_period))

    rsi_v = float(rsi_s.iloc[pivot_pos])
    ema_v = float(ema_s.iloc[pivot_pos])
    vwap_v = float(vwap_s.iloc[pivot_pos])

    if rsi_v != rsi_v:  # NaN
        return None
    if cfg.require_mr_regime and not _is_mr_regime(market.indicators):
        return None

    # Fundo válido (estrutural, sem lookahead): pivot_low é o menor low em uma janela recente.
    pivot_low = float(df["low"].iloc[pivot_pos])
    lb = max(2, int(cfg.pivot_low_lookback))
    start = max(0, pivot_pos - (lb - 1))
    window_min = float(df["low"].iloc[start : pivot_pos + 1].min())
    if pivot_low > window_min:
        return None

    last_close = float(close.iloc[pivot_pos])
    if cfg.require_below_ema_or_vwap:
        if not (last_close < ema_v or last_close < vwap_v):
            return None

    # Perda de momentum vendedor: 2 quedas seguidas, com a última menor em magnitude.
    if cfg.require_momentum_fade:
        d1 = float(close.iloc[pivot_pos] - close.iloc[pivot_pos - 1])
        d2 = float(close.iloc[pivot_pos - 1] - close.iloc[pivot_pos - 2])
        if not (d1 < 0 and d2 < 0 and abs(d1) < abs(d2)):
            return None

    # pivot_low já calculado acima
    if "time" in df.columns:
        ts = df["time"].iloc[pivot_pos]
    else:
        ts = df.index[pivot_pos]

    return ReversalSetup(
        pivot_pos=int(pivot_pos),
        pivot_low=float(pivot_low),
        timestamp=ts,
        rsi=rsi_v,
        ema=ema_v,
        vwap=vwap_v,
        meta={
            "require_below_ema_or_vwap": bool(cfg.require_below_ema_or_vwap),
            "require_momentum_fade": bool(cfg.require_momentum_fade),
            "pivot_low_lookback": int(cfg.pivot_low_lookback),
            "require_mr_regime": bool(cfg.require_mr_regime),
            "regime": _extract_regime(market.indicators),
        },
    )


class MeanReversionStateMachine:
    """Máquina de estados (esqueleto) para Mean Reversion.

    Objetivos:
    - estados explícitos e centralização de transições
    - separa leitura de mercado / confirmação / execução (placeholders)
    - nenhuma ordem é enviada aqui

    Integração típica (loop):
      fsm = MeanReversionStateMachine(...)
      while True:
          snapshot = MarketSnapshot(...)
          pos = PositionSnapshot(...)
          transitions = fsm.step(snapshot, pos)
          # usar transitions para logs e para acionar módulos de execução (em outro arquivo)
    """

    def __init__(
        self,
        *,
        cfg: FSMConfig | None = None,
        now_fn: Callable[[], datetime] = _utc_now,
    ):
        self.cfg = cfg or FSMConfig()
        self._now_fn = now_fn

        self.state: MRState = MRState.IDLE
        self.state_since: datetime = self._now_fn()
        self.last_transition: Transition | None = None

        self._armed_at: datetime | None = None
        self._cooldown_until: datetime | None = None
        self.reversal_setup: ReversalSetup | None = None
        self.stop_limit_intent: StopLimitIntent | None = None
        self.position_plan: PositionPlan | None = None

    def step(self, market: MarketSnapshot, position: PositionSnapshot) -> list[Transition]:
        """Executa 1 ciclo de decisão e aplica transições, se houver."""
        transitions: list[Transition] = []

        if self.state == MRState.IDLE:
            if position.in_position:
                transitions.append(self._transition(MRState.EM_POSICAO, MRTrigger.ENTRADA_CONFIRMADA, "posicao_detectada"))
                return transitions

            setup = detect_fundo_potencial_setup(market, self.cfg)
            if setup:
                # Ao detectar o fundo (sem lookahead), já arma a stop-limit condicional.
                # A confirmação NÃO vem de indicadores; vem do PREÇO batendo o stop e negociando <= limit.
                intent = compute_stop_limit_intent(
                    market=market,
                    setup=setup,
                    cfg=self.cfg,
                    side="buy",
                    computed_from_pos=int(setup.pivot_pos),
                    created_at=market.now,
                    expires_at=market.now + timedelta(minutes=int(self.cfg.order_expiry_minutes)),
                )
                if intent is None:
                    return transitions

                self._armed_at = market.now
                self.reversal_setup = setup
                self.stop_limit_intent = intent
                self.position_plan = None
                transitions.append(
                    self._transition(
                        MRState.REVERSAO_EM_FORMACAO,
                        MRTrigger.FUNDO_POTENCIAL,
                        "fundo_potencial_detectado",
                        data={
                            "armed_at": market.now.isoformat(),
                            "pivot_pos": setup.pivot_pos,
                            "pivot_low": setup.pivot_low,
                            "timestamp": setup.timestamp,
                            "stop_limit_intent": intent.to_dict(),
                        },
                    )
                )
                return transitions

            return transitions

        if self.state == MRState.REVERSAO_EM_FORMACAO:
            if position.in_position:
                transitions.append(self._transition(MRState.EM_POSICAO, MRTrigger.ENTRADA_CONFIRMADA, "entrada_detectada"))
                return transitions

            df = _as_ohlcv_df(market.df)
            setup = self.reversal_setup
            if df is None or setup is None:
                self._armed_at = None
                self.reversal_setup = None
                self.stop_limit_intent = None
                self.position_plan = None
                transitions.append(self._transition(MRState.IDLE, MRTrigger.RESET, "estado_inconsistente"))
                return transitions

            last_pos = _last_closed_pos(df)
            if last_pos < 0:
                return transitions

            bars_em_reversao = max(0, int(last_pos) - int(setup.pivot_pos) + 1)

            # Arma/atualiza a intenção da stop-limit SEM lookahead:
            # - Os níveis (microRes/ATR/stop/limit) são calculados usando o candle fechado anterior
            #   (`computed_from_pos = last_pos - 1`), portanto a ordem vale para o candle `last_pos`.
            intent = self.stop_limit_intent
            if intent is None:
                self._armed_at = None
                self.reversal_setup = None
                self.stop_limit_intent = None
                self.position_plan = None
                transitions.append(self._transition(MRState.IDLE, MRTrigger.RESET, "estado_inconsistente_intent"))
                return transitions

            if self.cfg.update_stop_limit_each_bar:
                computed_from_pos = int(last_pos) - 1
                if computed_from_pos >= int(setup.pivot_pos):
                    updated = compute_stop_limit_intent(
                        market=market,
                        setup=setup,
                        cfg=self.cfg,
                        side="buy",
                        computed_from_pos=computed_from_pos,
                        created_at=intent.created_at,
                        expires_at=intent.expires_at,
                    )
                    if updated is not None:
                        self.stop_limit_intent = updated
                        intent = updated

            # Prioridade de eventos no mesmo candle:
            # 1. Invalidação do fundo
            # 2. Expiração por tempo
            # 3. Avaliação de fill da stop-limit

            # 1) Invalidação do fundo: low <= fundoLow - atr * kInv
            inval_level = float(setup.pivot_low) - float(intent.atr) * float(self.cfg.k_inv)
            low_now = float(df["low"].iloc[last_pos])
            if low_now < float(setup.pivot_low):
                self._armed_at = None
                self.reversal_setup = None
                self.stop_limit_intent = None
                self.position_plan = None
                transitions.append(
                    self._transition(
                        MRState.IDLE,
                        MRTrigger.INVALIDACAO,
                        "novo_fundo_abaixo_do_pivot",
                        data={"low": low_now, "pivot_low": float(setup.pivot_low), "bars_em_reversao": bars_em_reversao},
                    )
                )
                if log_mr_event:
                    try:
                        log_mr_event(
                            timestamp=market.now,
                            symbol=market.symbol,
                            strategy="MEAN_REVERSION",
                            event="INVALIDOU",
                            motivo="novo_fundo",
                            timeframe=market.timeframe,
                        )
                    except Exception:
                        logger.warning("[MR-TELEM] Falha ao registrar INVALIDOU", exc_info=True)
                return transitions
            if low_now <= inval_level:
                self._armed_at = None
                self.reversal_setup = None
                self.stop_limit_intent = None
                self.position_plan = None
                transitions.append(
                    self._transition(
                        MRState.IDLE,
                        MRTrigger.INVALIDACAO,
                        "fundo_invalidado",
                        data={"low": low_now, "inval_level": inval_level, "bars_em_reversao": bars_em_reversao},
                    )
                )
                if log_mr_event:
                    try:
                        log_mr_event(
                            timestamp=market.now,
                            symbol=market.symbol,
                            strategy="MEAN_REVERSION",
                            event="INVALIDOU",
                            motivo="novo_fundo",
                            timeframe=market.timeframe,
                        )
                    except Exception:
                        logger.warning("[MR-TELEM] Falha ao registrar INVALIDOU", exc_info=True)
                return transitions

            # 2) Expiração: bars_em_reversao >= maxBars (e/ou expires_at, se usado)
            expired_by_bars = bars_em_reversao >= int(self.cfg.max_bars_reversao)
            expired_by_time = market.now >= intent.expires_at
            if expired_by_bars or expired_by_time:
                self._armed_at = None
                self.reversal_setup = None
                self.stop_limit_intent = None
                self.position_plan = None
                transitions.append(
                    self._transition(
                        MRState.IDLE,
                        MRTrigger.TIMEOUT,
                        "reversao_expirada",
                        data={
                            "bars_em_reversao": bars_em_reversao,
                            "max_bars": int(self.cfg.max_bars_reversao),
                            "expired_by_time": bool(expired_by_time),
                        },
                    )
                )
                if log_mr_event:
                    try:
                        log_mr_event(
                            timestamp=market.now,
                            symbol=market.symbol,
                            strategy="MEAN_REVERSION",
                            event="EXPIROU",
                            bars_em_reversao=bars_em_reversao,
                            timeframe=market.timeframe,
                        )
                    except Exception:
                        logger.warning("[MR-TELEM] Falha ao registrar EXPIROU", exc_info=True)
                return transitions

            # 3) Fill: stop atingido e houve negociação <= limit (sem chase)
            if bool(self.cfg.confirm_fill_via_exchange):
                return transitions
            if int(last_pos) < int(intent.active_from_pos):
                return transitions
            filled, fill_meta = evaluate_stop_limit_fill(df=df, candle_pos=last_pos, intent=intent)
            if filled:
                entry_price = float(fill_meta.get("entry_price", intent.limit))
                sl = float(setup.pivot_low) - float(intent.atr) * float(self.cfg.k_sl)
                self.position_plan = PositionPlan(
                    side="buy",
                    entry_price=entry_price,
                    stop_loss=sl,
                    atr=float(intent.atr),
                    pivot_low=float(setup.pivot_low),
                )
                self._armed_at = None
                self.reversal_setup = None
                self.stop_limit_intent = None
                transitions.append(
                    self._transition(
                        MRState.EM_POSICAO,
                        MRTrigger.FILL,
                        "stop_limit_preenchida",
                        data={"fill": fill_meta, "position_plan": self.position_plan.to_dict()},
                    )
                )
                return transitions

            # Placeholder para futura confirmação (ex: RSI saindo de sobrevenda, volume, candle, etc.)
            # Quando você for implementar, gere um evento ENTRADA_CONFIRMADA e deixe o executor enviar a ordem.
            return transitions

        if self.state == MRState.EM_POSICAO:
            if not position.in_position:
                self._cooldown_until = market.now + timedelta(minutes=int(self.cfg.cooldown_minutes))
                self.reversal_setup = None
                self.stop_limit_intent = None
                self.position_plan = None
                transitions.append(self._transition(MRState.COOLDOWN, MRTrigger.SAIDA, "saida_detectada"))
                return transitions
            return transitions

        if self.state == MRState.COOLDOWN:
            if position.in_position:
                transitions.append(self._transition(MRState.EM_POSICAO, MRTrigger.ENTRADA_CONFIRMADA, "posicao_detectada"))
                return transitions

            if self._cooldown_until and market.now >= self._cooldown_until:
                self._cooldown_until = None
                self.reversal_setup = None
                self.stop_limit_intent = None
                self.position_plan = None
                transitions.append(self._transition(MRState.IDLE, MRTrigger.TIMEOUT, "cooldown_encerrado"))
                return transitions
            return transitions

        return transitions

    def apply_exchange_fill(
        self,
        *,
        entry_price: float,
        market_now: datetime,
        fill_meta: dict[str, Any] | None = None,
    ) -> list[Transition]:
        """Aplica um fill confirmado pela exchange (produção), sem usar OHLC."""
        if self.state != MRState.REVERSAO_EM_FORMACAO:
            return []
        setup = self.reversal_setup
        intent = self.stop_limit_intent
        if setup is None or intent is None:
            return []

        sl = float(setup.pivot_low) - float(intent.atr) * float(self.cfg.k_sl)
        self.position_plan = PositionPlan(
            side=str(intent.side).lower(),
            entry_price=float(entry_price),
            stop_loss=float(sl),
            atr=float(intent.atr),
            pivot_low=float(setup.pivot_low),
        )

        self._armed_at = None
        self.reversal_setup = None
        self.stop_limit_intent = None

        meta = dict(fill_meta or {})
        meta.setdefault("entry_price", float(entry_price))
        meta.setdefault("intent", intent.to_dict())

        return [
            self._transition(
                MRState.EM_POSICAO,
                MRTrigger.FILL,
                "stop_limit_preenchida",
                data={"fill": meta, "position_plan": self.position_plan.to_dict(), "filled_at": market_now.isoformat()},
            )
        ]

    def _transition(
        self,
        new_state: MRState,
        trigger: MRTrigger,
        reason: str,
        *,
        data: dict[str, Any] | None = None,
    ) -> Transition:
        prev = self.state
        self.state = new_state
        self.state_since = self._now_fn()
        t = Transition(
            from_state=prev,
            to_state=new_state,
            trigger=trigger,
            reason=reason,
            data=data or {},
        )
        self.last_transition = t
        return t

    def _reversao_timeout(self, now: datetime) -> bool:
        if not self._armed_at:
            return False
        max_wait = timedelta(minutes=int(self.cfg.max_wait_reversao_minutes))
        return now - self._armed_at >= max_wait

    # --------- Placeholders (implementar na estratégia) ---------
    def _detect_fundo_potencial(self, market: MarketSnapshot) -> bool:
        """Detecta 'fundo potencial' (sobresold/exaustão/perda de momentum vendedor).

        Placeholder: retornar False por padrão.
        Integração: usar seus indicadores/heurísticas/ML e retornar True quando quiser armar a reversão.
        """
        _ = market
        return False

    def _invalidate_reversao(self, market: MarketSnapshot) -> bool:
        """Invalida a reversão em formação (ex: rompe o fundo, regime muda, etc.)."""
        _ = market
        return False


def example_usage() -> None:
    fsm = MeanReversionStateMachine(cfg=FSMConfig(max_wait_reversao_minutes=30, cooldown_minutes=10))
    market = MarketSnapshot(symbol="BTCUSDT", timeframe="1m", now=_utc_now(), df=None, indicators=None)
    position = PositionSnapshot(in_position=False)
    transitions = fsm.step(market, position)
    for t in transitions:
        print(t)


if __name__ == "__main__":
    example_usage()
