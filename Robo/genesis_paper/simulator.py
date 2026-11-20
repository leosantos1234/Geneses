"""Deterministic paper order simulation with no live execution dependencies."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .contracts import (
    PaperExecutionAuthority,
    PaperFill,
    PaperOrder,
    PaperOrderStatus,
    PaperOrderType,
    PaperSide,
    _decimal,
    _decimal_string,
    _utc,
)


MONEY_QUANT = Decimal("0.00000001")
_BPS = Decimal("10000")


def _money(value: Decimal | str | int | float, name: str) -> Decimal:
    return _decimal(value, name).quantize(MONEY_QUANT, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class PaperSimulatorConfig:
    fee_bps: Decimal | str | int | float
    slippage_bps: Decimal | str | int | float
    max_position_notional: Decimal | str | int | float
    max_portfolio_exposure: Decimal | str | int | float
    allow_short: bool
    allow_fractional_quantity: bool

    def __post_init__(self) -> None:
        for name in ("fee_bps", "slippage_bps"):
            value = _decimal(getattr(self, name), name, non_negative=True)
            object.__setattr__(self, name, value)
        for name in ("max_position_notional", "max_portfolio_exposure"):
            value = _decimal(getattr(self, name), name, positive=True)
            object.__setattr__(self, name, value)
        if not isinstance(self.allow_short, bool) or not isinstance(self.allow_fractional_quantity, bool):
            raise ValueError("boolean simulator flags required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "fee_bps": _decimal_string(self.fee_bps),
            "slippage_bps": _decimal_string(self.slippage_bps),
            "max_position_notional": _decimal_string(self.max_position_notional),
            "max_portfolio_exposure": _decimal_string(self.max_portfolio_exposure),
            "allow_short": self.allow_short,
            "allow_fractional_quantity": self.allow_fractional_quantity,
        }


@dataclass(frozen=True)
class PaperSimulationResult:
    updated_order: PaperOrder
    fill: PaperFill | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "updated_order": self.updated_order.to_dict(),
            "fill": None if self.fill is None else self.fill.to_dict(),
            "reason": self.reason,
        }


_TRANSITIONS = {
    PaperOrderStatus.CREATED: {
        PaperOrderStatus.FILLED,
        PaperOrderStatus.PENDING,
        PaperOrderStatus.REJECTED,
    },
    PaperOrderStatus.PENDING: {
        PaperOrderStatus.FILLED,
        PaperOrderStatus.CANCELLED,
        PaperOrderStatus.EXPIRED,
        PaperOrderStatus.PENDING,
        PaperOrderStatus.REJECTED,
    },
}


def transition_order(order: PaperOrder, target: PaperOrderStatus) -> PaperOrder:
    if not isinstance(target, PaperOrderStatus):
        raise ValueError("target status invalid")
    if target not in _TRANSITIONS.get(order.status, set()):
        raise ValueError(f"invalid order transition: {order.status.value} -> {target.value}")
    return replace(order, status=target)


def _reject(order: PaperOrder, reason: str) -> PaperSimulationResult:
    if order.status not in (PaperOrderStatus.CREATED, PaperOrderStatus.PENDING):
        raise ValueError("terminal order cannot be rejected")
    return PaperSimulationResult(transition_order(order, PaperOrderStatus.REJECTED), None, reason)


def _validate_input(timestamp_utc: datetime, reference_price: Decimal | str | int | float) -> Decimal:
    _utc(timestamp_utc, "timestamp_utc")
    return _money(_decimal(reference_price, "reference_price", positive=True), "reference_price")


def simulate_order(
    order: PaperOrder,
    config: PaperSimulatorConfig,
    *,
    timestamp_utc: datetime,
    reference_price: Decimal | str | int | float,
    current_price: Decimal | str | int | float | None = None,
    current_portfolio_exposure: Decimal | str | int | float | None = None,
    opening_short: bool = False,
) -> PaperSimulationResult:
    """Evaluate one order from explicit market inputs only.

    Partial fills are intentionally unsupported: a crossed limit fills fully;
    an uncrossed limit remains pending.  LIMIT fills use the requested limit
    price and have zero slippage cost.
    """
    if order.status not in (PaperOrderStatus.CREATED, PaperOrderStatus.PENDING):
        raise ValueError("only CREATED or PENDING orders can be evaluated")
    reference = _validate_input(timestamp_utc, reference_price)
    quantity = _money(order.requested_quantity, "requested_quantity")
    if not config.allow_fractional_quantity and quantity != quantity.to_integral_value():
        return _reject(order, "FRACTIONAL_QUANTITY_NOT_ALLOWED")
    if opening_short and not config.allow_short:
        return _reject(order, "SHORT_NOT_ALLOWED")
    if order.order_type is PaperOrderType.LIMIT and order.requested_price is None:
        return _reject(order, "LIMIT_PRICE_REQUIRED")
    notional_basis = reference if order.order_type is PaperOrderType.MARKET else _money(order.requested_price, "requested_price")
    requested_notional = _money(quantity * notional_basis, "requested_notional")
    if requested_notional > config.max_position_notional:
        return _reject(order, "MAX_POSITION_NOTIONAL_EXCEEDED")
    if current_portfolio_exposure is not None:
        exposure = _decimal(current_portfolio_exposure, "current_portfolio_exposure", non_negative=True)
        if exposure + requested_notional > config.max_portfolio_exposure:
            return _reject(order, "MAX_PORTFOLIO_EXPOSURE_EXCEEDED")

    if order.order_type is PaperOrderType.LIMIT:
        if current_price is None:
            return _reject(order, "LIMIT_CURRENT_PRICE_REQUIRED")
        market = _money(_decimal(current_price, "current_price", positive=True), "current_price")
        limit = _money(order.requested_price, "requested_price")
        crossed = market <= limit if order.side is PaperSide.BUY else market >= limit
        if not crossed:
            return PaperSimulationResult(transition_order(order, PaperOrderStatus.PENDING), None, "LIMIT_NOT_CROSSED")
        fill_price = limit
        slippage_cost = Decimal("0")
    else:
        slippage = _money(reference * config.slippage_bps / _BPS, "slippage_amount")
        fill_price = _money(reference + slippage if order.side is PaperSide.BUY else reference - slippage, "fill_price")
        if fill_price <= 0:
            return _reject(order, "INVALID_SLIPPED_PRICE")
        slippage_cost = _money(abs(fill_price - reference) * quantity, "slippage_cost")

    notional = _money(fill_price * quantity, "filled_notional")
    fee = _money(notional * config.fee_bps / _BPS, "fee")
    fill_id = str(uuid5(
        NAMESPACE_URL,
        "genesis-paper-fill|"
        f"{order.paper_order_id}|{timestamp_utc.isoformat()}|{order.symbol}|"
        f"{order.side.value}|{quantity}|{fill_price}|{fee}|{slippage_cost}",
    ))
    fill = PaperFill(
        paper_fill_id=fill_id,
        paper_order_id=order.paper_order_id,
        timestamp_utc=timestamp_utc,
        symbol=order.symbol,
        side=order.side,
        quantity=quantity,
        fill_price=fill_price,
        fee=fee,
        slippage_cost=slippage_cost,
        paper_only=True,
        record_provenance=order.record_provenance,
    )
    return PaperSimulationResult(transition_order(order, PaperOrderStatus.FILLED), fill, "FILLED")


def cancel_order(order: PaperOrder) -> PaperSimulationResult:
    return PaperSimulationResult(transition_order(order, PaperOrderStatus.CANCELLED), None, "CANCELLED")


def expire_order(order: PaperOrder) -> PaperSimulationResult:
    return PaperSimulationResult(transition_order(order, PaperOrderStatus.EXPIRED), None, "EXPIRED")
