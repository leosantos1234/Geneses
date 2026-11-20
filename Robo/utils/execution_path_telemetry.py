"""Non-invasive telemetry for the production analysis path.

This module deliberately writes only structured log records.  It must never
raise into the trading loop and must not make a strategy or execution choice.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any


_LOGGER = logging.getLogger(__name__)


def new_cycle(symbol: str, cycle_time: Any = None) -> dict[str, Any]:
    """Create an in-memory cycle record with safe defaults."""
    return {
        "cycle_id": f"cycle:{str(symbol).upper()}:{uuid.uuid4().hex}",
        "cycle_time": cycle_time or datetime.now(timezone.utc).isoformat(),
        "symbol": str(symbol).upper(),
        "new_5m_candle_detected": True,
        "df_1m_loaded": False,
        "df_5m_loaded": False,
        "df_15m_loaded": False,
        "closed_1m_persisted": False,
        "backtest_started": False,
        "backtest_completed": False,
        "backtest_valid": False,
        "selected_strategy": None,
        "selected_strategy_profit": None,
        "setup_probe_ok": False,
        "setup_probe_side": None,
        "setup_probe_reason": None,
        "breakout_execution_requested": False,
        "breakout_called": False,
        "breakout_evaluation_created": False,
        "analytical_breakout_created": False,
        "shadow_created": False,
        "macro_state": None,
        "macro_reason": None,
        "execution_authorized": False,
        "first_exit_stage": None,
        "first_exit_reason": None,
        "worker_identity": f"pid:{os.getpid()}",
        "runtime_context": "PRODUCTION_RUNTIME",
    }


def mark_exit(record: dict[str, Any] | None, stage: str, reason: str) -> None:
    """Set the first pre-breakout exit exactly once."""
    if not record:
        return
    if record.get("first_exit_stage") is None:
        record["first_exit_stage"] = stage
        record["first_exit_reason"] = reason
        _LOGGER.info(
            "[ENTRY-BLOCK] ativo=%s estrategia=%s etapa=%s motivo=%s",
            record.get("symbol") or "UNKNOWN",
            record.get("selected_strategy") or "NONE",
            stage,
            reason,
        )


def emit_cycle(record: dict[str, Any] | None, event: str = "cycle") -> None:
    """Emit one sanitized JSON log record; telemetry can never block trading."""
    if not record:
        return
    try:
        payload = {"event": event, **record}
        _LOGGER.info("[EXECUTION_PATH] %s", json.dumps(payload, default=str, sort_keys=True))
    except Exception:
        _LOGGER.debug("[EXECUTION_PATH] telemetry emission failed", exc_info=True)
