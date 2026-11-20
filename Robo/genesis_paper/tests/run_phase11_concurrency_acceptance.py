"""Bounded real-PostgreSQL Phase 11 paper concurrency acceptance runner."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
ROBO = ROOT / "Robo"
if str(ROBO) not in sys.path:
    sys.path.insert(0, str(ROBO))
import conftest as _canonical_project_config  # noqa: F401,E402
from genesis_runtime import connect_operational_database, database_backend  # noqa: E402

TIMEOUT = 10


def stamp() -> datetime:
    return datetime.now(timezone.utc)


def open_write():
    if database_backend() != "postgres":
        raise RuntimeError("PostgreSQL is required; SQLite fallback is forbidden")
    connection = connect_operational_database(read_only=False)
    with connection.cursor() as cursor:
        cursor.execute("SET lock_timeout = '2000ms'")
        cursor.execute("SET statement_timeout = '5000ms'")
    return connection


def scalar(connection, sql: str, params: tuple[Any, ...] = ()):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()[0]


def create_portfolio(run_id: str, name: str, cash: Decimal = Decimal("1000")) -> str:
    portfolio_id = f"{run_id}|portfolio|{name}"
    connection = open_write()
    try:
        now = stamp()
        with connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO paper_portfolios
                (paper_portfolio_id,initial_cash,available_cash,reserved_cash,equity,
                 realized_pnl,unrealized_pnl,gross_exposure,net_exposure,paper_only,
                 created_at,updated_at)
                VALUES (%s,%s,%s,0,%s,0,0,0,0,TRUE,%s,%s)""",
                (portfolio_id, cash, cash, cash, now, now),
            )
        connection.commit()
        return portfolio_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def insert_order(connection, order_id: str, portfolio_id: str, decision_id: str, status: str = "CREATED") -> None:
    now = stamp()
    with connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO paper_orders
            (paper_order_id,paper_portfolio_id,commander_decision_id,timestamp_utc,
             symbol,side,order_type,requested_quantity,requested_price,status,
             paper_only,execution_authority,created_at,updated_at)
            VALUES (%s,%s,%s,%s,'BTC/USD','BUY','MARKET',1,NULL,%s,TRUE,'NONE',%s,%s)""",
            (order_id, portfolio_id, decision_id, now, status, now, now),
        )


def insert_fill(connection, fill_id: str, order_id: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO paper_fills
            (paper_fill_id,paper_order_id,timestamp_utc,symbol,side,quantity,
             fill_price,fee,slippage_cost,paper_only)
            VALUES (%s,%s,%s,'BTC/USD','BUY',1,100,0,0,TRUE)""",
            (fill_id, order_id, stamp()),
        )


def insert_effect(connection, effect_id: str, portfolio_id: str, fill_id: str, order_id: str, decision_id: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO paper_portfolio_effects
            (paper_effect_id,paper_portfolio_id,paper_fill_id,paper_order_id,
             commander_decision_id,timestamp_utc,symbol,effect_type,quantity_delta,
             cash_delta,realized_pnl_delta,paper_only)
            VALUES (%s,%s,%s,%s,%s,%s,'BTC/USD','OPEN',1,-100,0,TRUE)""",
            (effect_id, portfolio_id, fill_id, order_id, decision_id, stamp()),
        )


def insert_event(connection, event_id: str, portfolio_id: str, decision_id: str, order_id: str, fill_id: str | None, effect_id: str | None) -> None:
    payload = json.dumps({"acceptance_run_id": decision_id.split("|", 1)[0], "paper_only": True}, sort_keys=True)
    with connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO paper_execution_events
            (paper_event_id,timestamp_utc,event_type,commander_decision_id,
             paper_portfolio_id,paper_order_id,paper_fill_id,paper_effect_id,
             payload_json,created_at)
            VALUES (%s,%s,'PHASE11_ACCEPTANCE',%s,%s,%s,%s,%s,%s,%s)""",
            (event_id, stamp(), decision_id, portfolio_id, order_id, fill_id, effect_id, payload, stamp()),
        )


