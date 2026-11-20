"""Backtest read-only de Mean Reversion com Bollinger/RSI em vários ativos."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
PULLBACK = ROOT / "Robo" / "dados_historicos" / "pullback_36m"
SWING = ROOT / "Robo" / "dados_historicos" / "swing_36m"
OUT = ROOT / "Robo" / "Backtest"
INITIAL = 1000.0
RISK = 0.005
COST = 0.0019
WINDOW = 20
STOP_ATR = 2.0
MAX_BARS = 30


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
    mean = df["close"].rolling(WINDOW).mean()
    std = df["close"].rolling(WINDOW).std()
    df["mid"], df["upper"], df["lower"] = mean, mean + 2 * std, mean - 2 * std
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()
    delta = df["close"].diff()
    gain, loss = delta.clip(lower=0).rolling(14).mean(), (-delta.clip(upper=0)).rolling(14).mean()
    df["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, pd.NA))
    df = df.dropna().reset_index(drop=True)

    equity, peak, max_dd = INITIAL, INITIAL, 0.0
    position = None
    trades = []
    for i in range(len(df) - 1):
        row, nxt = df.iloc[i], df.iloc[i + 1]
        if position:
            position["bars"] += 1
            exit_price, reason = None, None
            if position["side"] == "long":
                if float(row["low"]) <= position["stop"]:
                    exit_price, reason = position["stop"], "stop"
                elif float(row["close"]) >= float(row["mid"]):
                    exit_price, reason = float(row["mid"]), "mean"
            else:
                if float(row["high"]) >= position["stop"]:
                    exit_price, reason = position["stop"], "stop"
                elif float(row["close"]) <= float(row["mid"]):
                    exit_price, reason = float(row["mid"]), "mean"
            if position["bars"] >= MAX_BARS and exit_price is None:
                exit_price, reason = float(row["close"]), "timeout"
            if exit_price is not None:
                gross = ((exit_price - position["entry"]) / position["entry"] if position["side"] == "long" else (position["entry"] - exit_price) / position["entry"])
                net = gross - COST
                equity *= 1 + net * position["notional"] / equity
                trades.append({"exit_time": row["time"], "return_pct": net * 100, "equity": equity, "reason": reason})
                peak = max(peak, equity)
                max_dd = min(max_dd, equity / peak - 1)
                position = None
            continue

        lateral = abs(float(row["ema50"]) / float(row["ema200"]) - 1) <= 0.03
        long_signal = lateral and float(row["close"]) <= float(row["lower"]) and float(row["rsi"]) <= 35
        short_signal = lateral and float(row["close"]) >= float(row["upper"]) and float(row["rsi"]) >= 65
        if not (long_signal or short_signal):
            continue
        side = "long" if long_signal else "short"
        entry = float(nxt["open"])
        atr = float(row["atr"])
        stop = entry - STOP_ATR * atr if side == "long" else entry + STOP_ATR * atr
        notional = min(equity, equity * RISK * entry / abs(entry - stop))
        position = {"side": side, "entry": entry, "stop": stop, "notional": notional, "bars": 0}

    if position:
        row = df.iloc[-1]
        exit_price = float(row["close"])
        gross = ((exit_price - position["entry"]) / position["entry"] if position["side"] == "long" else (position["entry"] - exit_price) / position["entry"])
        equity *= 1 + (gross - COST) * position["notional"] / equity
        trades.append({"exit_time": row["time"], "return_pct": (gross - COST) * 100, "equity": equity, "reason": "end"})

    t = pd.DataFrame(trades)
    split = df["time"].min() + (df["time"].max() - df["time"].min()) * 2 / 3
    oos = t[t["exit_time"] > split] if not t.empty else t
    return {"strategy": "MEAN_REVERSION_BOLLINGER_RSI", "asset": f"{asset}USDT", "timeframe": f"{hours}h", "trades": len(t), "return_pct": (equity / INITIAL - 1) * 100, "final_balance": equity, "max_drawdown_pct": max_dd * 100, "winrate_pct": float((t["return_pct"] > 0).mean() * 100) if len(t) else 0.0, "oos_trades": len(oos), "oos_return_pct": float((oos["equity"].iloc[-1] / (oos["equity"].iloc[0] / (1 + oos["return_pct"].iloc[0] / 100) if len(oos) else INITIAL) - 1) * 100) if len(oos) else 0.0, "oos_winrate_pct": float((oos["return_pct"] > 0).mean() * 100) if len(oos) else 0.0}


if __name__ == "__main__":
    rows = []
    for asset in ("BTC", "ETH", "SOL", "AVAX", "BNB", "XRP"):
        for hours in (1, 2, 4):
            try:
                rows.append(run(asset, hours))
            except FileNotFoundError:
                pass
    result = pd.DataFrame(rows).sort_values(["oos_return_pct", "return_pct"], ascending=False)
    result.to_csv(OUT / "mean_reversion_matrix_summary.csv", index=False)
    print(result.to_string(index=False))
