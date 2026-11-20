import argparse
import csv
import json
import os
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import urlopen, Request


BASE_URL = "https://api.bybit.com/v5/market/kline"


def fetch_klines(symbol: str, interval: str, start_ms: int, limit: int = 1000):
    params = {
        "category": "linear",
        "symbol": symbol,
        "interval": interval,
        "start": start_ms,
        "limit": limit,
    }
    url = f"{BASE_URL}?{urlencode(params)}"
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
    data = json.loads(raw)
    if data.get("retCode") != 0:
        raise RuntimeError(f"Bybit error: {data.get('retMsg')} | url={url}")
    return data.get("result", {}).get("list", [])


def to_csv(symbol: str, interval: str, days: int, out_path: str):
    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days)
    start_ms = int(start_dt.timestamp() * 1000)

    rows = []
    last_ms = start_ms
    seen = set()

    while True:
        batch = fetch_klines(symbol, interval, last_ms, limit=1000)
        if not batch:
            break

        # API returns newest-first; reverse to keep ascending time.
        batch = list(reversed(batch))
        new_rows = 0
        for item in batch:
            # item: [start, open, high, low, close, volume, turnover]
            ts = int(item[0])
            if ts < start_ms:
                continue
            if ts in seen:
                continue
            seen.add(ts)
            rows.append(item)
            new_rows += 1

        last_ms = int(batch[-1][0]) + 1

        # If we got fewer than limit rows, likely done.
        if len(batch) < 1000 or new_rows == 0:
            break

        time.sleep(0.05)

    if not rows:
        raise RuntimeError(f"Nenhum dado retornado para {symbol}.")

    # Sort ascending by time just in case.
    rows.sort(key=lambda r: int(r[0]))

    # Backup existing file
    if os.path.exists(out_path):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{out_path}.bak_{ts}"
        shutil.copy(out_path, backup_path)

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["time", "open", "high", "low", "close", "volume", "turnover"])
        writer.writerows(rows)

    return len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT")
    parser.add_argument("--interval", default="5")
    parser.add_argument("--days", type=int, default=120)
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        print("Nenhum simbolo informado.")
        sys.exit(1)

    for sym in symbols:
        out_path = f"market_data_{sym}_{args.interval}m.csv"
        count = to_csv(sym, args.interval, args.days, out_path)
        print(f"{sym}: {count} candles salvos em {out_path}")


if __name__ == "__main__":
    main()