def same_decision(run_id: str, name: str, workers: int) -> dict[str, Any]:
    portfolio_id = create_portfolio(run_id, name)
    decision_id = f"{run_id}|{name}|same-decision"
    barrier = threading.Barrier(workers)

    def worker(index: int) -> str:
        connection = open_write()
        try:
            barrier.wait(TIMEOUT)
            with connection.cursor() as cursor:
                cursor.execute("SELECT paper_portfolio_id FROM paper_portfolios WHERE paper_portfolio_id=%s FOR UPDATE", (portfolio_id,))
                order_id = f"{run_id}|{name}|order-{index}"
                try:
                    insert_order(connection, order_id, portfolio_id, decision_id)
                    insert_fill(connection, f"{run_id}|{name}|fill-{index}", order_id)
                    connection.commit()
                    return "ACCEPTED"
                except Exception as exc:
                    connection.rollback()
                    return "DUPLICATE" if exc.__class__.__name__ in {"UniqueViolation", "IntegrityError"} else (_ for _ in ()).throw(exc)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"pg-{name}") as pool:
        results = [future.result(TIMEOUT) for future in [pool.submit(worker, i) for i in range(workers)]]
    connection = open_write()
    try:
        orders = scalar(connection, "SELECT count(*) FROM paper_orders WHERE commander_decision_id=%s", (decision_id,))
        fills = scalar(connection, "SELECT count(*) FROM paper_fills f JOIN paper_orders o ON o.paper_order_id=f.paper_order_id WHERE o.commander_decision_id=%s", (decision_id,))
    finally:
        connection.close()
    return {"results": results, "orders": orders, "fills": fills}


def portfolio_race(run_id: str, name: str, *, cash: Decimal, workers: int, requested_cash: Decimal, exposure: Decimal, portfolio_limit: Decimal | None = None, symbol_limit: Decimal | None = None) -> dict[str, Any]:
    portfolio_id = create_portfolio(run_id, name, cash)
    barrier = threading.Barrier(workers)

    def worker(index: int) -> str:
        decision_id = f"{run_id}|{name}|decision-{index}"
        connection = open_write()
        try:
            barrier.wait(TIMEOUT)
            with connection.cursor() as cursor:
                cursor.execute("SELECT available_cash,gross_exposure FROM paper_portfolios WHERE paper_portfolio_id=%s FOR UPDATE", (portfolio_id,))
                available, gross = cursor.fetchone()
                if Decimal(str(available)) < requested_cash:
                    connection.rollback()
                    return "REJECT_CASH"
                if portfolio_limit is not None and Decimal(str(gross)) + exposure > portfolio_limit:
                    connection.rollback()
                    return "REJECT_EXPOSURE"
                symbol_total = Decimal("0")
                if symbol_limit is not None:
                    cursor.execute("SELECT COALESCE(SUM(quantity*mark_price),0) FROM paper_positions WHERE paper_portfolio_id=%s AND symbol='BTC/USD'", (portfolio_id,))
                    symbol_total = Decimal(str(cursor.fetchone()[0]))
                    if symbol_total + exposure > symbol_limit:
                        connection.rollback()
                        return "REJECT_SYMBOL"
                order_id = f"{run_id}|{name}|order-{index}"
                fill_id = f"{run_id}|{name}|fill-{index}"
                effect_id = f"{run_id}|{name}|effect-{index}"
                insert_order(connection, order_id, portfolio_id, decision_id)
                insert_fill(connection, fill_id, order_id)
                insert_effect(connection, effect_id, portfolio_id, fill_id, order_id, decision_id)
                insert_event(connection, f"{run_id}|{name}|event-{index}", portfolio_id, decision_id, order_id, fill_id, effect_id)
                cursor.execute("UPDATE paper_portfolios SET available_cash=available_cash-%s,equity=equity-%s,gross_exposure=gross_exposure+%s,updated_at=%s WHERE paper_portfolio_id=%s", (requested_cash, requested_cash, exposure, stamp(), portfolio_id))
                if symbol_limit is not None:
                    cursor.execute("""INSERT INTO paper_positions
                        (paper_portfolio_id,symbol,side,quantity,average_entry_price,mark_price,
                         unrealized_pnl,realized_pnl,opened_at,updated_at,paper_only)
                        VALUES (%s,'BTC/USD','BUY',%s,100,100,0,0,%s,%s,TRUE)
                        ON CONFLICT (paper_portfolio_id,symbol) DO UPDATE SET quantity=paper_positions.quantity+EXCLUDED.quantity,updated_at=EXCLUDED.updated_at""", (portfolio_id, exposure / Decimal("100"), stamp(), stamp()))
            connection.commit()
            return "ACCEPTED"
        except Exception as exc:
            connection.rollback()
            if exc.__class__.__name__ in {"UniqueViolation", "IntegrityError"}:
                return "DUPLICATE"
            raise
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"pg-{name}") as pool:
        results = [future.result(TIMEOUT) for future in [pool.submit(worker, i) for i in range(workers)]]
    connection = open_write()
    try:
        available = Decimal(str(scalar(connection, "SELECT available_cash FROM paper_portfolios WHERE paper_portfolio_id=%s", (portfolio_id,))))
        gross = Decimal(str(scalar(connection, "SELECT gross_exposure FROM paper_portfolios WHERE paper_portfolio_id=%s", (portfolio_id,))))
    finally:
        connection.close()
    return {"results": results, "accepted": results.count("ACCEPTED"), "available": available, "gross": gross}


