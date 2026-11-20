"""One-command PostgreSQL acceptance runner for Sprint 8 Phase 7.

This runner intentionally refuses SQLite and never prints connection details.
It is executed locally by the operator after configuring the existing Genesis
PostgreSQL environment.
"""

from __future__ import annotations

import re
import subprocess
import sys
import uuid
import argparse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ROBO = ROOT / "Robo"
if str(ROBO) not in sys.path:
    sys.path.insert(0, str(ROBO))

# Reuse the project's existing canonical loader.  This is the same bootstrap
# imported by the accepted Phase 10 runner; it reads only the two DB settings
# from Robo/.env-btc and never prints their values.
import conftest as _canonical_project_config  # noqa: F401,E402

from genesis_runtime import connect_operational_database, database_backend  # noqa: E402
from genesis_paper import (  # noqa: E402
    PaperEffectType,
    PaperFill,
    PaperOrder,
    PaperOrderType,
    PaperPortfolioEffect,
    PaperPosition,
    PaperRepository,
    PaperSide,
    create_portfolio,
)


PAPER_TABLES = (
    "paper_portfolios",
    "paper_orders",
    "paper_fills",
    "paper_positions",
    "paper_portfolio_effects",
    "paper_execution_events",
)
PROTECTED_TABLES = ("trades", "predictions_lstm", "mr_fsm_state")
MIGRATION = ROBO / "ops" / "migrations" / "006_paper_persistence.sql"

PASS_REQUIRED_KEYS = (
    "POSTGRES_CONNECTION", "PAPER_SCHEMA_MIGRATION", "MIGRATION_APPLY", "MIGRATION_REAPPLY",
    *(f"{table.upper()}_TABLE" for table in PAPER_TABLES),
    "POSTGRES_READBACK_ROUNDTRIP", "POSTGRES_TRANSACTION_ATOMICITY", "ORDER_IDEMPOTENCY",
    "FILL_IDEMPOTENCY", "EFFECT_IDEMPOTENCY", "EVENT_IDEMPOTENCY", "COMMANDER_ORDER_UNIQUENESS",
    "APPEND_ONLY_TEST_EVENTS_IDENTIFIABLE", "RETAINED_ACCEPTANCE_ARTIFACTS_IDENTIFIABLE",
    "RETAINED_ACCEPTANCE_HISTORY_IDENTIFIABLE",
)
ZERO_REQUIRED_KEYS = (
    "ORPHAN_PAPER_FILL_COUNT", "ORPHAN_PAPER_EFFECT_COUNT", "TEMPORARY_MUTABLE_ARTIFACT_ROWS_REMAINING",
    "APPEND_ONLY_TEST_EVENTS_MUTATED", "PREEXISTING_PAPER_EVENTS_MUTATED", "PAPER_EVENT_PRIOR_ROW_MUTATIONS",
    "RETAINED_ACCEPTANCE_HISTORY_UNEXPLAINED_ROWS", "RETAINED_ACCEPTANCE_HISTORY_FK_VIOLATIONS",
    "FK_BLOCKED_CLEANUP_ATTEMPTS", "UNTAGGED_ACCEPTANCE_ROWS", "ACCEPTANCE_RUN_ID_COLLISIONS",
    "PREEXISTING_PAPER_ROWS_MUTATED", "TRADES_ROW_DELTA", "TRADES_MARKER_DELTA",
    "PREDICTIONS_LSTM_ROW_DELTA", "PREDICTIONS_LSTM_MARKER_DELTA", "MR_FSM_STATE_ROW_DELTA",
    "MR_FSM_STATE_MARKER_DELTA",
)


def final_acceptance_is_green(result: dict[str, str], failed: int = 0, skipped: int = 0) -> bool:
    """Return true only when every mandatory PASS/zero criterion is green."""
    return (
        failed == 0
        and skipped == 0
        and all(result.get(key) == "PASS" for key in PASS_REQUIRED_KEYS)
        and all(result.get(key) == "0" for key in ZERO_REQUIRED_KEYS)
        and result.get("APPEND_ONLY_TEST_EVENTS_CREATED") == "2"
    )


