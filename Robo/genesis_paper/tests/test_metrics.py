from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from Robo.genesis_paper import (
    HistoricalMarketEvent,
    MetricStatus,
    PaperClosedTrade,
    PaperEquityPoint,
    PaperSide,
    calculate_paper_performance,
    create_portfolio,
    recalculate_portfolio,
)


T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)


def trade(*, trade_id="t1", side=PaperSide.BUY, entry="100", exit="110", fees="2", slippage="1", paper_only=True):
    return PaperClosedTrade(trade_id, "BTC/USD", side, T0, T0 + timedelta(minutes=2), entry, exit, "2", fees, slippage, paper_only)


def curve(*values):
    return tuple(PaperEquityPoint(T0 + timedelta(minutes=index), value) for index, value in enumerate(values))


def test_metrics_formulae_and_counts_are_deterministic():
    trades = (trade(), trade(trade_id="t2", entry="100", exit="90", fees="0", slippage="0"), trade(trade_id="t3", entry="100", exit="100", fees="0", slippage="0"))
    result = calculate_paper_performance(initial_equity="1000", equity_curve=curve("1000", "1040", "980"), closed_trades=trades)
    assert result.total_return_pct.value == Decimal("-2")
    assert result.gross_pnl.value == Decimal("0")
    assert result.net_pnl.value == Decimal("-3")
    assert result.realized_pnl.value == Decimal("-3")
    assert (result.winning_trade_count, result.losing_trade_count, result.breakeven_trade_count) == (1, 1, 1)
    assert result.win_rate.value == Decimal("50")
    assert result.loss_rate.value == Decimal("50")
    assert result.expectancy.value == Decimal("-1")
    assert result.fees_paid.value == Decimal("2")
    assert result.slippage_cost.value == Decimal("1")
    assert result.turnover.value == Decimal("1200")


def test_gross_and_net_are_not_double_counted():
    result = calculate_paper_performance(initial_equity=100, equity_curve=curve(100), closed_trades=(trade(entry=100, exit=101, fees=1, slippage=1),))
    assert result.gross_pnl.value == Decimal("2")
    assert result.net_pnl.value == Decimal("0")
    assert result.realized_pnl.value == Decimal("0")


def test_profit_factor_uses_gross_profit_and_loss():
    result = calculate_paper_performance(initial_equity=1000, equity_curve=curve(1000), closed_trades=(trade(), trade(trade_id="t2", entry=100, exit=90, fees=0, slippage=0)))
    assert result.profit_factor.value == Decimal("1")


def test_profit_factor_no_losses_is_positive_infinity():
    result = calculate_paper_performance(initial_equity=1000, equity_curve=curve(1000), closed_trades=(trade(),))
    assert result.profit_factor.value == Decimal("Infinity")


def test_empty_sample_uses_not_applicable_and_no_sample():
    result = calculate_paper_performance(initial_equity=1000, equity_curve=(), closed_trades=())
    assert result.sample_size_status == "INSUFFICIENT_SAMPLE"
    assert result.win_rate.status is MetricStatus.NOT_APPLICABLE
    assert result.mfe.status is MetricStatus.NOT_APPLICABLE


def test_unrealized_and_exposure_are_data_unavailable_without_final_portfolio():
    result = calculate_paper_performance(initial_equity=1000, equity_curve=curve(1000), closed_trades=())
    assert result.unrealized_pnl.status is MetricStatus.DATA_NOT_AVAILABLE
    assert result.gross_exposure.status is MetricStatus.DATA_NOT_AVAILABLE
    assert result.net_exposure.status is MetricStatus.DATA_NOT_AVAILABLE


