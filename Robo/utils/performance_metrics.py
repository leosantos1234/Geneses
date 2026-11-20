"""Telemetria leve de ciclos e chamadas externas."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROBO_ROOT = Path(__file__).resolve().parents[1]
PERFORMANCE_DIR = Path(
    os.getenv("GENESIS_PERFORMANCE_METRICS_DIR", str(ROBO_ROOT / "runtime" / "metrics"))
).expanduser()
if not PERFORMANCE_DIR.is_absolute():
    PERFORMANCE_DIR = (ROBO_ROOT / PERFORMANCE_DIR).resolve()


def record_timing(
    event: str,
    duration_seconds: float,
    *,
    role: str,
    symbol: str,
    operation: str,
    service: str | None = None,
    status: str = "success",
    error_type: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Grava uma métrica sem incluir URL, payload ou credenciais."""
    PERFORMANCE_DIR.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "event": event,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        "role": role,
        "active_symbol": symbol,
        "operation": operation,
        "service": service,
        "status": status,
        "duration_ms": round(max(0.0, float(duration_seconds)) * 1000.0, 3),
    }
    if error_type:
        payload["error_type"] = error_type
    if extra:
        payload.update(extra)
    target = PERFORMANCE_DIR / f"performance_{os.getpid()}.jsonl"
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