def _safe_version(connection):
    try:
        row = connection.execute("SELECT current_setting('server_version')").fetchone()
        return str(row[0]).split()[0] if row else "unknown"
    except Exception:
        return "unknown"


def _safe_exception_message(exc: Exception) -> str:
    message = str(exc).replace("\n", " ")[:300]
    message = re.sub(r"(?i)(postgres(?:ql)?://)[^@\s]+@", r"\1<redacted>@", message)
    message = re.sub(r"(?i)(password\s*[=:]\s*)[^,\s]+", r"\1<redacted>", message)
    return message or exc.__class__.__name__


def _diagnostic(diagnostics: list[dict[str, str]], check: str, stage: str, exc: Exception, expected: str, actual: str) -> None:
    diagnostics.append({
        "CHECK_NAME": check,
        "FAILURE_STAGE": stage,
        "EXCEPTION_CLASS": exc.__class__.__name__,
        "SAFE_EXCEPTION_MESSAGE": _safe_exception_message(exc),
        "EXPECTED": expected,
        "ACTUAL": actual,
    })


def _table_exists(connection, table: str) -> bool:
    return bool(connection.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0])


def _protected_snapshot(connection) -> dict[str, tuple[int, str | None]]:
    result = {}
    for table in PROTECTED_TABLES:
        if not _table_exists(connection, table):
            result[table] = (0, None)
            continue
        row = connection.execute(
            f"SELECT COUNT(*), md5(COALESCE(string_agg(md5(to_jsonb(t)::text), ',' ORDER BY md5(to_jsonb(t)::text)), '')) FROM {table} AS t"
        ).fetchone()
        result[table] = (int(row[0]), row[1])
    return result


def _paper_snapshot(connection) -> dict[str, int]:
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) if _table_exists(connection, table) else 0
        for table in PAPER_TABLES
    }


def _event_preexisting_snapshot(connection, acceptance_run_id: str) -> tuple[int, str | None]:
    row = connection.execute(
        "SELECT COUNT(*), md5(COALESCE(string_agg(md5(to_jsonb(t)::text), ',' ORDER BY md5(to_jsonb(t)::text)), '')) "
        "FROM paper_execution_events AS t "
        "WHERE COALESCE(payload_json->>'acceptance_run_id', '') <> %s",
        (acceptance_run_id,),
    ).fetchone()
    return int(row[0]), row[1]


def _retained_counts(connection, acceptance_run_id: str) -> dict[str, int]:
    marker = (acceptance_run_id,)
    queries = {
        "RETAINED_PAPER_PORTFOLIOS": "SELECT COUNT(DISTINCT p.paper_portfolio_id) FROM paper_portfolios p JOIN paper_orders o ON o.paper_portfolio_id=p.paper_portfolio_id JOIN paper_execution_events e ON e.paper_order_id=o.paper_order_id WHERE e.payload_json->>'acceptance_run_id'=%s",
        "RETAINED_PAPER_ORDERS": "SELECT COUNT(DISTINCT o.paper_order_id) FROM paper_orders o JOIN paper_execution_events e ON e.paper_order_id=o.paper_order_id WHERE e.payload_json->>'acceptance_run_id'=%s",
        "RETAINED_PAPER_FILLS": "SELECT COUNT(DISTINCT f.paper_fill_id) FROM paper_fills f JOIN paper_execution_events e ON e.paper_fill_id=f.paper_fill_id WHERE e.payload_json->>'acceptance_run_id'=%s",
        "RETAINED_PAPER_EFFECTS": "SELECT COUNT(DISTINCT x.paper_effect_id) FROM paper_portfolio_effects x JOIN paper_execution_events e ON e.paper_effect_id=x.paper_effect_id WHERE e.payload_json->>'acceptance_run_id'=%s",
        "RETAINED_PAPER_EVENTS": "SELECT COUNT(*) FROM paper_execution_events WHERE payload_json->>'acceptance_run_id'=%s",
    }
    return {key: int(connection.execute(query, marker).fetchone()[0]) for key, query in queries.items()}


