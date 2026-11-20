"""Compara o gatilho da Swing no AVAX mantendo o regime 4h/diário."""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pandas as pd

sys.path.insert(0, str(__file__).split("\\Backtest")[0])
from estrategia_entrada.swing_trend_following.strategy import backtest_entrada_1h


ENDPOINT = "https://www.okx.com/api/v5/market/history-candles"
START = datetime(2023, 10, 17, tzinfo=timezone.utc)
END = datetime(2026, 10, 17, tzinfo=timezone.utc)
SYMBOL = "AVAX-USDT-SWAP"
COST_PER_TRADE = 0.00095  # maker + taker + slippage, por operação


def fetch_chunk(bar: str, start_ms: int, end_ms: int, chunk_id: int) -> dict[int, list[float | int]]:
    rows: dict[int, list[float | int]] = {}
    after = end_ms
    while True:
        query = urlencode({"instId": SYMBOL, "bar": bar, "limit": 100, "after": str(after)})
        request = Request(f"{ENDPOINT}?{query}", headers={"User-Agent": "genesis-swing-timeframe-test/1.0"})
        for attempt in range(6):
            try:
                with urlopen(request, timeout=30) as response:
                    payload = json.load(response)
                break
            except HTTPError as error:
                if error.code != 429 or attempt == 5:
                    raise
                time.sleep(2.0 * (attempt + 1))
        if payload.get("code") != "0":
            raise RuntimeError(f"{bar}: {payload}")
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
        time.sleep(0.15)
    print(f"  {bar}: bloco {chunk_id + 1} concluido ({len(rows):,} candles)", flush=True)
    return rows


def fetch(bar: str) -> pd.DataFrame:
    start_ms = int(START.timestamp() * 1000)
    end_ms = int(END.timestamp() * 1000)
    span = (end_ms - start_ms) // 16
    ranges = [(start_ms + i * span, end_ms if i == 15 else start_ms + (i + 1) * span - 1, i) for i in range(16)]
    rows: dict[int, list[float | int]] = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(fetch_chunk, bar, chunk_start, chunk_end, index)
                   for chunk_start, chunk_end, index in ranges]
        for future in as_completed(futures):
            rows.update(future.result())
    frame = pd.DataFrame(
        [rows[key] for key in sorted(rows)],
        columns=["time", "open", "high", "low", "close", "volume"],
    )
    frame["time"] = pd.to_datetime(frame["time"], unit="ms", utc=True)
    print(f"  {bar}: {len(frame):,} candles coletados em paralelo", flush=True)
    return frame


def summarize(timeframe: str, result: dict) -> dict:
    balance = 1000.0
    peak = balance
    max_drawdown = 0.0
    for trade in sorted(result.get("trade_log", []), key=lambda item: pd.Timestamp(item["exit_time"])):
        balance *= 1.0 + float(trade["pnl_pct"]) / 100.0 - COST_PER_TRADE
        peak = max(peak, balance)
        max_drawdown = min(max_drawdown, balance / peak - 1.0)
    return {
        "timeframe_entrada": timeframe,
        "trades": int(result.get("num_trades", 0)),
        "winrate_pct": float(result.get("winrate", 0.0)),
        "resultado_bruto_pct": float(result.get("lucro_total", 0.0)),
        "saldo_final_composto": balance,
        "retorno_composto_pct": (balance / 1000.0 - 1.0) * 100.0,
        "drawdown_max_pct": max_drawdown * 100.0,
        "regimes": ",".join(map(str, result.get("regimes_operados", []))),
    }


def main() -> None:
    print(f"AVAX Swing | {START.date()} -> {END.date()} | regime fixo: 4h + 1D", flush=True)
    four = fetch("4H")
    daily = fetch("1D")
    results = []
    for timeframe, bar in (("5m", "5m"), ("15m", "15m"), ("30m", "30m"), ("1h", "1H"), ("4h", "4H")):
        print(f"Executando entrada em {timeframe}...", flush=True)
        entry = fetch(bar)
        result = backtest_entrada_1h(
            entry,
            four,
            daily,
            paper_entry_mode="IMMEDIATE",
            stop_atr=2.0,
            target_atr=4.0,
            trailing_atr=2.5,
        )
        results.append(summarize(timeframe, result))
        print(results[-1], flush=True)
    output = pd.DataFrame(results)
    output.to_csv("Backtest/comparacao_timeframes_swing_avax_36m.csv", index=False)
    print("\nRESULTADO FINAL")
    print(output.to_string(index=False))


if __name__ == "__main__":
    main()
