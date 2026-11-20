"""Heartbeat compartilhado entre workers e endpoints operacionais."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


ROBO_ROOT = Path(__file__).resolve().parents[1]
HEARTBEAT_DIR = Path(os.getenv("GENESIS_HEARTBEAT_DIR", str(ROBO_ROOT / "runtime" / "heartbeats"))).expanduser()
if not HEARTBEAT_DIR.is_absolute():
    HEARTBEAT_DIR = (ROBO_ROOT / HEARTBEAT_DIR).resolve()
HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)


def _safe_name(symbol: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in str(symbol or "worker").upper())


def write_heartbeat(symbol: str, state: str = "running", **details: Any) -> None:
    payload = {
        "symbol": str(symbol or "unknown").upper(),
        "state": state,
        "timestamp": time.time(),
        **details,
    }
    target = HEARTBEAT_DIR / f"{_safe_name(symbol)}.json"
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def latest_heartbeat() -> dict[str, Any]:
    latest: dict[str, Any] | None = None
    for path in HEARTBEAT_DIR.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if latest is None or float(payload.get("timestamp", 0)) > float(latest.get("timestamp", 0)):
                latest = payload
        except (OSError, ValueError, TypeError):
            continue
    if not latest:
        return {"status": "missing", "age_seconds": None, "path": str(HEARTBEAT_DIR)}
    age = max(0.0, time.time() - float(latest.get("timestamp", time.time())))
    latest["age_seconds"] = round(age, 3)
    latest["status"] = "stale" if age > float(os.getenv("GENESIS_HEARTBEAT_STALE_SECONDS", "120")) else "healthy"
    return latest
