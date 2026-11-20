"""Backtest read-only da Swing Trend Following em SOL-USDT-SWAP."""

from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from estrategia_entrada.swing_trend_following.strategy import backtest_entrada_1h


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "dados_historicos" / "pullback_36m"
OUT = Path(__file__).resolve().parent
COST_PER_TRADE = 0.00095


def main() -> None:
    one = pd.read_csv(DATA / "SOLUSDT_1h.csv")
    four = pd.read_csv(DATA / "SOLUSDT_4h.csv")
    daily = pd.read_csv(DATA / "SOLUSDT_1d.csv")
    result = backtest_entrada_1h(
        one,
        four,
        daily,
        paper_entry_mode="IMMEDIATE",
        stop_atr=2.0,
        target_atr=4.0,
        trailing_atr=2.5,
    )
    trades = sorted(result.get("trade_log", []), key=lambda item: pd.Timestamp(item["exit_time"]))
    balance = 1000.0
    peak = balance
    max_drawdown = 0.0
    for trade in trades:
        balance *= 1.0 + float(trade["pnl_pct"]) / 100.0 - COST_PER_TRADE
        peak = max(peak, balance)
        max_drawdown = min(max_drawdown, balance / peak - 1.0)
    wins = sum(float(trade.get("pnl_pct", 0.0)) > 0 for trade in trades)
    summary = {
        "strategy": "SWING_TREND_FOLLOWING",
        "symbol": "SOL-USDT-SWAP",
        "entry_timeframe": "1h",
        "period_start": str(pd.to_datetime(one["time"], utc=True).min()),
        "period_end": str(pd.to_datetime(one["time"], utc=True).max()),
        "candles_1h": len(one),
        "trades": len(trades),
        "wins": wins,
        "winrate_pct": float(result.get("winrate", 0.0)),
        "gross_return_pct": float(result.get("lucro_total", 0.0)),
        "final_balance_compounded": balance,
        "compound_return_pct": (balance / 1000.0 - 1.0) * 100.0,
        "max_drawdown_pct": max_drawdown * 100.0,
        "cost_per_trade_pct": COST_PER_TRADE * 100.0,
        "stop_atr": 2.0,
        "target_atr": 4.0,
        "trailing_atr": 2.5,
        "closed_candles_only": True,
        "data_source": "local_cache",
    }
    pd.DataFrame([summary]).to_csv(OUT / "swing_trend_following_sol_36m_summary.csv", index=False)
    pd.DataFrame(trades).to_csv(OUT / "swing_trend_following_sol_36m_trades.csv", index=False)
    print(pd.Series(summary).to_string())


if __name__ == "__main__":
    main()