def test_final_portfolio_metrics_are_read_without_mutation():
    portfolio = create_portfolio(1000, paper_portfolio_id="p1")
    portfolio.unrealized_pnl = Decimal("12")
    portfolio.gross_exposure = Decimal("50")
    portfolio.net_exposure = Decimal("-50")
    before = portfolio.to_dict()
    result = calculate_paper_performance(initial_equity=1000, equity_curve=curve(1000), closed_trades=(), final_portfolio=portfolio)
    assert result.unrealized_pnl.value == Decimal("12")
    assert result.gross_exposure.value == Decimal("50")
    assert result.net_exposure.value == Decimal("-50")
    assert portfolio.to_dict() == before


def test_drawdown_is_negative_and_peak_based():
    result = calculate_paper_performance(initial_equity=1000, equity_curve=curve(1000, 1100, 990, 1200, 1140), closed_trades=())
    assert result.max_drawdown.value == Decimal("-110")
    assert result.max_drawdown_pct.value == Decimal("-10")


def test_average_and_median_trade_return_are_net_returns():
    result = calculate_paper_performance(initial_equity=1000, equity_curve=curve(1000), closed_trades=(trade(entry=100, exit=110, fees=0, slippage=0), trade(trade_id="t2", entry=100, exit=105, fees=0, slippage=0), trade(trade_id="t3", entry=100, exit=90, fees=0, slippage=0)))
    assert result.average_trade_return.value == Decimal("1.666666666666666666666666667")
    assert result.median_trade_return.value == Decimal("5.00")


def test_short_trade_direction_is_reversed():
    result = calculate_paper_performance(initial_equity=1000, equity_curve=curve(1000), closed_trades=(trade(side=PaperSide.SELL, entry=100, exit=90, fees=0, slippage=0),))
    assert result.gross_pnl.value == Decimal("20")


def test_mfe_mae_are_average_excursions():
    events = (HistoricalMarketEvent(T0, "BTC/USD", 100), HistoricalMarketEvent(T0 + timedelta(minutes=1), "BTC/USD", 115), HistoricalMarketEvent(T0 + timedelta(minutes=2), "BTC/USD", 95), HistoricalMarketEvent(T0 + timedelta(minutes=3), "BTC/USD", 1000))
    result = calculate_paper_performance(initial_equity=1000, equity_curve=curve(1000), closed_trades=(trade(fees=0, slippage=0),), market_events=events)
    assert result.mfe.value == Decimal("30")
    assert result.mae.value == Decimal("-10")


def test_mfe_mae_exclude_post_close_prices():
    events = (HistoricalMarketEvent(T0, "BTC/USD", 100), HistoricalMarketEvent(T0 + timedelta(minutes=2), "BTC/USD", 110), HistoricalMarketEvent(T0 + timedelta(minutes=3), "BTC/USD", 1000))
    result = calculate_paper_performance(initial_equity=1000, equity_curve=curve(1000), closed_trades=(trade(fees=0, slippage=0),), market_events=events)
    assert result.mfe.value == Decimal("20")
    assert result.mae.value == Decimal("0")


def test_missing_market_observation_is_data_not_available():
    result = calculate_paper_performance(initial_equity=1000, equity_curve=curve(1000), closed_trades=(trade(),), market_events=())
    assert result.mfe.status is MetricStatus.DATA_NOT_AVAILABLE
    assert result.mae.status is MetricStatus.DATA_NOT_AVAILABLE


def test_sample_policy_becomes_sufficient_at_thirty():
    trades = tuple(trade(trade_id=f"t{i}", entry=100, exit=101, fees=0, slippage=0) for i in range(30))
    result = calculate_paper_performance(initial_equity=1000, equity_curve=curve(1000), closed_trades=trades)
    assert result.sample_size_status == "SUFFICIENT_SAMPLE"


def test_equity_curve_must_be_chronological():
    with pytest.raises(ValueError, match="chronological"):
        calculate_paper_performance(initial_equity=1000, equity_curve=(PaperEquityPoint(T0 + timedelta(minutes=1), 1000), PaperEquityPoint(T0, 1000)), closed_trades=())


