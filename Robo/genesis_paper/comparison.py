"""Read-only comparison of Commander shadow decisions with paper outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable

from .contracts import (
    PaperExecutionAuthority,
    PaperExecutionDecision,
    PaperExecutionDecisionType,
    PaperFill,
    PaperOrder,
    PaperOrderStatus,
    PaperRiskDecision,
    PaperRiskDecisionType,
    PaperSide,
    _decimal,
    _required_id,
    _symbol,
    _utc,
)
from .gate import PaperExecutionGateResult, PaperGateDecisionType
from .lineage import PaperPortfolioEffect
from .metrics import MetricStatus, MetricValue, PaperPerformanceMetrics


class ComparisonOutcomeStatus(Enum):
    NO_PAPER_ACTION = "NO_PAPER_ACTION"
    RISK_REJECTED = "RISK_REJECTED"
    EXECUTION_BLOCKED = "EXECUTION_BLOCKED"
    ORDER_PENDING = "ORDER_PENDING"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    ORDER_EXPIRED = "ORDER_EXPIRED"
    DATA_NOT_AVAILABLE = "DATA_NOT_AVAILABLE"


class DataQualityStatus(Enum):
    COMPLETE = "COMPLETE"
    DATA_NOT_AVAILABLE = "DATA_NOT_AVAILABLE"
    INVALID_LINEAGE = "INVALID_LINEAGE"


class DecisionQuality(Enum):
    CORRECT_DIRECTION = "CORRECT_DIRECTION"
    WRONG_DIRECTION = "WRONG_DIRECTION"
    NEUTRAL = "NEUTRAL"
    NOT_EXECUTED = "NOT_EXECUTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    DATA_NOT_AVAILABLE = "DATA_NOT_AVAILABLE"


def _attr(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _decision_value(value: Any) -> str:
    value = _attr(value, "decision", value)
    return value.value if isinstance(value, Enum) else str(value)


def _confidence_value(value: Any) -> str | None:
    confidence = _attr(value, "confidence")
    if confidence is None:
        return None
    return confidence.value if isinstance(confidence, Enum) else str(confidence)


def _metric(performance: PaperPerformanceMetrics | None, name: str) -> MetricValue:
    if performance is None:
        return MetricValue.unavailable()
    value = getattr(performance, name, None)
    return value if isinstance(value, MetricValue) else MetricValue.unavailable()


@dataclass(frozen=True)
class ShadowPaperComparison:
    comparison_id: str
    commander_decision_id: str
    timestamp_utc: datetime
    symbol: str | None
    commander_decision: str
    commander_confidence: str | None
    paper_execution_decision: str | None
    paper_risk_decision: str | None
    paper_risk_reasons: tuple[str, ...]
    paper_order_id: str | None
    paper_order_status: str | None
    paper_fill_id: str | None
    fill_price: Decimal | None
    fees_paid: MetricValue
    slippage_cost: MetricValue
    reference_price: MetricValue
    execution_slippage_pct: MetricValue
    paper_position_effect: str | None
    paper_return_pct: MetricValue
    realized_pnl: MetricValue
    unrealized_pnl: MetricValue
    mfe: MetricValue
    mae: MetricValue
    outcome_status: ComparisonOutcomeStatus
    data_quality_status: DataQualityStatus
    decision_quality: DecisionQuality
    metrics_fees_paid: MetricValue
    metrics_slippage_cost: MetricValue
    paper_only: bool = True

    def __post_init__(self) -> None:
        _required_id(self.comparison_id, "comparison_id")
        _required_id(self.commander_decision_id, "commander_decision_id")
        if not isinstance(self.timestamp_utc, datetime) or self.timestamp_utc.tzinfo is None:
            raise ValueError("timestamp_utc must be timezone-aware")
        if self.symbol is not None:
            _symbol(self.symbol)
        if self.paper_only is not True:
            raise ValueError("paper_only must be True")
        if self.fill_price is not None:
            _decimal(self.fill_price, "fill_price", positive=True)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in self.__dict__.items():
            if isinstance(value, MetricValue):
                result[name] = value.to_dict()
            elif isinstance(value, Enum):
                result[name] = value.value
            elif isinstance(value, datetime):
                result[name] = value.isoformat()
            elif isinstance(value, tuple):
                result[name] = list(value)
            elif isinstance(value, Decimal):
                result[name] = format(value, "f")
            else:
                result[name] = value
        return result


@dataclass(frozen=True)
class ComparisonValidationReport:
    comparison_lineage_crossover_count: int
    comparison_fee_mismatch_count: int
    comparison_slippage_mismatch_count: int
    comparison_nondeterministic_count: int


def _quality(decision: str, net_pnl: MetricValue, outcome: ComparisonOutcomeStatus) -> DecisionQuality:
    if decision in {"NO_ACTION", "WATCH"}:
        return DecisionQuality.NOT_APPLICABLE
    if decision == "INSUFFICIENT_DATA":
        return DecisionQuality.DATA_NOT_AVAILABLE
    if outcome is not ComparisonOutcomeStatus.ORDER_FILLED:
        return DecisionQuality.NOT_EXECUTED
    if net_pnl.status is not MetricStatus.VALUE:
        return DecisionQuality.DATA_NOT_AVAILABLE
    if net_pnl.value == 0:
        return DecisionQuality.NEUTRAL
    if (decision == "CANDIDATE_LONG" and net_pnl.value > 0) or (decision == "CANDIDATE_SHORT" and net_pnl.value > 0):
        return DecisionQuality.CORRECT_DIRECTION
    return DecisionQuality.WRONG_DIRECTION


def compare_shadow_decision(
    commander_decision: Any,
    *,
    comparison_id: str,
    execution_decision: PaperExecutionDecision | None = None,
    risk_decision: PaperRiskDecision | None = None,
    order: PaperOrder | None = None,
    fill: PaperFill | None = None,
    effect: PaperPortfolioEffect | None = None,
    performance: PaperPerformanceMetrics | None = None,
    reference_price: Decimal | str | int | float | None = None,
    gate_result: PaperExecutionGateResult | None = None,
) -> ShadowPaperComparison:
    """Compare one shadow decision without mutating any source artifact."""
    decision_id = _required_id(str(_attr(commander_decision, "commander_decision_id", "")), "commander_decision_id")
    decision = _decision_value(commander_decision)
    timestamp = _attr(commander_decision, "timestamp_utc")
    if timestamp is None:
        timestamp = execution_decision.timestamp_utc if execution_decision else datetime.now(timezone.utc)
    timestamp = _utc(timestamp, "timestamp_utc")
    symbol = _attr(commander_decision, "symbol") or (order.symbol if order else (fill.symbol if fill else None))
    if symbol is not None:
        symbol = _symbol(symbol)

    for artifact, artifact_id_name in ((execution_decision, "commander_decision_id"), (risk_decision, "commander_decision_id"), (order, "commander_decision_id"), (effect, "commander_decision_id")):
        if artifact is not None and getattr(artifact, artifact_id_name) != decision_id:
            raise ValueError("comparison lineage mismatch")
    if fill is not None and order is None:
        raise ValueError("fill requires order")
    if fill is not None and (fill.paper_order_id != order.paper_order_id or fill.symbol != order.symbol or fill.side != order.side):
        raise ValueError("fill lineage mismatch")
    if effect is not None and (fill is None or effect.paper_fill_id != fill.paper_fill_id or effect.paper_order_id != order.paper_order_id):
        raise ValueError("effect lineage mismatch")
    if order is not None and decision not in {"CANDIDATE_LONG", "CANDIDATE_SHORT"}:
        raise ValueError("non-candidate decision cannot create paper order")

    execution_value = None if execution_decision is None else execution_decision.decision.value
    risk_value = None if risk_decision is None else risk_decision.decision.value
    reasons = () if risk_decision is None else tuple(risk_decision.reasons)
    outcome = ComparisonOutcomeStatus.DATA_NOT_AVAILABLE
    if decision in {"NO_ACTION", "WATCH"}:
        outcome = ComparisonOutcomeStatus.NO_PAPER_ACTION
    elif decision == "INSUFFICIENT_DATA":
        outcome = ComparisonOutcomeStatus.DATA_NOT_AVAILABLE
    elif risk_decision is not None and risk_decision.decision is PaperRiskDecisionType.REJECT:
        outcome = ComparisonOutcomeStatus.RISK_REJECTED
    elif gate_result is not None and gate_result.decision is PaperGateDecisionType.INSUFFICIENT_DATA:
        outcome = ComparisonOutcomeStatus.DATA_NOT_AVAILABLE
    elif gate_result is not None and gate_result.decision is PaperGateDecisionType.BLOCK_PAPER_EXECUTION:
        outcome = ComparisonOutcomeStatus.EXECUTION_BLOCKED
    elif order is None:
        outcome = ComparisonOutcomeStatus.EXECUTION_BLOCKED if execution_decision is not None else ComparisonOutcomeStatus.DATA_NOT_AVAILABLE
    elif fill is not None:
        outcome = ComparisonOutcomeStatus.ORDER_FILLED
    elif order.status is PaperOrderStatus.PENDING:
        outcome = ComparisonOutcomeStatus.ORDER_PENDING
    elif order.status is PaperOrderStatus.CANCELLED:
        outcome = ComparisonOutcomeStatus.ORDER_CANCELLED
    elif order.status is PaperOrderStatus.EXPIRED:
        outcome = ComparisonOutcomeStatus.ORDER_EXPIRED
    elif gate_result is not None:
        outcome = ComparisonOutcomeStatus.EXECUTION_BLOCKED

    reference = MetricValue.unavailable() if reference_price is None else MetricValue.of(_decimal(reference_price, "reference_price", positive=True))
    fill_price = None if fill is None else fill.fill_price
    if fill is not None and reference.status is MetricStatus.VALUE:
        slippage_pct = MetricValue.of(abs(fill.fill_price - reference.value) / reference.value * Decimal("100"))
    else:
        slippage_pct = MetricValue.unavailable()
    fees = MetricValue.of(fill.fee) if fill is not None else MetricValue.not_applicable()
    slippage = MetricValue.of(fill.slippage_cost) if fill is not None else MetricValue.not_applicable()
    net_pnl = _metric(performance, "realized_pnl")
    return ShadowPaperComparison(
        comparison_id=_required_id(comparison_id, "comparison_id"), commander_decision_id=decision_id,
        timestamp_utc=timestamp, symbol=symbol, commander_decision=decision,
        commander_confidence=_confidence_value(commander_decision), paper_execution_decision=execution_value,
        paper_risk_decision=risk_value, paper_risk_reasons=reasons,
        paper_order_id=None if order is None else order.paper_order_id,
        paper_order_status=None if order is None else order.status.value,
        paper_fill_id=None if fill is None else fill.paper_fill_id, fill_price=fill_price,
        fees_paid=fees, slippage_cost=slippage, reference_price=reference,
        execution_slippage_pct=slippage_pct, paper_position_effect=None if effect is None else effect.paper_effect_id,
        paper_return_pct=_metric(performance, "total_return_pct"), realized_pnl=net_pnl,
        unrealized_pnl=_metric(performance, "unrealized_pnl"), mfe=_metric(performance, "mfe"),
        mae=_metric(performance, "mae"), outcome_status=outcome,
        data_quality_status=DataQualityStatus.COMPLETE if outcome is not ComparisonOutcomeStatus.DATA_NOT_AVAILABLE else DataQualityStatus.DATA_NOT_AVAILABLE,
        decision_quality=_quality(decision, net_pnl, outcome), metrics_fees_paid=_metric(performance, "fees_paid"),
        metrics_slippage_cost=_metric(performance, "slippage_cost"), paper_only=True,
    )


def validate_comparisons(comparisons: Iterable[ShadowPaperComparison]) -> ComparisonValidationReport:
    items = tuple(comparisons)
    ids = [item.commander_decision_id for item in items]
    crossover = len(ids) - len(set(ids))
    fee_mismatch = sum(1 for item in items if item.fees_paid.status is MetricStatus.VALUE and item.metrics_fees_paid.status is MetricStatus.VALUE and item.fees_paid.value != item.metrics_fees_paid.value)
    slippage_mismatch = sum(1 for item in items if item.slippage_cost.status is MetricStatus.VALUE and item.metrics_slippage_cost.status is MetricStatus.VALUE and item.slippage_cost.value != item.metrics_slippage_cost.value)
    return ComparisonValidationReport(crossover, fee_mismatch, slippage_mismatch, 0)


__all__ = ["ComparisonOutcomeStatus", "DataQualityStatus", "DecisionQuality", "ShadowPaperComparison", "ComparisonValidationReport", "compare_shadow_decision", "validate_comparisons"]
