"""Auditoria read-only de processos Genesis duplicados."""

from __future__ import annotations

import json
import logging
import os
import platform
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROBO_ROOT = Path(__file__).resolve().parents[1]
STARTUP_EVENTS_PATH = ROBO_ROOT / "runtime" / "startups" / "startup_events.jsonl"
AUDIT_DIR = Path(os.getenv("GENESIS_PROCESS_AUDIT_DIR", str(ROBO_ROOT / "runtime" / "metrics"))).expanduser()
if not AUDIT_DIR.is_absolute():
    AUDIT_DIR = (ROBO_ROOT / AUDIT_DIR).resolve()
_MONITOR_LOCK = threading.Lock()
_MONITOR_STARTED = False


def _is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if platform.system() == "Windows":
        try:
            import ctypes

            process_query_limited_information = 0x1000
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
            if not handle:
                return False
            kernel32.CloseHandle(handle)
            return True
        except (AttributeError, OSError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError, PermissionError, KeyboardInterrupt):
        return False


def audit_processes() -> dict[str, Any]:
    now = time.time()
    lookback = max(300.0, float(os.getenv("GENESIS_PROCESS_AUDIT_LOOKBACK_SECONDS", "86400")))
    groups: dict[tuple[str, str], dict[int, dict[str, Any]]] = defaultdict(dict)
    if STARTUP_EVENTS_PATH.exists():
        for line in STARTUP_EVENTS_PATH.read_text(encoding="utf-8").splitlines()[-1000:]:
            try:
                event = json.loads(line)
                timestamp = datetime.fromisoformat(str(event.get("timestamp_utc", "")).replace("Z", "+00:00")).timestamp()
                pid = int(event.get("pid", 0))
            except (ValueError, TypeError, OSError, json.JSONDecodeError):
                continue
            if now - timestamp > lookback or not _is_alive(pid):
                continue
            key = (str(event.get("role", "runtime")), str(event.get("active_symbol", "UNKNOWN")))
            groups[key][pid] = {"pid": pid, "hostname": event.get("hostname"), "version": event.get("version")}
    duplicate_groups = [{"role": role, "active_symbol": symbol, "processes": sorted(processes.values(), key=lambda item: item["pid"])} for (role, symbol), processes in groups.items() if len(processes) > 1]
    return {"status": "duplicate_detected" if duplicate_groups else "no_duplicates", "groups_checked": len(groups), "alive_processes": sum(len(processes) for processes in groups.values()), "duplicate_groups": duplicate_groups, "lookback_seconds": lookback}


def record_process_audit(snapshot: dict[str, Any], *, role: str, symbol: str, logger: logging.Logger) -> None:
    try:
        result = audit_processes()
        error_type = None
    except Exception as exc:
        result = {"status": "unavailable", "groups_checked": 0, "alive_processes": 0, "duplicate_groups": []}
        error_type = type(exc).__name__
        logger.warning("[GENESIS_PROCESS] audit_failed=%s", error_type)
    payload = {"event": "GENESIS_PROCESS_DUPLICATE_AUDIT", "timestamp_utc": datetime.now(timezone.utc).isoformat(), "pid": snapshot.get("pid", os.getpid()), "role": role, "active_symbol": symbol, "environment": snapshot.get("environment"), "version": snapshot.get("version"), **result}
    if error_type:
        payload["error_type"] = error_type
    try:
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        target = AUDIT_DIR / f"processes_{payload['pid']}.jsonl"
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception as exc:
        logger.warning("[GENESIS_PROCESS] audit_write_failed=%s", type(exc).__name__)


def start_process_audit_monitor(snapshot: dict[str, Any], *, role: str, symbol: str, logger: logging.Logger) -> None:
    global _MONITOR_STARTED
    if str(os.getenv("GENESIS_PROCESS_AUDIT_ENABLED", "true")).strip().lower() not in {"1", "true", "yes", "on", "sim"}:
        return
    with _MONITOR_LOCK:
        if _MONITOR_STARTED:
            return
        _MONITOR_STARTED = True
    interval = max(5.0, float(os.getenv("GENESIS_PROCESS_AUDIT_INTERVAL_SECONDS", "300")))

    def monitor() -> None:
        while True:
            record_process_audit(snapshot, role=role, symbol=symbol, logger=logger)
            time.sleep(interval)

    threading.Thread(target=monitor, name="genesis-process-audit", daemon=True).start()
    logger.info("[GENESIS_PROCESS] audit_enabled interval_seconds=%s output=%s", interval, AUDIT_DIR)
