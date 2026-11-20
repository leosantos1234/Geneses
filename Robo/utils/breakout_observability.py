"""Production-safe, additive telemetry for the canonical Breakout funnel.

This module records analytical evidence only.  It never calls an exchange and
never decides whether a signal is valid.  Writes are best-effort and
asynchronous so telemetry failures cannot change the trading path.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from genesis_runtime import connect_operational_database
from utils.analytical_telemetry import normalize_db_value

STAGES = (
    "REGIME_ELIGIBLE", "STRUCTURE_AVAILABLE", "PRICE_BREAKOUT",
    "VOLUME_CONFIRMATION", "DIRECTION_DETERMINED", "EMA_TREND_CONFIRMATION",
    "WEIGHTED_SCORE", "REGIME_MIN_SCORE", "TRIGGER_CONFIRMATION",
    "RRR_VALIDATION", "FINAL_ENTRY_ELIGIBLE",
)
STAGE_TO_REPORT = {
    "REGIME_ELIGIBLE": "REGIME_PASS",
    "STRUCTURE_AVAILABLE": "STRUCTURE_PASS",
    "PRICE_BREAKOUT": "PRICE_BREAKOUT_PASS",
    "VOLUME_CONFIRMATION": "VOLUME_PASS",
    "EMA_TREND_CONFIRMATION": "TREND_PASS",
    "WEIGHTED_SCORE": "SCORE_PASS",
    "TRIGGER_CONFIRMATION": "TRIGGER_PASS",
    "RRR_VALIDATION": "RRR_PASS",
    "FINAL_ENTRY_ELIGIBLE": "FINAL_CANDIDATES",
}
PROVENANCE_VALUES = frozenset({
    "PRODUCTION_RUNTIME", "BACKTEST_RUNTIME", "TEST", "ACCEPTANCE", "SYNTHETIC", "UNKNOWN",
})

_jobs: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
_worker: threading.Thread | None = None


def _forensic_db_values(forensic: dict[str, Any], data: dict[str, Any]) -> tuple[Any, ...]:
    """Build psycopg-safe forensic parameters for current evaluations."""
    values = (
        forensic.get("trend_direction_expected"), forensic.get("trend_ema_short_period"),
        forensic.get("trend_ema_medium_period"), forensic.get("trend_ema_long_period"),
        forensic.get("trend_ema_short_value"), forensic.get("trend_ema_medium_value"),
        forensic.get("trend_ema_long_value"), forensic.get("trend_ema_short_slope"),
        forensic.get("trend_ema_medium_slope"), forensic.get("trend_ema_long_slope"),
        forensic.get("trend_condition_short_medium"), forensic.get("trend_condition_medium_long"),
        forensic.get("trend_condition_final"), forensic.get("trend_distance_short_medium_pct"),
        forensic.get("trend_distance_medium_long_pct"), forensic.get("trend_state"),
        forensic.get("trend_failure_reason"), data["record_provenance"],
    )
    return tuple(normalize_db_value(value) for value in values)
_lock = threading.Lock()


def _utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _coerce_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _utc(value)
    try:
        if isinstance(value, (int, float)) or str(value).strip().replace(".", "", 1).isdigit():
            number = float(value)
            if number > 10_000_000_000:
                number /= 1000.0
            return datetime.fromtimestamp(number, tz=timezone.utc)
    except Exception:
        return None
    try:
        text = str(value).strip().replace("Z", "+00:00")
        return _utc(datetime.fromisoformat(text))
    except Exception:
        return None


def derive_candle_identity(values: Any, timeframe: str, *, symbol: str | None = None,
                           candle_closed: bool | None = None) -> dict[str, Any]:
    """Derive identity metadata without changing any strategy calculation."""
    row = values.iloc[-1] if hasattr(values, "iloc") and len(values) else values
    getter = row.get if hasattr(row, "get") else lambda key, default=None: default
    raw_open = next((getter(name) for name in ("candle_open_time", "open_time", "timestamp", "time")
                     if getter(name) is not None), None)
    candle_open_time = _coerce_timestamp(raw_open)
    minutes = 0
    try:
        minutes = int(str(timeframe).lower().replace("m", ""))
    except Exception:
        minutes = 0
    candle_close_time = candle_open_time + __import__("datetime").timedelta(minutes=minutes) if candle_open_time and minutes else None
    analysis_key = None
    if candle_open_time and symbol:
        analysis_key = f"{str(symbol).upper()}|{str(timeframe)}|{candle_open_time.isoformat()}"
    return {
        "candle_open_time": candle_open_time,
        "candle_close_time": candle_close_time,
        "candle_closed": candle_closed,
        "analysis_key": analysis_key,
    }


def breakout_regime_eligible(regime: Any) -> bool:
    """Telemetry definition: only effective Regime 1/2 is Breakout-eligible."""
    try:
        return int(regime) in (1, 2)
    except (TypeError, ValueError):
        return False


def deterministic_breakout_evaluation_id(analysis_key: str) -> str:
    """Stable primary key for one natural candle/context evaluation."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, str(analysis_key)))


