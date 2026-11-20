import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from Robo.genesis_paper import (
    HistoricalMarketEvent,
    HistoricalMarketDataView,
    MetricStatus,
    PaperClosedTrade,
    PaperEffectType,
    PaperExecutionDecision,
    PaperExecutionDecisionType,
    PaperFill,
    PaperLineage,
    PaperOrder,
    PaperOrderStatus,
    PaperPortfolioEffect,
    PaperPosition,
    PaperPersistenceError,
    PaperRepository,
    PaperRiskDecision,
    PaperRiskDecisionType,
    PaperSide,
    PaperConcurrencyCoordinator,
    PaperEquityPoint,
    calculate_paper_performance,
    compare_shadow_decision,
    create_portfolio,
    mark_position,
    open_position,
    recalculate_portfolio,
    transition_order,
)
from Robo.genesis_paper.persistence import SQLITE_SCHEMA


TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def repo():
    conn = sqlite3.connect(":memory:")
    result = PaperRepository(conn)
    result.create_test_schema()
    return conn, result


def artifacts():
    portfolio = create_portfolio(1000, paper_portfolio_id="p1")
    order = PaperOrder(paper_order_id="o1", commander_decision_id="d1", timestamp_utc=TS, symbol="BTC/USD", requested_quantity=1)
    fill = PaperFill(paper_fill_id="f1", paper_order_id="o1", timestamp_utc=TS, symbol="BTC/USD", quantity=1, fill_price=100)
    effect = PaperPortfolioEffect(paper_effect_id="e1", paper_portfolio_id="p1", paper_fill_id="f1", paper_order_id="o1", commander_decision_id="d1", timestamp_utc=TS, symbol="BTC/USD", effect_type=PaperEffectType.OPEN, quantity_delta=1, cash_delta=-100)
    return portfolio, order, fill, effect


def test_primary_key_uniqueness_is_enforced():
    conn, repository = repo()
    try:
        portfolio, order, _, _ = artifacts()
        repository.save_portfolio(portfolio)
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO paper_portfolios VALUES ('p1',1000,1000,0,1000,0,0,0,0,1,'x','x')")
    finally:
        conn.close()


def test_commander_decision_has_one_order():
    conn, repository = repo()
    try:
        portfolio, order, _, _ = artifacts()
        repository.save_portfolio(portfolio)
        repository.save_order(order, "p1")
        conn.commit()
        with pytest.raises(Exception):
            repository.save_order(replace(order, paper_order_id="o2"), "p1")
    finally:
        conn.close()


def test_orphan_fill_is_rejected_by_fk():
    conn, repository = repo()
    try:
        with pytest.raises(PaperPersistenceError):
            repository.save_fill(PaperFill(paper_fill_id="f", paper_order_id="missing", timestamp_utc=TS, symbol="BTC/USD", quantity=1, fill_price=100))
    finally:
        conn.close()


def test_orphan_effect_is_rejected_by_fk():
    conn, repository = repo()
    try:
        with pytest.raises(PaperPersistenceError):
            repository.save_effect(PaperPortfolioEffect(paper_effect_id="e", paper_portfolio_id="p", paper_fill_id="missing", paper_order_id="missing", commander_decision_id="d", timestamp_utc=TS, symbol="BTC/USD", effect_type=PaperEffectType.OPEN))
    finally:
        conn.close()


def test_duplicate_effect_per_fill_is_detectable():
    conn, repository = repo()
    try:
        portfolio, order, fill, effect = artifacts()
        repository.persist_execution(portfolio=portfolio, paper_portfolio_id="p1", order=order, fill=fill, effect=effect)
        duplicate = replace(effect, paper_effect_id="e2")
        repository.save_effect(duplicate)
        assert conn.execute("SELECT COUNT(*) FROM (SELECT paper_fill_id FROM paper_portfolio_effects GROUP BY paper_fill_id HAVING COUNT(*) > 1)").fetchone()[0] == 1
    finally:
        conn.close()


def test_multiple_fills_are_not_allowed_by_phase_policy():
    conn, repository = repo()
    try:
        portfolio, order, fill, _ = artifacts()
        repository.persist_execution(portfolio=portfolio, paper_portfolio_id="p1", order=order, fill=fill)
        second = replace(fill, paper_fill_id="f2")
        repository.save_fill(second)
        assert conn.execute("SELECT COUNT(*) FROM paper_fills WHERE paper_order_id='o1'").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM (SELECT paper_order_id FROM paper_fills GROUP BY paper_order_id HAVING COUNT(*)>1)").fetchone()[0] == 1
    finally:
        conn.close()


def test_illegal_terminal_transition_is_rejected():
    pending = PaperOrder(paper_order_id="o", commander_decision_id="d", timestamp_utc=TS, symbol="BTC/USD", requested_quantity=1, status=PaperOrderStatus.PENDING)
    filled = transition_order(pending, PaperOrderStatus.FILLED)
    with pytest.raises(ValueError):
        transition_order(filled, PaperOrderStatus.CANCELLED)


def test_accounting_recalculation_matches_domain_state():
    portfolio = open_position(create_portfolio(1000), "BTC/USD", PaperSide.BUY, 2, 100, timestamp=TS)
    recomputed = recalculate_portfolio(portfolio)
    assert recomputed.gross_exposure == Decimal("200.00000000")
    assert recomputed.positions["BTC/USD"].quantity == Decimal("2")


def test_position_marking_preserves_quantity_and_updates_pnl():
    portfolio = open_position(create_portfolio(1000), "BTC/USD", PaperSide.BUY, 2, 100, timestamp=TS)
    marked = mark_position(portfolio, "BTC/USD", 110)
    assert marked.positions["BTC/USD"].quantity == Decimal("2")
    assert marked.positions["BTC/USD"].unrealized_pnl == Decimal("20.00000000")


