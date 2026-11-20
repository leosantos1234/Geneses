import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, "Robo")

from genesis_paper import (  # noqa: E402
    PaperEffectType,
    PaperFill,
    PaperOrder,
    PaperOrderType,
    PaperPortfolioEffect,
    PaperRepository,
    PaperSide,
    create_portfolio,
)
from genesis_paper.persistence import PaperPersistenceError  # noqa: E402
from genesis_paper.persistence import _timestamp_read  # noqa: E402


TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def repo():
    connection = sqlite3.connect(":memory:")
    repository = PaperRepository(connection)
    repository.create_test_schema()
    yield repository
    connection.close()


def artifacts():
    portfolio = create_portfolio("1000", paper_portfolio_id="portfolio-1")
    order = PaperOrder(paper_order_id="order-1", commander_decision_id="decision-1", timestamp_utc=TS, symbol="BTC/USD", order_type=PaperOrderType.MARKET, requested_quantity="2")
    fill = PaperFill(paper_fill_id="fill-1", paper_order_id="order-1", timestamp_utc=TS, symbol="BTC/USD", side=PaperSide.BUY, quantity="2", fill_price="100")
    effect = PaperPortfolioEffect(paper_effect_id="effect-1", paper_portfolio_id="portfolio-1", paper_fill_id="fill-1", paper_order_id="order-1", commander_decision_id="decision-1", timestamp_utc=TS, symbol="BTC/USD", effect_type=PaperEffectType.OPEN, quantity_delta="2", cash_delta="-200")
    return portfolio, order, fill, effect


def test_paper_schema_and_full_roundtrip(repo):
    portfolio, order, fill, effect = artifacts()
    repo.persist_execution(
        portfolio=portfolio, paper_portfolio_id=portfolio.paper_portfolio_id, order=order, fill=fill, effect=effect,
        events=[{"paper_event_id": "event-1", "timestamp_utc": TS, "event_type": "PAPER_ORDER_FILLED", "commander_decision_id": "decision-1", "paper_portfolio_id": "portfolio-1", "paper_order_id": "order-1", "paper_fill_id": "fill-1", "paper_effect_id": "effect-1", "payload": {"status": "FILLED"}}],
    )
    read_portfolio = repo.get_paper_portfolio("portfolio-1")
    assert read_portfolio.paper_portfolio_id == portfolio.paper_portfolio_id
    assert read_portfolio.initial_cash == portfolio.initial_cash
    assert read_portfolio.available_cash == portfolio.available_cash
    assert read_portfolio.equity == portfolio.equity
    assert repo.get_paper_order("order-1").to_dict() == order.to_dict()
    assert repo.get_paper_fill("fill-1").to_dict() == fill.to_dict()
    assert repo.get_paper_effect("effect-1").to_dict() == effect.to_dict()
    lineage = repo.get_by_commander_decision_id("decision-1")
    assert [item.paper_order_id for item in lineage["orders"]] == ["order-1"]
    assert [item.paper_fill_id for item in lineage["fills"]] == ["fill-1"]
    assert [item.paper_effect_id for item in lineage["effects"]] == ["effect-1"]


def test_idempotent_order_fill_effect_and_event_writes(repo):
    portfolio, order, fill, effect = artifacts()
    kwargs = dict(portfolio=portfolio, paper_portfolio_id="portfolio-1", order=order, fill=fill, effect=effect)
    repo.persist_execution(**kwargs)
    repo.persist_execution(**kwargs)
    for table in ("paper_orders", "paper_fills", "paper_portfolio_effects"):
        assert repo.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
    event = {"paper_event_id": "event-1", "timestamp_utc": TS, "event_type": "PAPER_ORDER_FILLED", "commander_decision_id": "decision-1", "paper_portfolio_id": "portfolio-1", "payload": {"a": 1}}
    with repo.transaction(): repo.append_event(**event)
    with repo.transaction(): repo.append_event(**event)
    assert repo.connection.execute("SELECT COUNT(*) FROM paper_execution_events").fetchone()[0] == 1


