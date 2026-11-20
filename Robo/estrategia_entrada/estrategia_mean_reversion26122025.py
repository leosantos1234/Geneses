from utils.logger_config import configurar_logger_global
configurar_logger_global("mean_reversion.log")
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from regime.regime_configurator import RegimeConfigurator
from regime.regime_detector import RegimeDetector
from utils.entrada_breakout_confirmada import (
    calcular_preco_confirmacao,
    executar_entrada_breakout_confirmada,
)
from utils.indicadores import calculate_indicators, calcular_lote
from utils.logica_stop_take_adaptativo import calcular_stop_take_adaptativo
from utils.trailing_adaptativo import calcular_trailing_adaptativo
from utils.utils_market import get_market_data_bybit
from utils.utils_telegram import send_telegram_message
from utils.validacoes import validar_preco
from estrategia_entrada.pos_execucao_monitoramento import preparar_trade_para_monitoramento

logger = logging.getLogger(__name__)

# =============================
# Regime e risco
# =============================
STRATEGY_NAME = "mean_reversion"
REGIMES_PERMITIDOS = {1, 2}
REGIMES_BLOQUEADOS = {3, 4}
MIN_CANDLES = int(os.getenv("MR_MIN_CANDLES", 80))
RSI_OB = int(os.getenv("MR_RSI_OB", 74))
RSI_OS = int(os.getenv("MR_RSI_OS", 26))
DESLOCAMENTO_MIN = float(os.getenv("MR_DESLOCAMENTO_MIN", 0.006))  # 0.6% vs VWAP
VOL_SPIKE_MULT = float(os.getenv("MR_VOL_SPIKE_MULT", 1.8))
VOL_STABILIZE_MULT = float(os.getenv("MR_VOL_STABILIZE_MULT", 1.05))
WICK_RATIO_MIN = float(os.getenv("MR_WICK_RATIO_MIN", 1.6))
RISK_PCT = float(os.getenv("MR_RISK_PCT", 0.4))  # % do saldo por trade
RRR_MIN = float(os.getenv("MR_RRR_MIN", 2.2))
RRR_EXT_STEP = float(os.getenv("MR_RRR_EXT_STEP", 0.8))
TRAILING_ACTIVATION_R = float(os.getenv("MR_TRAILING_ACTIVATION_R", 1.0))
VWAP_WINDOW = int(os.getenv("MR_VWAP_WINDOW", 60))
ESTRUTURA_LOOKBACK = int(os.getenv("MR_ESTRUTURA_LOOKBACK", 20))
COOLDOWN_SEGUNDOS = int(os.getenv("MR_COOLDOWN_SEGUNDOS", 90))
DEFAULT_SYMBOL = os.getenv("ATIVOS", "BTCUSDT")
DEFAULT_INTERVAL = os.getenv("INTERVAL", "1")
_ULTIMA_EXECUCAO_TS: Dict[str, float] = {}


@dataclass
class TradePlan:
    side: str
    entry_price: float
    stop_loss: float
    take_profit: float
    trailing_offset: float
    qty: float
    regime: int
    contexto: Dict[str, float] = field(default_factory=dict)
    trailing_activation: float = 0.0
    tp_extensions: List[float] = field(default_factory=list)


@dataclass
class TradeState:
    plan: TradePlan
    entry_time: pd.Timestamp
    filled: bool = False
    trailing_price: Optional[float] = None
    closed: bool = False
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    realized_rr: float = 0.0


# =============================
# Helpers
# =============================
def _calc_true_range(row: pd.Series, prev_close: float) -> float:
    high_low = row["high"] - row["low"]
    high_close = abs(row["high"] - prev_close)
    low_close = abs(row["low"] - prev_close)
    return max(high_low, high_close, low_close)


def _ensure_true_range(df: pd.DataFrame) -> pd.DataFrame:
    if "TrueRange" in df.columns:
        return df
    closes = df["close"].shift(1).fillna(df["close"])
    df["TrueRange"] = df.apply(lambda row: _calc_true_range(row, closes.loc[row.name]), axis=1)
    return df


def _calcular_vwap(df: pd.DataFrame, window: int = VWAP_WINDOW) -> pd.Series:
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    volume = df["volume"].replace(0, np.nan)
    vwap = (typical_price * volume).rolling(window).sum() / volume.rolling(window).sum()
    return vwap.bfill().ffill()


