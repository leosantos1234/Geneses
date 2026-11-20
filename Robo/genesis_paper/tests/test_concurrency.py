from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from Robo.genesis_paper import (
    HistoricalMarketEvent,
    HistoricalReplayEngine,
    PaperConcurrencyCoordinator,
    PaperEffectType,
    PaperExecutionDecision,
    PaperExecutionDecisionType,
    PaperFill,
    PaperOrder,
    PaperOrderStatus,
    PaperOrderType,
    PaperPortfolioEffect,
    PaperRiskConfig,
    PaperRiskDecision,
    PaperRiskDecisionType,
    PaperSide,
    PaperSimulatorConfig,
    ReplayOrderIntent,
    compare_shadow_decision,
    create_portfolio,
)


TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_coordinator(cash=1000, **limits):
    return PaperConcurrencyCoordinator(create_portfolio(cash, paper_portfolio_id="p1"), **limits)


def submitter(coordinator, decision_id, *, symbol="BTC/USD", exposure=10, cash=None, fill=True, side=PaperSide.BUY):
    def make_order():
        return PaperOrder(paper_order_id=f"o-{decision_id}", commander_decision_id=decision_id, timestamp_utc=TS, symbol=symbol, side=side, order_type=PaperOrderType.MARKET, requested_quantity="1")

    def make_fill(order):
        return PaperFill(paper_fill_id=f"f-{decision_id}", paper_order_id=order.paper_order_id, timestamp_utc=TS, symbol=symbol, side=side, quantity="1", fill_price="100")

    def make_effect(order, paper_fill):
        return PaperPortfolioEffect(paper_effect_id=f"e-{decision_id}", paper_portfolio_id="p1", paper_fill_id=paper_fill.paper_fill_id, paper_order_id=order.paper_order_id, commander_decision_id=decision_id, timestamp_utc=TS, symbol=symbol, effect_type=PaperEffectType.OPEN, quantity_delta="1", cash_delta="-100")

    return coordinator.submit(commander_decision_id=decision_id, symbol=symbol, requested_cash=exposure if cash is None else cash, requested_exposure=exposure, order_factory=make_order, fill_factory=make_fill if fill else None, effect_factory=make_effect if fill else None)


def parallel(functions, timeout=3):
    with ThreadPoolExecutor(max_workers=len(functions), thread_name_prefix="paper-test") as pool:
        futures = [pool.submit(function) for function in functions]
        return [future.result(timeout=timeout) for future in futures]


def test_same_decision_concurrent_x2_has_one_order_and_fill():
    coordinator = make_coordinator()
    results = parallel([lambda: submitter(coordinator, "d1"), lambda: submitter(coordinator, "d1")])
    assert sum(result.accepted for result in results) == 1
    assert sum(result.duplicate for result in results) == 1
    assert len({result.order.paper_order_id for result in results}) == 1
    assert len({result.fill.paper_fill_id for result in results}) == 1
    assert coordinator.artifact_counts() == {"orders": 1, "fills": 1, "effects": 1, "decisions": 1}


def test_same_decision_concurrent_x5_retries_converge_to_existing_result():
    coordinator = make_coordinator()
    results = parallel([lambda: submitter(coordinator, "d1") for _ in range(5)])
    assert sum(result.accepted for result in results) == 1
    assert sum(result.duplicate for result in results) == 4
    assert all(result.order.paper_order_id == "o-d1" for result in results)
    assert coordinator.artifact_counts()["orders"] == 1


def test_different_decisions_concurrent_x3_have_isolated_lineage():
    coordinator = make_coordinator()
    results = parallel([lambda decision=decision: submitter(coordinator, decision) for decision in ("d1", "d2", "d3")])
    assert all(result.accepted for result in results)
    assert len({result.order.paper_order_id for result in results}) == 3
    assert len({result.fill.paper_fill_id for result in results}) == 3
    assert len({result.effect.paper_effect_id for result in results}) == 3