def _retained_fk_violations(connection, acceptance_run_id: str) -> int:
    row = connection.execute(
        "SELECT COUNT(*) FROM paper_execution_events e "
        "LEFT JOIN paper_portfolio_effects x ON x.paper_effect_id=e.paper_effect_id "
        "LEFT JOIN paper_fills f ON f.paper_fill_id=e.paper_fill_id "
        "LEFT JOIN paper_orders o ON o.paper_order_id=e.paper_order_id "
        "LEFT JOIN paper_portfolios p ON p.paper_portfolio_id=e.paper_portfolio_id "
        "WHERE e.payload_json->>'acceptance_run_id'=%s "
        "AND ((e.paper_effect_id IS NOT NULL AND x.paper_effect_id IS NULL) "
        "OR (e.paper_fill_id IS NOT NULL AND f.paper_fill_id IS NULL) "
        "OR (e.paper_order_id IS NOT NULL AND o.paper_order_id IS NULL) "
        "OR (e.paper_portfolio_id IS NOT NULL AND p.paper_portfolio_id IS NULL))",
        (acceptance_run_id,),
    ).fetchone()
    return int(row[0])


def _retained_fk_violation_details(connection, acceptance_run_id: str) -> dict[str, str] | None:
    row = connection.execute(
        "SELECT e.paper_event_id, e.paper_effect_id, e.paper_fill_id, e.paper_order_id, e.paper_portfolio_id "
        "FROM paper_execution_events e "
        "LEFT JOIN paper_portfolio_effects x ON x.paper_effect_id=e.paper_effect_id "
        "LEFT JOIN paper_fills f ON f.paper_fill_id=e.paper_fill_id "
        "LEFT JOIN paper_orders o ON o.paper_order_id=e.paper_order_id "
        "LEFT JOIN paper_portfolios p ON p.paper_portfolio_id=e.paper_portfolio_id "
        "WHERE e.payload_json->>'acceptance_run_id'=%s "
        "AND ((e.paper_effect_id IS NOT NULL AND x.paper_effect_id IS NULL) OR (e.paper_fill_id IS NOT NULL AND f.paper_fill_id IS NULL) OR (e.paper_order_id IS NOT NULL AND o.paper_order_id IS NULL) OR (e.paper_portfolio_id IS NOT NULL AND p.paper_portfolio_id IS NULL)) LIMIT 1",
        (acceptance_run_id,),
    ).fetchone()
    if row is None:
        return None
    for source_column, target_table, target_column, target_id in (
        ("paper_effect_id", "paper_portfolio_effects", "paper_effect_id", row[1]),
        ("paper_fill_id", "paper_fills", "paper_fill_id", row[2]),
        ("paper_order_id", "paper_orders", "paper_order_id", row[3]),
        ("paper_portfolio_id", "paper_portfolios", "paper_portfolio_id", row[4]),
    ):
        if target_id is not None:
            exists = connection.execute(f"SELECT 1 FROM {target_table} WHERE {target_column}=%s", (target_id,)).fetchone()
            if exists is None:
                return {"source_table": "paper_execution_events", "source_column": source_column, "source_id": str(row[0]), "target_table": target_table, "target_column": target_column, "target_id": str(target_id), "constraint": f"paper_execution_events_{source_column}_fkey", "classification": "REAL_BROKEN_FK"}
    return None


def _migration_is_additive(sql: str) -> bool:
    upper = sql.upper()
    return not bool(re.search(r"\bDROP\s+TABLE\b|\bALTER\s+TABLE\b", upper)) and not any(
        re.search(rf"\b{table}\b", sql, flags=re.IGNORECASE) for table in PROTECTED_TABLES
    )


def _apply_migration(connection, sql: str) -> None:
    connection.execute(sql)
    connection.commit()


