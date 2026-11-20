"""Append-only current-vs-shadow Breakout telemetry.

This module is intentionally observational.  It never selects a strategy,
calls Commander/risk/paper execution, or writes to an exchange.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from genesis_runtime import connect_operational_database
from utils.analytical_telemetry import normalize_db_value, normalize_json_value

_log = logging.getLogger(__name__)
SHADOW_POLICY = "SHADOW_NO_IMPULSE_RESET"
V2_VERSION = "V2"
V2_STAGES = ("PRICE_BREAKOUT", "TREND", "SCORE", "FINAL_ENTRY_ELIGIBLE")


def _forensic_db_values(forensic: dict[str, Any], row: dict[str, Any], source_id: Any, kind: str) -> tuple[Any, ...]:
    """Build psycopg-safe forensic parameters, including numpy/pandas scalars."""
    values = (
        source_id, kind, row["symbol"], row["timeframe"], row.get("candle_open_time"),
        row.get("candle_close_time"), forensic.get("trend_direction_expected"),
        forensic.get("trend_ema_short_period"), forensic.get("trend_ema_medium_period"),
        forensic.get("trend_ema_long_period"), forensic.get("trend_ema_short_value"),
        forensic.get("trend_ema_medium_value"), forensic.get("trend_ema_long_value"),
        forensic.get("trend_ema_short_slope"), forensic.get("trend_ema_medium_slope"),
        forensic.get("trend_ema_long_slope"), forensic.get("trend_condition_short_medium"),
        forensic.get("trend_condition_medium_long"), forensic.get("trend_condition_final"),
        forensic.get("trend_distance_short_medium_pct"), forensic.get("trend_distance_medium_long_pct"),
        forensic.get("trend_state"), forensic.get("trend_failure_reason"), row["record_provenance"],
    )
    return tuple(normalize_db_value(value) for value in values)


def _utc(value: Any) -> Any:
    value = normalize_db_value(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return value


def telemetry_id(symbol: str, timeframe: str, candle_open_time: Any, shadow_policy: str = SHADOW_POLICY) -> str:
    key = "|".join((str(symbol).upper(), str(timeframe), str(_utc(candle_open_time)), str(shadow_policy)))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _stage(snapshot: dict[str, Any], name: str) -> dict[str, Any]:
    return (snapshot.get("stage_results") or {}).get(name) or {}


def _stage_result(snapshot: dict[str, Any], *names: str) -> str:
    """Return a persisted stage state without turning missing data into PASS."""
    stages = snapshot.get("stage_results") or {}
    values = [str((stages.get(name) or {}).get("result") or "").upper() for name in names]
    for value in values:
        if value in {"PASS", "FAIL", "NOT_REACHED", "NOT_APPLICABLE"}:
            if value == "PASS":
                return value
    for value in values:
        if value in {"FAIL", "NOT_REACHED", "NOT_APPLICABLE"}:
            return value
    return "NOT_REACHED"


def _normalise_stages(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Map canonical Breakout stage names to the shadow funnel contract."""
    stages = {
        "PRICE_BREAKOUT": _stage_result(snapshot, "PRICE_BREAKOUT"),
        "TREND": _stage_result(snapshot, "TREND", "EMA_TREND_CONFIRMATION"),
        "SCORE": _stage_result(snapshot, "SCORE", "WEIGHTED_SCORE", "REGIME_MIN_SCORE"),
        "FINAL_ENTRY_ELIGIBLE": _stage_result(snapshot, "FINAL_ENTRY_ELIGIBLE"),
    }
    reached = [name for name, result in stages.items() if result not in {"NOT_REACHED", "NOT_APPLICABLE"}]
    passed = [name for name, result in stages.items() if result == "PASS"]
    blocker = snapshot.get("primary_blocker")
    blocker_map = {
        "IMPULSE_RESET_WAIT": "IMPULSE_RESET",
        "PRICE_NOT_BROKEN": "PRICE_BREAKOUT",
        "TREND_NOT_CONFIRMED": "TREND",
    }
    first_failed = next((name for name in V2_STAGES if stages[name] == "FAIL"), None)
    if not first_failed and blocker:
        first_failed = blocker_map.get(str(blocker), "OTHER")
    final_candidate = all(stages[name] == "PASS" for name in V2_STAGES)
    final_blocker = None if final_candidate else (blocker or first_failed)
    return {
        "stage_results": stages,
        "stage_reached": reached,
        "stage_passed": passed,
        "first_failed_stage": first_failed,
        "final_blocker": final_blocker,
        "final_candidate": final_candidate,
        "raw_candidate": bool(snapshot.get("raw_breakout")),
    }