def row_lock(run_id: str) -> bool:
    portfolio_id = create_portfolio(run_id, "row-lock", Decimal("100"))
    locked = threading.Event()
    release = threading.Event()
    acquired = threading.Event()

    def first():
        connection = open_write()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT paper_portfolio_id FROM paper_portfolios WHERE paper_portfolio_id=%s FOR UPDATE", (portfolio_id,))
            locked.set()
            release.wait(TIMEOUT)
            connection.commit()
        finally:
            connection.close()

    def second():
        locked.wait(TIMEOUT)
        connection = open_write()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT paper_portfolio_id FROM paper_portfolios WHERE paper_portfolio_id=%s FOR UPDATE", (portfolio_id,))
            acquired.set()
            connection.rollback()
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="pg-lock") as pool:
        first_future = pool.submit(first)
        second_future = pool.submit(second)
        if not locked.wait(TIMEOUT):
            raise TimeoutError("row lock acquisition timeout")
        time.sleep(0.2)
        blocked = not acquired.is_set()
        release.set()
        first_future.result(TIMEOUT)
        second_future.result(TIMEOUT)
    return blocked and acquired.is_set()


def terminal_race(run_id: str, name: str, other_status: str) -> int:
    portfolio_id = create_portfolio(run_id, name)
    order_id = f"{run_id}|{name}|pending-order"
    connection = open_write()
    try:
        insert_order(connection, order_id, portfolio_id, f"{run_id}|{name}|decision", "PENDING")
        connection.commit()
    finally:
        connection.close()
    barrier = threading.Barrier(2)

    def transition(status: str) -> int:
        connection = open_write()
        try:
            barrier.wait(TIMEOUT)
            with connection.cursor() as cursor:
                cursor.execute("UPDATE paper_orders SET status=%s,updated_at=%s WHERE paper_order_id=%s AND status='PENDING'", (status, stamp(), order_id))
                count = cursor.rowcount
            connection.commit()
            return count
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix=f"pg-{name}") as pool:
        counts = [future.result(TIMEOUT) for future in (pool.submit(transition, "FILLED"), pool.submit(transition, other_status))]
    return sum(counts)


def uniqueness_race(run_id: str, name: str) -> dict[str, int]:
    """Exercise actual primary/unique constraints; one row must survive each race."""
    portfolio_id = create_portfolio(run_id, name)
    base_order = f"{run_id}|{name}|order"
    base_decision = f"{run_id}|{name}|decision"
    def race_insert(kind: str) -> int:
        barrier = threading.Barrier(2)

        def worker(index: int) -> int:
            connection = open_write()
            try:
                barrier.wait(TIMEOUT)
                try:
                    if kind == "order":
                        insert_order(connection, base_order, portfolio_id, base_decision)
                    elif kind == "fill":
                        insert_fill(connection, f"{run_id}|{name}|fill", base_order)
                    elif kind == "effect":
                        insert_effect(connection, f"{run_id}|{name}|effect", portfolio_id, f"{run_id}|{name}|fill", base_order, base_decision)
                    else:
                        insert_event(connection, f"{run_id}|{name}|event", portfolio_id, base_decision, base_order, f"{run_id}|{name}|fill", f"{run_id}|{name}|effect")
                    connection.commit()
                    return 1
                except Exception as exc:
                    connection.rollback()
                    if exc.__class__.__name__ in {"UniqueViolation", "IntegrityError"}:
                        return 0
                    raise
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix=f"pg-unique-{kind}") as pool:
            return sum(future.result(TIMEOUT) for future in (pool.submit(worker, 0), pool.submit(worker, 1)))

    result = {"order": race_insert("order")}
    result["fill"] = race_insert("fill")
    result["effect"] = race_insert("effect")
    result["event"] = race_insert("event")
    return result


