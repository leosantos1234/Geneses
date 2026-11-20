"""Backtest read-only de Volatility Breakout (Donchian + ATR) em 1h.

Usa somente os caches locais de SOL e AVAX. A entrada ocorre no rompimento
do canal Donchian dos candles anteriores, com filtro de volume e stop/target
baseados em ATR. Não acessa a exchange e não envia ordens.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "Robo" / "dados_historicos" / "swing_36m"
OUT = ROOT / "Robo" / "Backtest"
INITIAL = 1000.0
COST_PER_TRADE = 0.00095
LOOKBACK = 20
BREAKOUT_ATR_BUFFER = 0.10
VOLUME_RATIO_MIN = 1.0
STOP_ATR = 2.0
TARGET_ATR = 4.0
TRAIL_ATR = 2.5


def load(asset: str) -> pd.DataFrame:
    frame = pd.read_csv(DATA / f"{asset}USDT_1h.csv")
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    frame = frame.sort_values("time").drop_duplicates("time").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame.dropna(subset=["time", "open", "high", "low", "close", "volume"])


def enrich(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    close = out["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [out["high"] - out["low"], (out["high"] - prev_close).abs(), (out["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    out["atr"] = tr.rolling(14, min_periods=14).mean()
    out["volume_sma20"] = out["volume"].rolling(20, min_periods=20).mean()
    out["prior_high"] = out["high"].shift(1).rolling(LOOKBACK, min_periods=LOOKBACK).max()
    out["prior_low"] = out["low"].shift(1).rolling(LOOKBACK, min_periods=LOOKBACK).min()
    return out


def run(asset: str) -> tuple[dict, pd.DataFrame]:
    data = enrich(load(asset)).dropna(subset=["atr", "volume_sma20", "prior_high", "prior_low"]).reset_index(drop=True)
    position = None
    trades: list[dict] = []

    for row in data.itertuples(index=False):
        high, low, close, atr = map(float, (row.high, row.low, row.close, row.atr))
        if position is not None:
            stop, target = position["stop"], position["target"]
            exit_price = None
            reason = None
            if position["side"] == "buy":
                if low <= stop:
                    exit_price, reason = stop, "stop"
                elif high >= target:
                    exit_price, reason = target, "target"
            else:
                if high >= stop:
                    exit_price, reason = stop, "stop"
                elif low <= target:
                    exit_price, reason = target, "target"
            if exit_price is not None:
                pnl = ((exit_price - position["entry"]) / position["entry"] * 100.0
                       if position["side"] == "buy"
                       else (position["entry"] - exit_price) / position["entry"] * 100.0)
                trades.append({**position, "exit": exit_price, "exit_time": row.time, "exit_reason": reason, "pnl_pct": pnl})
                position = None
            elif position["side"] == "buy":
                position["stop"] = max(stop, close - position["trail"])
            else:
                position["stop"] = min(stop, close + position["trail"])
            continue

        volume_ok = float(row.volume) >= float(row.volume_sma20) * VOLUME_RATIO_MIN
        long_break = close >= float(row.prior_high) + BREAKOUT_ATR_BUFFER * atr
        short_break = close <= float(row.prior_low) - BREAKOUT_ATR_BUFFER * atr
        side = "buy" if long_break and volume_ok else "sell" if short_break and volume_ok else None
        if side == "buy":
            position = {"side": side, "entry": close, "entry_time": row.time, "stop": close - STOP_ATR * atr, "target": close + TARGET_ATR * atr, "trail": TRAIL_ATR * atr}
        elif side == "sell":
            position = {"side": side, "entry": close, "entry_time": row.time, "stop": close + STOP_ATR * atr, "target": close - TARGET_ATR * atr, "trail": TRAIL_ATR * atr}

    if position is not None:
        final = float(data.iloc[-1].close)
        pnl = ((final - position["entry"]) / position["entry"] * 100.0
               if position["side"] == "buy"
               else (position["entry"] - final) / position["entry"] * 100.0)
        trades.append({**position, "exit": final, "exit_time": data.iloc[-1].time, "exit_reason": "end", "pnl_pct": pnl})

    trade_df = pd.DataFrame(trades)
    balance = INITIAL
    peak = INITIAL
    max_dd = 0.0
    for trade in trade_df.sort_values("exit_time").to_dict("records") if not trade_df.empty else []:
        balance *= 1.0 + float(trade["pnl_pct"]) / 100.0 - COST_PER_TRADE
        peak = max(peak, balance)
        max_dd = min(max_dd, balance / peak - 1.0)
    wins = int((trade_df["pnl_pct"] > 0).sum()) if not trade_df.empty else 0
    summary = {
        "strategy": "VOLATILITY_BREAKOUT_DONCHIAN_ATR",
        "asset": f"{asset}USDT",
        "timeframe": "1h",
        "period_start": str(data["time"].min()),
        "period_end": str(data["time"].max()),
        "trades": int(len(trade_df)),
        "wins": wins,
        "winrate_pct": wins / len(trade_df) * 100.0 if len(trade_df) else 0.0,
        "compound_return_pct": (balance / INITIAL - 1.0) * 100.0,
        "final_balance": balance,
        "max_drawdown_pct": max_dd * 100.0,
        "lookback": LOOKBACK,
        "breakout_atr_buffer": BREAKOUT_ATR_BUFFER,
        "stop_atr": STOP_ATR,
        "target_atr": TARGET_ATR,
        "trailing_atr": TRAIL_ATR,
        "cost_per_trade_pct": COST_PER_TRADE * 100.0,
        "closed_candles_only": True,
    }
    return summary, trade_df


if __name__ == "__main__":
    results = []
    for asset in ("SOL", "AVAX"):
        summary, trades = run(asset)
        results.append(summary)
        trades.to_csv(OUT / f"volatility_breakout_{asset.lower()}_36m_trades.csv", index=False)
    result_df = pd.DataFrame(results)
    result_df.to_csv(OUT / "volatility_breakout_donchian_atr_36m_summary.csv", index=False)
    print(result_df.to_string(index=False))