def build_comparison_payload(*, current: dict[str, Any], shadow: dict[str, Any],
                             feature_vector: dict[str, Any] | None = None,
                             shadow_policy: str = SHADOW_POLICY) -> dict[str, Any]:
    """Build one natural-candle comparison without inventing missing values."""
    c_norm = _normalise_stages(current)
    s_norm = _normalise_stages(shadow)
    c_stage = _stage(current, "STRUCTURE_AVAILABLE")
    s_stage = _stage(shadow, "STRUCTURE_AVAILABLE")
    current_blocker = c_norm["final_blocker"]
    shadow_blocker = s_norm["final_blocker"]
    return {
        "symbol": str(current.get("symbol") or shadow.get("symbol") or "UNKNOWN").upper(),
        "timeframe": str(current.get("timeframe") or shadow.get("timeframe") or "5"),
        "candle_open_time": _utc(current.get("candle_open_time") or shadow.get("candle_open_time")),
        "candle_close_time": _utc(current.get("candle_close_time") or shadow.get("candle_close_time")),
        "regime": current.get("regime") if current.get("regime") is not None else shadow.get("regime"),
        "feature_vector": feature_vector or current.get("feature_vector") or shadow.get("feature_vector") or {},
        "current_blocker": current_blocker,
        "current_raw_breakout": current.get("raw_breakout"),
        "current_direction": current.get("direction_candidate"),
        "current_candidate": c_norm["final_candidate"],
        "shadow_blocker": shadow_blocker,
        "shadow_raw_breakout": shadow.get("raw_breakout"),
        "shadow_direction": shadow.get("direction_candidate"),
        "shadow_candidate": s_norm["final_candidate"],
        "telemetry_version": V2_VERSION,
        "current_stage_results": c_norm["stage_results"],
        "shadow_stage_results": s_norm["stage_results"],
        "current_stage_reached": c_norm["stage_reached"],
        "shadow_stage_reached": s_norm["stage_reached"],
        "current_stage_passed": c_norm["stage_passed"],
        "shadow_stage_passed": s_norm["stage_passed"],
        "current_first_failed_stage": c_norm["first_failed_stage"],
        "shadow_first_failed_stage": s_norm["first_failed_stage"],
        "current_final_blocker": c_norm["final_blocker"],
        "shadow_final_blocker": s_norm["final_blocker"],
        "shadow_raw_candidate": s_norm["raw_candidate"],
        "shadow_final_candidate": s_norm["final_candidate"],
        "impulse_reset_divergence": (c_stage.get("result") != s_stage.get("result")) or current_blocker != shadow_blocker,
        "shadow_trade_created": bool(shadow.get("shadow_trade_created", False)),
        "shadow_entry_time": _utc(shadow.get("shadow_entry_time")),
        "shadow_entry_price": shadow.get("shadow_entry_price"),
        "shadow_exit_time": _utc(shadow.get("shadow_exit_time")),
        "shadow_exit_price": shadow.get("shadow_exit_price"),
        "shadow_pnl": shadow.get("shadow_pnl"),
        "shadow_r_multiple": shadow.get("shadow_r_multiple"),
        "current_policy": "CURRENT",
        "shadow_policy": shadow_policy,
        "record_provenance": "SHADOW_PAPER",
        "current_evaluation_id": current.get("evaluation_id"),
        "shadow_evaluation_id": shadow.get("evaluation_id"),
        "current_trend_forensics": current.get("trend_forensics") or {},
        "shadow_trend_forensics": shadow.get("trend_forensics") or {},
    }


