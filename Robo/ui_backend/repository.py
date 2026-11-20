from __future__ import annotations

import sqlite3
import os
import uuid
from datetime import datetime, timedelta
from typing import Any

from banco_trade.db_trades import DB_PATH
from genesis_runtime import database_backend, database_path, database_url

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover
    psycopg = None
    dict_row = None


def _get_conn(*, read_only: bool = False) -> sqlite3.Connection:
    resolved_url = database_url()
    if database_backend() == "postgres":
        if psycopg is None:
            raise RuntimeError("Instale psycopg para usar PostgreSQL")
        options = "-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000" if read_only else None
        return psycopg.connect(resolved_url, row_factory=dict_row, options=options)
    conn = sqlite3.connect(database_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _execute(conn, query: str, args=()):
    if database_backend() == "postgres" or conn.__class__.__module__.startswith("psycopg"):
        query = query.replace("?", "%s")
    return conn.execute(query, args)


def _iso_since(days: int) -> str:
    return (datetime.utcnow() - timedelta(days=max(1, days))).isoformat()


def _dict_row(row: sqlite3.Row | None) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def ensure_ui_users_table() -> None:
    """Create the UI users table when the API first needs authentication.

    This is intentionally additive and idempotent so a fresh Render database
    does not depend on a separate, manually-run migration command.
    """
    conn = _get_conn()
    try:
        _execute(conn, """
            CREATE TABLE IF NOT EXISTS ui_users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'viewer',
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK (role IN ('admin', 'viewer'))
            )
        """)
        _execute(conn, "CREATE UNIQUE INDEX IF NOT EXISTS ui_users_email_lower_idx ON ui_users (LOWER(email))")
        conn.commit()
    finally:
        conn.close()


def get_ui_user(email: str) -> dict[str, Any] | None:
    conn = _get_conn()
    try:
        row = _execute(
            conn,
            "SELECT id, email, password_hash, role, is_active FROM ui_users WHERE LOWER(email) = LOWER(?) LIMIT 1",
            [email.strip()],
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def upsert_ui_admin(email: str, password_hash: str) -> dict[str, Any]:
    """Keep the environment-configured administrator available after deploys."""
    conn = _get_conn()
    try:
        existing = _execute(
            conn,
            "SELECT id FROM ui_users WHERE LOWER(email) = LOWER(?) LIMIT 1",
            [email.strip()],
        ).fetchone()
        if existing:
            _execute(
                conn,
                """UPDATE ui_users
                   SET email = ?, password_hash = ?, role = 'admin', is_active = TRUE,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                [email.strip().lower(), password_hash, existing["id"]],
            )
            user_id = existing["id"]
        else:
            user_id = str(uuid.uuid4())
            _execute(
                conn,
                """INSERT INTO ui_users (id, email, password_hash, role, is_active)
                   VALUES (?, ?, ?, 'admin', TRUE)""",
                [user_id, email.strip().lower(), password_hash],
            )
        conn.commit()
        return {"id": user_id, "email": email.strip().lower(), "role": "admin"}
    finally:
        conn.close()


def create_ui_user(email: str, password_hash: str, role: str = "viewer") -> dict[str, Any]:
    conn = _get_conn()
    try:
        user_id = str(uuid.uuid4())
        _execute(
            conn,
            """INSERT INTO ui_users (id, email, password_hash, role, is_active)
               VALUES (?, ?, ?, ?, TRUE)""",
            [user_id, email.strip().lower(), password_hash, role],
        )
        conn.commit()
        return {"id": user_id, "email": email.strip().lower(), "role": role, "is_active": True}
    finally:
        conn.close()


def list_ui_users() -> list[dict[str, Any]]:
    conn = _get_conn(read_only=True)
    try:
        rows = _execute(
            conn,
            "SELECT id, email, role, is_active, created_at, updated_at FROM ui_users ORDER BY email ASC",
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def ensure_ui_worker_states_table() -> None:
    conn = _get_conn()
    try:
        _execute(conn, """
            CREATE TABLE IF NOT EXISTS ui_worker_states (
                asset TEXT PRIMARY KEY,
                strategy TEXT NOT NULL DEFAULT 'SWING_TREND_FOLLOWING',
                running BOOLEAN NOT NULL DEFAULT FALSE,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    finally:
        conn.close()


def set_ui_worker_states(assets: list[str], *, running: bool, strategy: str | None = None) -> list[dict[str, Any]]:
    ensure_ui_worker_states_table()
    conn = _get_conn()
    try:
        for asset in assets:
            if strategy:
                _execute(conn, """INSERT INTO ui_worker_states (asset, strategy, running)
                    VALUES (?, ?, ?) ON CONFLICT (asset) DO UPDATE SET strategy = EXCLUDED.strategy,
                    running = EXCLUDED.running, updated_at = CURRENT_TIMESTAMP""", [asset, strategy, running])
            else:
                _execute(conn, """INSERT INTO ui_worker_states (asset, strategy, running)
                    VALUES (?, 'SWING_TREND_FOLLOWING', ?) ON CONFLICT (asset) DO UPDATE SET
                    running = EXCLUDED.running, updated_at = CURRENT_TIMESTAMP""", [asset, running])
        conn.commit()
        return list_ui_worker_states()
    finally:
        conn.close()


def list_ui_worker_states() -> list[dict[str, Any]]:
    conn = _get_conn(read_only=True)
    try:
        rows = _execute(conn, "SELECT asset, strategy, running, updated_at FROM ui_worker_states ORDER BY asset ASC").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


_OPEN_TRADE_SQL = "LOWER(TRIM(COALESCE(status, ''))) IN ('aberto', 'open', 'pending', 'partially_filled') AND COALESCE(quantity, 0) > 0"


def _build_where(symbol: str | None, status: str | None) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    args: list[Any] = []
    if symbol:
        clauses.append("symbol = ?")
        args.append(symbol.upper())
    if status:
        s = status.lower()
        if s == "open":
            clauses.append(_OPEN_TRADE_SQL)
        elif s == "closed":
            clauses.append("status = 'fechado'")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, args


def get_summary(days: int = 7, symbol: str | None = None) -> dict[str, Any]:
    conn = _get_conn()
    try:
        since = _iso_since(days)
        where_symbol = "AND symbol = ?" if symbol else ""
        args = [since] + ([symbol.upper()] if symbol else [])

        total_row = _execute(conn, 
            f"""
            SELECT
              COUNT(*) AS total_trades,
              COALESCE(SUM(CASE WHEN profit_loss > 0 THEN 1 ELSE 0 END), 0) AS wins,
              COALESCE(SUM(CASE WHEN profit_loss < 0 THEN 1 ELSE 0 END), 0) AS losses,
              COALESCE(SUM(profit_loss), 0) AS pnl_total
            FROM trades
            WHERE timestamp >= ?
              AND status = 'fechado'
              {where_symbol}
            """,
            args,
        ).fetchone()
        total = _dict_row(total_row)
        closed = int(total.get("total_trades") or 0)
        wins = int(total.get("wins") or 0)
        losses = int(total.get("losses") or 0)
        win_rate = (wins / closed * 100.0) if closed > 0 else 0.0

        open_row = _execute(conn, 
            f"""
            SELECT COUNT(*) AS open_positions
            FROM trades
            WHERE {_OPEN_TRADE_SQL}
              {where_symbol.replace("AND ", "AND ")}
            """,
            ([symbol.upper()] if symbol else []),
        ).fetchone()
        open_positions = int(open_row["open_positions"]) if open_row else 0

        return {
            "window_days": days,
            "symbol": symbol.upper() if symbol else None,
            "total_trades": closed,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(win_rate, 2),
            "pnl_total": float(total.get("pnl_total") or 0.0),
            "open_positions": open_positions,
        }
    finally:
        conn.close()


def list_open_positions(symbol: str | None = None, limit: int = 100, *, read_only: bool = False) -> list[dict[str, Any]]:
    conn = _get_conn(read_only=read_only)
    try:
        where, args = _build_where(symbol=symbol, status="open")
        rows = _execute(conn, 
            f"""
            SELECT
              trade_id, timestamp, symbol, side, entry_price, stop_loss, take_profit,
              trailing_stop, strategy, status
            FROM trades
            {where}
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            [*args, max(1, min(limit, 500))],
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_trades(
    *,
    symbol: str | None = None,
    status: str | None = None,
    strategy: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int = 100,
    offset: int = 0,
    read_only: bool = False,
) -> list[dict[str, Any]]:
    conn = _get_conn(read_only=read_only)
    try:
        where, args = _build_where(symbol=symbol, status=status)
        extra = []
        if strategy:
            extra.append("strategy = ?")
            args.append(strategy)
        if start_time:
            extra.append("timestamp >= ?")
            args.append(start_time)
        if end_time:
            extra.append("timestamp <= ?")
            args.append(end_time)
        if extra:
            where = f"{where} {'AND ' if where else 'WHERE '}{' AND '.join(extra)}"
        rows = _execute(conn, 
            f"""
            SELECT
              trade_id, timestamp, symbol, side, entry_price, exit_price, profit_loss,
              stop_loss, take_profit, trailing_stop, strategy, decision_source,
              regime, tendencia, status
            FROM trades
            {where}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
            """,
            [*args, max(1, min(limit, 500)), max(0, offset)],
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def count_trades(*, symbol: str | None = None, status: str | None = None, strategy: str | None = None, start_time: str | None = None, end_time: str | None = None, read_only: bool = False) -> int:
    conn = _get_conn(read_only=read_only)
    try:
        where, args = _build_where(symbol=symbol, status=status)
        extra = []
        if strategy:
            extra.append("strategy = ?")
            args.append(strategy)
        if start_time:
            extra.append("timestamp >= ?")
            args.append(start_time)
        if end_time:
            extra.append("timestamp <= ?")
            args.append(end_time)
        if extra:
            where = f"{where} {'AND ' if where else 'WHERE '}{' AND '.join(extra)}"
        row = _execute(conn, 
            f"SELECT COUNT(*) AS total FROM trades {where}",
            args,
        ).fetchone()
        return int(row["total"]) if row else 0
    finally:
        conn.close()


def get_trade(trade_id: str, *, read_only: bool = False) -> dict[str, Any] | None:
    conn = _get_conn(read_only=read_only)
    try:
        row = _execute(conn, 
            "SELECT * FROM trades WHERE trade_id = ? LIMIT 1",
            [trade_id],
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_filters() -> dict[str, list[str]]:
    conn = _get_conn()
    try:
        symbols = [
            r["symbol"]
            for r in _execute(conn, 
                "SELECT DISTINCT symbol FROM trades WHERE symbol IS NOT NULL ORDER BY symbol ASC"
            ).fetchall()
        ]
        strategies = [
            r["strategy"]
            for r in _execute(conn, 
                "SELECT DISTINCT strategy FROM trades WHERE strategy IS NOT NULL ORDER BY strategy ASC"
            ).fetchall()
        ]
        return {
            "symbols": symbols,
            "strategies": strategies,
            "statuses": ["open", "closed", "cancelled"],
        }
    finally:
        conn.close()


def get_open_position_stats(symbol: str | None = None, *, read_only: bool = False) -> dict[str, float | int]:
    """Leitura DB-only das posições persistidas; não consulta exchange nem grava estado."""
    conn = _get_conn(read_only=read_only)
    try:
        where, args = _build_where(symbol=symbol, status="open")
        row = _execute(
            conn,
            f"""SELECT COUNT(*) AS count,
                       COALESCE(SUM(COALESCE(entry_price, 0) * COALESCE(quantity, 0)), 0) AS exposure
                FROM trades {where}""",
            args,
        ).fetchone()
        return {"count": int(row["count"] or 0), "exposure": float(row["exposure"] or 0.0)}
    finally:
        conn.close()


def _table_exists(conn, table_name: str) -> bool:
    """Check optional observability tables without making the dashboard fail closed."""
    row = _execute(
        conn,
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = ? ) AS present",
        [table_name],
    ).fetchone()
    return bool(row and row["present"])


def get_control_center(*, days: int = 30) -> dict[str, Any]:
    """Read-only aggregate for the Genesis operational control center.

    Optional telemetry tables are queried independently so a partially migrated
    installation remains visible instead of returning fake operational status.
    """
    conn = _get_conn(read_only=True)
    try:
        since = _iso_since(days)
        assets = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]
        asset_rows: dict[str, dict[str, Any]] = {a: {"symbol": a, "regime": None, "regime_label": "Sem leitura", "confidence": None, "last_seen": None, "raw_breakouts": 0, "blocked": 0, "candidates": 0} for a in assets}

        if _table_exists(conn, "regime_evaluations"):
            rows = _execute(conn, """
                SELECT DISTINCT ON (symbol) symbol, regime, winning_rule, candle_close_time
                FROM regime_evaluations
                WHERE candle_close_time >= ? AND symbol = ANY(?)
                ORDER BY symbol, candle_close_time DESC
            """, [since, assets]).fetchall()
            labels = {1: "Tendência forte", 2: "Tendência moderada", 3: "Lateral", 4: "Caótico"}
            for row in rows:
                item = asset_rows.get(row["symbol"])
                if item:
                    regime = int(row["regime"]) if row["regime"] is not None else None
                    item.update({"regime": regime, "regime_label": labels.get(regime, "Desconhecido"), "last_seen": row["candle_close_time"].isoformat() if row["candle_close_time"] else None})

        blocker_rows: list[dict[str, Any]] = []
        recent_events: list[dict[str, Any]] = []
        if _table_exists(conn, "breakout_evaluations"):
            for row in _execute(conn, """
                SELECT symbol, primary_blocker, COUNT(*) AS total,
                       SUM(CASE WHEN raw_breakout THEN 1 ELSE 0 END) AS raw_breakouts,
                       SUM(CASE WHEN final_state IN ('CANDIDATE', 'ELIGIBLE', 'APPROVED') THEN 1 ELSE 0 END) AS candidates
                FROM breakout_evaluations
                WHERE timestamp_utc >= ? AND symbol = ANY(?)
                GROUP BY symbol, primary_blocker
                ORDER BY total DESC
                LIMIT 100
            """, [since, assets]).fetchall():
                symbol = row["symbol"]
                item = asset_rows.get(symbol)
                if item:
                    item["raw_breakouts"] += int(row["raw_breakouts"] or 0)
                    item["blocked"] += int(row["total"] or 0)
                    item["candidates"] += int(row["candidates"] or 0)
                blocker_rows.append({"symbol": symbol, "blocker": row["primary_blocker"] or "sem motivo", "total": int(row["total"] or 0)})
            recent_events = [dict(row) for row in _execute(conn, """
                SELECT timestamp_utc, symbol, direction_candidate, final_state, primary_blocker, raw_breakout
                FROM breakout_evaluations
                WHERE timestamp_utc >= ? AND symbol = ANY(?)
                ORDER BY timestamp_utc DESC LIMIT 12
            """, [since, assets]).fetchall()]

        shadow = {"events": 0, "divergences": 0, "paper_trades": 0, "pnl": 0.0, "policies": []}
        if _table_exists(conn, "shadow_decision_telemetry"):
            row = _execute(conn, """
                SELECT COUNT(*) AS events,
                       COALESCE(SUM(CASE WHEN impulse_reset_divergence THEN 1 ELSE 0 END), 0) AS divergences,
                       COALESCE(SUM(CASE WHEN shadow_trade_created THEN 1 ELSE 0 END), 0) AS paper_trades,
                       COALESCE(SUM(shadow_pnl), 0) AS pnl
                FROM shadow_decision_telemetry
                WHERE candle_open_time >= ? AND symbol = ANY(?)
            """, [since, assets]).fetchone()
            if row:
                shadow.update({"events": int(row["events"] or 0), "divergences": int(row["divergences"] or 0), "paper_trades": int(row["paper_trades"] or 0), "pnl": float(row["pnl"] or 0)})
            shadow["policies"] = [dict(r) for r in _execute(conn, """
                SELECT shadow_policy AS policy, COUNT(*) AS events,
                       COALESCE(SUM(CASE WHEN shadow_trade_created THEN 1 ELSE 0 END), 0) AS trades,
                       COALESCE(SUM(shadow_pnl), 0) AS pnl
                FROM shadow_decision_telemetry
                WHERE candle_open_time >= ? AND symbol = ANY(?)
                GROUP BY shadow_policy ORDER BY events DESC
            """, [since, assets]).fetchall()]

        runtime = {}
        try:
            from genesis_runtime import runtime_snapshot
            runtime = runtime_snapshot()
        except Exception as exc:
            runtime = {"error": type(exc).__name__}
        return {
            "window_days": days,
            "runtime": runtime,
            "assets": list(asset_rows.values()),
            "blockers": blocker_rows[:10],
            "recent_events": recent_events,
            "shadow": shadow,
            "generated_at": datetime.utcnow().isoformat(),
        }
    finally:
        conn.close()
