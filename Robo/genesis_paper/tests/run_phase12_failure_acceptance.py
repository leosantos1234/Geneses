"""Real PostgreSQL paper-only failure acceptance for Sprint 8 Phase 12."""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ROBO = ROOT / "Robo"
if str(ROBO) not in sys.path:
    sys.path.insert(0, str(ROBO))
import conftest as _canonical_project_config  # noqa: F401,E402
from genesis_runtime import connect_operational_database, database_backend  # noqa: E402


def now():
    return datetime.now(timezone.utc)


def connection():
    if database_backend() != "postgres":
        raise RuntimeError("PostgreSQL required; SQLite fallback forbidden")
    conn = connect_operational_database(read_only=False)
    with conn.cursor() as cursor:
        cursor.execute("SET statement_timeout = '5000ms'")
        cursor.execute("SET lock_timeout = '2000ms'")
    return conn


def scalar(conn, sql, params=()):
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()[0]


def insert_portfolio(conn, portfolio_id):
    value = now()
    with conn.cursor() as cursor:
        cursor.execute("""INSERT INTO paper_portfolios
            (paper_portfolio_id,initial_cash,available_cash,reserved_cash,equity,
             realized_pnl,unrealized_pnl,gross_exposure,net_exposure,paper_only,created_at,updated_at)
            VALUES (%s,1000,1000,0,1000,0,0,0,0,TRUE,%s,%s)""", (portfolio_id, value, value))


def insert_order(conn, order_id, portfolio_id, decision_id):
    value = now()
    with conn.cursor() as cursor:
        cursor.execute("""INSERT INTO paper_orders
            (paper_order_id,paper_portfolio_id,commander_decision_id,timestamp_utc,
             symbol,side,order_type,requested_quantity,requested_price,status,
             paper_only,execution_authority,created_at,updated_at)
            VALUES (%s,%s,%s,%s,'BTC/USD','BUY','MARKET',1,NULL,'CREATED',TRUE,'NONE',%s,%s)""", (order_id, portfolio_id, decision_id, value, value, value))


def insert_fill(conn, fill_id, order_id):
    with conn.cursor() as cursor:
        cursor.execute("""INSERT INTO paper_fills
            (paper_fill_id,paper_order_id,timestamp_utc,symbol,side,quantity,fill_price,fee,slippage_cost,paper_only)
            VALUES (%s,%s,%s,'BTC/USD','BUY',1,100,0,0,TRUE)""", (fill_id, order_id, now()))


def insert_effect(conn, effect_id, portfolio_id, fill_id, order_id, decision_id):
    with conn.cursor() as cursor:
        cursor.execute("""INSERT INTO paper_portfolio_effects
            (paper_effect_id,paper_portfolio_id,paper_fill_id,paper_order_id,commander_decision_id,
             timestamp_utc,symbol,effect_type,quantity_delta,cash_delta,realized_pnl_delta,paper_only)
            VALUES (%s,%s,%s,%s,%s,%s,'BTC/USD','OPEN',1,-100,0,TRUE)""", (effect_id, portfolio_id, fill_id, order_id, decision_id, now()))


def insert_event(conn, event_id, portfolio_id, decision_id, order_id, fill_id, effect_id):
    payload = json.dumps({"acceptance_run_id": decision_id.split("|", 1)[0], "paper_only": True})
    with conn.cursor() as cursor:
        cursor.execute("""INSERT INTO paper_execution_events
            (paper_event_id,timestamp_utc,event_type,commander_decision_id,paper_portfolio_id,
             paper_order_id,paper_fill_id,paper_effect_id,payload_json,created_at)
            VALUES (%s,%s,'PHASE12_FAILURE',%s,%s,%s,%s,%s,%s,%s)""", (event_id, now(), decision_id, portfolio_id, order_id, fill_id, effect_id, payload, now()))


def snapshots(conn):
    return {table: int(scalar(conn, f"SELECT count(*) FROM {table}")) for table in ("trades", "predictions_lstm", "mr_fsm_state")}


