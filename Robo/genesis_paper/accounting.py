"""Pure deterministic accounting primitives for paper portfolios.

This module performs no order execution and has no network, database, exchange,
Risk Guard, or Commander execution dependencies.  ``PaperSide.BUY`` represents
long accounting and ``PaperSide.SELL`` represents short accounting.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Mapping

from .contracts import PaperPortfolio, PaperPosition, PaperSide, _decimal, _required_id, _symbol, _utc


MONEY_QUANT = Decimal("0.00000001")
ROUNDING_POLICY = "Decimal quantized to 8 places using ROUND_HALF_EVEN"


def _money(value: Decimal | str | int | float, name: str = "amount") -> Decimal:
    parsed = _decimal(value, name)
    return parsed.quantize(MONEY_QUANT, rounding=ROUND_HALF_EVEN)


def _quantity(value: Decimal | str | int | float) -> Decimal:
    return _money(_decimal(value, "quantity", positive=True), "quantity")


def _price(value: Decimal | str | int | float, name: str = "price") -> Decimal:
    return _money(_decimal(value, name, positive=True), name)


def _timestamp(value: datetime | None) -> datetime:
    return _utc(value or datetime.now(timezone.utc), "timestamp_utc")


def validate_cash(portfolio: PaperPortfolio) -> None:
    """Reject negative cash and ensure the portfolio remains paper-only."""
    if portfolio.paper_only is not True:
        raise ValueError("paper_only must be True")
    if portfolio.initial_cash <= 0:
        raise ValueError("initial_cash must be greater than zero")
    if portfolio.available_cash < 0 or portfolio.reserved_cash < 0:
        raise ValueError("cash values must not be negative")


def position_market_value(position: PaperPosition, mark_price: Decimal | str | int | float | None = None) -> Decimal:
    price = position.mark_price if mark_price is None else _price(mark_price, "mark_price")
    return _money(position.quantity * price, "market_value")


def position_unrealized_pnl(position: PaperPosition, mark_price: Decimal | str | int | float | None = None) -> Decimal:
    price = position.mark_price if mark_price is None else _price(mark_price, "mark_price")
    if position.side is PaperSide.BUY:
        pnl = (price - position.average_entry_price) * position.quantity
    else:
        pnl = (position.average_entry_price - price) * position.quantity
    return _money(pnl, "unrealized_pnl")


def aggregate_realized_pnl(positions: Mapping[str, PaperPosition]) -> Decimal:
    return _money(sum((position.realized_pnl for position in positions.values()), Decimal("0")), "realized_pnl")


def calculate_exposure(positions: Mapping[str, PaperPosition]) -> tuple[Decimal, Decimal]:
    gross = sum((position_market_value(position) for position in positions.values()), Decimal("0"))
    net = sum(
        (position_market_value(position) if position.side is PaperSide.BUY else -position_market_value(position)
         for position in positions.values()),
        Decimal("0"),
    )
    return _money(gross, "gross_exposure"), _money(net, "net_exposure")


def calculate_equity(portfolio: PaperPortfolio) -> Decimal:
    signed_market_value = sum(
        (position_market_value(position) if position.side is PaperSide.BUY else -position_market_value(position)
         for position in portfolio.positions.values()),
        Decimal("0"),
    )
    return _money(portfolio.available_cash + portfolio.reserved_cash + signed_market_value, "equity")


def recalculate_portfolio(portfolio: PaperPortfolio) -> PaperPortfolio:
    """Return a new portfolio with all derived accounting fields recalculated."""
    validate_cash(portfolio)
    positions = {
        symbol: replace(position, unrealized_pnl=position_unrealized_pnl(position))
        for symbol, position in sorted(portfolio.positions.items())
    }
    gross, net = calculate_exposure(positions)
    updated = replace(
        portfolio,
        positions=positions,
        # Portfolio realized P&L is authoritative and includes closed lots;
        # position-level realized P&L remains local audit detail.
        realized_pnl=portfolio.realized_pnl,
        unrealized_pnl=_money(sum((position.unrealized_pnl for position in positions.values()), Decimal("0")), "unrealized_pnl"),
        gross_exposure=gross,
        net_exposure=net,
    )
    return replace(updated, equity=calculate_equity(updated))


def create_portfolio(initial_cash: Decimal | str | int | float, *, paper_portfolio_id: str = "portfolio-1") -> PaperPortfolio:
    cash = _money(_decimal(initial_cash, "initial_cash", positive=True), "initial_cash")
    return PaperPortfolio(
        paper_portfolio_id=_required_id(paper_portfolio_id, "paper_portfolio_id"),
        initial_cash=cash,
        available_cash=cash,
        reserved_cash=Decimal("0"),
        equity=cash,
        paper_only=True,
    )


def open_position(
    portfolio: PaperPortfolio,
    symbol: str,
    side: PaperSide,
    quantity: Decimal | str | int | float,
    entry_price: Decimal | str | int | float,
    *,
    timestamp: datetime | None = None,
) -> PaperPortfolio:
    """Open or increase one same-direction position without simulating an order."""
    validate_cash(portfolio)
    symbol = _symbol(symbol)
    if not isinstance(side, PaperSide):
        raise ValueError("side invalid")
    qty, price, now = _quantity(quantity), _price(entry_price, "entry_price"), _timestamp(timestamp)
    existing = portfolio.positions.get(symbol)
    if existing is not None and existing.side is not side:
        raise ValueError("opposite-side position must be reduced before opening")
    if existing is None:
        position = PaperPosition(symbol=symbol, side=side, quantity=qty, average_entry_price=price, mark_price=price, opened_at=now, updated_at=now)
    else:
        total = existing.quantity + qty
        average = _money((existing.quantity * existing.average_entry_price + qty * price) / total, "average_entry_price")
        position = replace(existing, quantity=_money(total, "quantity"), average_entry_price=average, mark_price=price, updated_at=now)
    cash_delta = -(qty * price) if side is PaperSide.BUY else qty * price
    updated = replace(portfolio, positions={**portfolio.positions, symbol: position}, available_cash=_money(portfolio.available_cash + cash_delta, "available_cash"))
    return recalculate_portfolio(updated)


def reduce_position(
    portfolio: PaperPortfolio,
    symbol: str,
    quantity: Decimal | str | int | float,
    exit_price: Decimal | str | int | float,
    *,
    timestamp: datetime | None = None,
) -> PaperPortfolio:
    """Reduce a position and realize its P&L; a full reduction removes it."""
    validate_cash(portfolio)
    symbol = _symbol(symbol)
    existing = portfolio.positions.get(symbol)
    if existing is None:
        raise ValueError("position not found")
    qty, price, now = _quantity(quantity), _price(exit_price, "exit_price"), _timestamp(timestamp)
    if qty > existing.quantity:
        raise ValueError("reduction exceeds position quantity")
    realized = (price - existing.average_entry_price) * qty if existing.side is PaperSide.BUY else (existing.average_entry_price - price) * qty
    cash_delta = qty * price if existing.side is PaperSide.BUY else -(qty * price)
    positions = dict(portfolio.positions)
    if qty == existing.quantity:
        del positions[symbol]
    else:
        positions[symbol] = replace(
            existing,
            quantity=_money(existing.quantity - qty, "quantity"),
            mark_price=price,
            realized_pnl=_money(existing.realized_pnl + realized, "realized_pnl"),
            updated_at=now,
        )
    updated = replace(
        portfolio,
        positions=positions,
        available_cash=_money(portfolio.available_cash + cash_delta, "available_cash"),
        realized_pnl=_money(portfolio.realized_pnl + realized, "realized_pnl"),
    )
    return recalculate_portfolio(updated)


def close_position(portfolio: PaperPortfolio, symbol: str, exit_price: Decimal | str | int | float, *, timestamp: datetime | None = None) -> PaperPortfolio:
    existing = portfolio.positions.get(_symbol(symbol))
    if existing is None:
        raise ValueError("position not found")
    return reduce_position(portfolio, symbol, existing.quantity, exit_price, timestamp=timestamp)


def mark_position(portfolio: PaperPortfolio, symbol: str, mark_price: Decimal | str | int | float) -> PaperPortfolio:
    symbol = _symbol(symbol)
    existing = portfolio.positions.get(symbol)
    if existing is None:
        raise ValueError("position not found")
    updated = replace(portfolio, positions={**portfolio.positions, symbol: replace(existing, mark_price=_price(mark_price, "mark_price"))})
    return recalculate_portfolio(updated)


increase_position = open_position