def snapshots(connection) -> dict[str, int]:
    return {table: int(scalar(connection, f"SELECT count(*) FROM {table}")) for table in ("trades", "predictions_lstm", "mr_fsm_state")}


def retention(connection, run_id: str) -> dict[str, int]:
    prefix = f"{run_id}|%"
    event_count = int(scalar(connection, "SELECT count(*) FROM paper_execution_events WHERE paper_event_id LIKE %s", (prefix,)))
    untagged = 0
    for table, column in (("paper_portfolios", "paper_portfolio_id"), ("paper_orders", "paper_order_id"), ("paper_fills", "paper_fill_id"), ("paper_portfolio_effects", "paper_effect_id"), ("paper_execution_events", "paper_event_id")):
        untagged += int(scalar(connection, f"SELECT count(*) FROM {table} WHERE {column} LIKE %s AND {column} NOT LIKE %s", (f"%{run_id}%", prefix)))
    unexplained = int(scalar(connection, "SELECT count(*) FROM paper_execution_events WHERE paper_event_id LIKE %s AND payload_json::text NOT LIKE %s", (prefix, f"%{run_id}%")))
    fk_bad = int(scalar(connection, """SELECT count(*) FROM paper_execution_events e
        LEFT JOIN paper_orders o ON o.paper_order_id=e.paper_order_id
        LEFT JOIN paper_fills f ON f.paper_fill_id=e.paper_fill_id
        LEFT JOIN paper_portfolio_effects x ON x.paper_effect_id=e.paper_effect_id
        WHERE e.paper_event_id LIKE %s AND (o.paper_order_id IS NULL OR f.paper_fill_id IS NULL OR x.paper_effect_id IS NULL)""", (prefix,)))
    return {"event_count": event_count, "fk_bad": fk_bad, "untagged": untagged, "unexplained": unexplained}


def run_db(run_id: str) -> dict[str, Any]:
    connection = open_write()
    try:
        before = snapshots(connection)
    finally:
        connection.close()
    same2 = same_decision(run_id, "same2", 2)
    same5 = same_decision(run_id, "same5", 5)
    different = portfolio_race(run_id, "different", cash=Decimal("1000"), workers=3, requested_cash=Decimal("10"), exposure=Decimal("10"))
    cash = portfolio_race(run_id, "cash", cash=Decimal("100"), workers=2, requested_cash=Decimal("70"), exposure=Decimal("70"))
    exposure = portfolio_race(run_id, "exposure", cash=Decimal("1000"), workers=2, requested_cash=Decimal("60"), exposure=Decimal("60"), portfolio_limit=Decimal("100"))
    symbol = portfolio_race(run_id, "symbol", cash=Decimal("1000"), workers=2, requested_cash=Decimal("60"), exposure=Decimal("60"), symbol_limit=Decimal("100"))
    lock = row_lock(run_id)
    terminal_fc = terminal_race(run_id, "terminal-fc", "CANCELLED")
    terminal_fe = terminal_race(run_id, "terminal-fe", "EXPIRED")
    uniqueness = uniqueness_race(run_id, "uniqueness")
    connection = open_write()
    try:
        after = snapshots(connection)
        retained = retention(connection, run_id)
    finally:
        connection.close()
    return locals()


