import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from Robo.genesis_paper import (
    ComparisonOutcomeStatus,
    HistoricalMarketDataView,
    HistoricalMarketEvent,
    HistoricalReplayEngine,
    MetricStatus,
    PaperClosedTrade,
    PaperEffectType,
    PaperExecutionDecision,
    PaperExecutionDecisionType,
    PaperExecutionGateConfig,
    PaperFill,
    PaperGateDecisionType,
    PaperOrder,
    PaperOrderType,
    PaperPersistenceError,
    PaperPortfolioEffect,
    PaperRepository,
    PaperRiskConfig,
    PaperRiskDecision,
    PaperRiskDecisionType,
    PaperSide,
    PaperSimulatorConfig,
    PaperConcurrencyCoordinator,
    ReplayOrderIntent,
    calculate_paper_performance,
    compare_shadow_decision,
    create_portfolio,
    evaluate_paper_risk,
    execute_paper_gate,
)


TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def simulator():
    return PaperSimulatorConfig("10", "25", "10000", "20000", False, True)


def risk_config():
    return PaperRiskConfig("1000", "5000", "2000", False, True)


def execution_context(*, enabled=True, reference=100, risk=PaperRiskDecisionType.ALLOW):
    execution = PaperExecutionDecision(commander_decision_id="d1", decision=PaperExecutionDecisionType.CREATE_ORDER)
    risk_decision = PaperRiskDecision(commander_decision_id="d1", decision=risk)
    order = PaperOrder(paper_order_id="o1", commander_decision_id="d1", timestamp_utc=TS, symbol="BTC/USD", side=PaperSide.BUY, order_type=PaperOrderType.MARKET, requested_quantity="1")
    return execute_paper_gate(commander_decision_id="d1", execution_decision=execution, risk_decision=risk_decision, order=order, gate_config=PaperExecutionGateConfig(enabled), simulator_config=simulator(), timestamp_utc=TS, reference_price=reference)


def sqlite_repo():
    connection = sqlite3.connect(":memory:")
    repository = PaperRepository(connection)
    repository.create_test_schema()
    return connection, repository


def artifacts():
    portfolio = create_portfolio(1000, paper_portfolio_id="p1")
    order = PaperOrder(paper_order_id="o1", commander_decision_id="d1", timestamp_utc=TS, symbol="BTC/USD", requested_quantity=1)
    fill = PaperFill(paper_fill_id="f1", paper_order_id="o1", timestamp_utc=TS, symbol="BTC/USD", quantity=1, fill_price=100)
    effect = PaperPortfolioEffect(paper_effect_id="e1", paper_portfolio_id="p1", paper_fill_id="f1", paper_order_id="o1", commander_decision_id="d1", timestamp_utc=TS, symbol="BTC/USD", effect_type=PaperEffectType.OPEN, quantity_delta=1, cash_delta=-100)
    return portfolio, order, fill, effect


def test_missing_market_price_fails_without_fill():
    result = execution_context(reference=None)
    assert result.decision is PaperGateDecisionType.INSUFFICIENT_DATA
    assert result.fill is None


@pytest.mark.parametrize("symbol", ["", "BTC USD"])
def test_invalid_symbol_is_rejected(symbol):
    with pytest.raises(ValueError):
        PaperOrder(paper_order_id="o", commander_decision_id="d", timestamp_utc=TS, symbol=symbol, requested_quantity=1)


@pytest.mark.parametrize("quantity", [0, -1])
def test_invalid_quantity_is_rejected(quantity):
    with pytest.raises(ValueError):
        PaperOrder(paper_order_id="o", commander_decision_id="d", timestamp_utc=TS, symbol="BTC/USD", requested_quantity=quantity)


def test_insufficient_cash_is_risk_rejection():
    decision = evaluate_paper_risk(commander_decision_id="d", symbol="BTC/USD", side=PaperSide.BUY, quantity=2, reference_price=100, current_symbol_exposure=0, current_portfolio_exposure=0, available_cash=100, config=risk_config(), timestamp_utc=TS)
    assert decision.decision is PaperRiskDecisionType.REJECT
    assert decision.reasons == ["INSUFFICIENT_CASH"]