def _write(kind: str, data: dict[str, Any]) -> None:
    attempts = 3 if kind == "link" else 1
    for attempt in range(attempts):
        try:
            with connect_operational_database() as conn:
                with conn.cursor() as cur:
                    if kind == "insert":
                        cur.execute(
                            """INSERT INTO breakout_evaluations
                            (breakout_evaluation_id,timestamp_utc,symbol,timeframe,regime,
                             direction_candidate,stage_results,primary_blocker,failed_stages,
                             raw_breakout,final_state,commander_decision_id,risk_status,
                             risk_reason,risk_evaluated_at,paper_order_id,paper_fill_id,
                             paper_effect_id,record_provenance,candle_open_time,candle_close_time,
                             candle_closed,analysis_key,evaluation_context,run_id,commander_invoked,
                             feature_vector,breakout_timeframe,breakout_candle_open_time,
                             breakout_candle_close_time,breakout_candle_closed,
                             canonical_regime_timeframe,canonical_regime_candle_open_time,
                             canonical_regime_candle_close_time)
                            VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (breakout_evaluation_id) DO NOTHING""",
                            (data["breakout_evaluation_id"], data["timestamp_utc"], data["symbol"],
                             data["timeframe"], data["regime"], data["direction_candidate"],
                             _json(data["stage_results"]), data["primary_blocker"],
                             _json(data["failed_stages"]), data["raw_breakout"], data["final_state"],
                             data.get("commander_decision_id"), data.get("risk_status"),
                             data.get("risk_reason"), data.get("risk_evaluated_at"),
                             data.get("paper_order_id"), data.get("paper_fill_id"),
                             data.get("paper_effect_id"), data["record_provenance"],
                             data.get("candle_open_time"), data.get("candle_close_time"),
                             data.get("candle_closed"), data.get("analysis_key"),
                             data.get("evaluation_context"), data.get("run_id"),
                             data.get("commander_invoked", False), _json(data.get("feature_vector") or {}),
                             data.get("breakout_timeframe"), data.get("breakout_candle_open_time"),
                             data.get("breakout_candle_close_time"), data.get("breakout_candle_closed"),
                            data.get("canonical_regime_timeframe"),
                            data.get("canonical_regime_candle_open_time"),
                            data.get("canonical_regime_candle_close_time")),
                        )
                        forensic = data.get("trend_forensics") or {}
                        if forensic:
                            cur.execute(
                                """INSERT INTO breakout_trend_forensics
                                (evaluation_id,evaluation_kind,symbol,timeframe,candle_open_time,
                                 candle_close_time,expected_direction,trend_ema_short_period,
                                 trend_ema_medium_period,trend_ema_long_period,trend_ema_short_value,
                                 trend_ema_medium_value,trend_ema_long_value,trend_ema_short_slope,
                                 trend_ema_medium_slope,trend_ema_long_slope,trend_condition_short_medium,
                                 trend_condition_medium_long,trend_condition_final,trend_distance_short_medium_pct,
                                 trend_distance_medium_long_pct,trend_state,trend_failure_reason,record_provenance)
                                VALUES (%s,'CURRENT',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                ON CONFLICT (evaluation_id,evaluation_kind) DO UPDATE SET
                                  trend_state=EXCLUDED.trend_state,
                                  trend_failure_reason=EXCLUDED.trend_failure_reason""",
                                (data["breakout_evaluation_id"], data["symbol"], data["timeframe"],
                                 data.get("candle_open_time"), data.get("candle_close_time"),
                                 *_forensic_db_values(forensic, data)),
                            )
                    elif kind == "link":
                        cur.execute(
                            """UPDATE breakout_evaluations SET commander_decision_id=COALESCE(%s,commander_decision_id),
                               commander_invoked=CASE WHEN %s IS NOT NULL THEN TRUE ELSE commander_invoked END,
                               risk_status=COALESCE(%s,risk_status), risk_reason=COALESCE(%s,risk_reason),
                               risk_evaluated_at=COALESCE(%s,risk_evaluated_at),
                               paper_order_id=COALESCE(%s,paper_order_id), paper_fill_id=COALESCE(%s,paper_fill_id),
                               paper_effect_id=COALESCE(%s,paper_effect_id), final_state=COALESCE(%s,final_state)
                             WHERE breakout_evaluation_id=%s""",
                            (data.get("commander_decision_id"), data.get("commander_decision_id"), data.get("risk_status"),
                             data.get("risk_reason"), data.get("risk_evaluated_at"),
                             data.get("paper_order_id"), data.get("paper_fill_id"),
                             data.get("paper_effect_id"), data.get("final_state"),
                             data["breakout_evaluation_id"]),
                        )
                        if cur.rowcount == 0 and attempt < attempts - 1:
                            time.sleep(0.05)
                            continue
                conn.commit()
            return
        except Exception as exc:
            if attempt < attempts - 1:
                time.sleep(0.05)
                continue
            # Observability is intentionally non-blocking and must never alter
            # a Breakout result when PostgreSQL is unavailable or not migrated.
            diag = getattr(exc, "diag", None)
            sqlstate = getattr(diag, "sqlstate", None) or getattr(exc, "sqlstate", None) or "UNKNOWN"
            message = " ".join(str(exc).split())[:300]
            _logger.error(
                "[BREAKOUT_OBSERVABILITY][ERROR] writer=breakout_evaluations exception=%s sqlstate=%s message=%s cycle_id=%s",
                type(exc).__name__, sqlstate, message, data.get("cycle_id", "UNKNOWN"),
            )
            return


