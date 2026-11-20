"""Revisão controlada da Breakout Trend Following.

Somente leitura: usa o cache local, não acessa exchange e não envia ordens.
Compara uma configuração-base com filtros anti-falso-rompimento.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from backtest_breakout_trend_following_avax_36m import (
    COST_PER_TRADE,
    DATA,
    INITIAL,
    attach_context,
    enrich,
    load,
)


CONFIGS = [
    {"name": "base", "buffer_atr": 0.00, "confirm": 1, "volume": 1.00, "ema_gap": 0.000, "cooldown": 0},
    {"name": "buffer025_confirm2_vol12", "buffer_atr": 0.25, "confirm": 2, "volume": 1.20, "ema_gap": 0.001, "cooldown": 3},
    {"name": "buffer020_confirm2_vol12", "buffer_atr": 0.20, "confirm": 2, "volume": 1.20, "ema_gap": 0.001, "cooldown": 3},
    {"name": "buffer025_confirm1_vol12", "buffer_atr": 0.25, "confirm": 1, "volume": 1.20, "ema_gap": 0.001, "cooldown": 3},
    {"name": "buffer020_confirm2_vol15", "buffer_atr": 0.20, "confirm": 2, "volume": 1.50, "ema_gap": 0.001, "cooldown": 3},
]


def metrics(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"trades": 0, "winrate_pct": 0.0, "gross_return_pct": 0.0, "compound_return_pct": 0.0, "max_drawdown_pct": 0.0}
    balance = INITIAL
    peak = balance
    max_dd = 0.0
    for pnl in trades.sort_values("exit_time")["pnl_pct"]:
        balance *= 1.0 + float(pnl) / 100.0 - COST_PER_TRADE
        peak = max(peak, balance)
        max_dd = min(max_dd, balance / peak - 1.0)
    return {
        "trades": int(len(trades)),
        "winrate_pct": float((trades["pnl_pct"] > 0).mean() * 100.0),
        "gross_return_pct": float(trades["pnl_pct"].sum()),
        "compound_return_pct": float((balance / INITIAL - 1.0) * 100.0),
        "max_drawdown_pct": float(max_dd * 100.0),
    }


def run_config(data: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    position = None
    cooldown = 0
    trades = []
    for idx, row in enumerate(data.itertuples(index=False)):
        high, low, close, atr = map(float, (row.high, row.low, row.close, row.atr))
        if position is not None:
            exit_price = None
            reason = None
            if position["side"] == "buy":
                if low <= position["stop"]:
                    exit_price, reason = position["stop"], "stop"
                elif high >= position["target"]:
                    exit_price, reason = position["target"], "target"
            else:
                if high >= position["stop"]:
                    exit_price, reason = position["stop"], "stop"
                elif low <= position["target"]:
                    exit_price, reason = position["target"], "target"
            if exit_price is not None:
                pnl = ((exit_price - position["entry"]) / position["entry"] * 100.0) if position["side"] == "buy" else ((position["entry"] - exit_price) / position["entry"] * 100.0)
                trades.append({**position, "exit": exit_price, "exit_time": row.time, "exit_reason": reason, "pnl_pct": pnl})
                cooldown = cfg["cooldown"] if reason == "stop" else 0
                position = None
            elif position["side"] == "buy":
                position["stop"] = max(position["stop"], close - position["trail"])
            else:
                position["stop"] = min(position["stop"], close + position["trail"])
            continue
        if cooldown:
            cooldown -= 1
            continue

        long_context = row.four_close > row.four_ema20 > row.four_ema50 and row.day_close > row.day_ema20 > row.day_ema50 and (row.four_ema20 / row.four_ema50 - 1.0) >= cfg["ema_gap"]
        short_context = row.four_close < row.four_ema20 < row.four_ema50 and row.day_close < row.day_ema20 < row.day_ema50 and (row.four_ema50 / row.four_ema20 - 1.0) >= cfg["ema_gap"]
        volume_ok = float(row.volume) >= float(row.volume_sma20) * cfg["volume"]
        recent = data.iloc[max(0, idx - cfg["confirm"] + 1): idx + 1]
        long_break = volume_ok and all(r.close >= r.prior_high20 + cfg["buffer_atr"] * r.atr and r.volume >= r.volume_sma20 * cfg["volume"] for r in recent.itertuples())
        short_break = volume_ok and all(r.close <= r.prior_low20 - cfg["buffer_atr"] * r.atr and r.volume >= r.volume_sma20 * cfg["volume"] for r in recent.itertuples())
        side = "buy" if long_context and long_break else "sell" if short_context and short_break else None
        if side == "buy":
            position = {"side": side, "entry": close, "entry_time": row.time, "stop": close - 2.0 * atr, "target": close + 4.0 * atr, "trail": 2.5 * atr}
        elif side == "sell":
            position = {"side": side, "entry": close, "entry_time": row.time, "stop": close + 2.0 * atr, "target": close - 4.0 * atr, "trail": 2.5 * atr}
    return pd.DataFrame(trades)


def main() -> None:
    one = enrich(load("AVAXUSDT_1h.csv"))
    data = attach_context(one, load("AVAXUSDT_4h.csv"), load("AVAXUSDT_1d.csv"))
    data = data.dropna(subset=["prior_high20", "prior_low20", "atr", "volume_sma20", "four_ema20", "four_ema50", "day_ema20", "day_ema50"]).reset_index(drop=True)
    rows = []
    detail = []
    oos_start = data["time"].max() - pd.Timedelta(days=365)
    for cfg in CONFIGS:
        trades = run_config(data, cfg)
        all_m = metrics(trades)
        oos = trades[trades["entry_time"] >= oos_start] if not trades.empty else trades
        oos_m = metrics(oos)
        rows.append({**cfg, **{f"all_{k}": v for k, v in all_m.items()}, **{f"oos_{k}": v for k, v in oos_m.items()}, "oos_start": oos_start})
        if not trades.empty:
            trades.insert(0, "config", cfg["name"])
            detail.append(trades)
    result = pd.DataFrame(rows)
    out = Path(__file__).resolve().parent
    result.to_csv(out / "breakout_trend_following_avax_36m_revisoes.csv", index=False)
    pd.concat(detail, ignore_index=True).to_csv(out / "breakout_trend_following_avax_36m_revisoes_trades.csv", index=False)
    print(result.to_string(index=False))


if __name__ == "__main__":
    main()
