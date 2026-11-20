from __future__ import annotations

import csv
import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()

_DEFAULT_PATH = "telemetria_mr.csv"

_FIELDS = [
    "timestamp",
    "symbol",
    "strategy",
    "event",
    "timeframe",
    "pivot_low",
    "micro_res",
    "stop",
    "limit",
    "atr",
    "order_id",
    "fill_price",
    "qty",
    "sl",
    "motivo",
    "bars_em_reversao",
    "extra_json",
]


def _utc_iso(ts: Any | None) -> str:
    if isinstance(ts, datetime):
        dt = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


def _enabled() -> bool:
    enabled = os.getenv("MR_TELEMETRY_ENABLED", "1").strip().lower()
    if enabled in {"0", "false", "no", "off"}:
        return False
    backtest_mode = os.getenv("BACKTEST_MODE", "").strip().lower()
    if backtest_mode in {"1", "true", "yes", "on"}:
        return False
    return True


def log_mr_event(**kwargs: Any) -> None:
    """Registra telemetria da MR em CSV (best-effort, nunca quebra o robô)."""
    if not _enabled():
        return

    path = os.getenv("MR_TELEMETRY_PATH", _DEFAULT_PATH).strip() or _DEFAULT_PATH

    try:
        row: dict[str, Any] = {k: None for k in _FIELDS}
        row["timestamp"] = _utc_iso(kwargs.get("timestamp"))
        row["strategy"] = kwargs.get("strategy") or "MEAN_REVERSION"

        # Campos principais
        for k in _FIELDS:
            if k in {"timestamp", "strategy", "extra_json"}:
                continue
            if k in kwargs:
                row[k] = kwargs.get(k)

        # Extra
        extra = {k: v for k, v in kwargs.items() if k not in set(_FIELDS)}
        row["extra_json"] = json.dumps(extra, ensure_ascii=False, default=str) if extra else ""

        # Serializa para texto/float simples
        for k, v in list(row.items()):
            if v is None:
                continue
            if isinstance(v, (int, float, str)):
                continue
            row[k] = str(v)

        with _LOCK:
            write_header = not os.path.exists(path)
            with open(path, "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=_FIELDS)
                if write_header:
                    w.writeheader()
                w.writerow(row)

    except Exception as e:
        logger.warning("[MR-TELEM] Falha ao escrever telemetria: %s", e)

