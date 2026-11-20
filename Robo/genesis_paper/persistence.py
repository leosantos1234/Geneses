"""Isolated persistence for the paper domain.

Only ``paper_*`` tables are touched.  The repository accepts a DB-API
connection, making transaction boundaries explicit and testable.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from .contracts import PaperFill, PaperOrder, PaperPortfolio, PaperPosition, PaperSide, PaperOrderStatus, PaperOrderType, PaperExecutionAuthority, _utc
from .lineage import PaperEffectType, PaperPortfolioEffect


class PaperPersistenceError(RuntimeError):
    pass


class DuplicatePaperArtifactError(PaperPersistenceError):
    pass


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_portfolios (paper_portfolio_id TEXT PRIMARY KEY, initial_cash NUMERIC NOT NULL, available_cash NUMERIC NOT NULL, reserved_cash NUMERIC NOT NULL, equity NUMERIC NOT NULL, realized_pnl NUMERIC NOT NULL, unrealized_pnl NUMERIC NOT NULL, gross_exposure NUMERIC NOT NULL, net_exposure NUMERIC NOT NULL, paper_only INTEGER NOT NULL CHECK (paper_only = 1), created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS paper_orders (paper_order_id TEXT PRIMARY KEY, paper_portfolio_id TEXT NOT NULL REFERENCES paper_portfolios(paper_portfolio_id), commander_decision_id TEXT NOT NULL UNIQUE, timestamp_utc TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, order_type TEXT NOT NULL, requested_quantity NUMERIC NOT NULL, requested_price NUMERIC, status TEXT NOT NULL, paper_only INTEGER NOT NULL CHECK (paper_only = 1), execution_authority TEXT NOT NULL CHECK (execution_authority = 'NONE'), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, record_provenance TEXT NOT NULL DEFAULT 'UNKNOWN');
CREATE TABLE IF NOT EXISTS paper_fills (paper_fill_id TEXT PRIMARY KEY, paper_order_id TEXT NOT NULL REFERENCES paper_orders(paper_order_id), timestamp_utc TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, quantity NUMERIC NOT NULL, fill_price NUMERIC NOT NULL, fee NUMERIC NOT NULL, slippage_cost NUMERIC NOT NULL, paper_only INTEGER NOT NULL CHECK (paper_only = 1), record_provenance TEXT NOT NULL DEFAULT 'UNKNOWN');
CREATE TABLE IF NOT EXISTS paper_positions (paper_portfolio_id TEXT NOT NULL REFERENCES paper_portfolios(paper_portfolio_id), symbol TEXT NOT NULL, side TEXT NOT NULL, quantity NUMERIC NOT NULL, average_entry_price NUMERIC NOT NULL, mark_price NUMERIC NOT NULL, unrealized_pnl NUMERIC NOT NULL, realized_pnl NUMERIC NOT NULL, opened_at TEXT NOT NULL, updated_at TEXT NOT NULL, paper_only INTEGER NOT NULL CHECK (paper_only = 1), PRIMARY KEY (paper_portfolio_id, symbol));
CREATE TABLE IF NOT EXISTS paper_portfolio_effects (paper_effect_id TEXT PRIMARY KEY, paper_portfolio_id TEXT NOT NULL REFERENCES paper_portfolios(paper_portfolio_id), paper_fill_id TEXT NOT NULL REFERENCES paper_fills(paper_fill_id), paper_order_id TEXT NOT NULL REFERENCES paper_orders(paper_order_id), commander_decision_id TEXT NOT NULL, timestamp_utc TEXT NOT NULL, symbol TEXT NOT NULL, effect_type TEXT NOT NULL, quantity_delta NUMERIC NOT NULL, cash_delta NUMERIC NOT NULL, realized_pnl_delta NUMERIC NOT NULL, paper_only INTEGER NOT NULL CHECK (paper_only = 1), record_provenance TEXT NOT NULL DEFAULT 'UNKNOWN');
CREATE TABLE IF NOT EXISTS paper_execution_events (paper_event_id TEXT PRIMARY KEY, timestamp_utc TEXT NOT NULL, event_type TEXT NOT NULL, commander_decision_id TEXT NOT NULL, paper_portfolio_id TEXT NOT NULL REFERENCES paper_portfolios(paper_portfolio_id), paper_order_id TEXT REFERENCES paper_orders(paper_order_id), paper_fill_id TEXT REFERENCES paper_fills(paper_fill_id), paper_effect_id TEXT REFERENCES paper_portfolio_effects(paper_effect_id), payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS paper_events_no_update BEFORE UPDATE ON paper_execution_events BEGIN SELECT RAISE(ABORT, 'paper_execution_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS paper_events_no_delete BEFORE DELETE ON paper_execution_events BEGIN SELECT RAISE(ABORT, 'paper_execution_events is append-only'); END;
"""


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _money_read(value: Any) -> Decimal:
    return _dec(value).quantize(Decimal("0.00000001"))


