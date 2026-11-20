"""Migração aditiva e idempotente para o PostgreSQL existente."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import psycopg
from psycopg import sql

from genesis_runtime import database_url

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.getenv("GENESIS_SQLITE_PATH", str(ROOT / "trades.db")))
TABLES = ("trades", "predictions_lstm", "log_events", "mr_fsm_state")


def migrate_table(pg, sqlite_conn, table: str) -> int:
    columns = sqlite_conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    names = [row[1] for row in columns]
    identifiers = sql.SQL(", ").join(sql.Identifier(n) for n in names)
    placeholders = sql.SQL(", ").join(sql.Placeholder() for _ in names)
    statement = sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT DO NOTHING").format(
        sql.Identifier(table), identifiers, placeholders
    )
    cur = sqlite_conn.execute(f'SELECT {", ".join(chr(34)+n.replace(chr(34), chr(34)*2)+chr(34) for n in names)} FROM "{table}"')
    inserted = 0
    with pg.cursor() as out:
        while True:
            rows = cur.fetchmany(2000)
            if not rows:
                break
            out.executemany(statement, [tuple(row) for row in rows])
            inserted += max(out.rowcount, 0)
    return inserted


def ensure_safe_schema(pg) -> None:
    pg.execute("""
        CREATE TABLE IF NOT EXISTS genesis_migration_runs (
            migration_key TEXT PRIMARY KEY, source_path TEXT NOT NULL,
            completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), status TEXT NOT NULL,
            details JSONB NOT NULL DEFAULT '{}'::jsonb
        )
    """)
    for table in TABLES:
        duplicate = pg.execute(sql.SQL(
            "SELECT id, COUNT(*) FROM {} WHERE id IS NOT NULL GROUP BY id HAVING COUNT(*) > 1 LIMIT 1"
        ).format(sql.Identifier(table))).fetchone()
        if duplicate:
            raise RuntimeError(f"IDs duplicados no PostgreSQL em {table}: {duplicate[0]}")
        index_name = f"genesis_{table}_id_uidx"
        pg.execute(sql.SQL("CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} (id)").format(
            sql.Identifier(index_name), sql.Identifier(table)
        ))


def main() -> None:
    backend = os.getenv("GENESIS_DATABASE_BACKEND", "").strip().lower()
    if backend not in {"postgres", "postgresql", "psql"}:
        raise SystemExit("Abortado: defina GENESIS_DATABASE_BACKEND=postgres.")
    if not SOURCE.exists():
        raise SystemExit(f"SQLite não encontrado: {SOURCE}")
    with sqlite3.connect(SOURCE) as sqlite_conn, psycopg.connect(database_url()) as pg:
        pg.execute("SET lock_timeout = '5s'")
        ensure_safe_schema(pg)
        totals = {table: migrate_table(pg, sqlite_conn, table) for table in TABLES}
        pg.execute(
            """INSERT INTO genesis_migration_runs (migration_key, source_path, status, details)
               VALUES (%s, %s, 'completed', %s::jsonb)
               ON CONFLICT (migration_key) DO UPDATE SET completed_at=NOW(), status=EXCLUDED.status, details=EXCLUDED.details""",
            ("sqlite-to-existing-postgres-v1", str(SOURCE), str(totals).replace("'", '"')),
        )
        pg.commit()
    print(f"MIGRATION_OK target=existing-postgres source={SOURCE} totals={totals}")


if __name__ == "__main__":
    main()
