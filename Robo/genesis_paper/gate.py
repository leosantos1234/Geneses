"""Fail-closed paper execution gate for the Phase 3 simulator."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from .contracts import (
    PaperExecutionAuthority,
    PaperExecutionDecision,
    PaperExecutionDecisionType,
    PaperFill,
    PaperOrder,
    PaperRiskDecision,
    PaperRiskDecisionType,
    _required_id,
    _utc,
)
from .simulator import PaperSimulationResult, PaperSimulatorConfig, simulate_order


class PaperGateDecisionType(Enum):
    ALLOW_PAPER_EXECUTION = "ALLOW_PAPER_EXECUTION"
    BLOCK_PAPER_EXECUTION = "BLOCK_PAPER_EXECUTION"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class PaperExecutionGateConfig:
    paper_execution_enabled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.paper_execution_enabled, bool):
            raise ValueError("paper_execution_enabled must be bool")

    def to_dict(self) -> dict[str, bool]:
        return {"paper_execution_enabled": self.paper_execution_enabled}


@dataclass(frozen=True)
class PaperExecutionGateResult:
    decision: PaperGateDecisionType
    reason: str
    simulation: PaperSimulationResult | None = None

    @property
    def fill(self) -> PaperFill | None:
        return None if self.simulation is None else self.simulation.fill

    @property
    def updated_order(self) -> PaperOrder | None:
        return None if self.simulation is None else self.simulation.updated_order

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "simulation": None if self.simulation is None else self.simulation.to_dict(),
        }


def _blocked(reason: str, *, insufficient: bool = False) -> PaperExecutionGateResult:
    return PaperExecutionGateResult(
        PaperGateDecisionType.INSUFFICIENT_DATA if insufficient else PaperGateDecisionType.BLOCK_PAPER_EXECUTION,
        reason,
    )


def execute_paper_gate(
    *,
    commander_decision_id: str,
    execution_decision: PaperExecutionDecision | None,
    risk_decision: PaperRiskDecision | None,
    order: PaperOrder | None,
    gate_config: PaperExecutionGateConfig,
    simulator_config: PaperSimulatorConfig,
    timestamp_utc: datetime | None,
    reference_price: Any,
    current_price: Any = None,
    current_portfolio_exposure: Any = None,
    duplicate_execution_exists: bool = False,
) -> PaperExecutionGateResult:
    """Run the paper simulator only after all fail-closed checks pass."""
    try:
        commander_id = _required_id(commander_decision_id, "commander_decision_id")
    except ValueError:
        return _blocked("MISSING_REQUIRED_DATA", insufficient=True)
    if not isinstance(gate_config, PaperExecutionGateConfig) or not gate_config.paper_execution_enabled:
        return _blocked("PAPER_EXECUTION_DISABLED")
    if execution_decision is None or risk_decision is None:
        return _blocked("MISSING_REQUIRED_DATA", insufficient=True)
    if execution_decision.commander_decision_id != commander_id or risk_decision.commander_decision_id != commander_id:
        return _blocked("INVALID_EXECUTION_DECISION")
    if execution_decision.paper_only is not True or risk_decision.paper_only is not True:
        return _blocked("INVALID_PAPER_INVARIANT")
    if execution_decision.execution_authority is not PaperExecutionAuthority.NONE:
        return _blocked("INVALID_PAPER_INVARIANT")
    if execution_decision.decision is not PaperExecutionDecisionType.CREATE_ORDER:
        return _blocked("INVALID_EXECUTION_DECISION")
    if risk_decision.decision is not PaperRiskDecisionType.ALLOW:
        return _blocked("RISK_NOT_ALLOWED")
    if order is None:
        return _blocked("MISSING_ORDER", insufficient=True)
    if order.commander_decision_id != commander_id:
        return _blocked("INVALID_EXECUTION_DECISION")
    if order.paper_only is not True or order.execution_authority is not PaperExecutionAuthority.NONE:
        return _blocked("INVALID_PAPER_INVARIANT")
    if duplicate_execution_exists:
        return _blocked("DUPLICATE_EXECUTION")
    if timestamp_utc is None or reference_price is None:
        return _blocked("MISSING_MARKET_DATA", insufficient=True)
    try:
        _utc(timestamp_utc, "timestamp_utc")
    except ValueError:
        return _blocked("MISSING_MARKET_DATA", insufficient=True)
    if order.order_type.value == "LIMIT" and current_price is None:
        return _blocked("MISSING_MARKET_DATA", insufficient=True)
    if not isinstance(simulator_config, PaperSimulatorConfig):
        return _blocked("INVALID_SIMULATOR_CONFIG", insufficient=True)
    try:
        simulation = simulate_order(
            order,
            simulator_config,
            timestamp_utc=timestamp_utc,
            reference_price=reference_price,
            current_price=current_price,
            current_portfolio_exposure=current_portfolio_exposure,
        )
    except (TypeError, ValueError):
        return _blocked("INVALID_SIMULATOR_INPUT", insufficient=True)
    return PaperExecutionGateResult(PaperGateDecisionType.ALLOW_PAPER_EXECUTION, "PAPER_EXECUTION_EVALUATED", simulation)

