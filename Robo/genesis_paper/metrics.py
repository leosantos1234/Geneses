"""Deterministic paper/replay performance metrics.

This module is deliberately independent from live execution.  A closed trade
is the canonical economic unit: fees and slippage belong to that trade and
are counted exactly once.  Market observations are filtered through the
trade's close timestamp, so MFE/MAE cannot use future data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from statistics import median
from typing import Any, Iterable

from .contracts import PaperPortfolio, PaperSide, _decimal, _required_id, _symbol, _utc
from .replay import HistoricalMarketEvent


class MetricStatus(Enum):
    VALUE = "VALUE"
    DATA_NOT_AVAILABLE = "DATA_NOT_AVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class MetricValue:
    status: MetricStatus
    value: Decimal | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, MetricStatus):
            raise ValueError("invalid metric status")
        if self.status is MetricStatus.VALUE:
            if self.value is None or (not self.value.is_finite() and self.value != Decimal("Infinity")):
                raise ValueError("VALUE metric requires finite value or positive infinity")
        elif self.value is not None:
            raise ValueError("non-VALUE metric must not contain a value")

    @classmethod
    def of(cls, value: Decimal | str | int | float) -> "MetricValue":
        parsed = Decimal(str(value))
        if parsed.is_nan() or (parsed.is_infinite() and parsed != Decimal("Infinity")):
            raise ValueError("metric value must be finite or positive infinity")
        return cls(MetricStatus.VALUE, parsed)

    @classmethod
    def unavailable(cls) -> "MetricValue":
        return cls(MetricStatus.DATA_NOT_AVAILABLE)

    @classmethod
    def not_applicable(cls) -> "MetricValue":
        return cls(MetricStatus.NOT_APPLICABLE)

    def to_dict(self) -> dict[str, str | None]:
        return {"status": self.status.value, "value": None if self.value is None else format(self.value, "f")}


def _timestamp(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class PaperClosedTrade:
    trade_id: str
    symbol: str
    side: PaperSide
    opened_at: datetime
    closed_at: datetime
    entry_price: Decimal | str | int | float
    exit_price: Decimal | str | int | float
    quantity: Decimal | str | int | float
    fees: Decimal | str | int | float = Decimal("0")
    slippage_cost: Decimal | str | int | float = Decimal("0")
    paper_only: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "trade_id", _required_id(self.trade_id, "trade_id"))
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        if not isinstance(self.side, PaperSide):
            raise ValueError("side invalid")
        if self.paper_only is not True:
            raise ValueError("paper_only must be True")
        opened = _timestamp(self.opened_at, "opened_at")
        closed = _timestamp(self.closed_at, "closed_at")
        if closed < opened:
            raise ValueError("closed_at must not precede opened_at")
        object.__setattr__(self, "opened_at", opened)
        object.__setattr__(self, "closed_at", closed)
        for name, positive, non_negative in (("entry_price", True, False), ("exit_price", True, False), ("quantity", True, False), ("fees", False, True), ("slippage_cost", False, True)):
            object.__setattr__(self, name, _decimal(getattr(self, name), name, positive=positive, non_negative=non_negative))

    @property
    def entry_notional(self) -> Decimal:
        return self.entry_price * self.quantity

    @property
    def exit_notional(self) -> Decimal:
        return self.exit_price * self.quantity

    @property
    def gross_pnl(self) -> Decimal:
        direction = Decimal("1") if self.side is PaperSide.BUY else Decimal("-1")
        return (self.exit_price - self.entry_price) * self.quantity * direction

    @property
    def net_pnl(self) -> Decimal:
        return self.gross_pnl - self.fees - self.slippage_cost

    @property
    def return_pct(self) -> Decimal:
        return self.net_pnl / self.entry_notional * Decimal("100")


@dataclass(frozen=True)
class PaperEquityPoint:
    timestamp_utc: datetime
    equity: Decimal | str | int | float

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp_utc", _timestamp(self.timestamp_utc, "timestamp_utc"))
        object.__setattr__(self, "equity", _decimal(self.equity, "equity", non_negative=True))


@dataclass(frozen=True)
class PaperPerformanceMetrics:
    total_return_pct: MetricValue
    gross_pnl: MetricValue
    net_pnl: MetricValue
    realized_pnl: MetricValue
    unrealized_pnl: MetricValue
    win_rate: MetricValue
    loss_rate: MetricValue
    profit_factor: MetricValue
    expectancy: MetricValue
    max_drawdown: MetricValue
    max_drawdown_pct: MetricValue
    average_trade_return: MetricValue
    median_trade_return: MetricValue
    mfe: MetricValue
    mae: MetricValue
    gross_exposure: MetricValue
    net_exposure: MetricValue
    turnover: MetricValue
    fees_paid: MetricValue
    slippage_cost: MetricValue
    closed_trade_count: int
    winning_trade_count: int
    losing_trade_count: int
    breakeven_trade_count: int
    sample_size_status: str

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in self.__dict__.items():
            result[name] = value.to_dict() if isinstance(value, MetricValue) else value
        return result


def _sum(values: Iterable[Decimal]) -> Decimal:
    return sum(values, Decimal("0"))


def _value_or_na(value: Decimal | None) -> MetricValue:
    return MetricValue.not_applicable() if value is None else MetricValue.of(value)


def calculate_paper_performance(
    *,
    initial_equity: Decimal | str | int | float,
    equity_curve: Iterable[PaperEquityPoint],
    closed_trades: Iterable[PaperClosedTrade],
    final_portfolio: PaperPortfolio | None = None,
    market_events: Iterable[HistoricalMarketEvent] = (),
) -> PaperPerformanceMetrics:
    """Calculate a deterministic snapshot from paper/replay artifacts only."""
    initial = _decimal(initial_equity, "initial_equity", positive=True)
    points = tuple(equity_curve)
    if any(not isinstance(point, PaperEquityPoint) for point in points):
        raise ValueError("equity_curve must contain PaperEquityPoint values")
    if any(b.timestamp_utc < a.timestamp_utc for a, b in zip(points, points[1:])):
        raise ValueError("equity_curve must be chronological")
    final_equity = points[-1].equity if points else initial
    total_return = (final_equity - initial) / initial * Decimal("100")

    trades = tuple(closed_trades)
    if any(not isinstance(trade, PaperClosedTrade) for trade in trades):
        raise ValueError("closed_trades must contain PaperClosedTrade values")
    trade_ids = [trade.trade_id for trade in trades]
    if len(set(trade_ids)) != len(trade_ids):
        raise ValueError("duplicate trade_id")
    gross = _sum(trade.gross_pnl for trade in trades)
    fees = _sum(trade.fees for trade in trades)
    slippage = _sum(trade.slippage_cost for trade in trades)
    net = gross - fees - slippage
    winners = tuple(trade for trade in trades if trade.net_pnl > 0)
    losers = tuple(trade for trade in trades if trade.net_pnl < 0)
    breakeven = tuple(trade for trade in trades if trade.net_pnl == 0)
    decisive_count = len(winners) + len(losers)
    win_rate = MetricValue.of(Decimal(len(winners)) / decisive_count * 100) if decisive_count else MetricValue.not_applicable()
    loss_rate = MetricValue.of(Decimal(len(losers)) / decisive_count * 100) if decisive_count else MetricValue.not_applicable()
    gross_wins = _sum(trade.gross_pnl for trade in trades if trade.gross_pnl > 0)
    gross_losses = _sum(trade.gross_pnl for trade in trades if trade.gross_pnl < 0)
    if not trades:
        profit_factor = MetricValue.not_applicable()
        expectancy = MetricValue.not_applicable()
    elif gross_losses == 0:
        profit_factor = MetricValue.of(Decimal("Infinity")) if gross_wins else MetricValue.not_applicable()
        expectancy = MetricValue.of(_sum(trade.net_pnl for trade in trades) / len(trades))
    else:
        profit_factor = MetricValue.of(gross_wins / abs(gross_losses))
        expectancy = MetricValue.of(_sum(trade.net_pnl for trade in trades) / len(trades))
    returns = tuple(trade.return_pct for trade in trades)
    average_return = _value_or_na(_sum(returns) / len(returns) if returns else None)
    median_return = _value_or_na(Decimal(str(median(returns))) if returns else None)

    peak = initial
    max_dd = Decimal("0")
    max_dd_pct = Decimal("0")
    for point in points:
        if point.equity > peak:
            peak = point.equity
        drawdown = point.equity - peak
        drawdown_pct = drawdown / peak * Decimal("100")
        if drawdown < max_dd:
            max_dd, max_dd_pct = drawdown, drawdown_pct

    events = tuple(market_events)
    excursions: list[tuple[Decimal, Decimal]] = []
    for trade in trades:
        observations = tuple(event for event in events if event.symbol == trade.symbol and trade.opened_at <= event.timestamp_utc <= trade.closed_at)
        if not observations:
            continue
        direction = Decimal("1") if trade.side is PaperSide.BUY else Decimal("-1")
        moves = tuple((event.reference_price - trade.entry_price) * trade.quantity * direction for event in observations)
        excursions.append((max(moves), min(moves)))
    if not trades:
        mfe, mae = MetricValue.not_applicable(), MetricValue.not_applicable()
    elif len(excursions) != len(trades):
        mfe, mae = MetricValue.unavailable(), MetricValue.unavailable()
    else:
        mfe = MetricValue.of(_sum(item[0] for item in excursions) / len(excursions))
        mae = MetricValue.of(_sum(item[1] for item in excursions) / len(excursions))

    if final_portfolio is None:
        unrealized = gross_exposure = net_exposure = MetricValue.unavailable()
    else:
        unrealized = MetricValue.of(final_portfolio.unrealized_pnl)
        gross_exposure = MetricValue.of(final_portfolio.gross_exposure)
        net_exposure = MetricValue.of(final_portfolio.net_exposure)

    if len(trades) == 0:
        sample_policy = "INSUFFICIENT_SAMPLE"
    elif len(trades) < 30:
        sample_policy = "LIMITED_SAMPLE"
    else:
        sample_policy = "SUFFICIENT_SAMPLE"
    return PaperPerformanceMetrics(
        total_return_pct=MetricValue.of(total_return), gross_pnl=MetricValue.of(gross), net_pnl=MetricValue.of(net),
        realized_pnl=MetricValue.of(net), unrealized_pnl=unrealized, win_rate=win_rate, loss_rate=loss_rate,
        profit_factor=profit_factor, expectancy=expectancy, max_drawdown=MetricValue.of(max_dd),
        max_drawdown_pct=MetricValue.of(max_dd_pct), average_trade_return=average_return,
        median_trade_return=median_return, mfe=mfe, mae=mae, gross_exposure=gross_exposure,
        net_exposure=net_exposure, turnover=MetricValue.of(_sum(trade.entry_notional + trade.exit_notional for trade in trades)),
        fees_paid=MetricValue.of(fees), slippage_cost=MetricValue.of(slippage), closed_trade_count=len(trades),
        winning_trade_count=len(winners), losing_trade_count=len(losers), breakeven_trade_count=len(breakeven),
        sample_size_status=sample_policy,
    )


__all__ = ["MetricStatus", "MetricValue", "PaperClosedTrade", "PaperEquityPoint", "PaperPerformanceMetrics", "calculate_paper_performance"]
