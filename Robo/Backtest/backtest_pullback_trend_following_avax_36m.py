"""Backtest read-only do Pullback Trend Following em AVAX por 36 meses."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from backtest_breakout_trend_following_avax_36m import (
    COST_PER_TRADE,
    DATA,
    INITIAL,
    attach_context,
    enrich,
    load,
)


STOP_ATR = 1.8
TARGET_ATR = 3.0
TRAIL_ATR = 2.0
VOLUME_MIN = 0.8
COOLDOWN_AFTER_STOP = 3


def add_pullback_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    out = enrich(frame)
    delta = out["close"].diff()
    gain = delta.clip(lower=0).rolling(14, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).rolling(14, min_periods=14).mean()
    out["rsi14"] = 100.0 - (100.0 / (1.0 + gain / loss.replace(0, pd.NA)))
    out["rsi14"] = out["rsi14"].fillna(50.0)
    return out


def mark_to_market(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"trades": 0, "wins": 0, "winrate_pct": 0.0, "gross_return_pct": 0.0, "compound_return_pct": 0.0, "max_drawdown_pct": 0.0}
    balance = INITIAL
    peak = balance
    max_dd = 0.0
    for pnl in trades.sort_values("exit_time")["pnl_pct"]:
        balance *= 1.0 + float(pnl) / 100.0 - COST_PER_TRADE
        peak = max(peak, balance)
        max_dd = min(max_dd, balance / peak - 1.0)
    wins = int((trades["pnl_pct"] > 0).sum())
    return {"trades": int(len(trades)), "wins": wins, "winrate_pct": wins / len(trades) * 100.0, "gross_return_pct": float(trades["pnl_pct"].sum()), "compound_return_pct": (balance / INITIAL - 1.0) * 100.0, "max_drawdown_pct": max_dd * 100.0}


def run(data: pd.DataFrame) -> pd.DataFrame:
    position = None
    cooldown = 0
    trades = []
    for idx, row in enumerate(data.itertuples(index=False)):
        high, low, close, atr = map(float, (row.high, row.low, row.close, row.atr))
        if position is not None:
            exit_price = None
            reason = None
            if position["side"] == "buy":
                if low <= position["stop"]:
                    exit_price, reason = position["stop"], "stop"
                elif high >= position["target"]:
                    exit_price, reason = position["target"], "target"
            else:
                if high >= position["stop"]:
                    exit_price, reason = position["stop"], "stop"
                elif low <= position["target"]:
                    exit_price, reason = position["target"], "target"
            if exit_price is not None:
                pnl = ((exit_price - position["entry"]) / position["entry"] * 100.0) if position["side"] == "buy" else ((position["entry"] - exit_price) / position["entry"] * 100.0)
                trades.append({**position, "exit": exit_price, "exit_time": row.time, "exit_reason": reason, "pnl_pct": pnl})
                cooldown = COOLDOWN_AFTER_STOP if reason == "stop" else 0
                position = None
            elif position["side"] == "buy":
                position["stop"] = max(position["stop"], close - position["trail"])
            else:
                position["stop"] = min(position["stop"], close + position["trail"])
            continue
        if cooldown:
            cooldown -= 1
            continue

        long_context = row.four_close > row.four_ema50 > row.four_ema200 and row.day_close > row.day_ema20 > row.day_ema50
        short_context = row.four_close < row.four_ema50 < row.four_ema200 and row.day_close < row.day_ema20 < row.day_ema50
        prev = data.iloc[idx - 1] if idx else None
        if prev is None:
            continue
        touched_long = low <= float(row.ema20) or low <= float(row.ema50)
        touched_short = high >= float(row.ema20) or high >= float(row.ema50)
        long_rejection = touched_long and close > float(row.ema20) and close > float(row.open) and close > float(prev.close) and 45.0 <= float(row.rsi14) <= 68.0
        short_rejection = touched_short and close < float(row.ema20) and close < float(row.open) and close < float(prev.close) and 32.0 <= float(row.rsi14) <= 55.0
        volume_ok = float(row.volume) >= float(row.volume_sma20) * VOLUME_MIN
        side = "buy" if long_context and long_rejection and volume_ok else "sell" if short_context and short_rejection and volume_ok else None
        if side == "buy":
            position = {"side": side, "entry": close, "entry_time": row.time, "stop": close - STOP_ATR * atr, "target": close + TARGET_ATR * atr, "trail": TRAIL_ATR * atr}
        elif side == "sell":
            position = {"side": side, "entry": close, "entry_time": row.time, "stop": close + STOP_ATR * atr, "target": close - TARGET_ATR * atr, "trail": TRAIL_ATR * atr}
    return pd.DataFrame(trades)


def main() -> None:
    one = add_pullback_indicators(load("AVAXUSDT_1h.csv"))
    data = attach_context(one, load("AVAXUSDT_4h.csv"), load("AVAXUSDT_1d.csv"))
    data = data.dropna(subset=["ema20", "ema50", "atr", "volume_sma20", "four_ema50", "four_ema200", "day_ema20", "day_ema50"]).reset_index(drop=True)
    trades = run(data)
    oos_start = data["time"].max() - pd.Timedelta(days=365)
    oos = trades[trades["entry_time"] >= oos_start] if not trades.empty else trades
    summary = {"strategy": "PULLBACK_TREND_FOLLOWING", "symbol": "AVAX-USDT-SWAP", "entry_timeframe": "1h", "period_start": str(data.time.min()), "period_end": str(data.time.max()), "candles_1h": len(one), "stop_atr": STOP_ATR, "target_atr": TARGET_ATR, "trailing_atr": TRAIL_ATR, "volume_min_ratio": VOLUME_MIN, "closed_candles_only": True, "data_source": "local_cache"}
    summary.update({f"all_{k}": v for k, v in mark_to_market(trades).items()})
    summary.update({f"oos_{k}": v for k, v in mark_to_market(oos).items()})
    out = Path(__file__).resolve().parent
    pd.DataFrame([summary]).to_csv(out / "pullback_trend_following_avax_36m_summary.csv", index=False)
    trades.to_csv(out / "pullback_trend_following_avax_36m_trades.csv", index=False)
    print(pd.Series(summary).to_string())
    if not trades.empty:
        print("\nPor lado:")
        print(trades.groupby("side")["pnl_pct"].agg(["count", "sum", "mean"]).to_string())
        print("\nPor saída:")
        print(trades.groupby("exit_reason")["pnl_pct"].agg(["count", "sum", "mean"]).to_string())


if __name__ == "__main__":
    main()