def _timestamp_read(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        # Domain contracts reject naive timestamps; never invent a timezone.
        return _utc(parsed, "timestamp_utc")
    return parsed.astimezone(timezone.utc)


def _placeholders(connection: Any, count: int) -> str:
    return ",".join("%s" if connection.__class__.__module__.startswith("psycopg") else "?" for _ in range(count))


def _execute(connection: Any, query: str, params: tuple[Any, ...] = ()):
    if connection.__class__.__module__.startswith("psycopg"):
        query = query.replace("?", "%s")
    return connection.execute(query, params)


def _json_value(connection: Any, payload: dict[str, Any]) -> Any:
    if connection.__class__.__module__.startswith("psycopg"):
        try:
            from psycopg.types.json import Jsonb
            return Jsonb(payload)
        except ImportError:
            pass
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


class PaperRepository:
    def __init__(self, connection: Any):
        self.connection = connection

    def create_test_schema(self) -> None:
        if not isinstance(self.connection, sqlite3.Connection):
            raise PaperPersistenceError("create_test_schema is for isolated SQLite tests; apply the PostgreSQL migration in production")
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SQLITE_SCHEMA)
        self.connection.commit()

    @contextmanager
    def transaction(self):
        try:
            yield
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def save_portfolio(self, portfolio: PaperPortfolio, *, now: datetime | None = None) -> None:
        stamp = _iso(now or datetime.now(timezone.utc))
        values = (portfolio.paper_portfolio_id, str(portfolio.initial_cash), str(portfolio.available_cash), str(portfolio.reserved_cash), str(portfolio.equity), str(portfolio.realized_pnl), str(portfolio.unrealized_pnl), str(portfolio.gross_exposure), str(portfolio.net_exposure), True, stamp, stamp)
        _execute(self.connection, "INSERT INTO paper_portfolios VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (paper_portfolio_id) DO UPDATE SET available_cash=excluded.available_cash, reserved_cash=excluded.reserved_cash, equity=excluded.equity, realized_pnl=excluded.realized_pnl, unrealized_pnl=excluded.unrealized_pnl, gross_exposure=excluded.gross_exposure, net_exposure=excluded.net_exposure, updated_at=excluded.updated_at", values)

    def save_order(self, order: PaperOrder, paper_portfolio_id: str, *, now: datetime | None = None) -> None:
        stamp = _iso(now or datetime.now(timezone.utc))
        values = (order.paper_order_id, paper_portfolio_id, order.commander_decision_id, _iso(order.timestamp_utc), order.symbol, order.side.value, order.order_type.value, str(order.requested_quantity), None if order.requested_price is None else str(order.requested_price), order.status.value, True, order.execution_authority.value, stamp, stamp, order.record_provenance)
        try:
            existing = _execute(self.connection, "SELECT paper_order_id FROM paper_orders WHERE commander_decision_id=?", (order.commander_decision_id,)).fetchone()
            if existing and existing[0] != order.paper_order_id:
                raise DuplicatePaperArtifactError("commander decision already has a paper order")
            _execute(self.connection, "INSERT INTO paper_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (paper_order_id) DO NOTHING", values)
        except DuplicatePaperArtifactError:
            raise
        except Exception as exc:
            raise DuplicatePaperArtifactError("duplicate paper order") from exc

    def save_fill(self, fill: PaperFill) -> None:
        values = (fill.paper_fill_id, fill.paper_order_id, _iso(fill.timestamp_utc), fill.symbol, fill.side.value, str(fill.quantity), str(fill.fill_price), str(fill.fee), str(fill.slippage_cost), True, fill.record_provenance)
        try:
            _execute(self.connection, "INSERT INTO paper_fills VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (paper_fill_id) DO NOTHING", values)
        except Exception as exc:
            raise PaperPersistenceError("invalid paper fill or duplicate") from exc

    def save_position(self, position: PaperPosition, paper_portfolio_id: str) -> None:
        values = (paper_portfolio_id, position.symbol, position.side.value, str(position.quantity), str(position.average_entry_price), str(position.mark_price), str(position.unrealized_pnl), str(position.realized_pnl), _iso(position.opened_at), _iso(position.updated_at), True)
        _execute(self.connection, "INSERT INTO paper_positions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (paper_portfolio_id, symbol) DO UPDATE SET side=excluded.side, quantity=excluded.quantity, average_entry_price=excluded.average_entry_price, mark_price=excluded.mark_price, unrealized_pnl=excluded.unrealized_pnl, realized_pnl=excluded.realized_pnl, updated_at=excluded.updated_at", values)

    def save_effect(self, effect: PaperPortfolioEffect) -> None:
        values = (effect.paper_effect_id, effect.paper_portfolio_id, effect.paper_fill_id, effect.paper_order_id, effect.commander_decision_id, _iso(effect.timestamp_utc), effect.symbol, effect.effect_type.value, str(effect.quantity_delta), str(effect.cash_delta), str(effect.realized_pnl_delta), True, effect.record_provenance)
        try:
            _execute(self.connection, "INSERT INTO paper_portfolio_effects VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (paper_effect_id) DO NOTHING", values)
        except Exception as exc:
            raise PaperPersistenceError("invalid paper effect or duplicate") from exc

    def append_event(self, *, paper_event_id: str, timestamp_utc: datetime, event_type: str, commander_decision_id: str, paper_portfolio_id: str, payload: dict[str, Any], paper_order_id: str | None = None, paper_fill_id: str | None = None, paper_effect_id: str | None = None) -> None:
        values = (paper_event_id, _iso(timestamp_utc), event_type, commander_decision_id, paper_portfolio_id, paper_order_id, paper_fill_id, paper_effect_id, _json_value(self.connection, payload), _iso(datetime.now(timezone.utc)))
        _execute(self.connection, "INSERT INTO paper_execution_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (paper_event_id) DO NOTHING", values)

    def persist_execution(self, *, portfolio: PaperPortfolio, paper_portfolio_id: str, order: PaperOrder, fill: PaperFill | None = None, position: PaperPosition | None = None, effect: PaperPortfolioEffect | None = None, events: Iterable[dict[str, Any]] = ()) -> None:
        with self.transaction():
            self.save_portfolio(portfolio)
            self.save_order(order, paper_portfolio_id)
            if fill is not None:
                self.save_fill(fill)
            if position is not None:
                self.save_position(position, paper_portfolio_id)
            if effect is not None:
                self.save_effect(effect)
            for event in events:
                self.append_event(**event)

    def _one(self, table: str, key: str, value: str):
        return _execute(self.connection, f"SELECT * FROM {table} WHERE {key}=?", (value,)).fetchone()

    def get_paper_portfolio(self, paper_portfolio_id: str) -> PaperPortfolio | None:
        row = self._one("paper_portfolios", "paper_portfolio_id", paper_portfolio_id)
        if row is None: return None
        return PaperPortfolio(paper_portfolio_id=row[0], initial_cash=_money_read(row[1]), available_cash=_money_read(row[2]), reserved_cash=_money_read(row[3]), equity=_money_read(row[4]), realized_pnl=_money_read(row[5]), unrealized_pnl=_money_read(row[6]), gross_exposure=_money_read(row[7]), net_exposure=_money_read(row[8]), positions={})

    def get_paper_order(self, paper_order_id: str) -> PaperOrder | None:
        row = self._one("paper_orders", "paper_order_id", paper_order_id)
        if row is None: return None
        return PaperOrder(paper_order_id=row[0], commander_decision_id=row[2], timestamp_utc=_timestamp_read(row[3]), symbol=row[4], side=PaperSide(row[5]), order_type=PaperOrderType(row[6]), requested_quantity=_dec(row[7]), requested_price=None if row[8] is None else _dec(row[8]), status=PaperOrderStatus(row[9]), execution_authority=PaperExecutionAuthority(row[11]), record_provenance=row[14] or "UNKNOWN")

    def get_paper_fill(self, paper_fill_id: str) -> PaperFill | None:
        row = self._one("paper_fills", "paper_fill_id", paper_fill_id)
        if row is None: return None
        return PaperFill(paper_fill_id=row[0], paper_order_id=row[1], timestamp_utc=_timestamp_read(row[2]), symbol=row[3], side=PaperSide(row[4]), quantity=_dec(row[5]), fill_price=_dec(row[6]), fee=_dec(row[7]), slippage_cost=_dec(row[8]), record_provenance=row[10] or "UNKNOWN")

    def get_paper_position(self, paper_portfolio_id: str, symbol: str) -> PaperPosition | None:
        row = _execute(self.connection, "SELECT * FROM paper_positions WHERE paper_portfolio_id=? AND symbol=?", (paper_portfolio_id, symbol)).fetchone()
        if row is None: return None
        return PaperPosition(symbol=row[1], side=PaperSide(row[2]), quantity=_dec(row[3]), average_entry_price=_dec(row[4]), mark_price=_dec(row[5]), unrealized_pnl=_dec(row[6]), realized_pnl=_dec(row[7]), opened_at=_timestamp_read(row[8]), updated_at=_timestamp_read(row[9]))

    def get_paper_effect(self, paper_effect_id: str) -> PaperPortfolioEffect | None:
        row = self._one("paper_portfolio_effects", "paper_effect_id", paper_effect_id)
        if row is None: return None
        return PaperPortfolioEffect(paper_effect_id=row[0], paper_portfolio_id=row[1], paper_fill_id=row[2], paper_order_id=row[3], commander_decision_id=row[4], timestamp_utc=_timestamp_read(row[5]), symbol=row[6], effect_type=PaperEffectType(row[7]), quantity_delta=_dec(row[8]), cash_delta=_dec(row[9]), realized_pnl_delta=_dec(row[10]), record_provenance=row[12] or "UNKNOWN")

    def get_by_commander_decision_id(self, commander_decision_id: str) -> dict[str, list[Any]]:
        order_row = _execute(self.connection, "SELECT paper_order_id FROM paper_orders WHERE commander_decision_id=?", (commander_decision_id,)).fetchall()
        orders = [self.get_paper_order(row[0]) for row in order_row]
        fills = [self.get_paper_fill(row[0]) for row in _execute(self.connection, "SELECT f.paper_fill_id FROM paper_fills f JOIN paper_orders o ON o.paper_order_id=f.paper_order_id WHERE o.commander_decision_id=?", (commander_decision_id,)).fetchall()]
        effects = [self.get_paper_effect(row[0]) for row in _execute(self.connection, "SELECT paper_effect_id FROM paper_portfolio_effects WHERE commander_decision_id=?", (commander_decision_id,)).fetchall()]
        return {"orders": orders, "fills": fills, "effects": effects}
