import os
import sys
import time
from datetime import datetime, timedelta

import pandas as pd
import requests
from dotenv import load_dotenv

ENDPOINT = "https://api.bybit.com/v5/market/kline"


def _load_env() -> str:
    env_arg = sys.argv[1] if len(sys.argv) > 1 else None
    env_path = env_arg or os.getenv("ENV_FILE") or ".env-multiativos"
    env_path = os.path.abspath(env_path)
    if not os.path.exists(env_path):
        raise FileNotFoundError(f"ENV file not found: {env_path}")
    load_dotenv(dotenv_path=env_path, override=True)
    return env_path


def _get_env(name: str, default=None, cast=None):
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    if cast:
        try:
            return cast(raw)
        except Exception:
            return default
    return raw


def _parse_list(raw: str):
    if not raw:
        return []
    return [x.strip().upper() for x in raw.split(",") if x.strip()]


def _parse_date(raw: str) -> datetime:
    return datetime.strptime(raw, "%Y-%m-%d")


def _fetch_klines(symbol: str, start_dt: datetime, end_dt: datetime, interval: str):
    start_ts = int(start_dt.timestamp() * 1000)
    end = end_dt
    data = []

    while end > start_dt:
        end_ts = int(end.timestamp() * 1000)
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "start": start_ts,
            "end": end_ts,
            "limit": 1000,
        }
        payload = None
        for attempt in range(3):
            try:
                r = requests.get(ENDPOINT, params=params, timeout=10)
            except requests.RequestException as exc:
                print(f"[WARN] {symbol} request error: {exc}")
                time.sleep(1.0 + attempt)
                continue

            if r.status_code != 200:
                print(f"[WARN] {symbol} status {r.status_code}")
                time.sleep(1.0 + attempt)
                continue

            payload = r.json()
            ret_code = payload.get("retCode", 0)
            if ret_code != 0:
                ret_msg = payload.get("retMsg", "unknown")
                print(f"[WARN] {symbol} retCode={ret_code} msg={ret_msg}")
                if ret_code in (10002, 10006):
                    time.sleep(1.5 + attempt)
                    continue
                return pd.DataFrame()
            break

        if payload is None:
            break
        candles = payload.get("result", {}).get("list", [])
        if not candles:
            break

        data.extend(candles)
        times = [int(c[0]) for c in candles]
        oldest = min(times)
        if oldest <= start_ts:
            break

        end = datetime.utcfromtimestamp((oldest - 1) / 1000.0)
        time.sleep(0.2)

    if not data:
        return pd.DataFrame()

    df = pd.DataFrame(
        data,
        columns=["time", "open", "high", "low", "close", "volume", "turnover"],
    )
    df["time"] = pd.to_numeric(df["time"], errors="coerce")
    df["time"] = pd.to_datetime(df["time"], unit="ms", errors="coerce")
    df = df[(df["time"] >= start_dt) & (df["time"] <= end_dt)]
    df = df.sort_values("time").drop_duplicates(subset=["time"])
    return df


def _file_covers_range(path: str, start_dt: datetime, end_dt: datetime) -> bool:
    if not os.path.exists(path):
        return False
    try:
        df = pd.read_csv(path, usecols=["time"])
    except Exception:
        return False
    if df.empty:
        return False
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.dropna()
    if df.empty:
        return False
    return df["time"].min() <= start_dt and df["time"].max() >= end_dt


def main():
    env_path = _load_env()
    interval = str(_get_env("INTERVAL", "5"))
    data_dir = _get_env("DATA_DIR", os.getcwd())
    os.makedirs(data_dir, exist_ok=True)

    start_raw = _get_env("DATA_START", "2025-06-01")
    end_raw = _get_env("DATA_END", "2025-12-31")
    start_dt = _parse_date(start_raw)
    end_dt = _parse_date(end_raw) + timedelta(days=1)

    btc_symbol = _get_env("BTC_SYMBOL", "BTCUSDT").upper()
    alts = _parse_list(_get_env("ATIVOS", ""))
    symbols = [btc_symbol] + alts

    print(f"Loaded ENV: {env_path}")
    print(f"Interval: {interval}m | Range: {start_raw} -> {end_raw}")
    print(f"Symbols: {', '.join(symbols)}")
    print(f"Output dir: {data_dir}")

    for symbol in symbols:
        print(f"\n[INFO] Downloading {symbol}...")
        out_path = os.path.join(data_dir, f"market_data_{symbol}_{interval}m.csv")
        if _file_covers_range(out_path, start_dt, end_dt - timedelta(minutes=1)):
            print(f"[SKIP] {symbol} already covers range -> {out_path}")
            continue
        df = _fetch_klines(symbol, start_dt, end_dt, interval)
        if df.empty:
            print(f"[WARN] No data for {symbol}")
            continue
        df.to_csv(out_path, index=False)
        print(f"[OK] Saved {len(df)} rows to {out_path}")


if __name__ == "__main__":
    main()
