"""Backtest read-only da Breakout Trend Following em AVAX por 36 meses.

Usa somente os caches locais já existentes. Não acessa a exchange e não envia
ordens. O gatilho é o rompimento da máxima/mínima dos 20 candles anteriores
no 1h, com confirmação de direção por EMA20/EMA50 no 4h e no diário.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "Robo" / "dados_historicos" / "swing_36m"
OUT = ROOT / "Robo" / "Backtest"
INITIAL = 1000.0
COST_PER_TRADE = 0.00095
LOOKBACK = 20
VOLUME_RATIO_MIN = 1.0
STOP_ATR = 2.0
TARGET_ATR = 4.0
TRAIL_ATR = 2.5


def load(name: str) -> pd.DataFrame:
    frame = pd.read_csv(DATA / name)
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    frame = frame.sort_values("time").drop_duplicates("time").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame.dropna(subset=["time", "open", "high", "low", "close", "volume"])


def enrich(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    close = out["close"]
    out["ema20"] = close.ewm(span=20, adjust=False, min_periods=20).mean()
    out["ema50"] = close.ewm(span=50, adjust=False, min_periods=50).mean()
    out["ema200"] = close.ewm(span=200, adjust=False, min_periods=200).mean()
    prev_close = close.shift(1)
    tr = pd.concat(
        [out["high"] - out["low"], (out["high"] - prev_close).abs(), (out["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    out["atr"] = tr.rolling(14, min_periods=14).mean()
    out["volume_sma20"] = out["volume"].rolling(20, min_periods=20).mean()
    out["prior_high20"] = out["high"].shift(1).rolling(LOOKBACK, min_periods=LOOKBACK).max()
    out["prior_low20"] = out["low"].shift(1).rolling(LOOKBACK, min_periods=LOOKBACK).min()
    return out


def attach_context(one: pd.DataFrame, four: pd.DataFrame, day: pd.DataFrame) -> pd.DataFrame:
    """Anexa somente o último candle 4h/diário já fechado (sem look-ahead)."""
    four_cols = ["time", "close", "ema20", "ema50", "ema200"]
    day_cols = ["time", "close", "ema20", "ema50"]
    four_ctx = enrich(four)[four_cols].rename(columns={c: f"four_{c}" for c in four_cols if c != "time"})
    day_ctx = enrich(day)[day_cols].rename(columns={c: f"day_{c}" for c in day_cols if c != "time"})
    # Os timestamps representam a abertura do candle. O candle anterior é o
    # último candle seguramente fechado no momento do candle de entrada.
    four_ctx["time"] = four_ctx["time"].shift(1)
    day_ctx["time"] = day_ctx["time"].shift(1)
    result = pd.merge_asof(one.sort_values("time"), four_ctx.dropna().sort_values("time"), on="time", direction="backward")
    return pd.merge_asof(result.sort_values("time"), day_ctx.dropna().sort_values("time"), on="time", direction="backward")


def run() -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    one = enrich(load("AVAXUSDT_1h.csv"))
    four = load("AVAXUSDT_4h.csv")
    day = load("AVAXUSDT_1d.csv")
    data = attach_context(one, four, day)
    data = data.dropna(subset=["prior_high20", "prior_low20", "atr", "volume_sma20", "four_ema20", "four_ema50", "day_ema20", "day_ema50"]).reset_index(drop=True)

    position = None
    trades: list[dict] = []
    for row in data.itertuples(index=False):
        high, low, close, atr = float(row.high), float(row.low), float(row.close), float(row.atr)
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
                pnl = ((exit_price - position["entry"]) / position["entry"] * 100.0) if position["side"] == "buy" else ((position["entry"] - exit_price) / position["entry"] * 100.0)
                trades.append({**position, "exit": exit_price, "exit_time": row.time, "exit_reason": reason, "pnl_pct": pnl})
                position = None
            elif position["side"] == "buy":
                position["stop"] = max(stop, close - position["trail"])
            else:
                position["stop"] = min(stop, close + position["trail"])
            continue

        long_context = row.four_close > row.four_ema20 > row.four_ema50 and row.day_close > row.day_ema20 > row.day_ema50
        short_context = row.four_close < row.four_ema20 < row.four_ema50 and row.day_close < row.day_ema20 < row.day_ema50
        volume_ok = float(row.volume) >= float(row.volume_sma20) * VOLUME_RATIO_MIN
        long_break = close >= float(row.prior_high20) and volume_ok
        short_break = close <= float(row.prior_low20) and volume_ok
        side = "buy" if long_context and long_break else "sell" if short_context and short_break else None
        if side is None:
            continue
        if side == "buy":
            position = {"side": side, "entry": close, "entry_time": row.time, "regime": 1, "stop": close - STOP_ATR * atr, "target": close + TARGET_ATR * atr, "trail": TRAIL_ATR * atr}
        else:
            position = {"side": side, "entry": close, "entry_time": row.time, "regime": 1, "stop": close + STOP_ATR * atr, "target": close - TARGET_ATR * atr, "trail": TRAIL_ATR * atr}

    if position is not None:
        final = float(data.iloc[-1].close)
        pnl = ((final - position["entry"]) / position["entry"] * 100.0) if position["side"] == "buy" else ((position["entry"] - final) / position["entry"] * 100.0)
        trades.append({**position, "exit": final, "exit_time": data.iloc[-1].time, "exit_reason": "end", "pnl_pct": pnl})

    trade_df = pd.DataFrame(trades)
    balance = INITIAL
    peak = balance
    max_dd = 0.0
    curve = []
    for trade in trade_df.sort_values("exit_time").to_dict("records") if not trade_df.empty else []:
        balance *= 1.0 + float(trade["pnl_pct"]) / 100.0 - COST_PER_TRADE
        peak = max(peak, balance)
        max_dd = min(max_dd, balance / peak - 1.0)
        curve.append({"time": trade["exit_time"], "balance": balance})
    wins = int((trade_df["pnl_pct"] > 0).sum()) if not trade_df.empty else 0
    summary = {
        "strategy": "BREAKOUT_TREND_FOLLOWING_MTF",
        "symbol": "AVAX-USDT-SWAP",
        "entry_timeframe": "1h",
        "period_start": str(data["time"].min()),
        "period_end": str(data["time"].max()),
        "candles_1h": int(len(one)),
        "trades": int(len(trade_df)),
        "wins": wins,
        "winrate_pct": wins / len(trade_df) * 100.0 if len(trade_df) else 0.0,
        "gross_return_pct": float(trade_df["pnl_pct"].sum()) if not trade_df.empty else 0.0,
        "final_balance_compounded": balance,
        "compound_return_pct": (balance / INITIAL - 1.0) * 100.0,
        "max_drawdown_pct": max_dd * 100.0,
        "cost_per_trade_pct": COST_PER_TRADE * 100.0,
        "lookback_1h": LOOKBACK,
        "volume_ratio_min": VOLUME_RATIO_MIN,
        "stop_atr": STOP_ATR,
        "target_atr": TARGET_ATR,
        "trailing_atr": TRAIL_ATR,
        "closed_candles_only": True,
        "data_source": "local_cache",
    }
    return summary, trade_df, pd.DataFrame(curve)


if __name__ == "__main__":
    summary, trades, curve = run()
    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([summary]).to_csv(OUT / "breakout_trend_following_avax_36m_summary.csv", index=False)
    trades.to_csv(OUT / "breakout_trend_following_avax_36m_trades.csv", index=False)
    curve.to_csv(OUT / "breakout_trend_following_avax_36m_equity.csv", index=False)
    print(pd.Series(summary).to_string())