def test_duplicate_order_prevention_is_deterministic():
    coordinator = make_coordinator()
    first = submitter(coordinator, "d1")
    retry = submitter(coordinator, "d1")
    assert retry.duplicate is True
    assert retry.reason == "DUPLICATE_EXECUTION"
    assert retry.order.paper_order_id == first.order.paper_order_id


def test_duplicate_fill_and_effect_prevention():
    coordinator = make_coordinator()
    results = parallel([lambda: submitter(coordinator, "d1") for _ in range(5)])
    assert len({result.fill.paper_fill_id for result in results}) == 1
    assert len({result.effect.paper_effect_id for result in results}) == 1


def test_duplicate_event_prevention():
    coordinator = make_coordinator()
    results = parallel([lambda: submitter(coordinator, "d1") for _ in range(5)])
    assert len({result.paper_event_id for result in results}) == 1


def test_cash_race_allows_only_one_of_two_unaffordable_combination():
    coordinator = make_coordinator(100)
    results = parallel([lambda decision=decision: submitter(coordinator, decision, exposure=70) for decision in ("a", "b")])
    assert sum(result.accepted for result in results) == 1
    assert coordinator.portfolio.available_cash >= 0
    assert coordinator.portfolio.available_cash == 30


def test_portfolio_exposure_race_is_serialized():
    coordinator = make_coordinator(max_portfolio_exposure=100)
    results = parallel([lambda decision=decision: submitter(coordinator, decision, exposure=60) for decision in ("a", "b")])
    assert sum(result.accepted for result in results) == 1
    assert coordinator.portfolio.gross_exposure <= 100


def test_symbol_exposure_race_is_serialized():
    coordinator = make_coordinator(max_symbol_exposure=100)
    results = parallel([lambda decision=decision: submitter(coordinator, decision, symbol="ETH/USD", exposure=60) for decision in ("a", "b")])
    assert sum(result.accepted for result in results) == 1


def test_different_symbols_can_pass_independent_symbol_limit():
    coordinator = make_coordinator(max_symbol_exposure=100)
    results = parallel([lambda decision=decision, symbol=symbol: submitter(coordinator, decision, symbol=symbol, exposure=60) for decision, symbol in (("a", "BTC/USD"), ("b", "ETH/USD"))])
    assert sum(result.accepted for result in results) == 2


def test_pending_terminal_race_has_one_winner():
    coordinator = make_coordinator()
    submitter(coordinator, "d1", fill=False)
    coordinator._orders["o-d1"] = replace_order = PaperOrder(paper_order_id="o-d1", commander_decision_id="d1", timestamp_utc=TS, symbol="BTC/USD", side=PaperSide.BUY, order_type=PaperOrderType.MARKET, requested_quantity="1", status=PaperOrderStatus.PENDING)
    results = parallel([lambda: coordinator.transition_terminal("o-d1", PaperOrderStatus.FILLED), lambda: coordinator.transition_terminal("o-d1", PaperOrderStatus.CANCELLED)])
    assert sum(result.won for result in results) == 1
    assert coordinator._orders["o-d1"].status in {PaperOrderStatus.FILLED, PaperOrderStatus.CANCELLED}


def test_pending_expired_race_has_one_winner():
    coordinator = make_coordinator()
    submitter(coordinator, "d1", fill=False)
    coordinator._orders["o-d1"] = PaperOrder(paper_order_id="o-d1", commander_decision_id="d1", timestamp_utc=TS, symbol="BTC/USD", side=PaperSide.BUY, order_type=PaperOrderType.MARKET, requested_quantity="1", status=PaperOrderStatus.PENDING)
    results = parallel([lambda: coordinator.transition_terminal("o-d1", PaperOrderStatus.FILLED), lambda: coordinator.transition_terminal("o-d1", PaperOrderStatus.EXPIRED)])
    assert sum(result.won for result in results) == 1