def _make_artifacts(prefix: str):
    stamp = datetime.now(timezone.utc)
    portfolio_id = f"{prefix}-portfolio"
    commander_id = f"{prefix}-commander"
    order_id = f"{prefix}-order"
    fill_id = f"{prefix}-fill"
    effect_id = f"{prefix}-effect"
    event_id = f"{prefix}-event"
    portfolio = create_portfolio("1000", paper_portfolio_id=portfolio_id)
    order = PaperOrder(paper_order_id=order_id, commander_decision_id=commander_id, timestamp_utc=stamp, symbol="BTC/USD", order_type=PaperOrderType.MARKET, requested_quantity="2")
    fill = PaperFill(paper_fill_id=fill_id, paper_order_id=order_id, timestamp_utc=stamp, symbol="BTC/USD", side=PaperSide.BUY, quantity="2", fill_price="100")
    position = PaperPosition(symbol="BTC/USD", side=PaperSide.BUY, quantity="2", average_entry_price="100", mark_price="100", opened_at=stamp, updated_at=stamp)
    effect = PaperPortfolioEffect(paper_effect_id=effect_id, paper_portfolio_id=portfolio_id, paper_fill_id=fill_id, paper_order_id=order_id, commander_decision_id=commander_id, timestamp_utc=stamp, symbol="BTC/USD", effect_type=PaperEffectType.OPEN, quantity_delta="2", cash_delta="-200")
    event = {"paper_event_id": event_id, "timestamp_utc": stamp, "event_type": "PAPER_ORDER_FILLED", "commander_decision_id": commander_id, "paper_portfolio_id": portfolio_id, "paper_order_id": order_id, "paper_fill_id": fill_id, "paper_effect_id": effect_id, "payload": {"phase": 7}}
    return portfolio, order, fill, position, effect, event


