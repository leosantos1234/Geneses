"""Tabela mensal composta do swing trend following (somente leitura)."""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

sys.path.insert(0, str(__file__).split("\\Backtest")[0])
from estrategia_entrada.swing_trend_following.strategy import backtest_entrada_1h


ENDPOINT = "https://www.okx.com/api/v5/market/history-candles"
START = datetime(2023, 10, 17, tzinfo=timezone.utc)
END = datetime(2026, 10, 17, tzinfo=timezone.utc)
INITIAL = 1000.0
COST_BY_ASSET = {"AVAX": 0.00095, "ADA": 0.00090}  # maker + taker + slippage, por trade


def fetch(inst_id: str, bar: str) -> pd.DataFrame:
    start_ms = int(START.timestamp() * 1000)
    end_ms = int(END.timestamp() * 1000)
    rows = {}
    after = end_ms
    while True:
        query = urlencode({"instId": inst_id, "bar": bar, "limit": 100, "after": str(after)})
        request = Request(f"{ENDPOINT}?{query}", headers={"User-Agent": "genesis-readonly-monthly-backtest/1.0"})
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
        if payload.get("code") != "0":
            raise RuntimeError(payload)
        batch = payload.get("data") or []
        if not batch:
            break
        oldest = None
        for raw in batch:
            ts = int(raw[0])
            oldest = ts if oldest is None else min(oldest, ts)
            if start_ms <= ts <= end_ms:
                rows[ts] = [ts, float(raw[1]), float(raw[2]), float(raw[3]), float(raw[4]), float(raw[5])]
        if oldest is None or oldest <= start_ms or len(batch) < 100:
            break
        after = oldest - 1
        time.sleep(0.12)
    frame = pd.DataFrame([rows[k] for k in sorted(rows)], columns=["time", "open", "high", "low", "close", "volume"])
    frame["time"] = pd.to_datetime(frame["time"], unit="ms", utc=True)
    return frame


def monthly_curve(trades: list[dict], net_cost_pct: float) -> pd.DataFrame:
    balance = INITIAL
    records = []
    for trade in sorted(trades, key=lambda x: pd.Timestamp(x["exit_time"])):
        net_return = float(trade["pnl_pct"]) / 100.0 - net_cost_pct
        balance *= 1.0 + net_return
        month = pd.Timestamp(trade["exit_time"]).tz_convert("UTC").strftime("%Y-%m")
        records.append((month, balance))
    curve = pd.DataFrame(records, columns=["month", "balance"])
    if curve.empty:
        return pd.DataFrame({"month": pd.period_range(START.date(), END.date(), freq="M").astype(str), "balance": INITIAL})
    curve = curve.groupby("month", as_index=False).last()
    months = pd.DataFrame({"month": pd.period_range(START.date(), END.date(), freq="M").astype(str)})
    curve = months.merge(curve, on="month", how="left")
    curve["balance"] = curve["balance"].ffill().fillna(INITIAL)
    curve["return_month_pct"] = curve["balance"].pct_change().fillna(curve["balance"] / INITIAL - 1.0) * 100.0
    return curve


def main() -> None:
    for asset in ("AVAX", "ADA"):
        print(f"baixando {asset}", flush=True)
        one = fetch(f"{asset}-USDT-SWAP", "1H")
        four = fetch(f"{asset}-USDT-SWAP", "4H")
        daily = fetch(f"{asset}-USDT-SWAP", "1D")
        result = backtest_entrada_1h(one, four, daily, paper_entry_mode="IMMEDIATE", stop_atr=2.0, target_atr=4.0, trailing_atr=2.5)
        curve = monthly_curve(result.get("trade_log", []), COST_BY_ASSET[asset])
        curve.to_csv(f"Robo/Backtest/{asset.lower()}_mensal_composto_36m.csv", index=False)
        print(asset, result.get("num_trades"), result.get("winrate"), flush=True)
        for row in curve.itertuples(index=False):
            print(f"{asset},{row.month},{row.balance:.2f},{row.return_month_pct:.2f}", flush=True)


if __name__ == "__main__":
    main()