def _avaliar_regime(df: pd.DataFrame, regime_forcado: Optional[int] = None) -> int:
    detector = RegimeDetector()
    configurador = RegimeConfigurator()
    regime = int(regime_forcado) if regime_forcado is not None else detector.detectar(df)
    regime_config = configurador.get(regime)
    if not regime_config.get("permissao_operar", False):
        logger.info("[MR] Regime sem permissao: %s", regime)
    return regime


def _wick_metrics(row: pd.Series) -> Tuple[float, float, float]:
    corpo = abs(row["close"] - row["open"])
    wick_sup = row["high"] - max(row["close"], row["open"])
    wick_inf = min(row["close"], row["open"]) - row["low"]
    corpo = max(corpo, 1e-9)
    return wick_sup / corpo, wick_inf / corpo, corpo


def _detectar_excesso(df: pd.DataFrame) -> Optional[Dict]:
    if len(df) < MIN_CANDLES:
        return None

    ultimo = df.iloc[-1]
    penultimo_close = df["close"].iloc[-2]
    tr_atual = _calc_true_range(ultimo, penultimo_close)
    df = _ensure_true_range(df)
    tr_mediana = df["TrueRange"].rolling(20).median().iloc[-1]

    vwap = _calcular_vwap(df)
    vwap_atual = float(vwap.iloc[-1]) if not vwap.empty else ultimo["close"]
    ema50 = float(df.get("EMA_50", pd.Series([np.nan])).iloc[-1]) if "EMA_50" in df.columns else np.nan
    ema200 = float(df.get("EMA_200", pd.Series([np.nan])).iloc[-1]) if "EMA_200" in df.columns else np.nan

    deslocamento_pct = (ultimo["close"] - vwap_atual) / vwap_atual
    rsi = float(ultimo.get("RSI", 50))
    wick_sup_ratio, wick_inf_ratio, corpo = _wick_metrics(ultimo)

    volatilidade_spike = tr_atual > tr_mediana * VOL_SPIKE_MULT
    volatilidade_estavel = tr_atual <= tr_mediana * VOL_STABILIZE_MULT

    side = None
    score = 0.0

    if deslocamento_pct > DESLOCAMENTO_MIN and rsi >= RSI_OB and volatilidade_spike and volatilidade_estavel and wick_sup_ratio >= WICK_RATIO_MIN:
        side = "sell"
    elif deslocamento_pct < -DESLOCAMENTO_MIN and rsi <= RSI_OS and volatilidade_spike and volatilidade_estavel and wick_inf_ratio >= WICK_RATIO_MIN:
        side = "buy"

    if side is None:
        return None

    score += min(abs(deslocamento_pct) / max(DESLOCAMENTO_MIN, 1e-9), 3)
    score += 1.0 if volatilidade_spike else 0
    score += 0.5 if volatilidade_estavel else 0
    score += 0.5 if (wick_sup_ratio if side == "sell" else wick_inf_ratio) >= WICK_RATIO_MIN else 0
    score += 0.5 if rsi >= RSI_OB or rsi <= RSI_OS else 0
    score = round(score, 2)

    return {
        "side": side,
        "score": score,
        "entry_reference": float(ultimo["close"]),
        "tr": float(tr_atual),
        "vwap": float(vwap_atual),
        "ema50": ema50,
        "ema200": ema200,
        "rsi": rsi,
        "deslocamento_pct": deslocamento_pct,
        "wick_ratio": wick_sup_ratio if side == "sell" else wick_inf_ratio,
    }


