"""Backtest read-only de Pairs Trading SOL/AVAX.

Spread logarítmico com hedge ratio rolling, entrada por z-score, saída pela
reversão à média e proteção por stop/tempo. As posições são long/short de
notional igual, portanto o resultado é medido sobre o capital total.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "Robo" / "dados_historicos" / "swing_36m"
OUT = ROOT / "Robo" / "Backtest"
INITIAL = 1000.0
ROUND_TRIP_BOTH_LEGS_COST = 0.0038
HEDGE_WINDOW = 720
Z_WINDOW = 240
ENTRY_Z = 2.0
EXIT_Z = 0.5
STOP_Z = 3.5
MAX_HOLD = 120


def load(asset: str) -> pd.DataFrame:
    x = pd.read_csv(DATA / f"{asset}USDT_1h.csv")
    x["time"] = pd.to_datetime(x["time"], utc=True)
    x["close"] = pd.to_numeric(x["close"], errors="coerce")
    return x.sort_values("time").drop_duplicates("time").set_index("time")[["close"]].dropna()


def run(hours: int) -> tuple[dict, pd.DataFrame]:
    sol = load("SOL").resample(f"{hours}h").last().rename(columns={"close": "sol"})
    avax = load("AVAX").resample(f"{hours}h").last().rename(columns={"close": "avax"})
    x = sol.join(avax, how="inner").dropna()
    ls, la = np.log(x["sol"]), np.log(x["avax"])
    cov = ls.rolling(HEDGE_WINDOW).cov(la).shift(1)
    var = la.rolling(HEDGE_WINDOW).var().shift(1)
    x["beta"] = (cov / var).clip(lower=0.1, upper=5.0)
    x["spread"] = ls - x["beta"] * la
    x["mean"] = x["spread"].rolling(Z_WINDOW).mean().shift(1)
    x["std"] = x["spread"].rolling(Z_WINDOW).std().shift(1)
    x["z"] = (x["spread"] - x["mean"]) / x["std"]
    x = x.dropna().reset_index()

    equity = INITIAL
    position = None
    trades = []
    peak = INITIAL
    max_dd = 0.0

    for i in range(len(x) - 1):
        row, nxt = x.iloc[i], x.iloc[i + 1]
        if position is not None:
            position["bars"] += 1
            z = float(row["z"])
            exit_signal = abs(z) <= EXIT_Z or abs(z) >= STOP_Z or position["bars"] >= MAX_HOLD
            if exit_signal:
                sol_ret = float(nxt["sol"]) / position["sol_entry"] - 1
                avax_ret = float(nxt["avax"]) / position["avax_entry"] - 1
                # long spread = long SOL/short AVAX; short spread is inverse.
                gross = 0.5 * (sol_ret - avax_ret) if position["side"] == "long_spread" else 0.5 * (-sol_ret + avax_ret)
                net = gross - ROUND_TRIP_BOTH_LEGS_COST
                equity *= 1 + net
                trades.append({"entry_time": position["entry_time"], "exit_time": nxt["time"], "side": position["side"], "entry_z": position["entry_z"], "exit_z": z, "bars": position["bars"], "return_pct": net * 100, "equity": equity})
                peak = max(peak, equity)
                max_dd = min(max_dd, equity / peak - 1)
                position = None
            continue

        z = float(row["z"])
        if z >= ENTRY_Z:
            side = "short_spread"
        elif z <= -ENTRY_Z:
            side = "long_spread"
        else:
            continue
        position = {"side": side, "entry_time": nxt["time"], "entry_z": z, "sol_entry": float(nxt["sol"]), "avax_entry": float(nxt["avax"]), "bars": 0}

    t = pd.DataFrame(trades)
    split = x["time"].min() + (x["time"].max() - x["time"].min()) * 2 / 3
    oos = t[t["exit_time"] > split] if not t.empty else t
    summary = {"strategy": "PAIRS_TRADING_SOL_AVAX", "timeframe": f"{hours}h", "period_start": str(x["time"].min()), "period_end": str(x["time"].max()), "trades": len(t), "return_pct": (equity / INITIAL - 1) * 100, "final_balance": equity, "max_drawdown_pct": max_dd * 100, "winrate_pct": float((t["return_pct"] > 0).mean() * 100) if len(t) else 0.0, "oos_trades": len(oos), "oos_return_pct": float((oos["equity"].iloc[-1] / (oos["equity"].iloc[0] / (1 + oos["return_pct"].iloc[0] / 100) if len(oos) else INITIAL) - 1) * 100) if len(oos) else 0.0, "oos_winrate_pct": float((oos["return_pct"] > 0).mean() * 100) if len(oos) else 0.0, "entry_z": ENTRY_Z, "exit_z": EXIT_Z, "stop_z": STOP_Z, "cost_both_legs_pct": ROUND_TRIP_BOTH_LEGS_COST * 100}
    return summary, t


if __name__ == "__main__":
    rows, details = [], []
    for hours in (1, 2, 4):
        summary, trades = run(hours)
        rows.append(summary)
        details.append(trades.assign(timeframe=f"{hours}h"))
    pd.DataFrame(rows).to_csv(OUT / "pairs_trading_sol_avax_summary.csv", index=False)
    pd.concat(details, ignore_index=True).to_csv(OUT / "pairs_trading_sol_avax_trades.csv", index=False)
    print(pd.DataFrame(rows).to_string(index=False))
