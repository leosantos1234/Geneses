"""Immutable, in-memory lineage for paper execution artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable
from uuid import uuid4

from .contracts import (
    PaperExecutionDecision,
    PaperExecutionDecisionType,
    PaperFill,
    PaperOrder,
    PaperRiskDecision,
    PaperRiskDecisionType,
    _decimal,
    _required_id,
    _symbol,
    _utc,
    RECORD_PROVENANCE_VALUES,
)


class PaperEffectType(Enum):
    OPEN = "OPEN"
    INCREASE = "INCREASE"
    REDUCE = "REDUCE"
    CLOSE = "CLOSE"
    MARK_ONLY = "MARK_ONLY"


@dataclass(frozen=True)
class PaperPortfolioEffect:
    paper_effect_id: str = field(default_factory=lambda: str(uuid4()))
    paper_portfolio_id: str = ""
    paper_fill_id: str = ""
    paper_order_id: str = ""
    commander_decision_id: str = ""
    timestamp_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    symbol: str = ""
    effect_type: PaperEffectType = PaperEffectType.OPEN
    quantity_delta: Decimal | str | int | float = Decimal("0")
    cash_delta: Decimal | str | int | float = Decimal("0")
    realized_pnl_delta: Decimal | str | int | float = Decimal("0")
    paper_only: bool = True
    record_provenance: str = "UNKNOWN"

    def __post_init__(self) -> None:
        if self.paper_only is not True:
            raise ValueError("paper_only must be True")
        if self.record_provenance not in RECORD_PROVENANCE_VALUES:
            raise ValueError("record_provenance invalid")
        object.__setattr__(self, "paper_effect_id", _required_id(self.paper_effect_id, "paper_effect_id"))
        object.__setattr__(self, "paper_portfolio_id", _required_id(self.paper_portfolio_id, "paper_portfolio_id"))
        object.__setattr__(self, "paper_fill_id", _required_id(self.paper_fill_id, "paper_fill_id"))
        object.__setattr__(self, "paper_order_id", _required_id(self.paper_order_id, "paper_order_id"))
        object.__setattr__(self, "commander_decision_id", _required_id(self.commander_decision_id, "commander_decision_id"))
        object.__setattr__(self, "timestamp_utc", _utc(self.timestamp_utc))
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        if not isinstance(self.effect_type, PaperEffectType):
            raise ValueError("effect_type invalid")
        for name in ("quantity_delta", "cash_delta", "realized_pnl_delta"):
            object.__setattr__(self, name, _decimal(getattr(self, name), name))

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_effect_id": self.paper_effect_id,
            "paper_portfolio_id": self.paper_portfolio_id,
            "paper_fill_id": self.paper_fill_id,
            "paper_order_id": self.paper_order_id,
            "commander_decision_id": self.commander_decision_id,
            "timestamp_utc": self.timestamp_utc.isoformat(),
            "symbol": self.symbol,
            "effect_type": self.effect_type.value,
            "quantity_delta": str(self.quantity_delta),
            "cash_delta": str(self.cash_delta),
            "realized_pnl_delta": str(self.realized_pnl_delta),
            "paper_only": self.paper_only,
            "record_provenance": self.record_provenance,
        }


@dataclass(frozen=True)
class PaperLineage:
    execution_decision: PaperExecutionDecision
    risk_decision: PaperRiskDecision
    order: PaperOrder | None = None
    fill: PaperFill | None = None
    effect: PaperPortfolioEffect | None = None

    def __post_init__(self) -> None:
        commander_id = self.execution_decision.commander_decision_id
        if self.risk_decision.commander_decision_id != commander_id:
            raise ValueError("risk decision commander mismatch")
        if self.order is None:
            if self.fill is not None or self.effect is not None:
                raise ValueError("fill/effect requires paper order")
            if self.execution_decision.decision is PaperExecutionDecisionType.CREATE_ORDER:
                raise ValueError("CREATE_ORDER lineage requires paper order")
            return
        if self.execution_decision.decision is not PaperExecutionDecisionType.CREATE_ORDER:
            raise ValueError("only CREATE_ORDER may create paper order")
        if self.order.commander_decision_id != commander_id:
            raise ValueError("order commander mismatch")
        if self.risk_decision.decision is PaperRiskDecisionType.REJECT:
            raise ValueError("rejected risk lineage cannot create paper order")
        if self.fill is not None:
            if self.fill.paper_order_id != self.order.paper_order_id:
                raise ValueError("fill order mismatch")
            if self.fill.symbol != self.order.symbol or self.fill.side != self.order.side:
                raise ValueError("fill order attributes mismatch")
        if self.effect is not None:
            if self.fill is None:
                raise ValueError("effect requires fill")
            if (self.effect.paper_fill_id != self.fill.paper_fill_id or
                    self.effect.paper_order_id != self.order.paper_order_id or
                    self.effect.commander_decision_id != commander_id):
                raise ValueError("effect lineage mismatch")

    @property
    def commander_decision_id(self) -> str:
        return self.execution_decision.commander_decision_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_decision": self.execution_decision.to_dict(),
            "risk_decision": self.risk_decision.to_dict(),
            "order": None if self.order is None else self.order.to_dict(),
            "fill": None if self.fill is None else self.fill.to_dict(),
            "effect": None if self.effect is None else self.effect.to_dict(),
        }


@dataclass(frozen=True)
class LineageValidationReport:
    orphan_paper_order_count: int = 0
    orphan_paper_fill_count: int = 0
    orphan_paper_effect_count: int = 0
    duplicate_paper_order_id_count: int = 0
    duplicate_paper_fill_id_count: int = 0
    duplicate_paper_effect_id_count: int = 0
    duplicate_commander_execution_count: int = 0


def _duplicates(values: Iterable[str]) -> int:
    seen: set[str] = set()
    duplicates = 0
    for value in values:
        if value in seen:
            duplicates += 1
        seen.add(value)
    return duplicates


def validate_lineage_artifacts(
    execution_decisions: Iterable[PaperExecutionDecision],
    risk_decisions: Iterable[PaperRiskDecision],
    orders: Iterable[PaperOrder],
    fills: Iterable[PaperFill],
    effects: Iterable[PaperPortfolioEffect],
) -> LineageValidationReport:
    decisions = tuple(execution_decisions)
    risks = tuple(risk_decisions)
    order_items = tuple(orders)
    fill_items = tuple(fills)
    effect_items = tuple(effects)
    decision_ids = {item.commander_decision_id for item in decisions}
    risk_by_commander = {item.commander_decision_id for item in risks}
    orders_by_id = {item.paper_order_id: item for item in order_items}
    fills_by_id = {item.paper_fill_id: item for item in fill_items}
    orphan_orders = sum(item.commander_decision_id not in decision_ids for item in order_items)
    orphan_fills = sum(item.paper_order_id not in orders_by_id for item in fill_items)
    orphan_effects = sum(
        item.paper_fill_id not in fills_by_id
        or item.paper_order_id not in orders_by_id
        or item.commander_decision_id not in decision_ids
        or item.commander_decision_id not in risk_by_commander
        or (item.paper_fill_id in fills_by_id and fills_by_id[item.paper_fill_id].paper_order_id != item.paper_order_id)
        or (item.paper_order_id in orders_by_id and orders_by_id[item.paper_order_id].commander_decision_id != item.commander_decision_id)
        for item in effect_items
    )
    commander_order_count: dict[str, int] = {}
    for item in order_items:
        commander_order_count[item.commander_decision_id] = commander_order_count.get(item.commander_decision_id, 0) + 1
    return LineageValidationReport(
        orphan_paper_order_count=orphan_orders,
        orphan_paper_fill_count=orphan_fills,
        orphan_paper_effect_count=orphan_effects,
        duplicate_paper_order_id_count=_duplicates(item.paper_order_id for item in order_items),
        duplicate_paper_fill_id_count=_duplicates(item.paper_fill_id for item in fill_items),
        duplicate_paper_effect_id_count=_duplicates(item.paper_effect_id for item in effect_items),
        duplicate_commander_execution_count=sum(max(count - 1, 0) for count in commander_order_count.values()),
    )


@dataclass(frozen=True)
class PaperLineageIndex:
    records: tuple[PaperLineage, ...] = ()

    def add(self, lineage: PaperLineage) -> "PaperLineageIndex":
        if any(item.commander_decision_id == lineage.commander_decision_id for item in self.records):
            raise ValueError("commander decision already has a lineage")
        if lineage.order and any(item.order and item.order.paper_order_id == lineage.order.paper_order_id for item in self.records):
            raise ValueError("duplicate paper_order_id")
        if lineage.fill and any(item.fill and item.fill.paper_fill_id == lineage.fill.paper_fill_id for item in self.records):
            raise ValueError("duplicate paper_fill_id")
        if lineage.effect and any(item.effect and item.effect.paper_effect_id == lineage.effect.paper_effect_id for item in self.records):
            raise ValueError("duplicate paper_effect_id")
        return PaperLineageIndex(self.records + (lineage,))

    def get_lineage_by_commander_decision_id(self, commander_decision_id: str) -> PaperLineage | None:
        return next((item for item in self.records if item.commander_decision_id == commander_decision_id), None)

    def get_lineage_by_paper_order_id(self, paper_order_id: str) -> PaperLineage | None:
        return next((item for item in self.records if item.order and item.order.paper_order_id == paper_order_id), None)

    def get_lineage_by_paper_fill_id(self, paper_fill_id: str) -> PaperLineage | None:
        return next((item for item in self.records if item.fill and item.fill.paper_fill_id == paper_fill_id), None)

    def get_lineage_by_paper_effect_id(self, paper_effect_id: str) -> PaperLineage | None:
        return next((item for item in self.records if item.effect and item.effect.paper_effect_id == paper_effect_id), None)