def _montar_plano(df: pd.DataFrame, sinal: Dict, regime: int, capital: float) -> Optional[TradePlan]:
    side = sinal["side"]
    entry_ref = sinal["entry_reference"]
    atr = float(df["ATR"].iloc[-1]) if "ATR" in df.columns else max(sinal.get("tr", 0), 1e-8)

    qty = calcular_lote(capital, entry_ref, atr, risk_percentage=RISK_PCT)
    if qty is None or qty <= 0:
        logger.warning("[MR] Quantidade invalida calculada. capital=%s entry=%s atr=%s", capital, entry_ref, atr)
        return None

    stop_loss, take_profit, _, _ = calcular_stop_take_adaptativo(
        df=df,
        entry_price=entry_ref,
        side=side,
        params={"rrr": max(RRR_MIN, 2.0), "atr_mult": 1.1, "sl_pct_max": 0.012, "sl_pct_min": 0.004},
        tipo_stop="ATR",
    )

    if stop_loss is None or take_profit is None:
        logger.warning("[MR] Falha ao calcular SL/TP adaptativo.")
        return None

    risco = abs(entry_ref - stop_loss)
    take_profit_min = entry_ref + risco * RRR_MIN if side == "buy" else entry_ref - risco * RRR_MIN
    take_profit = take_profit if (side == "buy" and take_profit >= take_profit_min) or (side == "sell" and take_profit <= take_profit_min) else take_profit_min

    trailing_offset = calcular_trailing_adaptativo(atr, "ATR")
    trailing_activation = entry_ref + risco * TRAILING_ACTIVATION_R if side == "buy" else entry_ref - risco * TRAILING_ACTIVATION_R

    tp_vwap = sinal.get("vwap")
    if tp_vwap:
        if side == "buy":
            take_profit = max(take_profit, min(tp_vwap, entry_ref + risco * (RRR_MIN + 0.5)))
        else:
            take_profit = min(take_profit, max(tp_vwap, entry_ref - risco * (RRR_MIN + 0.5)))

    return TradePlan(
        side=side,
        entry_price=entry_ref,
        stop_loss=float(stop_loss),
        take_profit=float(take_profit),
        trailing_offset=float(trailing_offset),
        qty=float(qty),
        regime=regime,
        trailing_activation=float(trailing_activation),
        contexto={"atr": atr, "score": sinal.get("score", 0), "deslocamento_pct": sinal.get("deslocamento_pct", 0)},
    )


def _simular_preenchimento_stop(candle: pd.Series, side: str, trigger: float) -> bool:
    if side == "buy":
        return candle["high"] >= trigger
    return candle["low"] <= trigger


def _atualizar_trailing(position: TradeState, candle: pd.Series) -> None:
    plan = position.plan
    price_high = candle["high"]
    price_low = candle["low"]

    if not position.filled:
        return

    if position.trailing_price is None:
        move = price_high - plan.entry_price if plan.side == "buy" else plan.entry_price - price_low
        if move >= abs(plan.entry_price - plan.stop_loss) * TRAILING_ACTIVATION_R:
            position.trailing_price = plan.entry_price + plan.trailing_offset if plan.side == "buy" else plan.entry_price - plan.trailing_offset
            logger.info("[MR] Trailing ativado em %.4f", position.trailing_price)
        return

    if plan.side == "buy":
        nova_trailing = price_high - plan.trailing_offset
        if nova_trailing > position.trailing_price:
            position.trailing_price = nova_trailing
    else:
        nova_trailing = price_low + plan.trailing_offset
        if nova_trailing < position.trailing_price:
            position.trailing_price = nova_trailing


def _verificar_rompimento_estrutural(df: pd.DataFrame, side: str) -> bool:
    if len(df) < ESTRUTURA_LOOKBACK + 2:
        return False
    recent = df.tail(ESTRUTURA_LOOKBACK)
    penultimo = df.iloc[-2]
    if side == "buy":
        nivel = recent["high"].max()
        return penultimo["close"] > nivel and df.iloc[-1]["close"] > nivel
    nivel = recent["low"].min()
    return penultimo["close"] < nivel and df.iloc[-1]["close"] < nivel


def _ajustar_tp_dinamico(state: TradeState, df: pd.DataFrame) -> None:
    if state.trailing_price is None:
        return
    plan = state.plan
    if plan.side == "buy":
        if df["close"].iloc[-1] <= plan.entry_price:
            return
    else:
        if df["close"].iloc[-1] >= plan.entry_price:
            return

    if not _verificar_rompimento_estrutural(df, plan.side):
        return

    risco = abs(plan.entry_price - plan.stop_loss)
    novo_tp = plan.entry_price + risco * (RRR_MIN + RRR_EXT_STEP * (len(plan.tp_extensions) + 1)) if plan.side == "buy" else plan.entry_price - risco * (RRR_MIN + RRR_EXT_STEP * (len(plan.tp_extensions) + 1))

    if (plan.side == "buy" and novo_tp > plan.take_profit) or (plan.side == "sell" and novo_tp < plan.take_profit):
        plan.tp_extensions.append(novo_tp)
        plan.take_profit = novo_tp
        # TP só avança após rompimento estrutural validado; nunca recua para evitar devolução de ganho.
        logger.info("[MR] TP estendido para %.4f apos rompimento estrutural", novo_tp)

    # Trailing soberano: se trailing encostar no TP, o TP é reposicionado à frente e nunca para trás.
    if plan.side == "buy" and state.trailing_price >= plan.take_profit:
        plan.take_profit = state.trailing_price + plan.trailing_offset
    if plan.side == "sell" and state.trailing_price <= plan.take_profit:
        plan.take_profit = state.trailing_price - plan.trailing_offset