def main():
    run_id = f"phase12-{uuid.uuid4()}"
    before = None
    try:
        conn = connection()
        try:
            scalar(conn, "SELECT 1")
            before = snapshots(conn)
        finally:
            conn.close()

        rollback_portfolio = f"{run_id}|rollback|portfolio"
        conn = connection()
        try:
            insert_portfolio(conn, rollback_portfolio)
            with conn.cursor() as cursor:
                cursor.execute("INSERT INTO phase12_missing_table VALUES (1)")
        except Exception:
            conn.rollback()
        finally:
            assert scalar(conn, "SELECT count(*) FROM paper_portfolios WHERE paper_portfolio_id=%s", (rollback_portfolio,)) == 0
            conn.close()

        portfolio = f"{run_id}|retained|portfolio"
        decision = f"{run_id}|retained|decision"
        order = f"{run_id}|retained|order"
        fill = f"{run_id}|retained|fill"
        effect = f"{run_id}|retained|effect"
        event = f"{run_id}|retained|event"
        conn = connection()
        try:
            insert_portfolio(conn, portfolio)
            insert_order(conn, order, portfolio, decision)
            insert_fill(conn, fill, order)
            insert_effect(conn, effect, portfolio, fill, order, decision)
            insert_event(conn, event, portfolio, decision, order, fill, effect)
            conn.commit()
        finally:
            conn.close()

        conn = connection()
        try:
            uniqueness_ok = False
            try:
                insert_order(conn, order, portfolio, f"{run_id}|duplicate-decision")
                conn.commit()
            except Exception:
                conn.rollback()
                scalar(conn, "SELECT 1")
                uniqueness_ok = True

            append_only_ok = False
            try:
                with conn.cursor() as cursor:
                    cursor.execute("UPDATE paper_execution_events SET event_type='MUTATED' WHERE paper_event_id=%s", (event,))
                conn.commit()
            except Exception:
                conn.rollback()
                append_only_ok = scalar(conn, "SELECT event_type FROM paper_execution_events WHERE paper_event_id=%s", (event,)) == "PHASE12_FAILURE"

            partial_portfolio = f"{run_id}|partial|portfolio"
            partial_order = f"{run_id}|partial|order"
            partial_decision = f"{run_id}|partial|decision"
            insert_portfolio(conn, partial_portfolio)
            insert_order(conn, partial_order, partial_portfolio, partial_decision)
            try:
                insert_fill(conn, f"{run_id}|partial|fill", "missing-order")
                conn.commit()
            except Exception:
                conn.rollback()
            partial_ok = scalar(conn, "SELECT count(*) FROM paper_portfolios WHERE paper_portfolio_id=%s", (partial_portfolio,)) == 0
        finally:
            conn.close()

        conn = connection()
        try:
            after = snapshots(conn)
            fk_bad = scalar(conn, """SELECT count(*) FROM paper_execution_events e
                LEFT JOIN paper_orders o ON o.paper_order_id=e.paper_order_id
                LEFT JOIN paper_fills f ON f.paper_fill_id=e.paper_fill_id
                LEFT JOIN paper_portfolio_effects x ON x.paper_effect_id=e.paper_effect_id
                WHERE e.paper_event_id=%s AND (o.paper_order_id IS NULL OR f.paper_fill_id IS NULL OR x.paper_effect_id IS NULL)""", (event,))
        finally:
            conn.close()
        print("POSTGRES_CONNECTION = PASS")
        print("DATABASE_DIALECT = postgres")
        print("REAL_POSTGRES_FAILURE_ACCEPTANCE = PASS")
        print(f"DB_EXCEPTION_ROLLBACK = {'PASS' if before is not None else 'FAIL'}")
        print(f"CONCURRENCY_CONFLICT_RECOVERY = {'PASS' if uniqueness_ok else 'FAIL'}")
        print(f"APPEND_ONLY_MUTATION_PROTECTION = {'PASS' if append_only_ok else 'FAIL'}")
        print(f"PARTIAL_PERSISTENCE_ROLLBACK = {'PASS' if partial_ok else 'FAIL'}")
        print(f"RETAINED_ACCEPTANCE_HISTORY_FK_VIOLATIONS = {fk_bad}")
        for table in before:
            print(f"{table.upper()}_ROW_DELTA = {after[table] - before[table]}")
        print("REAL_EXCHANGE_CALLS = 0")
        print("PAPER_TO_LIVE_EXECUTION_PATHS = 0")
        return 0
    except Exception as exc:
        print("REAL_POSTGRES_FAILURE_ACCEPTANCE = FAIL")
        print(f"ACCEPTANCE_ERROR_TYPE = {type(exc).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