def test_trade_interval_and_numeric_validation():
    with pytest.raises(ValueError):
        PaperClosedTrade("t", "BTC/USD", PaperSide.BUY, T0 + timedelta(minutes=1), T0, 100, 101, 1)
    with pytest.raises(ValueError):
        PaperClosedTrade("t", "BTC/USD", PaperSide.BUY, T0, T0, 100, 101, 1, fees=-1)


def test_serialization_exposes_status_without_secrets_or_runtime_objects():
    result = calculate_paper_performance(initial_equity=1000, equity_curve=curve(1000), closed_trades=())
    payload = result.to_dict()
    assert payload["total_return_pct"] == {"status": "VALUE", "value": "0"}
    assert payload["win_rate"] == {"status": "NOT_APPLICABLE", "value": None}


def test_duplicate_trade_ids_are_rejected_by_canonical_input_boundary():
    # Duplicate economic records would double count; reject them before metric calculation.
    # This check is intentionally explicit in the test contract.
    duplicate = (trade(), trade())
    with pytest.raises(ValueError, match="duplicate"):
        calculate_paper_performance(initial_equity=1000, equity_curve=curve(1000), closed_trades=duplicate)


def test_metrics_are_repeatable_for_identical_paper_history():
    inputs = dict(initial_equity=1000, equity_curve=curve(1000, 1020), closed_trades=(trade(fees=0, slippage=0),))
    assert calculate_paper_performance(**inputs).to_dict() == calculate_paper_performance(**inputs).to_dict()


def test_prefix_metrics_ignore_events_after_prefix_close():
    prefix_events = (HistoricalMarketEvent(T0, "BTC/USD", 100), HistoricalMarketEvent(T0 + timedelta(minutes=2), "BTC/USD", 110))
    future_events = prefix_events + (HistoricalMarketEvent(T0 + timedelta(minutes=3), "BTC/USD", 10000),)
    args = dict(initial_equity=1000, equity_curve=curve(1000), closed_trades=(trade(fees=0, slippage=0),))
    prefix = calculate_paper_performance(**args, market_events=prefix_events)
    extended = calculate_paper_performance(**args, market_events=future_events)
    assert prefix.to_dict() == extended.to_dict()


def test_paper_only_boundary_rejects_non_paper_trade():
    with pytest.raises(ValueError, match="paper_only"):
        trade(paper_only=False)


def test_short_mfe_mae_use_short_direction():
    events = (HistoricalMarketEvent(T0, "BTC/USD", 100), HistoricalMarketEvent(T0 + timedelta(minutes=1), "BTC/USD", 90), HistoricalMarketEvent(T0 + timedelta(minutes=2), "BTC/USD", 110))
    result = calculate_paper_performance(initial_equity=1000, equity_curve=curve(1000), closed_trades=(trade(side=PaperSide.SELL, entry=100, exit=90, fees=0, slippage=0),), market_events=events)
    assert result.mfe.value == Decimal("20")
    assert result.mae.value == Decimal("-20")


def test_limited_sample_status_is_explicit():
    result = calculate_paper_performance(initial_equity=1000, equity_curve=curve(1000), closed_trades=(trade(fees=0, slippage=0),))
    assert result.sample_size_status == "LIMITED_SAMPLE"


def test_metric_statuses_are_distinct():
    empty = calculate_paper_performance(initial_equity=1000, equity_curve=curve(1000), closed_trades=())
    missing = calculate_paper_performance(initial_equity=1000, equity_curve=curve(1000), closed_trades=(trade(),), market_events=())
    assert empty.win_rate.status is MetricStatus.NOT_APPLICABLE
    assert missing.mfe.status is MetricStatus.DATA_NOT_AVAILABLE


def test_no_live_or_network_dependency_is_present_in_metrics_contract():
    from Robo.genesis_paper import metrics
    assert not hasattr(metrics, "requests")
    assert not hasattr(metrics, "connect_operational_database")
