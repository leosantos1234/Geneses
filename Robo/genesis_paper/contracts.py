"""Canonical paper-only contracts.

This module contains data validation and deterministic serialization only.  It
does not import exchange, order, position, balance, or execution components.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

RECORD_PROVENANCE_VALUES = frozenset({"PRODUCTION_RUNTIME", "TEST", "ACCEPTANCE", "SYNTHETIC", "UNKNOWN"})


class _ValueEnum(Enum):
    def __str__(self) -> str:
        return self.value


class PaperExecutionAuthority(_ValueEnum):
    NONE = "NONE"


class PaperSide(_ValueEnum):
    BUY = "BUY"
    SELL = "SELL"


class PaperOrderType(_ValueEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class PaperOrderStatus(_ValueEnum):
    CREATED = "CREATED"
    REJECTED = "REJECTED"
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class PaperExecutionDecisionType(_ValueEnum):
    NO_ACTION = "NO_ACTION"
    CREATE_ORDER = "CREATE_ORDER"
    REJECTED = "REJECTED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class PaperRiskDecisionType(_ValueEnum):
    ALLOW = "ALLOW"
    REJECT = "REJECT"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


def _required_id(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} required")
    return value.strip()


def _symbol(value: str) -> str:
    value = _required_id(value, "symbol")
    if any(character.isspace() for character in value):
        raise ValueError("symbol must not contain whitespace")
    return value


def _utc(value: datetime, name: str = "timestamp_utc") -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware UTC")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError(f"{name} must be UTC (offset must be zero)")
    return value.astimezone(timezone.utc)


def _decimal(value: Any, name: str, *, positive: bool = False, non_negative: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{name} must be numeric") from None
    if not parsed.is_finite():
        raise ValueError(f"{name} must be finite")
    if positive and parsed <= 0:
        raise ValueError(f"{name} must be greater than zero")
    if non_negative and parsed < 0:
        raise ValueError(f"{name} must not be negative")
    return parsed


def _enum(value: Any, expected: type[Enum], name: str):
    if not isinstance(value, expected):
        raise ValueError(f"{name} invalid")
    return value


def _strings(values: list[str], name: str) -> list[str]:
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise ValueError(f"{name} must be a list of strings")
    return list(values)


def _decimal_string(value: Decimal) -> str:
    return format(value, "f")


@dataclass(frozen=True)
class PaperOrder:
    paper_order_id: str = field(default_factory=lambda: str(uuid4()))
    commander_decision_id: str = ""
    timestamp_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    symbol: str = ""
    side: PaperSide = PaperSide.BUY
    order_type: PaperOrderType = PaperOrderType.MARKET
    requested_quantity: Decimal | str | int | float = Decimal("0")
    requested_price: Decimal | str | int | float | None = None
    status: PaperOrderStatus = PaperOrderStatus.CREATED
    paper_only: bool = True
    execution_authority: PaperExecutionAuthority = PaperExecutionAuthority.NONE
    record_provenance: str = "UNKNOWN"

    def __post_init__(self) -> None:
        object.__setattr__(self, "paper_order_id", _required_id(self.paper_order_id, "paper_order_id"))
        object.__setattr__(self, "commander_decision_id", _required_id(self.commander_decision_id, "commander_decision_id"))
        object.__setattr__(self, "timestamp_utc", _utc(self.timestamp_utc))
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        object.__setattr__(self, "side", _enum(self.side, PaperSide, "side"))
        object.__setattr__(self, "order_type", _enum(self.order_type, PaperOrderType, "order_type"))
        object.__setattr__(self, "requested_quantity", _decimal(self.requested_quantity, "requested_quantity", positive=True))
        if self.requested_price is not None:
            object.__setattr__(self, "requested_price", _decimal(self.requested_price, "requested_price", positive=True))
        if self.order_type is PaperOrderType.LIMIT and self.requested_price is None:
            raise ValueError("LIMIT order requires requested_price")
        object.__setattr__(self, "status", _enum(self.status, PaperOrderStatus, "status"))
        if self.paper_only is not True:
            raise ValueError("paper_only must be True")
        object.__setattr__(self, "execution_authority", _enum(self.execution_authority, PaperExecutionAuthority, "execution_authority"))
        if self.execution_authority is not PaperExecutionAuthority.NONE:
            raise ValueError("execution_authority must be NONE")
        if self.record_provenance not in RECORD_PROVENANCE_VALUES:
            raise ValueError("record_provenance invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_order_id": self.paper_order_id,
            "commander_decision_id": self.commander_decision_id,
            "timestamp_utc": self.timestamp_utc.isoformat(),
            "symbol": self.symbol,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "requested_quantity": _decimal_string(self.requested_quantity),
            "requested_price": None if self.requested_price is None else _decimal_string(self.requested_price),
            "status": self.status.value,
            "paper_only": self.paper_only,
            "execution_authority": self.execution_authority.value,
            "record_provenance": self.record_provenance,
        }


@dataclass(frozen=True)
class PaperFill:
    paper_fill_id: str = field(default_factory=lambda: str(uuid4()))
    paper_order_id: str = ""
    timestamp_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    symbol: str = ""
    side: PaperSide = PaperSide.BUY
    quantity: Decimal | str | int | float = Decimal("0")
    fill_price: Decimal | str | int | float = Decimal("0")
    fee: Decimal | str | int | float = Decimal("0")
    slippage_cost: Decimal | str | int | float = Decimal("0")
    paper_only: bool = True
    record_provenance: str = "UNKNOWN"

    def __post_init__(self) -> None:
        object.__setattr__(self, "paper_fill_id", _required_id(self.paper_fill_id, "paper_fill_id"))
        object.__setattr__(self, "paper_order_id", _required_id(self.paper_order_id, "paper_order_id"))
        object.__setattr__(self, "timestamp_utc", _utc(self.timestamp_utc))
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        object.__setattr__(self, "side", _enum(self.side, PaperSide, "side"))
        object.__setattr__(self, "quantity", _decimal(self.quantity, "quantity", positive=True))
        object.__setattr__(self, "fill_price", _decimal(self.fill_price, "fill_price", positive=True))
        object.__setattr__(self, "fee", _decimal(self.fee, "fee", non_negative=True))
        object.__setattr__(self, "slippage_cost", _decimal(self.slippage_cost, "slippage_cost", non_negative=True))
        if self.paper_only is not True:
            raise ValueError("paper_only must be True")
        if self.record_provenance not in RECORD_PROVENANCE_VALUES:
            raise ValueError("record_provenance invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_fill_id": self.paper_fill_id,
            "paper_order_id": self.paper_order_id,
            "timestamp_utc": self.timestamp_utc.isoformat(),
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": _decimal_string(self.quantity),
            "fill_price": _decimal_string(self.fill_price),
            "fee": _decimal_string(self.fee),
            "slippage_cost": _decimal_string(self.slippage_cost),
            "paper_only": self.paper_only,
            "record_provenance": self.record_provenance,
        }


@dataclass
class PaperPosition:
    symbol: str = ""
    side: PaperSide = PaperSide.BUY
    quantity: Decimal | str | int | float = Decimal("0")
    average_entry_price: Decimal | str | int | float = Decimal("0")
    mark_price: Decimal | str | int | float = Decimal("0")
    unrealized_pnl: Decimal | str | int | float = Decimal("0")
    realized_pnl: Decimal | str | int | float = Decimal("0")
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    paper_only: bool = True

    def __post_init__(self) -> None:
        if self.paper_only is not True:
            raise ValueError("paper_only must be True")
        self.symbol = _symbol(self.symbol)
        self.side = _enum(self.side, PaperSide, "side")
        self.quantity = _decimal(self.quantity, "quantity", positive=True)
        self.average_entry_price = _decimal(self.average_entry_price, "average_entry_price", positive=True)
        self.mark_price = _decimal(self.mark_price, "mark_price", positive=True)
        self.unrealized_pnl = _decimal(self.unrealized_pnl, "unrealized_pnl")
        self.realized_pnl = _decimal(self.realized_pnl, "realized_pnl")
        self.opened_at = _utc(self.opened_at, "opened_at")
        self.updated_at = _utc(self.updated_at, "updated_at")
        if self.opened_at > self.updated_at:
            raise ValueError("opened_at must not be after updated_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": _decimal_string(self.quantity),
            "average_entry_price": _decimal_string(self.average_entry_price),
            "mark_price": _decimal_string(self.mark_price),
            "unrealized_pnl": _decimal_string(self.unrealized_pnl),
            "realized_pnl": _decimal_string(self.realized_pnl),
            "opened_at": self.opened_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "paper_only": self.paper_only,
        }


@dataclass
class PaperPortfolio:
    paper_portfolio_id: str = field(default_factory=lambda: str(uuid4()))
    initial_cash: Decimal | str | int | float = Decimal("0")
    available_cash: Decimal | str | int | float = Decimal("0")
    reserved_cash: Decimal | str | int | float = Decimal("0")
    equity: Decimal | str | int | float = Decimal("0")
    realized_pnl: Decimal | str | int | float = Decimal("0")
    unrealized_pnl: Decimal | str | int | float = Decimal("0")
    gross_exposure: Decimal | str | int | float = Decimal("0")
    net_exposure: Decimal | str | int | float = Decimal("0")
    positions: dict[str, PaperPosition] = field(default_factory=dict)
    paper_only: bool = True

    def __post_init__(self) -> None:
        if self.paper_only is not True:
            raise ValueError("paper_only must be True")
        self.paper_portfolio_id = _required_id(self.paper_portfolio_id, "paper_portfolio_id")
        for name in (
            "initial_cash", "available_cash", "reserved_cash", "equity",
            "realized_pnl", "unrealized_pnl", "gross_exposure", "net_exposure",
        ):
            setattr(self, name, _decimal(
                getattr(self, name),
                name,
                positive=name == "initial_cash",
                non_negative=name.endswith("cash") or name == "gross_exposure" or name == "equity",
            ))
        if not isinstance(self.positions, dict) or not all(isinstance(value, PaperPosition) for value in self.positions.values()):
            raise ValueError("positions must be a mapping of symbol to PaperPosition")

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_portfolio_id": self.paper_portfolio_id,
            "initial_cash": _decimal_string(self.initial_cash),
            "available_cash": _decimal_string(self.available_cash),
            "reserved_cash": _decimal_string(self.reserved_cash),
            "equity": _decimal_string(self.equity),
            "realized_pnl": _decimal_string(self.realized_pnl),
            "unrealized_pnl": _decimal_string(self.unrealized_pnl),
            "gross_exposure": _decimal_string(self.gross_exposure),
            "net_exposure": _decimal_string(self.net_exposure),
            "positions": {key: self.positions[key].to_dict() for key in sorted(self.positions)},
            "paper_only": self.paper_only,
        }


@dataclass
class PaperExecutionDecision:
    paper_execution_decision_id: str = field(default_factory=lambda: str(uuid4()))
    commander_decision_id: str = ""
    timestamp_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    decision: PaperExecutionDecisionType = PaperExecutionDecisionType.NO_ACTION
    reasons: list[str] = field(default_factory=list)
    paper_only: bool = True
    execution_authority: PaperExecutionAuthority = PaperExecutionAuthority.NONE

    def __post_init__(self) -> None:
        self.paper_execution_decision_id = _required_id(self.paper_execution_decision_id, "paper_execution_decision_id")
        self.commander_decision_id = _required_id(self.commander_decision_id, "commander_decision_id")
        self.timestamp_utc = _utc(self.timestamp_utc)
        self.decision = _enum(self.decision, PaperExecutionDecisionType, "decision")
        self.reasons = _strings(self.reasons, "reasons")
        if self.paper_only is not True:
            raise ValueError("paper_only must be True")
        self.execution_authority = _enum(self.execution_authority, PaperExecutionAuthority, "execution_authority")
        if self.execution_authority is not PaperExecutionAuthority.NONE:
            raise ValueError("execution_authority must be NONE")

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_execution_decision_id": self.paper_execution_decision_id,
            "commander_decision_id": self.commander_decision_id,
            "timestamp_utc": self.timestamp_utc.isoformat(),
            "decision": self.decision.value,
            "reasons": list(self.reasons),
            "paper_only": self.paper_only,
            "execution_authority": self.execution_authority.value,
        }


@dataclass(frozen=True)
class PaperRiskDecision:
    paper_risk_decision_id: str = field(default_factory=lambda: str(uuid4()))
    commander_decision_id: str = ""
    timestamp_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    decision: PaperRiskDecisionType = PaperRiskDecisionType.INSUFFICIENT_DATA
    reasons: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    paper_only: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "paper_risk_decision_id", _required_id(self.paper_risk_decision_id, "paper_risk_decision_id"))
        object.__setattr__(self, "commander_decision_id", _required_id(self.commander_decision_id, "commander_decision_id"))
        object.__setattr__(self, "timestamp_utc", _utc(self.timestamp_utc))
        object.__setattr__(self, "decision", _enum(self.decision, PaperRiskDecisionType, "decision"))
        object.__setattr__(self, "reasons", _strings(self.reasons, "reasons"))
        object.__setattr__(self, "risk_flags", _strings(self.risk_flags, "risk_flags"))
        if self.paper_only is not True:
            raise ValueError("paper_only must be True")

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_risk_decision_id": self.paper_risk_decision_id,
            "commander_decision_id": self.commander_decision_id,
            "timestamp_utc": self.timestamp_utc.isoformat(),
            "decision": self.decision.value,
            "reasons": list(self.reasons),
            "risk_flags": list(self.risk_flags),
            "paper_only": self.paper_only,
        }
