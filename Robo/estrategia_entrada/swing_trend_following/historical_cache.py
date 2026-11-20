"""Cache local do histórico de 36 meses usado pelo backtest Swing.

O backtest de referência usa candles públicos da OKX (SWAP), em UTC, nos
intervalos 1H, 4H e 1D. O cache é atualizado incrementalmente e nunca envia
ordens.
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
import requests


LOG = logging.getLogger(__name__)
OKX_HISTORY_URL = "https://www.okx.com/api/v5/market/history-candles"
BAR_MAP = {"1h": "1H", "4h": "4H", "1d": "1D"}
BAR_DELTA = {"1h": pd.Timedelta(hours=1), "4h": pd.Timedelta(hours=4), "1d": pd.Timedelta(days=1)}
REQUIRED_COLUMNS = ["time", "open", "high", "low", "close", "volume"]
_MEMORY_CACHE: dict[tuple[str, int], tuple[pd.Timestamp, dict[str, pd.DataFrame]]] = {}
_LAST_FAILURE: dict[tuple[str, int], pd.Timestamp] = {}
_MEMORY_REFRESH = pd.Timedelta(hours=1)
_FAILURE_COOLDOWN = pd.Timedelta(minutes=15)


def _symbol_to_inst_id(symbol: str) -> str:
    text = str(symbol or "").upper().replace("-", "")
    asset = text.replace("USDT", "")
    if not asset:
        raise ValueError("símbolo Swing vazio")
    return f"{asset}-USDT-SWAP"


def _cache_root() -> Path:
    configured = os.getenv("SWING_HISTORICAL_CACHE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[2] / "dados_historicos" / "swing_36m"


def _parse(raw: list[list[str]]) -> pd.DataFrame:
    rows = []
    for row in raw:
        # A OKX retorna confirm=1 para candle fechado (coluna 8).
        # Nunca usar candle aberto no backtest ou no setup.
        if len(row) < 6 or (len(row) > 8 and str(row[8]) != "1"):
            continue
        rows.append(row[:6])
    frame = pd.DataFrame(rows, columns=REQUIRED_COLUMNS)
    if frame.empty:
        return pd.DataFrame(columns=REQUIRED_COLUMNS)
    frame["time"] = pd.to_datetime(pd.to_numeric(frame["time"], errors="coerce"), unit="ms", utc=True)
    for column in REQUIRED_COLUMNS[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return (
        frame.dropna()
        .drop_duplicates("time")
        .sort_values("time")
        .reset_index(drop=True)
    )


def _fetch(inst_id: str, bar: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    after = end_ms
    collected: dict[int, list[str]] = {}
    while True:
        query = urlencode({"instId": inst_id, "bar": bar, "limit": "100", "after": str(after)})
        response = requests.get(
            OKX_HISTORY_URL,
            params={"instId": inst_id, "bar": bar, "limit": 100, "after": after},
            headers={"User-Agent": "genesis-swing-cache/1.0"},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != "0":
            raise RuntimeError(f"OKX histórico {bar}: {payload.get('msg') or payload.get('code')}")
        batch = payload.get("data") or []
        if not batch:
            break
        oldest = None
        for row in batch:
            ts = int(row[0])
            oldest = ts if oldest is None else min(oldest, ts)
            if start_ms <= ts <= end_ms:
                collected[ts] = row
        if oldest is None or oldest <= start_ms or len(batch) < 100:
            break
        after = oldest - 1
        time.sleep(0.08)
    return _parse([collected[key] for key in sorted(collected)])


def _safe_name(symbol: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(symbol).upper()) or "UNKNOWN"


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=REQUIRED_COLUMNS)
    frame = pd.read_csv(path)
    if not set(REQUIRED_COLUMNS).issubset(frame.columns):
        return pd.DataFrame(columns=REQUIRED_COLUMNS)
    frame["time"] = pd.to_datetime(frame["time"], utc=True, errors="coerce")
    for column in REQUIRED_COLUMNS[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna().drop_duplicates("time").sort_values("time").reset_index(drop=True)


def validate_swing_history(frames: dict[str, pd.DataFrame], months: int = 36,
                           now: pd.Timestamp | None = None) -> dict:
    """Valida a janela mínima usada pelo backtest histórico Swing.

    A validação é deliberadamente rígida: uma janela curta não pode ser
    promovida a evidência histórica nem participar do ranking operacional.
    """
    now = now or pd.Timestamp.now(tz="UTC")
    start = now - pd.DateOffset(months=int(months))
    result = {"valid": True, "months_required": int(months), "start": start, "end": now, "timeframes": {}}
    expected_ratio = {"1h": 0.97, "4h": 0.97, "1d": 0.97}
    for name, delta in BAR_DELTA.items():
        frame = frames.get(name) if isinstance(frames, dict) else None
        info = {"candles": 0, "span_days": 0.0, "gaps": 0, "valid": False}
        if isinstance(frame, pd.DataFrame) and not frame.empty and "time" in frame.columns:
            work = frame.copy()
            work["time"] = pd.to_datetime(work["time"], utc=True, errors="coerce")
            work = work.dropna(subset=["time"]).sort_values("time").drop_duplicates("time")
            work = work[(work["time"] >= start) & (work["time"] < now)]
            if len(work):
                timestamps = work["time"]
                gaps = timestamps.diff().dropna() > (delta * 1.5)
                expected = int(((now - start) / delta) * expected_ratio[name])
                info.update({
                    "candles": int(len(work)),
                    "span_days": round(float((timestamps.iloc[-1] - timestamps.iloc[0]).total_seconds() / 86400), 2),
                    "gaps": int(gaps.sum()),
                    "valid": bool(len(work) >= expected and int(gaps.sum()) == 0),
                })
        result["timeframes"][name] = info
        result["valid"] = bool(result["valid"] and info["valid"])
    return result


def load_swing_history(symbol: str, months: int = 36) -> dict[str, pd.DataFrame]:
    """Retorna 1h/4h/1d com janela móvel de *months* meses.

    O arquivo é criado na primeira execução e depois recebe apenas candles
    novos/atualizados. A última vela aberta é removida antes do retorno.
    """
    now = pd.Timestamp.now(tz="UTC")
    start = now - pd.DateOffset(months=int(months))
    key = (_safe_name(symbol), int(months))
    memory_entry = _MEMORY_CACHE.get(key)
    if memory_entry and now - memory_entry[0] < _MEMORY_REFRESH:
        LOG.info("[SWING][HISTORY] memory_hit %s months=%s", symbol, months)
        return {name: frame.copy() for name, frame in memory_entry[1].items()}
    last_failure = _LAST_FAILURE.get(key)
    if last_failure and now - last_failure < _FAILURE_COOLDOWN:
        retry_at = last_failure + _FAILURE_COOLDOWN
        raise RuntimeError(f"historico_swing_em_cooldown:retry_after={retry_at.isoformat()}")
    inst_id = _symbol_to_inst_id(symbol)
    root = _cache_root()
    root.mkdir(parents=True, exist_ok=True)
    result: dict[str, pd.DataFrame] = {}
    for name, bar in BAR_MAP.items():
        path = root / f"{_safe_name(symbol)}_{name}.csv"
        cached = _load_csv(path)
        if (
            not cached.empty
            and cached["time"].min() <= start + BAR_DELTA[name]
            and cached["time"].max() >= now - BAR_DELTA[name] * 1.5
        ):
            result[name] = cached[(cached["time"] >= start) & (cached["time"] < now)].reset_index(drop=True)
            LOG.info("[SWING][HISTORY] %s %s cache_hit candles=%s", symbol, name, len(result[name]))
            continue
        # Reconsulta uma janela recente para capturar o candle fechado mais novo
        # e mantém o cache correto mesmo após reinício do worker.
        refresh_start = start if cached.empty else max(start, cached["time"].max() - pd.Timedelta(days=3))
        try:
            fresh = _fetch(inst_id, bar, refresh_start, now)
        except Exception:
            _LAST_FAILURE[key] = now
            raise
        merged = pd.concat([cached, fresh], ignore_index=True)
        merged = merged.drop_duplicates("time", keep="last").sort_values("time")
        merged = merged[(merged["time"] >= start) & (merged["time"] < now)]
        merged = merged.reset_index(drop=True)
        merged.to_csv(path, index=False)
        result[name] = merged
        LOG.info("[SWING][HISTORY] %s %s candles=%s range=%s..%s cache=%s", symbol, name, len(merged),
                 merged["time"].min(), merged["time"].max(), path)
    validation = validate_swing_history(result, months=months, now=now)
    if not validation["valid"]:
        raise RuntimeError(f"historico_swing_insuficiente:{validation}")
    _MEMORY_CACHE[key] = (now, {name: frame.copy() for name, frame in result.items()})
    _LAST_FAILURE.pop(key, None)
    LOG.info("[SWING][HISTORY] validado months=%s timeframes=%s", months, validation["timeframes"])
    return result