def _processar_saida(position: TradeState, candle: pd.Series) -> None:
    if position.closed or not position.filled:
        return
    plan = position.plan
    high = candle["high"]
    low = candle["low"]

    if position.trailing_price is not None:
        if plan.side == "buy" and low <= position.trailing_price:
            position.closed = True
            position.exit_price = position.trailing_price
            position.exit_reason = "trailing_stop"
        elif plan.side == "sell" and high >= position.trailing_price:
            position.closed = True
            position.exit_price = position.trailing_price
            position.exit_reason = "trailing_stop"

    if not position.closed:
        if plan.side == "buy" and low <= plan.stop_loss:
            position.closed = True
            position.exit_price = plan.stop_loss
            position.exit_reason = "stop_loss"
        elif plan.side == "sell" and high >= plan.stop_loss:
            position.closed = True
            position.exit_price = plan.stop_loss
            position.exit_reason = "stop_loss"

    if not position.closed:
        if plan.side == "buy" and high >= plan.take_profit:
            position.closed = True
            position.exit_price = plan.take_profit
            position.exit_reason = "take_profit"
        elif plan.side == "sell" and low <= plan.take_profit:
            position.closed = True
            position.exit_price = plan.take_profit
            position.exit_reason = "take_profit"

    if position.closed and position.exit_price:
        risco = abs(plan.entry_price - plan.stop_loss)
        ganho = position.exit_price - plan.entry_price if plan.side == "buy" else plan.entry_price - position.exit_price
        position.realized_rr = ganho / risco if risco > 0 else 0


def criar_backtest_reversao(session, api_key, api_secret) -> Callable:
    def executar(df: pd.DataFrame, direcao: Optional[str] = None, capital_inicial: float = 10000) -> Dict:
        work_df = df.copy()
        work_df = calculate_indicators(work_df)
        if work_df is None or work_df.empty:
            return {"lucro": 0, "trades": [], "mensagem": "indicadores_invalidos"}

        work_df = _ensure_true_range(work_df)

        capital = capital_inicial
        trades: List[Dict] = []
        position: Optional[TradeState] = None
        cooldown_until = 0

        for idx in range(MIN_CANDLES, len(work_df)):
            candle_df = work_df.iloc[: idx + 1]
            candle = candle_df.iloc[-1]

            # Cooldown simples e determinístico evita múltiplas entradas no mesmo movimento corretivo.
            if time.time() < cooldown_until:
                continue

            regime = _avaliar_regime(candle_df)
            if regime in REGIMES_BLOQUEADOS or regime not in REGIMES_PERMITIDOS:
                continue

            if position and position.filled:
                _atualizar_trailing(position, candle)
                _ajustar_tp_dinamico(position, candle_df)
                _processar_saida(position, candle)
                if position.closed:
                    pnl = (position.exit_price - position.plan.entry_price) * position.plan.qty if position.plan.side == "buy" else (position.plan.entry_price - position.exit_price) * position.plan.qty
                    capital += pnl
                    trades.append({
                        "entry": position.plan.entry_price,
                        "exit": position.exit_price,
                        "side": position.plan.side,
                        "motivo": position.exit_reason,
                        "rr": position.realized_rr,
                        "regime": position.plan.regime,
                    })
                    position = None
                    cooldown_until = time.time() + COOLDOWN_SEGUNDOS
                continue

            if position and not position.filled:
                trigger = position.plan.entry_price
                if _simular_preenchimento_stop(candle, position.plan.side, trigger):
                    position.filled = True
                continue

            sinal = _detectar_excesso(candle_df)
            if sinal is None:
                # Elegibilidade é condicional: mesmo em regimes 1/2, sem confluência de excesso não há trade.
                continue
            if direcao and sinal["side"] != direcao.lower():
                continue

            plano = _montar_plano(candle_df, sinal, regime, capital)
            if plano is None:
                continue

            trigger_price = calcular_preco_confirmacao(
                preco_breakout=plano.entry_price,
                side=plano.side,
                tr=sinal.get("tr", plano.contexto.get("atr", 0)),
            )

            position = TradeState(plan=plano, entry_time=candle_df["time"].iloc[-1])
            position.plan.entry_price = trigger_price

        return {"lucro": capital - capital_inicial, "trades": trades, "capital_final": capital}

    return executar


