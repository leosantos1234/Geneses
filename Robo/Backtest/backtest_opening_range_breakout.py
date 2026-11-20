"""Backtest read-only de Opening Range Breakout em cripto 24/7."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
PULLBACK = ROOT / "Robo" / "dados_historicos" / "pullback_36m"
SWING = ROOT / "Robo" / "dados_historicos" / "swing_36m"
OUT = ROOT / "Robo" / "Backtest"
INITIAL, RISK, COST = 1000.0, 0.005, 0.0019


def load(asset: str) -> pd.DataFrame:
    source = next((p for p in (PULLBACK / f"{asset}USDT_1h.csv", SWING / f"{asset}USDT_1h.csv") if p.exists()), None)
    if source is None:
        raise FileNotFoundError(asset)
    x = pd.read_csv(source)
    x["time"] = pd.to_datetime(x["time"], utc=True)
    for c in ("open", "high", "low", "close", "volume"):
        x[c] = pd.to_numeric(x[c], errors="coerce")
    return x.sort_values("time").drop_duplicates("time").dropna().reset_index(drop=True)


def run(asset: str) -> dict:
    df = load(asset)
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()
    df["vol_avg"] = df["volume"].rolling(20).mean()
    df["day"] = df["time"].dt.floor("D")
    df["hour"] = df["time"].dt.hour
    equity, peak, max_dd = INITIAL, INITIAL, 0.0
    position = None
    trades = []
    ranges = {}
    for day, group in df.groupby("day", sort=True):
        first = group[group["hour"] < 4]
        if len(first) < 4:
            continue
        ranges[day] = (float(first["high"].max()), float(first["low"].min()))

    for i in range(len(df) - 1):
        row, nxt = df.iloc[i], df.iloc[i + 1]
        day = row["day"]
        if day not in ranges or row["hour"] < 4:
            continue
        high_range, low_range = ranges[day]
        if position:
            exit_price, reason = None, None
            if position["side"] == "long":
                if float(row["low"]) <= position["stop"]:
                    exit_price, reason = position["stop"], "stop"
                elif float(row["high"]) >= position["target"]:
                    exit_price, reason = position["target"], "target"
            else:
                if float(row["high"]) >= position["stop"]:
                    exit_price, reason = position["stop"], "stop"
                elif float(row["low"]) <= position["target"]:
                    exit_price, reason = position["target"], "target"
            if row["day"] != position["day"] and exit_price is None:
                exit_price, reason = float(nxt["open"]), "day_end"
            if exit_price is not None:
                gross = ((exit_price - position["entry"]) / position["entry"] if position["side"] == "long" else (position["entry"] - exit_price) / position["entry"])
                net = gross - COST
                equity *= 1 + net * position["notional"] / equity
                trades.append({"exit_time": row["time"], "return_pct": net * 100, "equity": equity})
                peak = max(peak, equity)
                max_dd = min(max_dd, equity / peak - 1)
                position = None
            continue
        volume_ok = float(row["volume"]) >= float(row["vol_avg"])
        long_signal = float(row["close"]) > high_range and volume_ok
        short_signal = float(row["close"]) < low_range and volume_ok
        if not (long_signal or short_signal):
            continue
        side = "long" if long_signal else "short"
        entry = float(nxt["open"])
        atr = float(row["atr"])
        stop = entry - 1.5 * atr if side == "long" else entry + 1.5 * atr
        target = entry + 2.5 * atr if side == "long" else entry - 2.5 * atr
        notional = min(equity, equity * RISK * entry / abs(entry - stop))
        position = {"side": side, "entry": entry, "stop": stop, "target": target, "notional": notional, "day": day}

    t = pd.DataFrame(trades)
    split = df["time"].min() + (df["time"].max() - df["time"].min()) * 2 / 3
    oos = t[t["exit_time"] > split] if not t.empty else t
    return {"strategy": "OPENING_RANGE_BREAKOUT", "asset": f"{asset}USDT", "timeframe": "1h", "trades": len(t), "return_pct": (equity / INITIAL - 1) * 100, "final_balance": equity, "max_drawdown_pct": max_dd * 100, "winrate_pct": float((t["return_pct"] > 0).mean() * 100) if len(t) else 0.0, "oos_trades": len(oos), "oos_return_pct": float((oos["equity"].iloc[-1] / (oos["equity"].iloc[0] / (1 + oos["return_pct"].iloc[0] / 100) if len(oos) else INITIAL) - 1) * 100) if len(oos) else 0.0, "oos_winrate_pct": float((oos["return_pct"] > 0).mean() * 100) if len(oos) else 0.0}


if __name__ == "__main__":
    rows = []
    for asset in ("BTC", "ETH", "SOL", "AVAX", "BNB", "XRP"):
        try:
            rows.append(run(asset))
        except FileNotFoundError:
            pass
    result = pd.DataFrame(rows).sort_values(["oos_return_pct", "return_pct"], ascending=False)
    result.to_csv(OUT / "opening_range_breakout_summary.csv", index=False)
    print(result.to_string(index=False))