def test_duplicate_commander_order_rejected(repo):
    portfolio, order, _, _ = artifacts()
    repo.save_portfolio(portfolio)
    repo.save_order(order, "portfolio-1")
    duplicate = PaperOrder(paper_order_id="order-2", commander_decision_id="decision-1", timestamp_utc=TS, symbol="ETH/USD", requested_quantity=1)
    with pytest.raises(Exception, match="commander decision"):
        repo.save_order(duplicate, "portfolio-1")


def test_invalid_foreign_keys_and_atomic_rollback(repo):
    portfolio, order, fill, effect = artifacts()
    with pytest.raises(PaperPersistenceError):
        repo.persist_execution(portfolio=portfolio, paper_portfolio_id="portfolio-1", order=order, fill=fill, effect=PaperPortfolioEffect(paper_effect_id="bad", paper_portfolio_id="portfolio-1", paper_fill_id="missing", paper_order_id="order-1", commander_decision_id="decision-1", timestamp_utc=TS, symbol="BTC/USD"))
    for table in ("paper_portfolios", "paper_orders", "paper_fills", "paper_portfolio_effects"):
        assert repo.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_event_is_append_only_and_no_live_tables_touched(repo):
    portfolio, _, _, _ = artifacts()
    repo.save_portfolio(portfolio)
    repo.connection.execute("CREATE TABLE trades (id INTEGER)")
    repo.connection.execute("INSERT INTO trades VALUES (1)")
    repo.connection.commit()
    with repo.transaction():
        repo.append_event(paper_event_id="event-1", timestamp_utc=TS, event_type="PAPER_ORDER_CREATED", commander_decision_id="decision-1", paper_portfolio_id="portfolio-1", payload={})
    with pytest.raises(sqlite3.IntegrityError):
        repo.connection.execute("UPDATE paper_execution_events SET event_type='MUTATED' WHERE paper_event_id='event-1'")
    assert repo.connection.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1


def test_migration_is_additive_and_paper_scoped():
    migration = Path("Robo/ops/migrations/006_paper_persistence.sql").read_text(encoding="utf-8")
    assert "DROP TABLE" not in migration.upper()
    assert "ALTER TABLE" not in migration.upper()
    for table in ("paper_portfolios", "paper_orders", "paper_fills", "paper_positions", "paper_portfolio_effects", "paper_execution_events"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in migration
    assert "trades" not in migration.lower()
    assert "predictions_lstm" not in migration.lower()
    assert "mr_fsm_state" not in migration.lower()


def test_postgres_timestamp_readback_normalizes_offset_without_changing_instant():
    utc_value = _timestamp_read(datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc))
    offset_value = _timestamp_read(datetime(2026, 1, 1, 14, 0, tzinfo=timezone(timedelta(hours=2))))
    assert utc_value == offset_value == datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert utc_value.isoformat() == offset_value.isoformat()
    with pytest.raises(ValueError, match="UTC"):
        _timestamp_read(datetime(2026, 1, 1, 12, 0))


def test_acceptance_runner_never_deletes_append_only_events():
    runner = Path("Robo/genesis_paper/tests/run_phase7_postgres_acceptance.py").read_text(encoding="utf-8")
    assert "DELETE FROM paper_execution_events" not in runner
    assert "DELETE FROM paper_portfolio_effects" not in runner
    assert "DELETE FROM paper_fills" not in runner
    assert "DELETE FROM paper_orders" not in runner
    assert "DELETE FROM paper_portfolios" not in runner
    assert "APPEND_ONLY_TEST_EVENTS_IDENTIFIABLE" in runner
    assert "TEMPORARY_MUTABLE_ARTIFACT_ROWS_REMAINING" in runner
    assert "e.paper_effect_id IS NOT NULL" in runner
    assert "e.paper_fill_id IS NOT NULL" in runner
    assert "e.paper_order_id IS NOT NULL" in runner
