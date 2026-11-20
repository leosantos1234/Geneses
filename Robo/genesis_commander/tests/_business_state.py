"""Read-only business-state snapshot via the canonical Genesis PostgreSQL helper."""

from genesis_runtime import connect_operational_database

SNAPSHOT_QUERIES = {
    "trades": ("SELECT COUNT(*), MAX(id) FROM trades", ("count", "max_id")),
    "predictions_lstm": ("SELECT COUNT(*), MAX(id) FROM predictions_lstm", ("count", "max_id")),
    "mr_fsm_state": ("SELECT COUNT(*), MAX(updated_at) FROM mr_fsm_state", ("count", "max_updated_at")),
    "decision_trace": ("SELECT COUNT(*) FROM decision_trace", ("count",)),
    "decision_events": ("SELECT COUNT(*) FROM decision_events", ("count",)),
    "rag_records": ("SELECT COUNT(*) FROM rag_records", ("count",)),
}


def take_business_state() -> dict:
    conn = connect_operational_database(read_only=True)
    state = {}
    try:
        with conn.cursor() as cur:
            for table, (sql, fields) in SNAPSHOT_QUERIES.items():
                cur.execute(f"SELECT to_regclass('public.{table}') IS NOT NULL")
                if not cur.fetchone()[0]:
                    raise AssertionError(f"business table missing: {table}")
                cur.execute(sql)
                row = cur.fetchone()
                for name, value in zip(fields, row):
                    state[f"{table}.{name}"] = value
    finally:
        conn.close()
    return state