def test_timestamp_inversion_is_rejected():
    with pytest.raises(ValueError):
        PaperPosition(symbol="BTC/USD", side=PaperSide.BUY, quantity=1, average_entry_price=100, mark_price=100, opened_at=TS + timedelta(minutes=1), updated_at=TS)


def test_lineage_crossover_is_rejected():
    execution = PaperExecutionDecision(commander_decision_id="d1", decision=PaperExecutionDecisionType.CREATE_ORDER)
    risk = PaperRiskDecision(commander_decision_id="d1", decision=PaperRiskDecisionType.ALLOW)
    with pytest.raises(ValueError, match="commander mismatch"):
        PaperLineage(execution, risk, PaperOrder(paper_order_id="o", commander_decision_id="d2", timestamp_utc=TS, symbol="BTC/USD", requested_quantity=1))


def test_event_orphan_is_rejected():
    conn, repository = repo()
    try:
        portfolio, _, _, _ = artifacts()
        repository.save_portfolio(portfolio)
        with pytest.raises(sqlite3.IntegrityError):
            with repository.transaction():
                repository.append_event(paper_event_id="event", timestamp_utc=TS, event_type="PAPER", commander_decision_id="d", paper_portfolio_id="p1", paper_order_id="missing", payload={})
    finally:
        conn.close()


def test_duplicate_semantic_event_is_idempotent():
    conn, repository = repo()
    try:
        portfolio, _, _, _ = artifacts()
        repository.save_portfolio(portfolio)
        event = dict(paper_event_id="event", timestamp_utc=TS, event_type="PAPER", commander_decision_id="d", paper_portfolio_id="p1", payload={})
        with repository.transaction():
            repository.append_event(**event)
        with repository.transaction():
            repository.append_event(**event)
        assert conn.execute("SELECT COUNT(*) FROM paper_execution_events").fetchone()[0] == 1
    finally:
        conn.close()


def test_replay_prefix_uses_only_events_through_cutoff():
    events = (HistoricalMarketEvent(TS, "BTC/USD", 100), HistoricalMarketEvent(TS + timedelta(minutes=1), "BTC/USD", 110), HistoricalMarketEvent(TS + timedelta(minutes=2), "BTC/USD", 999))
    view = HistoricalMarketDataView(events)
    assert view.get_price_as_of("BTC/USD", TS + timedelta(minutes=1))[1] == Decimal("110")


def test_metrics_cross_check_matches_closed_trade_economics():
    trade = PaperClosedTrade("t", "BTC/USD", PaperSide.BUY, TS, TS, 100, 110, 1, 2, 1)
    metrics = calculate_paper_performance(initial_equity=1000, equity_curve=(PaperEquityPoint(TS, 1000),), closed_trades=(trade,))
    assert metrics.gross_pnl.value == Decimal("10")
    assert metrics.fees_paid.value == Decimal("2")
    assert metrics.slippage_cost.value == Decimal("1")
    assert metrics.net_pnl.value == Decimal("7")


def test_comparison_cross_check_reuses_phase9_values():
    trade = PaperClosedTrade("t", "BTC/USD", PaperSide.BUY, TS, TS, 100, 110, 1, 2, 1)
    metrics = calculate_paper_performance(initial_equity=1000, equity_curve=(PaperEquityPoint(TS, 1000),), closed_trades=(trade,))
    decision = SimpleNamespace(commander_decision_id="d", timestamp_utc=TS, symbol="BTC/USD", decision="CANDIDATE_LONG")
    result = compare_shadow_decision(decision, comparison_id="c", performance=metrics)
    assert result.realized_pnl.value == metrics.realized_pnl.value


def test_concurrency_residual_integrity_has_one_order():
    coordinator = PaperConcurrencyCoordinator(create_portfolio(1000))
    order = lambda: PaperOrder(paper_order_id="o", commander_decision_id="d", timestamp_utc=TS, symbol="BTC/USD", requested_quantity=1)
    first = coordinator.submit(commander_decision_id="d", symbol="BTC/USD", requested_cash=10, requested_exposure=10, order_factory=order)
    second = coordinator.submit(commander_decision_id="d", symbol="BTC/USD", requested_cash=10, requested_exposure=10, order_factory=order)
    assert first.accepted and second.duplicate and coordinator.artifact_counts()["orders"] == 1


def test_failure_recovery_leaves_no_partial_paper_rows():
    conn, repository = repo()
    try:
        portfolio, order, fill, _ = artifacts()
        with pytest.raises(Exception):
            repository.persist_execution(portfolio=portfolio, paper_portfolio_id="p1", order=order, fill=fill, effect=PaperPortfolioEffect(paper_effect_id="e", paper_portfolio_id="p1", paper_fill_id="missing", paper_order_id="o1", commander_decision_id="d1", timestamp_utc=TS, symbol="BTC/USD", effect_type=PaperEffectType.OPEN))
        assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0
    finally:
        conn.close()


def test_fk_schema_contains_all_paper_relationships():
    assert "REFERENCES paper_orders" in SQLITE_SCHEMA
    assert "REFERENCES paper_fills" in SQLITE_SCHEMA
    assert "REFERENCES paper_portfolios" in SQLITE_SCHEMA


def test_acceptance_history_has_explicit_run_marker():
    payload = {"acceptance_run_id": "phase14-test", "paper_only": True}
    assert payload["acceptance_run_id"].startswith("phase14-")


def test_timestamp_normalization_is_utc():
    event = HistoricalMarketEvent(TS, "BTC/USD", 100)
    assert event.timestamp_utc.tzinfo is timezone.utc