def _worker_main() -> None:
    while True:
        kind, data = _jobs.get()
        try:
            _write(kind, data)
        finally:
            _jobs.task_done()


def _enqueue(kind: str, data: dict[str, Any]) -> None:
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_worker_main, name="genesis-breakout-observability", daemon=True)
            _worker.start()
    _jobs.put((kind, data))


def flush_breakout_observability(timeout: float = 5.0) -> bool:
    deadline = __import__("time").monotonic() + max(float(timeout), 0.0)
    while _jobs.unfinished_tasks and __import__("time").monotonic() < deadline:
        __import__("time").sleep(0.01)
    return _jobs.unfinished_tasks == 0


class BreakoutEvaluation:
    """In-memory stage ledger for one real Breakout evaluation."""

    def __init__(self, *, symbol: str, timeframe: str, regime: Any, direction_candidate: str | None = None,
                 timestamp_utc: datetime | None = None, record_provenance: str | None = None,
                 candle_open_time: datetime | None = None, candle_close_time: datetime | None = None,
                 candle_closed: bool | None = None, analysis_key: str | None = None,
                 evaluation_context: str | None = None, run_id: str | None = None,
                 breakout_evaluation_id: str | None = None, persist: bool = True,
                 feature_vector: dict[str, Any] | None = None,
                 breakout_timeframe: str | None = None,
                 breakout_candle_open_time: datetime | None = None,
                 breakout_candle_close_time: datetime | None = None,
                 breakout_candle_closed: bool | None = None,
                 canonical_regime_timeframe: str | None = None,
                 canonical_regime_candle_open_time: datetime | None = None,
                 canonical_regime_candle_close_time: datetime | None = None,
                 trend_forensics: dict[str, Any] | None = None):
        record_provenance = record_provenance if record_provenance in PROVENANCE_VALUES else "UNKNOWN"
        evaluation_context = evaluation_context if evaluation_context in PROVENANCE_VALUES else record_provenance
        self.breakout_evaluation_id = str(breakout_evaluation_id or uuid.uuid4())
        self.persist = bool(persist)
        self.timestamp_utc = _utc(timestamp_utc)
        self.symbol = str(symbol or "UNKNOWN").upper()
        self.timeframe = str(timeframe or "5")
        self.regime = None if regime is None else str(regime)
        self.direction_candidate = direction_candidate
        self.record_provenance = record_provenance
        self.evaluation_context = evaluation_context
        self.run_id = run_id
        self.candle_open_time = _coerce_timestamp(candle_open_time)
        self.candle_close_time = _coerce_timestamp(candle_close_time)
        self.candle_closed = candle_closed
        self.breakout_timeframe = str(breakout_timeframe) if breakout_timeframe is not None else None
        self.breakout_candle_open_time = _coerce_timestamp(breakout_candle_open_time)
        self.breakout_candle_close_time = _coerce_timestamp(breakout_candle_close_time)
        self.breakout_candle_closed = breakout_candle_closed
        self.canonical_regime_timeframe = str(canonical_regime_timeframe) if canonical_regime_timeframe is not None else None
        self.canonical_regime_candle_open_time = _coerce_timestamp(canonical_regime_candle_open_time)
        self.canonical_regime_candle_close_time = _coerce_timestamp(canonical_regime_candle_close_time)
        self.analysis_key = analysis_key or (
            f"{self.symbol}|{self.timeframe}|{self.candle_open_time.isoformat()}"
            if self.candle_open_time else None
        )
        self.commander_invoked = False
        self.feature_vector = dict(feature_vector or {})
        self.trend_forensics = dict(trend_forensics or {})
        self.stage_results: dict[str, dict[str, Any]] = {}
        self.failed_stages: list[str] = []
        self.primary_blocker: str | None = None
        self.raw_breakout = False
        self.final_state = "EVALUATED"

    def stage(self, name: str, result: str, *, observed_value: Any = None, threshold: Any = None,
              operator: str | None = None, reason_code: str | None = None) -> None:
        if name not in STAGES:
            raise ValueError(f"unknown Breakout stage: {name}")
        if result not in {"PASS", "FAIL", "NOT_APPLICABLE"}:
            raise ValueError("stage result must be PASS, FAIL or NOT_APPLICABLE")
        self.stage_results[name] = {
            "result": result,
            "observed_value": observed_value,
            "threshold": threshold,
            "operator": operator,
            "reason_code": reason_code,
        }
        if result == "FAIL":
            self.failed_stages.append(name)
            if self.primary_blocker is None:
                self.primary_blocker = reason_code or name

    def finish(self, final_state: str, *, commander_decision_id: str | None = None,
               risk_status: str | None = None, risk_reason: str | None = None,
               risk_evaluated_at: datetime | None = None, paper_order_id: str | None = None,
               paper_fill_id: str | None = None, paper_effect_id: str | None = None) -> str:
        self.final_state = str(final_state)
        if self.record_provenance == "PRODUCTION_RUNTIME" and self.direction_candidate is None:
            self.direction_candidate = "UNKNOWN"
        if commander_decision_id:
            self.commander_invoked = True
        for stage_name in STAGES:
            self.stage_results.setdefault(stage_name, {
                "result": "NOT_APPLICABLE",
                "observed_value": None,
                "threshold": None,
                "operator": None,
                "reason_code": "NOT_REACHED",
            })
        trace_callback = getattr(self, "_trace_callback", None)
        if callable(trace_callback):
            try:
                trace_callback(self)
            except Exception:
                # Telemetry must never affect Breakout evaluation.
                pass
        if not self.persist:
            return self.breakout_evaluation_id
        _enqueue("insert", {
            "breakout_evaluation_id": self.breakout_evaluation_id,
            "timestamp_utc": self.timestamp_utc,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "regime": self.regime,
            "direction_candidate": self.direction_candidate,
            "stage_results": self.stage_results,
            "primary_blocker": self.primary_blocker,
            "failed_stages": self.failed_stages,
            "raw_breakout": bool(self.raw_breakout),
            "final_state": self.final_state,
            "commander_decision_id": commander_decision_id,
            "risk_status": risk_status,
            "risk_reason": risk_reason,
            "risk_evaluated_at": risk_evaluated_at,
            "paper_order_id": paper_order_id,
            "paper_fill_id": paper_fill_id,
            "paper_effect_id": paper_effect_id,
            "record_provenance": self.record_provenance,
            "candle_open_time": self.candle_open_time,
            "candle_close_time": self.candle_close_time,
            "candle_closed": self.candle_closed,
            "analysis_key": self.analysis_key,
            "evaluation_context": self.evaluation_context,
            "run_id": self.run_id,
            "commander_invoked": self.commander_invoked,
            "feature_vector": self.feature_vector,
            "breakout_timeframe": self.breakout_timeframe,
            "breakout_candle_open_time": self.breakout_candle_open_time,
            "breakout_candle_close_time": self.breakout_candle_close_time,
            "breakout_candle_closed": self.breakout_candle_closed,
            "canonical_regime_timeframe": self.canonical_regime_timeframe,
            "canonical_regime_candle_open_time": self.canonical_regime_candle_open_time,
            "canonical_regime_candle_close_time": self.canonical_regime_candle_close_time,
            "trend_forensics": self.trend_forensics,
        })
        return self.breakout_evaluation_id