def test_risk_limit_bypass_is_impossible():
    coordinator = make_coordinator(100, max_portfolio_exposure=100)
    results = parallel([lambda decision=decision: submitter(coordinator, decision, exposure=101, cash=10) for decision in ("a", "b")])
    assert all(result.reason == "MAX_PORTFOLIO_EXPOSURE_EXCEEDED" for result in results)


def test_gate_like_cash_block_is_impossible():
    coordinator = make_coordinator(10)
    result = submitter(coordinator, "d1", exposure=11)
    assert result.accepted is False and result.reason == "INSUFFICIENT_CASH"


def test_retry_after_rejection_is_idempotent():
    coordinator = make_coordinator(10)
    first = submitter(coordinator, "d1", exposure=11)
    second = submitter(coordinator, "d1", exposure=1)
    assert first.accepted is False and second.duplicate is True
    assert second.reason == "DUPLICATE_EXECUTION"


def test_replay_sessions_are_isolated():
    events = (HistoricalMarketEvent(TS, "BTC/USD", 100), HistoricalMarketEvent(TS.replace(minute=1), "BTC/USD", 105))
    config = PaperSimulatorConfig("0", "0", "1000", "1000", False, True)
    risk_config = PaperRiskConfig(max_position_notional="1000", max_portfolio_exposure="1000", max_symbol_exposure="1000", allow_short=False, allow_fractional_quantity=True)
    def run():
        engine = HistoricalReplayEngine(events=events, initial_portfolio=create_portfolio(1000), simulator_config=config, risk_config=risk_config)
        return engine.run((ReplayOrderIntent(TS, "d", "BTC/USD", PaperSide.BUY, 1),), replay_id="r")
    results = parallel([run, run])
    assert results[0].economic_digest() == results[1].economic_digest()
    assert results[0] is not results[1]


def test_comparison_sessions_are_isolated():
    def run(decision):
        return compare_shadow_decision(SimpleNamespace(commander_decision_id=decision, timestamp_utc=TS, symbol="BTC/USD", decision="NO_ACTION"), comparison_id=f"c-{decision}")
    results = parallel([lambda: run("d1"), lambda: run("d2")])
    assert {result.commander_decision_id for result in results} == {"d1", "d2"}
    assert {result.comparison_id for result in results} == {"c-d1", "c-d2"}


def test_ids_are_unique_under_concurrent_different_decisions():
    coordinator = make_coordinator()
    results = parallel([lambda decision=decision: submitter(coordinator, decision) for decision in (f"d{i}" for i in range(20))])
    assert len({result.order.paper_order_id for result in results}) == 20
    assert len({result.fill.paper_fill_id for result in results}) == 20
    assert len({result.effect.paper_effect_id for result in results}) == 20
    assert len({result.paper_event_id for result in results}) == 20


def test_order_status_transition_retries_are_safe():
    coordinator = make_coordinator()
    submitter(coordinator, "d1", fill=False)
    coordinator._orders["o-d1"] = PaperOrder(paper_order_id="o-d1", commander_decision_id="d1", timestamp_utc=TS, symbol="BTC/USD", side=PaperSide.BUY, order_type=PaperOrderType.MARKET, requested_quantity="1", status=PaperOrderStatus.PENDING)
    first = coordinator.transition_terminal("o-d1", PaperOrderStatus.FILLED)
    retry = coordinator.transition_terminal("o-d1", PaperOrderStatus.FILLED)
    assert first.won is True and retry.won is False


def test_timeout_boundary_is_bounded():
    coordinator = make_coordinator()
    results = parallel([lambda: submitter(coordinator, f"d{i}") for i in range(10)], timeout=3)
    assert len(results) == 10


def test_paper_only_invariant_is_enforced():
    portfolio = create_portfolio(1000)
    portfolio.paper_only = False
    with pytest.raises(ValueError, match="paper-only"):
        PaperConcurrencyCoordinator(portfolio)
