"""Coleta periódica de CPU e memória do processo Genesis."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import logging
import os
import platform
import threading
import time
from pathlib import Path
from typing import Any


ROBO_ROOT = Path(__file__).resolve().parents[1]
METRICS_DIR = Path(os.getenv("GENESIS_RESOURCE_METRICS_DIR", str(ROBO_ROOT / "runtime" / "metrics"))).expanduser()
if not METRICS_DIR.is_absolute():
    METRICS_DIR = (ROBO_ROOT / METRICS_DIR).resolve()

_MONITOR_LOCK = threading.Lock()
_MONITOR_STARTED = False
_LAST_SAMPLE: tuple[float, float] | None = None


def _memory_rss_bytes() -> int | None:
    if platform.system() == "Windows":
        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        process = kernel32.GetCurrentProcess()
        ok = psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb)
        return int(counters.WorkingSetSize) if ok else None

    try:
        import resource

        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return value * (1024 if platform.system() != "Darwin" else 1)
    except (ImportError, AttributeError, OSError):
        return None


def _cpu_percent() -> float:
    global _LAST_SAMPLE
    now = time.monotonic()
    process_cpu = time.process_time()
    previous = _LAST_SAMPLE
    _LAST_SAMPLE = (now, process_cpu)
    if previous is None:
        return 0.0
    wall_delta = now - previous[0]
    if wall_delta <= 0:
        return 0.0
    return round(max(0.0, (process_cpu - previous[1]) / wall_delta * 100.0), 2)


def collect_resource_metrics(snapshot: dict[str, Any], *, role: str, symbol: str) -> dict[str, Any]:
    rss = _memory_rss_bytes()
    return {
        "event": "GENESIS_RESOURCE_SAMPLE",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pid": snapshot.get("pid", os.getpid()),
        "hostname": snapshot.get("hostname"),
        "role": role,
        "active_symbol": symbol,
        "active_symbols": snapshot.get("active_symbols", [symbol]),
        "environment": snapshot.get("environment"),
        "version": snapshot.get("version"),
        "cpu_percent_process": _cpu_percent(),
        "memory_rss_mb": round(rss / (1024 * 1024), 2) if rss is not None else None,
    }


def record_resource_metrics(snapshot: dict[str, Any], *, role: str, symbol: str) -> None:
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    event = collect_resource_metrics(snapshot, role=role, symbol=symbol)
    target = METRICS_DIR / f"resource_{event['pid']}.jsonl"
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def start_resource_monitor(snapshot: dict[str, Any], *, role: str, symbol: str, logger: logging.Logger) -> None:
    """Inicia uma única coleta por processo, imediatamente e a cada N segundos."""
    global _MONITOR_STARTED
    if str(os.getenv("GENESIS_RESOURCE_MONITOR_ENABLED", "true")).strip().lower() not in {"1", "true", "yes", "on", "sim"}:
        return
    with _MONITOR_LOCK:
        if _MONITOR_STARTED:
            return
        _MONITOR_STARTED = True

    interval = max(5.0, float(os.getenv("GENESIS_RESOURCE_SAMPLE_INTERVAL_SECONDS", "300")))

    def monitor() -> None:
        while True:
            try:
                record_resource_metrics(snapshot, role=role, symbol=symbol)
            except Exception as exc:
                logger.warning("[GENESIS_RESOURCE] sample_failed=%s", exc)
            time.sleep(interval)

    thread = threading.Thread(target=monitor, name="genesis-resource-monitor", daemon=True)
    thread.start()
    logger.info("[GENESIS_RESOURCE] enabled interval_seconds=%s output=%s", interval, METRICS_DIR)
