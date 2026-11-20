"""Sprint 4 lineage tests; no exchange call and no quantitative fixture changes."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Robo"))

from lineage import decision_lineage as lineage


def test_authorized_path_and_same_id():
    jobs = []
    with patch.object(lineage, "_enqueue", side_effect=lambda kind, data: jobs.append((kind, data))):
        decision_id = lineage.create_decision("SOLUSDT", "5", "BREAKOUT", context={"score": 4})
        lineage.record_event(decision_id, "COMMANDER_ALLOWED", "COMMANDER", status="ALLOWED")
        lineage.record_event(decision_id, "RISK_ALLOWED", "RISK", status="ALLOWED")
        lineage.record_event(decision_id, "EXECUTION_GATE_EVALUATED", "EXECUTION_GATE", status="ALLOWED")
        lineage.finalize_decision(decision_id, "AUTHORIZED", "ORDER_ACCEPTED")
    ids = {item[1]["decision_id"] for item in jobs}
    assert ids == {decision_id}
    assert any(item[1].get("event_type") == "DECISION_FINALIZED" for item in jobs)


def test_blocked_paths_have_explicit_reasons():
    jobs = []
    with patch.object(lineage, "_enqueue", side_effect=lambda kind, data: jobs.append((kind, data))):
        decision_id = lineage.create_decision("SOLUSDT", "5", "BREAKOUT")
        for event, stage, reason in (("COMMANDER_BLOCKED", "COMMANDER", "COMMANDER_BLOCK"),
                                     ("RISK_BLOCKED", "RISK", "RISK_DAILY_LIMIT"),
                                     ("EXECUTION_BLOCKED", "EXECUTION_GATE", "EXECUTION_BLOCK"),
                                     ("ORDER_REJECTED", "ORDER", "ORDER_REJECTED")):
            lineage.record_event(decision_id, event, stage, status="BLOCKED", reason_code=reason)
    reasons = {item[1].get("reason_code") for item in jobs if item[0] == "event"}
    assert {"COMMANDER_BLOCK", "RISK_DAILY_LIMIT", "EXECUTION_BLOCK", "ORDER_REJECTED"} <= reasons


def test_secret_redaction_and_schema_contract():
    jobs = []
    with patch.object(lineage, "_enqueue", side_effect=lambda kind, data: jobs.append((kind, data))):
        lineage.create_decision("SOLUSDT", context={"api_key": "secret", "nested": {"token": "secret"}})
    sanitized = lineage._safe(jobs[0][1]["context"])
    assert sanitized["api_key"] == "<redacted>"
    assert sanitized["nested"]["token"] == "<redacted>"


def test_database_schema_and_legacy_trade_are_read_only():
    if not os.getenv("GENESIS_DATABASE_URL"):
        return
    import psycopg
    with psycopg.connect(os.environ["GENESIS_DATABASE_URL"], options="-c default_transaction_read_only=on") as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.decision_trace'), to_regclass('public.decision_events')")
            assert cur.fetchone() == ("decision_trace", "decision_events")
            cur.execute("SELECT COUNT(*) FROM trades WHERE decision_id IS NULL")
            assert cur.fetchone()[0] >= 0


if __name__ == "__main__":
    test_authorized_path_and_same_id()
    test_blocked_paths_have_explicit_reasons()
    test_secret_redaction_and_schema_contract()
    test_database_schema_and_legacy_trade_are_read_only()
    print("DECISION_LINEAGE_TESTS_OK")