def link_breakout_evaluation(evaluation_id: str | None, **fields: Any) -> None:
    if evaluation_id:
        fields["breakout_evaluation_id"] = str(evaluation_id)
        fields["commander_invoked"] = bool(fields.get("commander_decision_id"))
        _enqueue("link", fields)


def report_breakout_evaluations(*, start_time: datetime | None = None, end_time: datetime | None = None,
                                symbol: str | None = None, direction: str | None = None,
                                connection: Any = None) -> list[dict[str, Any]]:
    """Read-only aggregate report; percentages are omitted when denominator is zero."""
    owned = connection is None
    connection = connection or connect_operational_database(read_only=True)
    try:
        clauses, params = ["1=1"], []
        if start_time is not None:
            clauses.append("timestamp_utc >= %s"); params.append(_utc(start_time))
        if end_time is not None:
            clauses.append("timestamp_utc < %s"); params.append(_utc(end_time))
        if symbol:
            clauses.append("symbol = %s"); params.append(str(symbol).upper())
        if direction:
            clauses.append("direction_candidate = %s"); params.append(str(direction).upper())
        sql = f"SELECT symbol,direction_candidate,stage_results,final_state,primary_blocker,raw_breakout,risk_status,paper_order_id,paper_fill_id,paper_effect_id,record_provenance,evaluation_context,run_id,candle_open_time,candle_close_time,candle_closed,analysis_key,commander_invoked,commander_decision_id,feature_vector,breakout_timeframe,breakout_candle_open_time,breakout_candle_close_time,breakout_candle_closed,canonical_regime_timeframe,canonical_regime_candle_open_time,canonical_regime_candle_close_time FROM breakout_evaluations WHERE {' AND '.join(clauses)}"
        with connection.cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        return [{"symbol": r[0], "direction": r[1], "stage_results": r[2] or {}, "final_state": r[3], "primary_blocker": r[4], "raw_breakout": r[5], "risk_status": r[6], "paper_order_id": r[7], "paper_fill_id": r[8], "paper_effect_id": r[9], "record_provenance": r[10], "evaluation_context": r[11], "run_id": r[12], "candle_open_time": r[13], "candle_close_time": r[14], "candle_closed": r[15], "analysis_key": r[16], "commander_invoked": r[17], "commander_decision_id": r[18], "feature_vector": r[19] or {}, "breakout_timeframe": r[20], "breakout_candle_open_time": r[21], "breakout_candle_close_time": r[22], "breakout_candle_closed": r[23], "canonical_regime_timeframe": r[24], "canonical_regime_candle_open_time": r[25], "canonical_regime_candle_close_time": r[26]} for r in rows]
    finally:
        if owned:
            connection.close()


