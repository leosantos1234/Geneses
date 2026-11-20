"""Backtest read-only de Momentum Multi-Timeframe."""

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
    return x.sort_values("time").drop_duplicates("time").dropna().set_index("time")


def run(asset: str, hours: int) -> dict:
    df = load(asset)[["open", "high", "low", "close", "volume"]].resample(f"{hours}h", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().reset_index()
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()
    df["roc20"] = df["close"].pct_change(20)
    df["roc50"] = df["close"].pct_change(50)
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()
    df["atr_med"] = df["atr"].rolling(100).median()
    df["vol_avg"] = df["volume"].rolling(20).mean()
    df = df.dropna().reset_index(drop=True)

    equity, peak, max_dd = INITIAL, INITIAL, 0.0
    position, trades = None, []
    for i in range(len(df) - 1):
        row, nxt = df.iloc[i], df.iloc[i + 1]
        if position:
            stop = position["stop"]
            if position["side"] == "long":
                hit = float(row["low"]) <= stop
                if not hit:
                    position["stop"] = max(stop, float(row["close"]) - 3 * position["atr"])
                exit_signal = float(row["roc20"]) < 0 or float(row["close"]) < float(row["ema50"])
            else:
                hit = float(row["high"]) >= stop
                if not hit:
                    position["stop"] = min(stop, float(row["close"]) + 3 * position["atr"])
                exit_signal = float(row["roc20"]) > 0 or float(row["close"]) > float(row["ema50"])
            if hit or exit_signal:
                exit_price = position["stop"] if hit else float(nxt["open"])
                gross = ((exit_price - position["entry"]) / position["entry"] if position["side"] == "long" else (position["entry"] - exit_price) / position["entry"])
                net = gross - COST
                equity *= 1 + net * position["notional"] / equity
                trades.append({"exit_time": row["time"], "return_pct": net * 100, "equity": equity})
                peak = max(peak, equity)
                max_dd = min(max_dd, equity / peak - 1)
                position = None
            continue

        expansion = float(row["atr"]) >= float(row["atr_med"])
        volume_ok = float(row["volume"]) >= float(row["vol_avg"])
        long_signal = float(row["roc20"]) > 0 and float(row["roc50"]) > 0 and float(row["close"]) > float(row["ema20"]) > float(row["ema50"]) > float(row["ema200"])
        short_signal = float(row["roc20"]) < 0 and float(row["roc50"]) < 0 and float(row["close"]) < float(row["ema20"]) < float(row["ema50"]) < float(row["ema200"])
        if not expansion or not volume_ok or not (long_signal or short_signal):
            continue
        side = "long" if long_signal else "short"
        entry = float(nxt["open"])
        atr = float(row["atr"])
        stop = entry - 2.5 * atr if side == "long" else entry + 2.5 * atr
        notional = min(equity, equity * RISK * entry / abs(entry - stop))
        position = {"side": side, "entry": entry, "stop": stop, "atr": atr, "notional": notional}

    if position:
        row = df.iloc[-1]
        exit_price = float(row["close"])
        gross = ((exit_price - position["entry"]) / position["entry"] if position["side"] == "long" else (position["entry"] - exit_price) / position["entry"])
        net = gross - COST
        equity *= 1 + net * position["notional"] / equity
        trades.append({"exit_time": row["time"], "return_pct": net * 100, "equity": equity})

    t = pd.DataFrame(trades)
    split = df["time"].min() + (df["time"].max() - df["time"].min()) * 2 / 3
    oos = t[t["exit_time"] > split] if not t.empty else t
    return {"strategy": "MOMENTUM_MULTITIMEFRAME", "asset": f"{asset}USDT", "timeframe": f"{hours}h", "trades": len(t), "return_pct": (equity / INITIAL - 1) * 100, "final_balance": equity, "max_drawdown_pct": max_dd * 100, "winrate_pct": float((t["return_pct"] > 0).mean() * 100) if len(t) else 0.0, "oos_trades": len(oos), "oos_return_pct": float((oos["equity"].iloc[-1] / (oos["equity"].iloc[0] / (1 + oos["return_pct"].iloc[0] / 100) if len(oos) else INITIAL) - 1) * 100) if len(oos) else 0.0, "oos_winrate_pct": float((oos["return_pct"] > 0).mean() * 100) if len(oos) else 0.0}


if __name__ == "__main__":
    rows = []
    for asset in ("BTC", "ETH", "SOL", "AVAX", "BNB", "XRP"):
        for hours in (1, 2, 4):
            try:
                rows.append(run(asset, hours))
            except FileNotFoundError:
                pass
    result = pd.DataFrame(rows).sort_values(["oos_return_pct", "return_pct"], ascending=False)
    result.to_csv(OUT / "momentum_multitimeframe_summary.csv", index=False)
    print(result.to_string(index=False))