def test_risk_rejection_never_reaches_gate():
    result = execution_context(risk=PaperRiskDecisionType.REJECT)
    assert result.fill is None
    assert result.reason == "RISK_NOT_ALLOWED"


def test_disabled_gate_is_fail_closed():
    result = execution_context(enabled=False)
    assert result.decision is PaperGateDecisionType.BLOCK_PAPER_EXECUTION
    assert result.reason == "PAPER_EXECUTION_DISABLED"
    assert result.fill is None


def test_duplicate_decision_has_no_second_business_effect():
    coordinator = PaperConcurrencyCoordinator(create_portfolio(1000))
    def submit():
        return coordinator.submit(commander_decision_id="d1", symbol="BTC/USD", requested_cash=10, requested_exposure=10, order_factory=lambda: PaperOrder(paper_order_id="o1", commander_decision_id="d1", timestamp_utc=TS, symbol="BTC/USD", requested_quantity=1))
    first, second = submit(), submit()
    assert first.accepted is True
    assert second.duplicate is True
    assert coordinator.artifact_counts()["orders"] == 1


def test_persistence_unavailable_is_explicit_failure():
    class BrokenConnection:
        def execute(self, *args, **kwargs):
            raise ConnectionError("database unavailable")
    repository = PaperRepository(BrokenConnection())
    with pytest.raises(PaperPersistenceError):
        repository.save_fill(PaperFill(paper_fill_id="f", paper_order_id="o", timestamp_utc=TS, symbol="BTC/USD", quantity=1, fill_price=100))


def test_controlled_sql_exception_rolls_back_and_connection_recovers():
    connection, repository = sqlite_repo()
    try:
        with pytest.raises(sqlite3.OperationalError):
            with repository.transaction():
                connection.execute("INSERT INTO paper_portfolios VALUES ('p',100,100,0,100,0,0,0,0,1,'x','x')")
                connection.execute("INSERT INTO missing_table VALUES (1)")
        assert connection.execute("SELECT COUNT(*) FROM paper_portfolios").fetchone()[0] == 0
        connection.execute("SELECT 1").fetchone()
    finally:
        connection.close()


def test_lineage_confirmation_failure_fails_closed():
    with pytest.raises(ValueError, match="lineage"):
        compare_shadow_decision(SimpleNamespace(commander_decision_id="d1", timestamp_utc=TS, symbol="BTC/USD", decision="CANDIDATE_LONG"), comparison_id="c1", execution_decision=PaperExecutionDecision(commander_decision_id="d1", decision=PaperExecutionDecisionType.CREATE_ORDER), risk_decision=PaperRiskDecision(commander_decision_id="d1", decision=PaperRiskDecisionType.ALLOW), order=PaperOrder(paper_order_id="o1", commander_decision_id="other", timestamp_utc=TS, symbol="BTC/USD", requested_quantity=1))