def persist_shadow_comparison(payload: dict[str, Any], *, connection: Any = None) -> bool:
    """Persist one row idempotently; failures never reach the trading path."""
    owned = connection is None
    try:
        row = {key: normalize_db_value(value) for key, value in payload.items()}
        row["id"] = telemetry_id(row["symbol"], row["timeframe"], row["candle_open_time"], row["shadow_policy"])
        row["feature_vector"] = json.dumps(normalize_json_value(row.get("feature_vector") or {}), separators=(",", ":"))
        for key in ("current_stage_results", "shadow_stage_results", "current_stage_reached",
                    "shadow_stage_reached", "current_stage_passed", "shadow_stage_passed"):
            row[key] = json.dumps(normalize_json_value(row.get(key) or ({} if key.endswith("results") else [])), separators=(",", ":"))
        conn = connection or connect_operational_database()
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO shadow_decision_telemetry
                (id,symbol,timeframe,candle_open_time,candle_close_time,regime,feature_vector,
                 current_blocker,current_raw_breakout,current_direction,current_candidate,
                 shadow_blocker,shadow_raw_breakout,shadow_direction,shadow_candidate,
                 impulse_reset_divergence,shadow_trade_created,shadow_entry_time,shadow_entry_price,
                 shadow_exit_time,shadow_exit_price,shadow_pnl,shadow_r_multiple,current_policy,
                 shadow_policy,record_provenance,telemetry_version,current_stage_results,
                 shadow_stage_results,current_stage_reached,shadow_stage_reached,
                 current_stage_passed,shadow_stage_passed,current_first_failed_stage,
                 shadow_first_failed_stage,current_final_blocker,shadow_final_blocker,
                 shadow_raw_candidate,shadow_final_candidate)
                VALUES (%(id)s,%(symbol)s,%(timeframe)s,%(candle_open_time)s,%(candle_close_time)s,
                        %(regime)s,%(feature_vector)s::jsonb,%(current_blocker)s,%(current_raw_breakout)s,
                        %(current_direction)s,%(current_candidate)s,%(shadow_blocker)s,%(shadow_raw_breakout)s,
                        %(shadow_direction)s,%(shadow_candidate)s,%(impulse_reset_divergence)s,
                        %(shadow_trade_created)s,%(shadow_entry_time)s,%(shadow_entry_price)s,
                        %(shadow_exit_time)s,%(shadow_exit_price)s,%(shadow_pnl)s,%(shadow_r_multiple)s,
                        %(current_policy)s,%(shadow_policy)s,%(record_provenance)s,%(telemetry_version)s,
                        %(current_stage_results)s::jsonb,%(shadow_stage_results)s::jsonb,
                        %(current_stage_reached)s::jsonb,%(shadow_stage_reached)s::jsonb,
                        %(current_stage_passed)s::jsonb,%(shadow_stage_passed)s::jsonb,
                        %(current_first_failed_stage)s,%(shadow_first_failed_stage)s,
                        %(current_final_blocker)s,%(shadow_final_blocker)s,
                        %(shadow_raw_candidate)s,%(shadow_final_candidate)s)
                ON CONFLICT (symbol,timeframe,candle_open_time,shadow_policy) DO NOTHING""",
                row,
            )
            for kind, source_key, forensic_key in (
                ("CURRENT", "current_evaluation_id", "current_trend_forensics"),
                (SHADOW_POLICY, "shadow_evaluation_id", "shadow_trend_forensics"),
            ):
                forensic = row.get(forensic_key) or {}
                source_id = row.get(source_key)
                if not forensic or not source_id:
                    continue
                cur.execute(
                    """INSERT INTO breakout_trend_forensics
                    (evaluation_id,evaluation_kind,symbol,timeframe,candle_open_time,candle_close_time,
                     expected_direction,trend_ema_short_period,trend_ema_medium_period,trend_ema_long_period,
                     trend_ema_short_value,trend_ema_medium_value,trend_ema_long_value,trend_ema_short_slope,
                     trend_ema_medium_slope,trend_ema_long_slope,trend_condition_short_medium,
                     trend_condition_medium_long,trend_condition_final,trend_distance_short_medium_pct,
                     trend_distance_medium_long_pct,trend_state,trend_failure_reason,record_provenance)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (evaluation_id,evaluation_kind) DO NOTHING""",
                    _forensic_db_values(forensic, row, source_id, kind),
                )
        if owned:
            conn.commit()
        return True
    except Exception as exc:
        if 'conn' in locals() and connection is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        diag = getattr(exc, "diag", None)
        sqlstate = getattr(diag, "sqlstate", None) or getattr(exc, "sqlstate", None) or "UNKNOWN"
        message = " ".join(str(exc).split())[:300]
        _log.error("[SHADOW_TELEMETRY][ERROR] writer=shadow_decision_telemetry exception=%s sqlstate=%s message=%s",
                   type(exc).__name__, sqlstate, message)
        return False
    finally:
        if owned and 'conn' in locals():
            conn.close()


def read_shadow_metrics(*, symbol: str | None = None, connection: Any = None) -> dict[str, Any]:
    """Read-only metrics; percentages are null when no rows exist."""
    owned = connection is None
    conn = connection or connect_operational_database(read_only=True)
    try:
        clauses, params = ["1=1"], []
        if symbol:
            clauses.append("symbol=%s"); params.append(str(symbol).upper())
        with conn.cursor() as cur:
            cur.execute(f"""SELECT count(*), count(*) FILTER (WHERE current_candidate),
                count(*) FILTER (WHERE shadow_candidate), count(*) FILTER (WHERE impulse_reset_divergence),
                count(*) FILTER (WHERE shadow_trade_created)
                FROM shadow_decision_telemetry WHERE telemetry_version='V2' AND {' AND '.join(clauses)}""", tuple(params))
            total, current_candidates, shadow_candidates, divergences, trades = cur.fetchone()
        return {"total": total, "current_candidates": current_candidates, "shadow_candidates": shadow_candidates,
                "divergences": divergences, "shadow_trades": trades,
                "divergence_pct": (divergences / total * 100.0) if total else None}
    finally:
        if owned:
            conn.close()


def read_impulse_reset_effectiveness(*, symbol: str | None = None, connection: Any = None) -> dict[str, Any]:
    """Read-only effectiveness summary for the two impulse-reset policies."""
    owned = connection is None
    conn = connection or connect_operational_database(read_only=True)
    try:
        clauses, params = ["1=1"], []
        if symbol:
            clauses.append("symbol=%s"); params.append(str(symbol).upper())
        with conn.cursor() as cur:
            cur.execute(f"""SELECT count(*),
                count(*) FILTER (WHERE current_candidate),
                count(*) FILTER (WHERE shadow_candidate),
                count(*) FILTER (WHERE impulse_reset_divergence),
                count(*) FILTER (WHERE shadow_trade_created),
                COALESCE(sum(shadow_pnl), 0)
                FROM shadow_decision_telemetry WHERE telemetry_version='V2' AND {' AND '.join(clauses)}""", tuple(params))
            total, current_candidates, shadow_candidates, divergences, trades, shadow_pnl = cur.fetchone()
        return {
            "total_candles": total,
            "current_candidates": current_candidates,
            "shadow_candidates": shadow_candidates,
            "candidate_delta": shadow_candidates - current_candidates,
            "divergent_candles": divergences,
            "shadow_trades": trades,
            "shadow_pnl": shadow_pnl,
            "effectiveness_pct": (divergences / total * 100.0) if total else None,
        }
    finally:
        if owned:
            conn.close()
