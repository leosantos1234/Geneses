import os
import sys
from itertools import product
from typing import Dict, List

import numpy as np
import pandas as pd
from dotenv import load_dotenv


def _load_env() -> str:
    env_arg = sys.argv[1] if len(sys.argv) > 1 else None
    env_path = env_arg or os.getenv("ENV_FILE") or ".env-multiativos"
    env_path = os.path.abspath(env_path)
    if not os.path.exists(env_path):
        raise FileNotFoundError(f"ENV file not found: {env_path}")
    load_dotenv(dotenv_path=env_path, override=True)
    return env_path


def _metric(trades) -> Dict[str, float]:
    if not trades:
        return {"trades": 0, "pnl": 0.0, "winrate": 0.0, "avg_win": 0.0, "avg_loss": 0.0}
    pnl = sum(t.pnl for t in trades)
    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl <= 0]
    winrate = (len(wins) / len(trades)) * 100.0
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    return {
        "trades": len(trades),
        "pnl": float(pnl),
        "winrate": float(winrate),
        "avg_win": float(avg_win),
        "avg_loss": float(avg_loss),
    }


def main():
    env_path = _load_env()

    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if root_dir not in sys.path:
        sys.path.insert(0, root_dir)

    module_path = os.path.join(root_dir, "Backtest", "backtest_multiativos.py")
    if not os.path.exists(module_path):
        raise FileNotFoundError(f"Missing backtest module: {module_path}")
    import importlib.util

    spec = importlib.util.spec_from_file_location("backtest_multiativos", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load backtest module spec")
    backtest_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backtest_mod)
    LeadLagBacktest = backtest_mod.LeadLagBacktest

    grid = {
        "RRR": [1.5, 2.0, 2.5],
        "SL_PCT_MAX": [0.008, 0.01],
        "TRAILING_ACTIVATION_PCT": [0.5, 0.6],
    }

    combos = list(product(grid["RRR"], grid["SL_PCT_MAX"], grid["TRAILING_ACTIVATION_PCT"]))
    results: List[Dict[str, float]] = []

    print(f"Loaded ENV: {env_path}")
    print(f"Running grid: {len(combos)} combinations")

    for rrr, sl_pct_max, trail_act in combos:
        os.environ["RRR"] = str(rrr)
        os.environ["SL_PCT_MAX"] = str(sl_pct_max)
        os.environ["TRAILING_ACTIVATION_PCT"] = str(trail_act)

        bt = LeadLagBacktest()
        trades = bt.run()
        metrics = _metric(trades)
        metrics.update(
            {
                "RRR": rrr,
                "SL_PCT_MAX": sl_pct_max,
                "TRAILING_ACTIVATION_PCT": trail_act,
            }
        )
        results.append(metrics)
        print(
            f"RRR={rrr} SL_PCT_MAX={sl_pct_max} TRAIL={trail_act} | "
            f"trades={metrics['trades']} pnl={metrics['pnl']:.2f} "
            f"winrate={metrics['winrate']:.2f}%"
        )

    df = pd.DataFrame(results)
    out_dir = os.getenv("OUTPUT_DIR", os.path.join(root_dir, "resultados_backtest"))
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "leadlag_grid_results.csv")
    df.to_csv(out_path, index=False)

    best = df.sort_values("pnl", ascending=False).head(5)
    print("\nTop 5 by PnL:")
    print(best.to_string(index=False))


if __name__ == "__main__":
    main()
