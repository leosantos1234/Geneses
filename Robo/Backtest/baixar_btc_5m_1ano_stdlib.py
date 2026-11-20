"""Baixa BTCUSDT 5m de um ano da API pública da Bybit sem dependências externas."""
import csv
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError

ENDPOINT = "https://api.bybit.com/v5/market/kline"
START = datetime(2025, 10, 23, tzinfo=timezone.utc)
END = datetime(2026, 10, 24, tzinfo=timezone.utc)
OUT = Path(__file__).resolve().parents[1] / "dados_historicos" / "market_making_1y" / "market_data_BTCUSDT_5m.csv"
PARTS = OUT.parent / "parts"


def fetch(start_ms: int, end_ms: int):
    for attempt in range(6):
        query = urlencode({"category": "linear", "symbol": "BTCUSDT", "interval": "5", "start": start_ms, "end": end_ms, "limit": 1000})
        req = Request(f"{ENDPOINT}?{query}", headers={"User-Agent": "genesis-backtest/1.0"})
        try:
            with urlopen(req, timeout=30) as response:
                payload = json.load(response)
        except (TimeoutError, URLError) as exc:
            wait = 5 + attempt * 5
            print(f"falha temporaria ({exc}); aguardando {wait}s", flush=True)
            time.sleep(wait)
            continue
        if payload.get("retCode") == 0:
            return payload.get("result", {}).get("list", [])
        if payload.get("retCode") == 10006:
            wait = 20 + attempt * 10
            print(f"rate limit da Bybit; aguardando {wait}s", flush=True)
            time.sleep(wait)
            continue
        raise RuntimeError(f"Bybit retCode={payload.get('retCode')} msg={payload.get('retMsg')}")
    raise RuntimeError("Limite da Bybit permaneceu ativo após as tentativas de espera")


def main():
    start_ms = int(START.timestamp() * 1000)
    end_ms = int(END.timestamp() * 1000)
    PARTS.mkdir(parents=True, exist_ok=True)
    existing = sorted(PARTS.glob("part_*.csv"), key=lambda p: int(p.stem.split("_")[1]))
    rows = {}
    cursor = min((int(p.stem.split("_")[1]) for p in existing), default=end_ms) - 1 if existing else end_ms
    for part in existing:
        with part.open(newline="", encoding="utf-8") as handle:
            for item in csv.DictReader(handle):
                ts = int(datetime.fromisoformat(item["time"]).timestamp() * 1000)
                rows[ts] = [str(ts), item["open"], item["high"], item["low"], item["close"], item["volume"], item["turnover"]]
    if existing:
        print(f"retomando de {len(rows):,} candles em {len(existing)} blocos", flush=True)
    while cursor > start_ms:
        batch = fetch(start_ms, cursor)
        if not batch:
            break
        for item in batch:
            ts = int(item[0])
            if start_ms <= ts <= end_ms:
                rows[ts] = item
        oldest = min(int(item[0]) for item in batch)
        part_path = PARTS / f"part_{oldest}.csv"
        with part_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["time", "open", "high", "low", "close", "volume", "turnover"])
            for item in sorted(batch, key=lambda x: int(x[0])):
                writer.writerow([datetime.fromtimestamp(int(item[0]) / 1000, timezone.utc).isoformat(), *item[1:]])
        print(f"baixados {len(rows):,} candles | ate {datetime.fromtimestamp(oldest / 1000, timezone.utc).isoformat()}", flush=True)
        if oldest <= start_ms:
            break
        cursor = oldest - 1
        time.sleep(0.8)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["time", "open", "high", "low", "close", "volume", "turnover"])
        for ts in sorted(rows):
            item = rows[ts]
            writer.writerow([datetime.fromtimestamp(ts / 1000, timezone.utc).isoformat(), *item[1:]])
    print(f"SALVO {OUT} | {len(rows):,} candles")


if __name__ == "__main__":
    main()
