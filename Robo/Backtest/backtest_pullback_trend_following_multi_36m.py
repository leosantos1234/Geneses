"""Baixa candles públicos e roda o Pullback Trend Following em vários ativos.

Somente endpoint público de candles; não usa credenciais nem envia ordens.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from backtest_pullback_trend_following_avax_36m import (
    add_pullback_indicators,
    mark_to_market,
    run,
)
from backtest_breakout_trend_following_avax_36m import attach_context


ENDPOINT = "https://www.okx.com/api/v5/market/history-candles"
ASSETS = ("BTC", "ETH", "BNB", "SOL", "XRP")
START = datetime.now(timezone.utc) - timedelta(days=1095)
END = datetime.now(timezone.utc)
OUT = Path(__file__).resolve().parents[1] / "dados_historicos" / "pullback_36m"


def fetch(inst_id: str, bar: str) -> pd.DataFrame:
    start_ms = int(START.timestamp() * 1000)
    end_ms = int(END.timestamp() * 1000)
    rows: dict[int, list] = {}
    after = end_ms
    while True:
        query = urlencode({"instId": inst_id, "bar": bar, "limit": 100, "after": str(after)})
        request = Request(f"{ENDPOINT}?{query}", headers={"User-Agent": "genesis-pullback-backtest/1.0"})
        last_error = None
        for attempt in range(6):
            try:
                with urlopen(request, timeout=30) as response:
                    payload = json.load(response)
                last_error = None
                break
            except Exception as error:
                last_error = error
                time.sleep(2.0 * (attempt + 1))
        if last_error is not None:
            raise last_error
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
        time.sleep(0.08)
    frame = pd.DataFrame([rows[key] for key in sorted(rows)], columns=["time", "open", "high", "low", "close", "volume"])
    frame["time"] = pd.to_datetime(frame["time"], unit="ms", utc=True)
    return frame


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for asset in ASSETS:
        inst = f"{asset}-USDT-SWAP"
        print(f"Baixando {inst}...", flush=True)
        frames = {}
        for label, bar in (("1h", "1H"), ("4h", "4H"), ("1d", "1D")):
            path = OUT / f"{asset}USDT_{label}.csv"
            frame = fetch(inst, bar)
            frame.to_csv(path, index=False)
            frames[label] = frame
            print(f"  {label}: {len(frame):,} candles", flush=True)
        one = add_pullback_indicators(frames["1h"])
        data = attach_context(one, frames["4h"], frames["1d"])
        data = data.dropna(subset=["ema20", "ema50", "atr", "volume_sma20", "four_ema50", "four_ema200", "day_ema20", "day_ema50"]).reset_index(drop=True)
        trades = run(data)
        all_m = mark_to_market(trades)
        oos_start = data["time"].max() - pd.Timedelta(days=365)
        oos = trades[trades["entry_time"] >= oos_start] if not trades.empty else trades
        oos_m = mark_to_market(oos)
        rows.append({"asset": asset, "period_start": str(data.time.min()), "period_end": str(data.time.max()), **{f"all_{k}": v for k, v in all_m.items()}, **{f"oos_{k}": v for k, v in oos_m.items()}})
        trades.to_csv(OUT / f"{asset}USDT_pullback_trades.csv", index=False)
    result = pd.DataFrame(rows)
    result.to_csv(Path(__file__).resolve().parent / "pullback_trend_following_multi_36m_summary.csv", index=False)
    print("\nRESULTADO")
    print(result.to_string(index=False))


if __name__ == "__main__":
    main()
