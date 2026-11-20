"""Validação mais rigorosa de Volatility Breakout em SOL/AVAX.

Regras: rompimento Donchian, tendência EMA50/EMA200, expansão de ATR,
entrada no candle seguinte, risco fixo de 1% por trade, limite de exposição
de 1x, custos de ida e volta e relatório separado em treino/OOS.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "Robo" / "dados_historicos" / "swing_36m"
OUT = ROOT / "Robo" / "Backtest"
INITIAL = 1000.0
RISK_PER_TRADE = 0.01
ROUND_TRIP_COST = 0.0019
LOOKBACK = 55
ATR_WINDOW = 14
ATR_MEDIAN_WINDOW = 100
ATR_EXPANSION = 1.05
STOP_ATR = 2.5
TRAIL_ATR = 3.0


def load(asset: str) -> pd.DataFrame:
    p = DATA / f"{asset}USDT_1h.csv"
    df = pd.read_csv(p)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("time").drop_duplicates("time").dropna().reset_index(drop=True)


def indicators(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    close, prev = x["close"], x["close"].shift(1)
    tr = pd.concat([x["high"] - x["low"], (x["high"] - prev).abs(), (x["low"] - prev).abs()], axis=1).max(axis=1)
    x["atr"] = tr.rolling(ATR_WINDOW, min_periods=ATR_WINDOW).mean()
    x["atr_median"] = x["atr"].rolling(ATR_MEDIAN_WINDOW, min_periods=ATR_MEDIAN_WINDOW).median()
    x["ema50"] = close.ewm(span=50, adjust=False, min_periods=50).mean()
    x["ema200"] = close.ewm(span=200, adjust=False, min_periods=200).mean()
    x["prior_high"] = x["high"].shift(1).rolling(LOOKBACK, min_periods=LOOKBACK).max()
    x["prior_low"] = x["low"].shift(1).rolling(LOOKBACK, min_periods=LOOKBACK).min()
    x["volume_avg"] = x["volume"].rolling(20, min_periods=20).mean()
    return x.dropna().reset_index(drop=True)


def run(asset: str) -> tuple[dict, pd.DataFrame]:
    df = indicators(load(asset))
    position = None
    trades: list[dict] = []
    equity = INITIAL
    equity_curve = []

    for i in range(len(df) - 1):
        row, nxt = df.iloc[i], df.iloc[i + 1]
        if position is not None:
            stop = position["stop"]
            if position["side"] == "long":
                if float(row["low"]) <= stop:
                    exit_price, reason = stop, "stop"
                else:
                    position["stop"] = max(stop, float(row["close"]) - position["atr"] * TRAIL_ATR)
                    exit_price, reason = None, None
            else:
                if float(row["high"]) >= stop:
                    exit_price, reason = stop, "stop"
                else:
                    position["stop"] = min(stop, float(row["close"]) + position["atr"] * TRAIL_ATR)
                    exit_price, reason = None, None
            if exit_price is not None:
                gross = ((exit_price - position["entry"]) / position["entry"] if position["side"] == "long" else (position["entry"] - exit_price) / position["entry"])
                net = gross - ROUND_TRIP_COST
                equity *= 1.0 + net * position["notional"] / equity
                trades.append({**position, "exit": exit_price, "exit_time": row["time"], "exit_reason": reason, "return_pct": net * 100.0, "equity": equity})
                equity_curve.append(equity)
                position = None
            continue

        expansion = float(row["atr"]) >= float(row["atr_median"]) * ATR_EXPANSION
        volume_ok = float(row["volume"]) >= float(row["volume_avg"])
        long_signal = float(row["close"]) > float(row["prior_high"]) and float(row["close"]) > float(row["ema200"]) and float(row["ema50"]) > float(row["ema200"])
        short_signal = float(row["close"]) < float(row["prior_low"]) and float(row["close"]) < float(row["ema200"]) and float(row["ema50"]) < float(row["ema200"])
        if not expansion or not volume_ok or not (long_signal or short_signal):
            continue

        side = "long" if long_signal else "short"
        entry = float(nxt["open"])
        atr = float(row["atr"])
        stop = entry - STOP_ATR * atr if side == "long" else entry + STOP_ATR * atr
        risk_per_unit = abs(entry - stop)
        notional = min(equity, equity * RISK_PER_TRADE * entry / risk_per_unit)
        position = {"side": side, "entry": entry, "entry_time": nxt["time"], "stop": stop, "atr": atr, "notional": notional}

    if position is not None:
        row = df.iloc[-1]
        exit_price = float(row["close"])
        gross = ((exit_price - position["entry"]) / position["entry"] if position["side"] == "long" else (position["entry"] - exit_price) / position["entry"])
        net = gross - ROUND_TRIP_COST
        equity *= 1.0 + net * position["notional"] / equity
        trades.append({**position, "exit": exit_price, "exit_time": row["time"], "exit_reason": "end", "return_pct": net * 100.0, "equity": equity})
        equity_curve.append(equity)

    t = pd.DataFrame(trades)
    split = df["time"].min() + (df["time"].max() - df["time"].min()) * 2 / 3
    train = t[t["exit_time"] <= split] if not t.empty else t
    oos = t[t["exit_time"] > split] if not t.empty else t
    peak = INITIAL
    max_dd = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        max_dd = min(max_dd, value / peak - 1.0)
    def metrics(part: pd.DataFrame) -> dict:
        return {"trades": len(part), "winrate_pct": float((part["return_pct"] > 0).mean() * 100) if len(part) else 0.0, "return_pct": float((part["equity"].iloc[-1] / (part["equity"].iloc[0] / (1 + part["return_pct"].iloc[0] / 100) if len(part) else INITIAL) - 1) * 100) if len(part) else 0.0}
    summary = {"strategy": "VOLATILITY_BREAKOUT_WALK_FORWARD", "asset": f"{asset}USDT", "period_start": str(df["time"].min()), "period_end": str(df["time"].max()), "trades": len(t), "winrate_pct": float((t["return_pct"] > 0).mean() * 100) if len(t) else 0.0, "return_pct": (equity / INITIAL - 1) * 100, "final_balance": equity, "max_drawdown_pct": max_dd * 100, "train_split": str(split), "train_trades": len(train), "oos_trades": len(oos), "oos_winrate_pct": float((oos["return_pct"] > 0).mean() * 100) if len(oos) else 0.0, "risk_per_trade_pct": RISK_PER_TRADE * 100, "round_trip_cost_pct": ROUND_TRIP_COST * 100, "donchian_lookback": LOOKBACK, "atr_expansion": ATR_EXPANSION}
    return summary, t


if __name__ == "__main__":
    results = []
    for asset in ("SOL", "AVAX"):
        summary, trades = run(asset)
        results.append(summary)
        trades.to_csv(OUT / f"volatility_breakout_walkforward_{asset.lower()}_trades.csv", index=False)
    out = pd.DataFrame(results)
    out.to_csv(OUT / "volatility_breakout_walkforward_summary.csv", index=False)
    print(out.to_string(index=False))