def _fmt_preco(valor: float, tick: float) -> float:
    if tick <= 0:
        return valor
    casas = max(0, len(str(tick).split(".")[-1].rstrip("0")))
    return round(round(valor / tick) * tick, casas)


def executar_mean_reversion_realtime(
    session,
    symbol: str = DEFAULT_SYMBOL,
    interval: str = DEFAULT_INTERVAL,
    regime_atual: Optional[int] = None,
    saldo_referencia: Optional[float] = None,
) -> Optional[Dict]:
    now = time.time()
    ultimo = _ULTIMA_EXECUCAO_TS.get(symbol, 0)
    # Cooldown explícito também no tempo real para não empilhar entradas no mesmo pullback.
    if now - ultimo < COOLDOWN_SEGUNDOS:
        logger.info("[MR] Cooldown ativo para %s; nenhuma nova entrada.", symbol)
        return None

    df = get_market_data_bybit(symbol, session=session, interval=interval, limit=max(150, MIN_CANDLES))
    if df is None or df.empty:
        logger.warning("[MR] Sem dados de mercado para %s", symbol)
        return None

    df = calculate_indicators(df)
    if df is None or df.empty:
        logger.warning("[MR] Falha ao calcular indicadores para %s", symbol)
        return None

    df = _ensure_true_range(df)

    regime = _avaliar_regime(df, regime_atual)
    if regime in REGIMES_BLOQUEADOS or regime not in REGIMES_PERMITIDOS:
        logger.info("[MR] Regime %s bloqueado. Nenhuma acao.", regime)
        return None

    sinal = _detectar_excesso(df)
    if sinal is None:
        logger.info("[MR] Nenhum sinal de reversao valido.")
        return None

    saldo = saldo_referencia if saldo_referencia is not None else float(os.getenv("MR_SALDO_REFERENCIA", 10000))
    plano = _montar_plano(df, sinal, regime, saldo)
    if plano is None:
        return None

    preco_trigger = calcular_preco_confirmacao(plano.entry_price, plano.side, sinal.get("tr", plano.contexto.get("atr", 0)))
    if not validar_preco(preco_trigger, symbol):
        return None

    entrada = executar_entrada_breakout_confirmada(
        api=session,
        symbol=symbol,
        side=plano.side,
        preco_breakout=plano.entry_price,
        tr=sinal.get("tr", plano.contexto.get("atr", 0)),
        qty=plano.qty,
    )

    if entrada is None:
        logger.info("[MR] Entrada nao confirmada para %s", symbol)
        return None

    plano.entry_price = float(entrada.get("entry_price", preco_trigger))
    price_info = session.get_instruments_info(category="linear", symbol=symbol)
    tick_size = float(price_info.get("result", {}).get("list", [{}])[0].get("priceFilter", {}).get("tickSize", 0.01))

    sl_fmt = _fmt_preco(plano.stop_loss, tick_size)
    tp_fmt = _fmt_preco(plano.take_profit, tick_size)
    trailing_fmt = _fmt_preco(plano.trailing_offset, tick_size)

    try:
        payload = {
            "category": "linear",
            "symbol": symbol,
            "stopLoss": sl_fmt,
            "takeProfit": tp_fmt,
            "trailingStop": trailing_fmt,
            "slTriggerBy": "LastPrice",
            "tpTriggerBy": "LastPrice",
        }
        session.set_trading_stop(**payload)
    except Exception as e:
        logger.warning("[MR] Falha ao ajustar SL/TP/Trailing: %s", e)

    indicadores_formatados = df.tail(1).copy()
    indicadores_formatados["entry_score"] = sinal.get("score", 0)
    try:
        preparar_trade_para_monitoramento(
            session=session,
            order_id=entrada.get("orderId"),
            symbol=symbol,
            trade_id=entrada.get("orderId"),
            strategy=STRATEGY_NAME,
            qty=float(entrada.get("qty", plano.qty)),
            indicadores_df=indicadores_formatados,
        )
    except Exception as e:
        logger.warning("[MR] Falha ao iniciar monitoramento inteligente: %s", e)

    try:
        msg = (
            f"{STRATEGY_NAME.upper()} EXECUTADA\n"
            f"Ativo: {symbol}\n"
            f"Regime: {regime}\n"
            f"Side: {plano.side.upper()}\n"
            f"Entrada: {plano.entry_price:.4f}\n"
            f"SL: {sl_fmt:.4f} | TP: {tp_fmt:.4f}\n"
            f"Trailing: {trailing_fmt:.4f} (ativacao {plano.trailing_activation:.4f})"
        )
        send_telegram_message(msg)
    except Exception:
        pass

    _ULTIMA_EXECUCAO_TS[symbol] = now

    return {
        "status": "executado",
        "symbol": symbol,
        "side": plano.side,
        "entry_price": plano.entry_price,
        "stop_loss": sl_fmt,
        "take_profit": tp_fmt,
        "trailing": trailing_fmt,
        "regime": regime,
        "rrr_min": RRR_MIN,
        "rrr_ext_step": RRR_EXT_STEP,
        "risk_atr": plano.contexto.get("atr"),
        "qty": plano.qty,
    }


