"""Gate de integridade para bases OHLCV usadas em backtests.

O gate guarda o hash do arquivo validado. Assim, arquivos sem alteração não
são reprocessados a cada execução, enquanto qualquer candle novo ou alteração
força uma validação completa antes do backtest.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd


REQUIRED_COLUMNS = {"time", "open", "high", "low", "close", "volume"}
DEFAULT_TTL_HOURS = 24.0


def _state_path(path: Optional[str] = None) -> Path:
    raw = path or os.getenv(
        "DATA_QUALITY_STATE_FILE",
        os.path.join("reports", "data_quality", "data_quality_state.json"),
    )
    return Path(raw)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_state(path: Path) -> Dict[str, Any]:
    try:
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
    except Exception:
        pass
    return {}


def _write_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _age_hours(value: Any) -> float:
    try:
        then = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - then).total_seconds() / 3600.0)
    except Exception:
        return math.inf


def validate_csv(path: str) -> Dict[str, Any]:
    file_path = Path(path).resolve()
    if not file_path.exists():
        raise FileNotFoundError(f"Base de candles nao encontrada: {file_path}")

    df = pd.read_csv(file_path)
    missing = sorted(REQUIRED_COLUMNS.difference(df.columns))
    if missing:
        raise ValueError(f"{file_path.name}: colunas ausentes: {missing}")
    if df.empty:
        raise ValueError(f"{file_path.name}: arquivo sem candles")

    df["time"] = pd.to_datetime(df["time"], errors="coerce", utc=True)
    if df["time"].isna().any():
        raise ValueError(f"{file_path.name}: timestamp inválido")
    if not df["time"].is_monotonic_increasing:
        raise ValueError(f"{file_path.name}: timestamps fora de ordem")
    duplicate_count = int(df["time"].duplicated().sum())
    if duplicate_count:
        raise ValueError(f"{file_path.name}: {duplicate_count} timestamps duplicados")

    numeric = ["open", "high", "low", "close", "volume"]
    for column in numeric:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    if not df[numeric].notna().all().all():
        raise ValueError(f"{file_path.name}: há valores OHLCV inválidos ou vazios")
    if (df[numeric] < 0).any().any() or (df[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError(f"{file_path.name}: há preços negativos/zerados")
    if (df["high"] < df[["open", "close"]].max(axis=1)).any():
        raise ValueError(f"{file_path.name}: high menor que open/close")
    if (df["low"] > df[["open", "close"]].min(axis=1)).any():
        raise ValueError(f"{file_path.name}: low maior que open/close")

    deltas = df["time"].diff().dropna().dt.total_seconds() / 60.0
    interval_minutes = float(deltas.median()) if not deltas.empty else 0.0
    max_gap_minutes = float(deltas.max()) if not deltas.empty else 0.0
    max_gap_multiple = float(os.getenv("DATA_QUALITY_MAX_GAP_MULT", "3"))
    if interval_minutes > 0 and max_gap_minutes > interval_minutes * max_gap_multiple:
        raise ValueError(
            f"{file_path.name}: lacuna anormal de {max_gap_minutes:.2f} min "
            f"(intervalo esperado {interval_minutes:.2f} min)"
        )

    return {
        "path": str(file_path),
        "rows": int(len(df)),
        "start": df["time"].iloc[0].isoformat(),
        "end": df["time"].iloc[-1].isoformat(),
        "interval_minutes": interval_minutes,
        "max_gap_minutes": max_gap_minutes,
        "duplicate_timestamps": duplicate_count,
        "sha256": _sha256(file_path),
        "validated_at": _utc_now(),
        "status": "valid",
    }


def ensure_validated_csv(path: str, state_file: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    """Valida somente quando necessário e bloqueia o uso de base inválida."""
    force = force or str(os.getenv("DATA_QUALITY_FORCE", "false")).strip().lower() in {"1", "true", "yes", "on"}
    file_path = Path(path).resolve()
    state_path = _state_path(state_file)
    state = _read_state(state_path)
    key = str(file_path)
    current_hash = _sha256(file_path) if file_path.exists() else None
    cached = state.get(key) if isinstance(state.get(key), dict) else None
    ttl_hours = float(os.getenv("DATA_QUALITY_TTL_HOURS", str(DEFAULT_TTL_HOURS)))
    cache_valid = (
        not force
        and cached
        and cached.get("status") == "valid"
        and cached.get("sha256") == current_hash
        and _age_hours(cached.get("validated_at")) <= ttl_hours
    )
    if cache_valid:
        return {**cached, "cached": True}

    result = validate_csv(str(file_path))
    state[key] = result
    _write_state(state_path, state)
    return {**result, "cached": False}