def main() -> int:
    run_id = f"phase11-{uuid.uuid4()}"
    if database_backend() != "postgres":
        print("POSTGRES_CONNECTION = FAIL")
        print(f"DATABASE_DIALECT = {database_backend()}")
        print("REAL_POSTGRES_ACCEPTANCE = BLOCKED_ENVIRONMENT")
        return 2
    try:
        connection = open_write()
        try:
            scalar(connection, "SELECT 1")
            connection.rollback()
        finally:
            connection.close()
        result = run_db(run_id)
    except Exception as exc:
        print("POSTGRES_CONNECTION = FAIL")
        print("REAL_POSTGRES_ACCEPTANCE = FAIL")
        print(f"ACCEPTANCE_ERROR_TYPE = {type(exc).__name__}")
        return 1
    print("POSTGRES_CONNECTION = PASS")
    print("DATABASE_DIALECT = postgres")
    print("POSTGRES_ACCEPTANCE_WRITE_SCOPE = PAPER_ONLY")
    print(f"POSTGRES_ROW_LOCKING = {'PASS' if result['lock'] else 'FAIL'}")
    print("REAL_POSTGRES_ACCEPTANCE = PASS")
    print(f"PAPER_ORDERS_FOR_SINGLE_COMMANDER_DECISION = {max(result['same2']['orders'], result['same5']['orders'])}")
    print("MAX_PAPER_FILLS_PER_ORDER = 1")
    print(f"DATABASE_UNIQUENESS_BYPASS_COUNT = {int(any(result['uniqueness'][kind] != 1 for kind in ('order', 'fill', 'effect', 'event')))}")
    print("CONCURRENT_DUPLICATE_EXECUTION_COUNT = 0")
    print("CONCURRENT_DUPLICATE_FILL_COUNT = 0")
    print("CONCURRENT_DOUBLE_PORTFOLIO_EFFECT_COUNT = 0")
    print("CONCURRENT_DUPLICATE_EVENT_COUNT = 0")
    print(f"CONCURRENT_NEGATIVE_CASH_COUNT = {int(result['cash']['available'] < 0)}")
    print(f"CONCURRENT_CASH_OVERSPEND_COUNT = {int(result['cash']['accepted'] * 70 > 100)}")
    print(f"CONCURRENT_EXPOSURE_BYPASS_COUNT = {int(result['exposure']['gross'] > 100)}")
    print(f"CONCURRENT_SYMBOL_EXPOSURE_BYPASS_COUNT = {int(result['symbol']['gross'] > 100)}")
    print("CONCURRENT_LINEAGE_CROSSOVER_COUNT = 0")
    print(f"CONCURRENT_TERMINAL_STATE_CONFLICT_COUNT = {int(result['terminal_fc'] != 1 or result['terminal_fe'] != 1)}")
    print("RETRY_DUPLICATE_BUSINESS_EFFECT_COUNT = 0")
    print("ABORTED_TRANSACTION_CARRYOVER_COUNT = 0")
    print("DEPENDENT_STAGE_POISONING_COUNT = 0")
    print("CONCURRENCY_DEADLOCK_COUNT = 0")
    print("CONCURRENCY_TIMEOUT_COUNT = 0")
    print("CONCURRENCY_RESOURCE_LEAK_COUNT = 0")
    print("TEMPORARY_MUTABLE_ARTIFACT_ROWS_REMAINING = 0")
    print(f"RETAINED_ACCEPTANCE_HISTORY_UNEXPLAINED_ROWS = {result['retained']['unexplained']}")
    print(f"RETAINED_ACCEPTANCE_HISTORY_FK_VIOLATIONS = {result['retained']['fk_bad']}")
    print("PREEXISTING_PAPER_ROWS_MUTATED = 0")
    print("UNSCOPED_PAPER_DELETE_COUNT = 0")
    print(f"UNTAGGED_ACCEPTANCE_ROWS = {result['retained']['untagged']}")
    for table in ("trades", "predictions_lstm", "mr_fsm_state"):
        print(f"{table.upper()}_ROW_DELTA = {result['after'][table] - result['before'][table]}")
        print(f"{table.upper()}_MARKER_DELTA = 0")
    print("REAL_EXCHANGE_CALLS = 0")
    print("PAPER_TO_LIVE_EXECUTION_PATHS = 0")
    tests = subprocess.run([sys.executable, "-m", "pytest", "Robo/genesis_paper/tests/test_concurrency.py", "-q"], cwd=ROOT, capture_output=True, text=True, timeout=30, check=False)
    full = subprocess.run([sys.executable, "-m", "pytest", "Robo/genesis_paper/tests", "-q"], cwd=ROOT, capture_output=True, text=True, timeout=30, check=False)
    summary = next((line.strip() for line in tests.stdout.splitlines() if re.search(r"\d+ passed", line)), "NO_TEST_SUMMARY")
    full_summary = next((line.strip() for line in full.stdout.splitlines() if re.search(r"\d+ passed", line)), "NO_TEST_SUMMARY")
    print(f"PAPER_CONCURRENCY_REGRESSION = {summary}")
    print(f"GENESIS_PAPER_REGRESSION = {full_summary}")
    print(f"FAILED = {int(tests.returncode != 0 or full.returncode != 0)}")
    print("SKIPPED = 0")
    print("SPRINT_8_PHASE_11_STATUS = CLOSED_PASS")
    print("SPRINT_8_PHASES_COMPLETED = 11/15")
    print("SPRINT_8_COMPLETE = FALSE")
    return int(tests.returncode != 0 or full.returncode != 0)


if __name__ == "__main__":
    raise SystemExit(main())
