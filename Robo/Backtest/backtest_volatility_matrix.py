"""Matriz de Volatility Breakout por ativo e timeframe.

Teste conservador: Donchian + EMA50/EMA200 + expansão de ATR, entrada no
próximo candle, risco fixo, limite de exposição e custos de ida/volta.
"""

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
LOOKBACK = 55
STOP_ATR = 2.5
TRAIL_ATR = 3.0


def load(asset: str) -> pd.DataFrame:
    candidates = [PULLBACK / f"{asset}USDT_1h.csv", SWING / f"{asset}USDT_1h.csv"]
    source = next((p for p in candidates if p.exists()), None)
    if source is None:
        raise FileNotFoundError(asset)
    df = pd.read_csv(source)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("time").drop_duplicates("time").dropna().reset_index(drop=True)


def timeframe(df: pd.DataFrame, hours: int) -> pd.DataFrame:
    x = df.set_index("time")[["open", "high", "low", "close", "volume"]].resample(f"{hours}h", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().reset_index()
    return x


def run(asset: str, hours: int) -> dict:
    df = timeframe(load(asset), hours)
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14, min_periods=14).mean()
    df["atr_med"] = df["atr"].rolling(100, min_periods=100).median()
    df["ema50"] = df["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False, min_periods=200).mean()
    df["prior_hi"] = df["high"].shift(1).rolling(LOOKBACK, min_periods=LOOKBACK).max()
    df["prior_lo"] = df["low"].shift(1).rolling(LOOKBACK, min_periods=LOOKBACK).min()
    df["vol_avg"] = df["volume"].rolling(20, min_periods=20).mean()
    df = df.dropna().reset_index(drop=True)

    equity = INITIAL
    peak = INITIAL
    max_dd = 0.0
    position = None
    trades: list[dict] = []
    for i in range(len(df) - 1):
        row = df.iloc[i]
        nxt = df.iloc[i + 1]
        if position:
            stop = position["stop"]
            if position["side"] == "long":
                hit = float(row["low"]) <= stop
                if not hit:
                    position["stop"] = max(stop, float(row["close"]) - position["atr"] * TRAIL_ATR)
            else:
                hit = float(row["high"]) >= stop
                if not hit:
                    position["stop"] = min(stop, float(row["close"]) + position["atr"] * TRAIL_ATR)
            if hit:
                exit_price = stop
                gross = ((exit_price - position["entry"]) / position["entry"] if position["side"] == "long" else (position["entry"] - exit_price) / position["entry"])
                net = gross - COST
                equity *= 1 + net * position["notional"] / equity
                trades.append({"exit_time": row["time"], "return_pct": net * 100, "equity": equity})
                position = None
                peak = max(peak, equity)
                max_dd = min(max_dd, equity / peak - 1)
            continue

        expansion = float(row["atr"]) >= float(row["atr_med"]) * 1.05
        volume_ok = float(row["volume"]) >= float(row["vol_avg"])
        long_signal = float(row["close"]) > float(row["prior_hi"]) and float(row["ema50"]) > float(row["ema200"])
        short_signal = float(row["close"]) < float(row["prior_lo"]) and float(row["ema50"]) < float(row["ema200"])
        if not expansion or not volume_ok or not (long_signal or short_signal):
            continue
        side = "long" if long_signal else "short"
        entry = float(nxt["open"])
        atr = float(row["atr"])
        stop = entry - STOP_ATR * atr if side == "long" else entry + STOP_ATR * atr
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
    return {"asset": f"{asset}USDT", "timeframe": f"{hours}h", "trades": len(t), "return_pct": (equity / INITIAL - 1) * 100, "final_balance": equity, "max_drawdown_pct": max_dd * 100, "winrate_pct": float((t["return_pct"] > 0).mean() * 100) if len(t) else 0.0, "oos_trades": len(oos), "oos_return_pct": float((oos["equity"].iloc[-1] / (oos["equity"].iloc[0] / (1 + oos["return_pct"].iloc[0] / 100) if len(oos) else INITIAL) - 1) * 100) if len(oos) else 0.0, "oos_winrate_pct": float((oos["return_pct"] > 0).mean() * 100) if len(oos) else 0.0, "start": str(df["time"].min()), "end": str(df["time"].max())}


if __name__ == "__main__":
    rows = []
    for asset in ("BTC", "ETH", "SOL", "AVAX", "BNB", "XRP"):
        for hours in (1, 2, 4):
            try:
                rows.append(run(asset, hours))
            except FileNotFoundError:
                pass
    result = pd.DataFrame(rows).sort_values(["oos_return_pct", "return_pct"], ascending=False)
    result.to_csv(OUT / "volatility_breakout_matrix_summary.csv", index=False)
    print(result.to_string(index=False))