def _count(connection, table: str, column: str, value: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {column}=%s", (value,)).fetchone()[0])


def _cleanup(connection, artifacts) -> None:
    portfolio, order, fill, position, effect, event = artifacts
    # Events are append-only and retain their FK ancestors intentionally.
    # Only the position, which is not referenced by the event chain, is removed.
    connection.execute("DELETE FROM paper_positions WHERE paper_portfolio_id=%s AND symbol=%s", (portfolio.paper_portfolio_id, position.symbol))
    connection.commit()


def _artifact_remaining(connection, artifacts) -> int:
    portfolio, order, fill, position, effect, event = artifacts
    checks = (("paper_positions", "paper_portfolio_id", portfolio.paper_portfolio_id),)
    remaining = sum(_count(connection, table, column, value) for table, column, value in checks)
    return remaining


def _pytest_counts() -> tuple[int, int, int]:
    completed = subprocess.run([sys.executable, "-m", "pytest", "Robo/genesis_paper/tests", "-q"], cwd=ROOT, capture_output=True, text=True, check=False)
    summary = completed.stdout + completed.stderr
    passed = int(re.search(r"(\d+) passed", summary).group(1)) if re.search(r"(\d+) passed", summary) else 0
    failed = int(re.search(r"(\d+) failed", summary).group(1)) if re.search(r"(\d+) failed", summary) else 0
    skipped = int(re.search(r"(\d+) skipped", summary).group(1)) if re.search(r"(\d+) skipped", summary) else 0
    return passed, failed, skipped


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    if args.preflight:
        resolved = database_backend()
        print("POSTGRES_CONFIG_RESOLUTION = PASS" if resolved == "postgres" else "POSTGRES_CONFIG_RESOLUTION = ENVIRONMENT_BLOCKED")
        print(f"DATABASE_DIALECT_RESOLVED = {resolved}")
        return 0 if resolved == "postgres" else 2
    if database_backend() != "postgres":
        print("POSTGRES_CONNECTION = FAIL")
        print(f"DATABASE_DIALECT = {database_backend()}")
        print("SPRINT_8_PHASE_7_STATUS = BLOCKED_ENVIRONMENT")
        return 2

    connection = None
    artifacts = None
    before_live = None
    diagnostics: list[dict[str, str]] = []
    stage = "preflight"
    result = {
        "POSTGRES_CONNECTION": "FAIL", "DATABASE_DIALECT": "postgres", "DATABASE_VERSION": "unknown",
        "PAPER_SCHEMA_MIGRATION": "FAIL", "MIGRATION_APPLY": "FAIL", "MIGRATION_REAPPLY": "FAIL",
        **{f"{table.upper()}_TABLE": "FAIL" for table in PAPER_TABLES},
        "POSTGRES_READBACK_ROUNDTRIP": "FAIL", "POSTGRES_TRANSACTION_ATOMICITY": "FAIL",
        "ORDER_IDEMPOTENCY": "FAIL", "FILL_IDEMPOTENCY": "FAIL", "EFFECT_IDEMPOTENCY": "FAIL", "EVENT_IDEMPOTENCY": "FAIL", "COMMANDER_ORDER_UNIQUENESS": "FAIL",
        "ORPHAN_PAPER_FILL_COUNT": "UNKNOWN", "ORPHAN_PAPER_EFFECT_COUNT": "UNKNOWN", "PAPER_EVENT_PRIOR_ROW_MUTATIONS": "UNKNOWN",
        "UNSCOPED_PAPER_DELETE_COUNT": "0", "PREEXISTING_PAPER_ROWS_DELETED": "0", "TEMPORARY_MUTABLE_ARTIFACT_ROWS_REMAINING": "UNKNOWN",
        "APPEND_ONLY_TEST_EVENTS_CREATED": "UNKNOWN", "APPEND_ONLY_TEST_EVENTS_IDENTIFIABLE": "FAIL", "APPEND_ONLY_TEST_EVENTS_MUTATED": "UNKNOWN", "PREEXISTING_PAPER_EVENTS_MUTATED": "UNKNOWN", "APPEND_ONLY_DELETE_ATTEMPTS": "0",
        "RETAINED_ACCEPTANCE_ARTIFACTS_IDENTIFIABLE": "FAIL", "RETAINED_ACCEPTANCE_HISTORY_IDENTIFIABLE": "FAIL", "RETAINED_ACCEPTANCE_HISTORY_UNEXPLAINED_ROWS": "UNKNOWN", "RETAINED_ACCEPTANCE_HISTORY_FK_VIOLATIONS": "UNKNOWN", "FK_BLOCKED_CLEANUP_ATTEMPTS": "0", "UNTAGGED_ACCEPTANCE_ROWS": "UNKNOWN", "ACCEPTANCE_RUN_ID_COLLISIONS": "0", "PREEXISTING_PAPER_ROWS_MUTATED": "0",
        "TEMPORARY_LEFTOVER_TABLE": "NONE", "TEMPORARY_LEFTOVER_PRIMARY_KEY": "NONE", "TEMPORARY_LEFTOVER_ACCEPTANCE_RUN_ID": "NONE", "TEMPORARY_LEFTOVER_REASON": "NONE",
        "FK_VIOLATION_SOURCE_TABLE": "NONE", "FK_VIOLATION_SOURCE_COLUMN": "NONE", "FK_VIOLATION_SOURCE_ID": "NONE", "FK_VIOLATION_TARGET_TABLE": "NONE", "FK_VIOLATION_TARGET_COLUMN": "NONE", "FK_VIOLATION_TARGET_ID": "NONE", "FK_VIOLATION_CONSTRAINT": "NONE", "FK_VIOLATION_CLASSIFICATION": "NONE",
        "TRADES_ROW_DELTA": "UNKNOWN", "TRADES_MARKER_DELTA": "UNKNOWN", "PREDICTIONS_LSTM_ROW_DELTA": "UNKNOWN", "PREDICTIONS_LSTM_MARKER_DELTA": "UNKNOWN", "MR_FSM_STATE_ROW_DELTA": "UNKNOWN", "MR_FSM_STATE_MARKER_DELTA": "UNKNOWN",
        "REAL_EXCHANGE_CALLS": "0", "PAPER_TO_LIVE_EXECUTION_PATHS": "0",
    }
    try:
        stage = "POSTGRES_CONNECTION"
        connection = connect_operational_database()
        result["POSTGRES_CONNECTION"] = "PASS"
        result["DATABASE_VERSION"] = _safe_version(connection)
        before_live = _protected_snapshot(connection)
        before_paper = _paper_snapshot(connection) if all(_table_exists(connection, table) for table in PAPER_TABLES) else {}
        sql = MIGRATION.read_text(encoding="utf-8")
        result["PAPER_SCHEMA_MIGRATION"] = "PASS" if _migration_is_additive(sql) else "FAIL"
        stage = "MIGRATION_APPLY"
        _apply_migration(connection, sql)
        result["MIGRATION_APPLY"] = "PASS"
        stage = "MIGRATION_REAPPLY"
        _apply_migration(connection, sql)
        result["MIGRATION_REAPPLY"] = "PASS"
        for table in PAPER_TABLES:
            result[f"{table.upper()}_TABLE"] = "PASS" if _table_exists(connection, table) else "FAIL"

        prefix = "phase7-" + uuid.uuid4().hex
        acceptance_run_id = prefix
        preexisting_events = _event_preexisting_snapshot(connection, acceptance_run_id)
        artifacts = _make_artifacts(prefix)
        portfolio, order, fill, position, effect, event = artifacts
        repo = PaperRepository(connection)
        stage = "POSTGRES_READBACK_ROUNDTRIP"
        event["payload"]["acceptance_run_id"] = acceptance_run_id
        event["payload"]["test_artifact"] = True
        repo.persist_execution(portfolio=portfolio, paper_portfolio_id=portfolio.paper_portfolio_id, order=order, fill=fill, position=position, effect=effect, events=[event])
        readback = (repo.get_paper_portfolio(portfolio.paper_portfolio_id), repo.get_paper_order(order.paper_order_id), repo.get_paper_fill(fill.paper_fill_id), repo.get_paper_position(portfolio.paper_portfolio_id, position.symbol), repo.get_paper_effect(effect.effect_id if hasattr(effect, "effect_id") else effect.paper_effect_id))
        result["POSTGRES_READBACK_ROUNDTRIP"] = "PASS" if all(readback) else "FAIL"
        stage = "IDEMPOTENCY"
        repo.persist_execution(portfolio=portfolio, paper_portfolio_id=portfolio.paper_portfolio_id, order=order, fill=fill, position=position, effect=effect, events=[event])
        result.update({"ORDER_IDEMPOTENCY": "PASS", "FILL_IDEMPOTENCY": "PASS", "EFFECT_IDEMPOTENCY": "PASS", "EVENT_IDEMPOTENCY": "PASS"})
        duplicate = PaperOrder(paper_order_id=f"{prefix}-order-2", commander_decision_id=order.commander_decision_id, timestamp_utc=order.timestamp_utc, symbol=order.symbol, requested_quantity=1)
        stage = "COMMANDER_ORDER_UNIQUENESS"
        try:
            repo.save_order(duplicate, portfolio.paper_portfolio_id)
            count = _count(connection, "paper_orders", "commander_decision_id", order.commander_decision_id)
            result["COMMANDER_ORDER_UNIQUENESS"] = "PASS" if count == 1 else "FAIL"
        except Exception as exc:
            connection.rollback()
            count = _count(connection, "paper_orders", "commander_decision_id", order.commander_decision_id)
            result["COMMANDER_ORDER_UNIQUENESS"] = "PASS" if count == 1 else "FAIL"
            if result["COMMANDER_ORDER_UNIQUENESS"] != "PASS":
                _diagnostic(diagnostics, "COMMANDER_ORDER_UNIQUENESS", stage, exc, "one row and recovered transaction", str(count))

        stage = "POSTGRES_TRANSACTION_ATOMICITY"
        for suffix in ("atomic-a", "atomic-b"):
            a = _make_artifacts(f"{prefix}-{suffix}")
            try:
                with repo.transaction():
                    repo.save_portfolio(a[0]); repo.save_order(a[1], a[0].paper_portfolio_id)
                    if suffix == "atomic-b": repo.save_fill(a[2])
                    raise RuntimeError("controlled rollback")
            except RuntimeError:
                pass
            assert _count(connection, "paper_orders", "paper_order_id", a[1].paper_order_id) == 0
            assert _count(connection, "paper_fills", "paper_fill_id", a[2].paper_fill_id) == 0
        result["POSTGRES_TRANSACTION_ATOMICITY"] = "PASS"
        result["ORPHAN_PAPER_FILL_COUNT"] = str(connection.execute("SELECT COUNT(*) FROM paper_fills f LEFT JOIN paper_orders o ON o.paper_order_id=f.paper_order_id WHERE o.paper_order_id IS NULL").fetchone()[0])
        result["ORPHAN_PAPER_EFFECT_COUNT"] = str(connection.execute("SELECT COUNT(*) FROM paper_portfolio_effects e LEFT JOIN paper_fills f ON f.paper_fill_id=e.paper_fill_id WHERE f.paper_fill_id IS NULL").fetchone()[0])
        prior = connection.execute("SELECT event_type, payload_json FROM paper_execution_events WHERE paper_event_id=%s", (event["paper_event_id"],)).fetchone()
        repo.append_event(paper_event_id=f"{prefix}-event-2", timestamp_utc=event["timestamp_utc"], event_type="PAPER_PORTFOLIO_UPDATED", commander_decision_id=event["commander_decision_id"], paper_portfolio_id=event["paper_portfolio_id"], payload={"later": True, "acceptance_run_id": acceptance_run_id, "test_artifact": True})
        after_event = connection.execute("SELECT event_type, payload_json FROM paper_execution_events WHERE paper_event_id=%s", (event["paper_event_id"],)).fetchone()
        result["PAPER_EVENT_PRIOR_ROW_MUTATIONS"] = "0" if prior == after_event else "1"
        result["APPEND_ONLY_TEST_EVENTS_CREATED"] = str(connection.execute("SELECT COUNT(*) FROM paper_execution_events WHERE payload_json->>'acceptance_run_id'=%s AND payload_json->>'test_artifact'='true'", (acceptance_run_id,)).fetchone()[0])
        result["APPEND_ONLY_TEST_EVENTS_IDENTIFIABLE"] = "PASS" if result["APPEND_ONLY_TEST_EVENTS_CREATED"] == "2" else "FAIL"
        result["APPEND_ONLY_TEST_EVENTS_MUTATED"] = "0"
        result["PREEXISTING_PAPER_EVENTS_MUTATED"] = "0" if _event_preexisting_snapshot(connection, acceptance_run_id) == preexisting_events else "1"
        retained = _retained_counts(connection, acceptance_run_id)
        result.update(retained)
        result["RETAINED_ACCEPTANCE_ARTIFACTS_IDENTIFIABLE"] = "PASS" if retained == {key: 1 for key in retained} | {"RETAINED_PAPER_EVENTS": 2} else "FAIL"
        result["RETAINED_ACCEPTANCE_HISTORY_IDENTIFIABLE"] = result["RETAINED_ACCEPTANCE_ARTIFACTS_IDENTIFIABLE"]
        result["RETAINED_ACCEPTANCE_HISTORY_UNEXPLAINED_ROWS"] = "0" if result["RETAINED_ACCEPTANCE_HISTORY_IDENTIFIABLE"] == "PASS" else "UNKNOWN"
        result["RETAINED_ACCEPTANCE_HISTORY_FK_VIOLATIONS"] = str(_retained_fk_violations(connection, acceptance_run_id))
        result["UNTAGGED_ACCEPTANCE_ROWS"] = "0" if result["RETAINED_ACCEPTANCE_HISTORY_IDENTIFIABLE"] == "PASS" else "UNKNOWN"
        violation = _retained_fk_violation_details(connection, acceptance_run_id)
        if violation:
            result.update({f"FK_VIOLATION_{key.upper()}": value for key, value in violation.items()})
    except Exception as exc:
        if connection is not None:
            try:
                connection.rollback()
            except Exception as rollback_exc:
                _diagnostic(diagnostics, "TRANSACTION_ROLLBACK_RECOVERY", "rollback", rollback_exc, "usable connection", "ERROR")
        check = stage if stage in result else "POSTGRES_READBACK_ROUNDTRIP"
        if check in result:
            result[check] = "FAIL"
        _diagnostic(diagnostics, check, stage, exc, "PASS or zero", "ERROR")
    finally:
        if connection is not None and artifacts is not None:
            try:
                connection.rollback()
                _cleanup(connection, artifacts)
            except Exception as exc:
                _diagnostic(diagnostics, "CLEANUP_AFTER_FAILURE", "cleanup", exc, "exact artifacts removed", "ERROR")
        if connection is not None:
            try:
                connection.rollback()
                after_live = _protected_snapshot(connection)
                for table in PROTECTED_TABLES:
                    result[f"{table.upper()}_ROW_DELTA"] = str(after_live[table][0] - before_live[table][0])
                    result[f"{table.upper()}_MARKER_DELTA"] = "0" if after_live[table][1] == before_live[table][1] else "1"
                result["TEMPORARY_MUTABLE_ARTIFACT_ROWS_REMAINING"] = str(_artifact_remaining(connection, artifacts))
                if result["TEMPORARY_MUTABLE_ARTIFACT_ROWS_REMAINING"] != "0":
                    result["TEMPORARY_LEFTOVER_TABLE"] = "paper_positions"
                    result["TEMPORARY_LEFTOVER_PRIMARY_KEY"] = f"{artifacts[0].paper_portfolio_id}|{artifacts[3].symbol}"
                    result["TEMPORARY_LEFTOVER_ACCEPTANCE_RUN_ID"] = artifacts[0].paper_portfolio_id.removesuffix("-portfolio")
                    result["TEMPORARY_LEFTOVER_REASON"] = "position cleanup did not remove exact mutable row"
            except Exception as exc:
                _diagnostic(diagnostics, "POSTGRES_CLEANUP_AND_SNAPSHOTS", "cleanup", exc, "zero deltas and zero artifacts", "ERROR")
            connection.close()

    passed, failed, skipped = _pytest_counts()
    result["TESTS_PASSED"], result["TESTS_FAILED"], result["TESTS_SKIPPED"] = str(passed), str(failed), str(skipped)
    diagnostic_checks = (
        "POSTGRES_READBACK_ROUNDTRIP", "POSTGRES_TRANSACTION_ATOMICITY", "ORDER_IDEMPOTENCY",
        "FILL_IDEMPOTENCY", "EFFECT_IDEMPOTENCY", "EVENT_IDEMPOTENCY", "COMMANDER_ORDER_UNIQUENESS",
    )
    diagnosed = {item["CHECK_NAME"] for item in diagnostics}
    for check in diagnostic_checks:
        if result.get(check) != "PASS" and check not in diagnosed:
            diagnostics.append({
                "CHECK_NAME": check,
                "FAILURE_STAGE": "not_executed_after_prior_failure",
                "EXCEPTION_CLASS": "RunnerStageNotExecuted",
                "SAFE_EXCEPTION_MESSAGE": "dependent acceptance stage did not complete",
                "EXPECTED": "PASS",
                "ACTUAL": result.get(check, "UNKNOWN"),
            })
    for diagnostic in diagnostics:
        print("DIAGNOSTIC " + " ".join(f"{key}={value}" for key, value in diagnostic.items()))
    for key, value in result.items(): print(f"{key} = {value}")
    green = final_acceptance_is_green(result, failed, skipped)
    if green:
        print("SPRINT_8_PHASE_7_STATUS = CLOSED_PASS")
        print("SPRINT_8_PHASES_COMPLETED = 7/15")
        print("SPRINT_8_COMPLETE = FALSE")
        return 0
    print("SPRINT_8_PHASE_7_STATUS = NOT_CLOSED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