def aggregate_breakout_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate already-read records without inventing denominators."""
    stage_names = tuple(STAGE_TO_REPORT.items())
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        grouped[(str(row.get("symbol") or "UNKNOWN"), str(row.get("direction") or "UNKNOWN").upper())].append(row)
    result: dict[str, Any] = {}
    for (symbol, direction), items in grouped.items():
        counts = {"TOTAL_EVALUATIONS": len(items)}
        counts["RAW_BREAKOUTS"] = sum(bool(item.get("raw_breakout")) for item in items)
        counts["CANDIDATES_CREATED"] = sum(str(item.get("final_state")) == "CANDIDATE_CREATED" for item in items)
        counts["RISK_APPROVED"] = sum(str(item.get("risk_status", "")).upper() in {"ALLOW", "APPROVED", "PASS"} for item in items)
        counts["PAPER_ORDERS"] = sum(bool(item.get("paper_order_id")) for item in items)
        counts["PAPER_FILLS"] = sum(bool(item.get("paper_fill_id")) for item in items)
        blockers = Counter(str(item["primary_blocker"]) for item in items if item.get("primary_blocker"))
        previous_count = len(items)
        for stage_name, report_name in stage_names:
            values = [(item.get("stage_results") or {}).get(stage_name, {}) for item in items]
            passes = sum(value.get("result") == "PASS" for value in values)
            fails = sum(value.get("result") == "FAIL" for value in values)
            counts[report_name] = {"count": passes, "failure_count": fails,
                                   "conversion_percentage": (passes / previous_count * 100.0) if previous_count else None,
                                   "failure_percentage": (fails / len(items) * 100.0) if items else None}
            if passes:
                previous_count = passes
        counts["TOP_BLOCKERS"] = [
            {"reason": reason, "count": count, "percentage": count / len(items) * 100.0 if items else None}
            for reason, count in blockers.most_common(3)
        ]
        result[f"{symbol}:{direction}"] = counts
    return result


def read_breakout_report(*, start_time: datetime | None = None, end_time: datetime | None = None,
                         symbol: str | None = None, direction: str | None = None,
                         connection: Any = None) -> dict[str, Any]:
    return aggregate_breakout_records(report_breakout_evaluations(
        start_time=start_time, end_time=end_time, symbol=symbol, direction=direction, connection=connection,
    ))


__all__ = ["BreakoutEvaluation", "STAGES", "STAGE_TO_REPORT", "aggregate_breakout_records", "breakout_regime_eligible", "derive_candle_identity", "deterministic_breakout_evaluation_id", "flush_breakout_observability", "link_breakout_evaluation", "read_breakout_report", "report_breakout_evaluations"]