def mean_reversion_sl_tp(candle_df: pd.DataFrame, saldo: float, estado: Dict) -> Tuple[str, float, Dict]:
    estado = estado or {"posicao": None}
    df = candle_df.copy()
    df = calculate_indicators(df)
    if df is None or df.empty:
        return "HOLD", saldo, estado

    df = _ensure_true_range(df)

    if estado.get("posicao"):
        side = estado.get("posicao")
        stop = estado.get("stop_loss")
        tp = estado.get("take_profit")
        trailing = estado.get("trailing_price")
        candle = df.iloc[-1]
        faux_plan = TradePlan(side=side, entry_price=estado.get("entry_price", candle["close"]), stop_loss=stop, take_profit=tp, trailing_offset=estado.get("trailing_offset", 0), qty=estado.get("qty", 0), regime=1)
        faux_state = TradeState(plan=faux_plan, entry_time=df["time"].iloc[-1], filled=True, trailing_price=trailing)
        _atualizar_trailing(faux_state, candle)
        _ajustar_tp_dinamico(faux_state, df)
        _processar_saida(faux_state, candle)
        estado.update({"trailing_price": faux_state.trailing_price})
        if faux_state.closed:
            saldo += (faux_state.exit_price - faux_plan.entry_price) * faux_plan.qty if side == "buy" else (faux_plan.entry_price - faux_state.exit_price) * faux_plan.qty
            estado = {"posicao": None}
            return "EXIT", saldo, estado
        return "HOLD", saldo, estado

    sinal = _detectar_excesso(df)
    if sinal is None:
        return "HOLD", saldo, estado

    regime = _avaliar_regime(df)
    if regime in REGIMES_BLOQUEADOS or regime not in REGIMES_PERMITIDOS:
        return "HOLD", saldo, estado

    plano = _montar_plano(df, sinal, regime, saldo)
    if plano is None:
        return "HOLD", saldo, estado

    estado = {
        "posicao": plano.side,
        "entry_price": plano.entry_price,
        "stop_loss": plano.stop_loss,
        "take_profit": plano.take_profit,
        "trailing_offset": plano.trailing_offset,
        "trailing_price": None,
        "qty": plano.qty,
    }
    return ("BUY" if plano.side == "buy" else "SELL"), saldo, estado


def monitorar_ordens(session=None, symbol: str = DEFAULT_SYMBOL, interval: str = DEFAULT_INTERVAL):
    try:
        return executar_mean_reversion_realtime(session=session, symbol=symbol, interval=interval)
    except Exception as e:
        logger.error("[MR] Erro no monitoramento: %s", e, exc_info=True)
        return None


__all__ = [
    "criar_backtest_reversao",
    "executar_mean_reversion_realtime",
    "mean_reversion_sl_tp",
    "monitorar_ordens",
]
