import sys
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, "Robo")

from genesis_paper import (  # noqa: E402
    HistoricalMarketDataView,
    HistoricalMarketEvent,
    HistoricalReplayEngine,
    PaperOrderType,
    PaperRiskConfig,
    PaperSide,
    PaperSimulatorConfig,
    ReplayDataStatus,
    ReplayOrderIntent,
    create_portfolio,
)


T1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T2 = T1 + timedelta(minutes=1)
T3 = T2 + timedelta(minutes=1)


def events():
    return [
        HistoricalMarketEvent(T1, "BTC/USD", "100", low="99", high="101"),
        HistoricalMarketEvent(T2, "BTC/USD", "90", low="89", high="91"),
        HistoricalMarketEvent(T3, "BTC/USD", "110", low="109", high="111"),
    ]


def engine(data=None):
    return HistoricalReplayEngine(
        events=data or events(),
        initial_portfolio=create_portfolio("1000", paper_portfolio_id="replay-portfolio"),
        simulator_config=PaperSimulatorConfig(fee_bps="0", slippage_bps="0", max_position_notional="10000", max_portfolio_exposure="20000", allow_short=True, allow_fractional_quantity=True),
        risk_config=PaperRiskConfig(max_position_notional="10000", max_portfolio_exposure="20000", max_symbol_exposure="10000", allow_short=True, allow_fractional_quantity=True),
    )


def test_single_market_replay_and_paper_state():
    result = engine().run([ReplayOrderIntent(T1, "decision-1", "BTC/USD", PaperSide.BUY, "1")])
    assert result.event_count == 3
    assert result.fill_count == 1
    assert result.steps[0].fills == 1
    assert result.final_portfolio.paper_only is True


def test_unsorted_events_are_rejected_and_timestamps_normalized():
    offset = HistoricalMarketEvent(datetime(2026, 1, 1, 2, 0, tzinfo=timezone(timedelta(hours=2))), "BTC/USD", 100)
    normalized = HistoricalMarketEvent(T1, "BTC/USD", 100)
    assert offset.timestamp_utc == normalized.timestamp_utc
    with pytest.raises(ValueError, match="non-decreasing"):
        HistoricalMarketDataView([HistoricalMarketEvent(T2, "BTC/USD", 100), HistoricalMarketEvent(T1, "BTC/USD", 100)])
    with pytest.raises(ValueError, match="UTC"):
        HistoricalMarketEvent(datetime(2026, 1, 1), "BTC/USD", 100)


def test_as_of_view_never_returns_future_data():
    view = HistoricalMarketDataView(events())
    status, price = view.get_price_as_of("BTC/USD", T1)
    assert status is ReplayDataStatus.AVAILABLE and price == 100
    status, price = view.get_price_as_of("ETH/USD", T3)
    assert status is ReplayDataStatus.DATA_NOT_AVAILABLE and price is None
    assert all(item.timestamp_utc <= T2 for item in view.get_window_as_of("BTC/USD", T2, 10))


def test_limit_buy_waits_for_later_current_event():
    result = engine().run([ReplayOrderIntent(T1, "limit-1", "BTC/USD", PaperSide.BUY, "1", PaperOrderType.LIMIT, "90")])
    assert result.steps[0].order_statuses == ("PENDING",)
    assert result.steps[1].fills == 1
    assert result.fill_count == 1


def test_limit_sell_uses_only_current_event():
    result = engine().run([ReplayOrderIntent(T1, "limit-sell", "BTC/USD", PaperSide.SELL, "1", PaperOrderType.LIMIT, "110")])
    assert result.steps[0].order_statuses == ("PENDING",)
    assert result.steps[2].fills == 1


def test_future_spike_does_not_change_prior_prefix():
    intent = [ReplayOrderIntent(T1, "prefix-1", "BTC/USD", PaperSide.BUY, "1")]
    full = engine().run(intent, replay_id="same")
    prefix = engine(events()[:1]).run(intent, replay_id="same")
    assert full.steps[0].portfolio.to_dict() == prefix.steps[0].portfolio.to_dict()
    assert full.steps[0].fills == prefix.steps[0].fills


def test_repeatability_and_isolated_sessions():
    intent = [ReplayOrderIntent(T1, "repeat-1", "BTC/USD", PaperSide.BUY, "1")]
    first = engine().run(intent, replay_id="fixed")
    second = engine().run(intent, replay_id="fixed")
    assert first.economic_digest() == second.economic_digest()
    other = engine().run(intent, replay_id="other")
    assert first.final_portfolio.to_dict() == other.final_portfolio.to_dict()


def test_no_fabricated_price_or_future_limit_fill():
    view = HistoricalMarketDataView(events())
    status, price = view.get_price_as_of("SOL/USD", T1)
    assert status is ReplayDataStatus.DATA_NOT_AVAILABLE and price is None
    result = engine().run([ReplayOrderIntent(T1, "future-limit", "BTC/USD", PaperSide.BUY, "1", PaperOrderType.LIMIT, "80")])
    assert all(step.fills == 0 for step in result.steps)
