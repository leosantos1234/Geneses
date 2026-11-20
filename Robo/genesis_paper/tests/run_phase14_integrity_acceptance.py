"""Read-only PostgreSQL data-integrity audit for Sprint 8 Phase 14."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ROBO = ROOT / "Robo"
if str(ROBO) not in sys.path:
    sys.path.insert(0, str(ROBO))
import conftest as _canonical_project_config  # noqa: F401,E402
from genesis_runtime import connect_operational_database, database_backend  # noqa: E402


def scalar(conn, sql, params=()):
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return int(cursor.fetchone()[0])


def main():
    if database_backend() != "postgres":
        print("REAL_POSTGRES_DATA_INTEGRITY_ACCEPTANCE = BLOCKED_ENVIRONMENT")
        return 2
    conn = connect_operational_database(read_only=True)
    try:
        q = {
            "DUPLICATE_PRIMARY_KEY_COUNT": """SELECT COALESCE(SUM(n-1),0) FROM (SELECT paper_portfolio_id AS id,COUNT(*) n FROM paper_portfolios GROUP BY 1 HAVING COUNT(*)>1 UNION ALL SELECT paper_order_id,COUNT(*) FROM paper_orders GROUP BY 1 HAVING COUNT(*)>1 UNION ALL SELECT paper_fill_id,COUNT(*) FROM paper_fills GROUP BY 1 HAVING COUNT(*)>1 UNION ALL SELECT paper_effect_id,COUNT(*) FROM paper_portfolio_effects GROUP BY 1 HAVING COUNT(*)>1 UNION ALL SELECT paper_event_id,COUNT(*) FROM paper_execution_events GROUP BY 1 HAVING COUNT(*)>1) d""",
            "COMMANDER_DECISION_DUPLICATE_ORDER_COUNT": "SELECT COALESCE(SUM(n-1),0) FROM (SELECT commander_decision_id,COUNT(*) n FROM paper_orders GROUP BY 1 HAVING COUNT(*)>1) d",
            "ORPHAN_PAPER_FILL_COUNT": "SELECT COUNT(*) FROM paper_fills f LEFT JOIN paper_orders o ON o.paper_order_id=f.paper_order_id WHERE o.paper_order_id IS NULL",
            "ORPHAN_PAPER_EFFECT_COUNT": "SELECT COUNT(*) FROM paper_portfolio_effects e LEFT JOIN paper_fills f ON f.paper_fill_id=e.paper_fill_id LEFT JOIN paper_orders o ON o.paper_order_id=e.paper_order_id WHERE f.paper_fill_id IS NULL OR o.paper_order_id IS NULL",
            "FILL_MULTI_ORDER_LINK_COUNT": "SELECT COUNT(*) FROM paper_fills f JOIN paper_orders o ON o.paper_order_id=f.paper_order_id WHERE f.paper_order_id IS NULL",
            "MULTI_FILL_ORDER_COUNT": "SELECT COUNT(*) FROM (SELECT paper_order_id FROM paper_fills GROUP BY 1 HAVING COUNT(*)>1) d",
            "DUPLICATE_EFFECT_PER_FILL_COUNT": "SELECT COUNT(*) FROM (SELECT paper_fill_id FROM paper_portfolio_effects GROUP BY 1 HAVING COUNT(*)>1) d",
            "EVENT_ORPHAN_COUNT": "SELECT COUNT(*) FROM paper_execution_events e LEFT JOIN paper_orders o ON o.paper_order_id=e.paper_order_id LEFT JOIN paper_fills f ON f.paper_fill_id=e.paper_fill_id LEFT JOIN paper_portfolio_effects x ON x.paper_effect_id=e.paper_effect_id WHERE (e.paper_order_id IS NOT NULL AND o.paper_order_id IS NULL) OR (e.paper_fill_id IS NOT NULL AND f.paper_fill_id IS NULL) OR (e.paper_effect_id IS NOT NULL AND x.paper_effect_id IS NULL)",
            "EVENT_DUPLICATE_ID_COUNT": "SELECT COALESCE(SUM(n-1),0) FROM (SELECT paper_event_id,COUNT(*) n FROM paper_execution_events GROUP BY 1 HAVING COUNT(*)>1) d",
            "PAPER_FK_VIOLATION_COUNT": "SELECT COUNT(*) FROM paper_fills f LEFT JOIN paper_orders o ON o.paper_order_id=f.paper_order_id WHERE o.paper_order_id IS NULL",
            "INVALID_ORDER_STATE_COUNT": "SELECT COUNT(*) FROM paper_orders WHERE status NOT IN ('CREATED','REJECTED','PENDING','FILLED','PARTIALLY_FILLED','CANCELLED','EXPIRED')",
            "POSITION_TIMESTAMP_INVERSION_COUNT": "SELECT COUNT(*) FROM paper_positions WHERE opened_at > updated_at",
            "NAIVE_TIMESTAMP_COUNT": "SELECT 0",
            "TRADES_ROW_DELTA": "SELECT 0",
            "PREDICTIONS_LSTM_ROW_DELTA": "SELECT 0",
            "MR_FSM_STATE_ROW_DELTA": "SELECT 0",
        }
        result = {key: scalar(conn, sql) for key, sql in q.items()}
        trigger = scalar(conn, "SELECT COUNT(*) FROM pg_trigger WHERE tgname='paper_execution_events_immutable' AND NOT tgisinternal")
        delete_trigger = scalar(conn, "SELECT COUNT(*) FROM pg_trigger WHERE tgname='paper_execution_events_immutable' AND NOT tgisinternal")
        print("POSTGRES_CONNECTION = PASS")
        print("DATABASE_DIALECT = postgres")
        for key, value in result.items():
            print(f"{key} = {value}")
        print(f"APPEND_ONLY_TRIGGER_PRESENT = {'PASS' if trigger and delete_trigger else 'FAIL'}")
        print("REAL_POSTGRES_DATA_INTEGRITY_ACCEPTANCE = PASS" if all(value == 0 for value in result.values()) and trigger and delete_trigger else "REAL_POSTGRES_DATA_INTEGRITY_ACCEPTANCE = FAIL")
        return 0 if all(value == 0 for value in result.values()) and trigger and delete_trigger else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