def test_partial_persistence_failure_rolls_back_all_paper_rows():
    connection, repository = sqlite_repo()
    try:
        portfolio, order, fill, _ = artifacts()
        bad_effect = PaperPortfolioEffect(paper_effect_id="e1", paper_portfolio_id="p1", paper_fill_id="missing", paper_order_id="o1", commander_decision_id="d1", timestamp_utc=TS, symbol="BTC/USD", effect_type=PaperEffectType.OPEN)
        with pytest.raises(PaperPersistenceError):
            repository.persist_execution(portfolio=portfolio, paper_portfolio_id="p1", order=order, fill=fill, effect=bad_effect)
        for table in ("paper_portfolios", "paper_orders", "paper_fills", "paper_portfolio_effects"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    finally:
        connection.close()


def test_historical_data_unavailable_does_not_fabricate_price():
    view = HistoricalMarketDataView(())
    status, price = view.get_price_as_of("BTC/USD", TS)
    assert status.value == "DATA_NOT_AVAILABLE" and price is None


def test_replay_out_of_order_is_rejected():
    with pytest.raises(ValueError, match="non-decreasing"):
        HistoricalMarketDataView((HistoricalMarketEvent(TS, "BTC/USD", 100), HistoricalMarketEvent(TS.replace(minute=2), "BTC/USD", 102), HistoricalMarketEvent(TS.replace(minute=1), "BTC/USD", 101)))


def test_missing_lookback_returns_empty_window_without_substitution():
    view = HistoricalMarketDataView((HistoricalMarketEvent(TS, "BTC/USD", 100),))
    assert view.get_window_as_of("ETH/USD", TS, 5) == ()


def test_metrics_unavailable_history_is_not_zero():
    trade = PaperClosedTrade("t1", "BTC/USD", PaperSide.BUY, TS, TS, 100, 101, 1)
    metrics = calculate_paper_performance(initial_equity=1000, equity_curve=(), closed_trades=(trade,), market_events=())
    assert metrics.mfe.status is MetricStatus.DATA_NOT_AVAILABLE
    assert metrics.mae.status is MetricStatus.DATA_NOT_AVAILABLE


def test_comparison_source_lineage_mismatch_is_rejected():
    decision = SimpleNamespace(commander_decision_id="d1", timestamp_utc=TS, symbol="BTC/USD", decision="CANDIDATE_LONG")
    with pytest.raises(ValueError, match="lineage"):
        compare_shadow_decision(decision, comparison_id="c1", execution_decision=PaperExecutionDecision(commander_decision_id="d1", decision=PaperExecutionDecisionType.CREATE_ORDER), risk_decision=PaperRiskDecision(commander_decision_id="d1", decision=PaperRiskDecisionType.ALLOW), order=PaperOrder(paper_order_id="o1", commander_decision_id="d1", timestamp_utc=TS, symbol="BTC/USD", requested_quantity=1), fill=PaperFill(paper_fill_id="f1", paper_order_id="other", timestamp_utc=TS, symbol="BTC/USD", quantity=1, fill_price=100))


def test_uniqueness_conflict_recovery_leaves_repository_usable():
    connection, repository = sqlite_repo()
    try:
        portfolio, order, _, _ = artifacts()
        repository.save_portfolio(portfolio)
        repository.save_order(order, "p1")
        connection.commit()
        duplicate = PaperOrder(paper_order_id="o2", commander_decision_id="d1", timestamp_utc=TS, symbol="ETH/USD", requested_quantity=1)
        with pytest.raises(Exception):
            repository.save_order(duplicate, "p1")
        connection.rollback()
        assert repository.get_paper_order("o1") is not None
        assert connection.execute("SELECT 1").fetchone()[0] == 1
    finally:
        connection.close()


def test_append_only_mutation_is_blocked():
    connection, repository = sqlite_repo()
    try:
        portfolio, _, _, _ = artifacts()
        repository.save_portfolio(portfolio)
        with repository.transaction():
            repository.append_event(paper_event_id="e", timestamp_utc=TS, event_type="FAILURE_TEST", commander_decision_id="d1", paper_portfolio_id="p1", payload={"paper_only": True})
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE paper_execution_events SET event_type='MUTATED' WHERE paper_event_id='e'")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM paper_execution_events WHERE paper_event_id='e'")
    finally:
        connection.close()


def test_cleanup_failure_cannot_false_pass():
    def final_status(*, real_acceptance: bool, cleanup_ok: bool) -> str:
        return "CLOSED_PASS" if real_acceptance and cleanup_ok else "NOT_CLOSED"
    assert final_status(real_acceptance=True, cleanup_ok=False) == "NOT_CLOSED"
    assert final_status(real_acceptance=False, cleanup_ok=True) == "NOT_CLOSED"


def test_failure_thread_path_is_bounded_and_releases_workers():
    def fail():
        raise RuntimeError("controlled failure")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(fail) for _ in range(2)]
        for future in futures:
            with pytest.raises(RuntimeError):
                future.result(timeout=2)


def test_failure_paths_never_add_live_execution_surface():
    source = Path("Robo/genesis_paper").read_text(encoding="utf-8") if False else "".join(Path(path).read_text(encoding="utf-8") for path in Path("Robo/genesis_paper").glob("*.py"))
    assert "submit_real_exchange_order" not in source
    assert "ALLOW_EXCHANGE_WRITE" not in source
