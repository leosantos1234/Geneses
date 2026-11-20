"""Backtest read-only de Grid Adaptativo com filtro de regime."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
PULLBACK = ROOT / "Robo" / "dados_historicos" / "pullback_36m"
SWING = ROOT / "Robo" / "dados_historicos" / "swing_36m"
OUT = ROOT / "Robo" / "Backtest"
INITIAL, COST = 1000.0, 0.0019


def load(asset: str) -> pd.DataFrame:
    source = next((p for p in (PULLBACK / f"{asset}USDT_1h.csv", SWING / f"{asset}USDT_1h.csv") if p.exists()), None)
    if source is None:
        raise FileNotFoundError(asset)
    x = pd.read_csv(source)
    x["time"] = pd.to_datetime(x["time"], utc=True)
    for c in ("open", "high", "low", "close", "volume"):
        x[c] = pd.to_numeric(x[c], errors="coerce")
    return x.sort_values("time").drop_duplicates("time").dropna().set_index("time")


def run(asset: str, hours: int) -> dict:
    df = load(asset)[["open", "high", "low", "close", "volume"]].resample(f"{hours}h", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().reset_index()
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()
    df = df.dropna().reset_index(drop=True)

    equity, peak, max_dd = INITIAL, INITIAL, 0.0
    position = None
    trades = []
    for i in range(len(df) - 1):
        row, nxt = df.iloc[i], df.iloc[i + 1]
        center, atr = float(row["ema20"]), float(row["atr"])
        lateral = abs(float(row["ema50"]) / float(row["ema200"]) - 1) <= 0.03
        if position:
            side, avg, qty, stop = position["side"], position["avg"], position["qty"], position["stop"]
            target = center
            stop_hit = float(row["low"]) <= stop if side == "long" else float(row["high"]) >= stop
            target_hit = float(row["high"]) >= target if side == "long" else float(row["low"]) <= target
            if stop_hit or target_hit or not lateral:
                exit_price = stop if stop_hit else (target if target_hit else float(nxt["open"]))
                gross = ((exit_price - avg) / avg if side == "long" else (avg - exit_price) / avg) * qty
                equity *= 1 + gross - COST * qty
                trades.append({"exit_time": row["time"], "return_pct": (gross - COST * qty) * 100, "equity": equity})
                peak = max(peak, equity)
                max_dd = min(max_dd, equity / peak - 1)
                position = None
            elif qty < 3:
                level = center - (qty + 1) * 0.5 * atr if side == "long" else center + (qty + 1) * 0.5 * atr
                add = float(row["close"]) <= level if side == "long" else float(row["close"]) >= level
                if add:
                    price = float(nxt["open"])
                    position["avg"] = (avg * qty + price) / (qty + 1)
                    position["qty"] += 1
            continue

        if not lateral:
            continue
        long_entry = float(row["close"]) <= center - 0.5 * atr
        short_entry = float(row["close"]) >= center + 0.5 * atr
        if not long_entry and not short_entry:
            continue
        side = "long" if long_entry else "short"
        entry = float(nxt["open"])
        stop = center - 2.0 * atr if side == "long" else center + 2.0 * atr
        position = {"side": side, "avg": entry, "qty": 1, "stop": stop}

    t = pd.DataFrame(trades)
    split = df["time"].min() + (df["time"].max() - df["time"].min()) * 2 / 3
    oos = t[t["exit_time"] > split] if not t.empty else t
    return {"strategy": "ADAPTIVE_GRID_REGIME_FILTERED", "asset": f"{asset}USDT", "timeframe": f"{hours}h", "trades": len(t), "return_pct": (equity / INITIAL - 1) * 100, "final_balance": equity, "max_drawdown_pct": max_dd * 100, "winrate_pct": float((t["return_pct"] > 0).mean() * 100) if len(t) else 0.0, "oos_trades": len(oos), "oos_return_pct": float((oos["equity"].iloc[-1] / (oos["equity"].iloc[0] / (1 + oos["return_pct"].iloc[0] / 100) if len(oos) else INITIAL) - 1) * 100) if len(oos) else 0.0, "oos_winrate_pct": float((oos["return_pct"] > 0).mean() * 100) if len(oos) else 0.0}


if __name__ == "__main__":
    rows = []
    for asset in ("BTC", "ETH", "SOL", "AVAX", "BNB", "XRP"):
        for hours in (1, 2, 4):
            try:
                rows.append(run(asset, hours))
            except FileNotFoundError:
                pass
    result = pd.DataFrame(rows).sort_values(["oos_return_pct", "return_pct"], ascending=False)
    result.to_csv(OUT / "adaptive_grid_matrix_summary.csv", index=False)
    print(result.to_string(index=False))
