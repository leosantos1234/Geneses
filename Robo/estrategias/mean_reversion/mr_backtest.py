from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .mean_reversion_fsm import FSMConfig, MRState, MeanReversionStateMachine, MarketSnapshot, PositionSnapshot, _utc_now


@dataclass(frozen=True)
class BacktestTrade:
    entry_pos: int
    entry_price: float
    stop_loss: float
    take_profit: float
    exit_pos: int
    exit_price: float
    pnl_pct: float


@dataclass(frozen=True)
class BacktestResult:
    symbol: str
    total_return: float
    trades: list[BacktestTrade]


def _ensure_df(df):
    import pandas as pd

    needed = {"open", "high", "low", "close", "volume"}
    if not needed.issubset(set(df.columns)):
        raise ValueError(f"DataFrame precisa conter {sorted(needed)}")
    out = df.copy()
    for c in ("open", "high", "low", "close", "volume"):
        out[c] = out[c].astype(float)
    return out


def _conservative_exit(
    *,
    df,
    pos: int,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
) -> float | None:
    """Retorna o preço de saída se houver SL/TP no candle.

    Conservador:
    - se SL e TP "acontecem" no mesmo candle: assume SL primeiro (pior caso para long)
    """
    low = float(df["low"].iloc[pos])
    high = float(df["high"].iloc[pos])
    if low <= stop_loss and high >= take_profit:
        return float(stop_loss)
    if low <= stop_loss:
        return float(stop_loss)
    if high >= take_profit:
        return float(take_profit)
    return None


def backtest_mean_reversion(
    *,
    df,
    symbol: str,
    cfg: FSMConfig | None = None,
    fee_rate: float = 0.0006,
    tp_atr_mult: float = 1.0,
    regime_provider: Callable[[int], dict[str, Any] | None] | None = None,
) -> BacktestResult:
    """Backtest simples event-driven da MR usando a FSM.

    - Executa somente LONG (buy) no momento em que a FSM entra em EmPosicao.
    - Sai por SL (PositionPlan) ou TP (entry + ATR*tp_atr_mult) com OHLC conservador.
    """
    df = _ensure_df(df)
    cfg = cfg or FSMConfig()
    fsm = MeanReversionStateMachine(cfg=cfg)

    trades: list[BacktestTrade] = []
    in_position = False
    entry_pos = -1
    entry_price = 0.0
    stop_loss = 0.0
    take_profit = 0.0

    # Começa com histórico mínimo para indicadores internos.
    start = max(cfg.min_bars, 30)
    for i in range(start, len(df)):
        # MarketSnapshot.df deve representar o "mundo conhecido" no candle i (inclui candle em formação),
        # e a FSM usa sempre o último FECHADO (len-2).
        window = df.iloc[: i + 1].copy()
        indicators = regime_provider(i) if regime_provider else None
        market = MarketSnapshot(symbol=symbol, timeframe="N/A", now=_utc_now(), df=window, indicators=indicators)
        pos = PositionSnapshot(in_position=in_position, side=("buy" if in_position else None), entry_price=entry_price if in_position else None, size=None)

        transitions = fsm.step(market, pos)

        if not in_position:
            # A entrada acontece quando a FSM confirma fill (EmPosicao) e gera PositionPlan.
            if any(t.to_state == MRState.EM_POSICAO for t in transitions) and fsm.position_plan:
                plan = fsm.position_plan
                in_position = True
                entry_pos = i
                entry_price = float(plan.entry_price)
                stop_loss = float(plan.stop_loss)
                take_profit = float(entry_price + float(plan.atr) * float(tp_atr_mult))
            continue

        # Em posição: aplica SL/TP conservador.
        exit_price = _conservative_exit(df=df, pos=i, entry_price=entry_price, stop_loss=stop_loss, take_profit=take_profit)
        if exit_price is None:
            continue

        # PnL com fee ida+volta.
        raw_ret = (exit_price - entry_price) / entry_price
        net_ret = raw_ret - float(fee_rate) * 2.0
        trades.append(
            BacktestTrade(
                entry_pos=int(entry_pos),
                entry_price=float(entry_price),
                stop_loss=float(stop_loss),
                take_profit=float(take_profit),
                exit_pos=int(i),
                exit_price=float(exit_price),
                pnl_pct=float(net_ret),
            )
        )

        # reseta posição e FSM volta para idle (conservador: reseta manualmente para permitir novos sinais)
        in_position = False
        entry_pos = -1
        entry_price = 0.0
        stop_loss = 0.0
        take_profit = 0.0
        fsm.state = MRState.IDLE
        fsm.reversal_setup = None
        fsm.stop_limit_intent = None
        fsm.position_plan = None

    total_return = 0.0
    for t in trades:
        total_return += t.pnl_pct
    return BacktestResult(symbol=symbol, total_return=float(total_return), trades=trades)
