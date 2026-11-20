"""Future-only Commander candidate routing into the paper domain.

This module has no exchange dependency.  It is deliberately pure except for
an optional event callback supplied by the runtime integration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from genesis_commander.contract import DecisionType

from .contracts import (
    PaperExecutionDecision,
    PaperExecutionDecisionType,
    PaperRiskDecision,
    PaperRiskDecisionType,
    PaperSide,
    PaperOrder,
    PaperOrderType,
)
from .gate import PaperExecutionGateConfig, PaperExecutionGateResult, execute_paper_gate
from .lineage import PaperEffectType, PaperLineage, PaperPortfolioEffect
from .risk import PaperRiskConfig, evaluate_paper_risk
from .simulator import PaperSimulatorConfig
from .accounting import open_position


PROVENANCE_VALUES = frozenset({"PRODUCTION_RUNTIME", "TEST", "ACCEPTANCE", "SYNTHETIC", "UNKNOWN"})


@dataclass(frozen=True)
class CandidatePaperRoute:
    commander_decision_id: str
    risk: PaperRiskDecision
    execution: PaperExecutionDecision
    gate: PaperExecutionGateResult | None
    order: PaperOrder | None
    fill: Any | None
    effect: PaperPortfolioEffect | None
    lineage: PaperLineage | None
    record_provenance: str


def route_candidate_to_paper(
    decision: Any,
    *,
    strategy_id: str | None,
    strategy_name: str | None,
    quantity: Decimal | str | int | float,
    reference_price: Decimal | str | int | float,
    current_price: Decimal | str | int | float | None,
    current_symbol_exposure: Decimal | str | int | float,
    current_portfolio_exposure: Decimal | str | int | float,
    available_cash: Decimal | str | int | float,
    paper_risk_config: PaperRiskConfig,
    simulator_config: PaperSimulatorConfig,
    paper_enabled: bool,
    record_provenance: str,
    paper_portfolio_id: str = "runtime-paper",
    event_callback: Callable[[str, dict[str, Any]], None] | None = None,
    repository: Any = None,
    portfolio: Any = None,
) -> CandidatePaperRoute | None:
    """Route only CANDIDATE_LONG/SHORT; never invokes an exchange.

    Strategy identity is required at this boundary.  Missing identity is
    represented by an insufficient-data risk result, never inferred.
    """
    if record_provenance not in PROVENANCE_VALUES:
        raise ValueError("record_provenance invalid")
    decision_type = getattr(getattr(decision, "decision", None), "value", getattr(decision, "decision", None))
    if decision_type not in {DecisionType.CANDIDATE_LONG.value, DecisionType.CANDIDATE_SHORT.value}:
        return None
    commander_id = str(decision.commander_decision_id)
    timestamp = decision.timestamp_utc.astimezone(timezone.utc)
    side = PaperSide.BUY if decision_type == DecisionType.CANDIDATE_LONG.value else PaperSide.SELL
    identity_missing = not strategy_id and not strategy_name
    risk = evaluate_paper_risk(
        commander_decision_id=commander_id,
        symbol=decision.symbol,
        side=side,
        quantity=quantity,
        reference_price=reference_price,
        current_symbol_exposure=current_symbol_exposure,
        current_portfolio_exposure=current_portfolio_exposure,
        available_cash=available_cash,
        config=paper_risk_config,
        timestamp_utc=timestamp,
        opening_short=side is PaperSide.SELL,
        short_collateral_available=available_cash if side is PaperSide.SELL else None,
    )
    if identity_missing and risk.decision is PaperRiskDecisionType.ALLOW:
        # Preserve the existing risk engine; identity is a separate gate.
        risk = evaluate_paper_risk(
            commander_decision_id=commander_id, symbol=None, side=side,
            quantity=None, reference_price=None, current_symbol_exposure=None,
            current_portfolio_exposure=None, available_cash=None,
            config=paper_risk_config, timestamp_utc=timestamp,
        )
    execution = PaperExecutionDecision(
        commander_decision_id=commander_id,
        timestamp_utc=timestamp,
        decision=PaperExecutionDecisionType.CREATE_ORDER if risk.decision is PaperRiskDecisionType.ALLOW else PaperExecutionDecisionType.REJECTED,
        reasons=list(risk.reasons),
    )
    if risk.decision is not PaperRiskDecisionType.ALLOW:
        if event_callback:
            event_callback("RISK_EVALUATED", {"commander_decision_id": commander_id, "risk_status": risk.decision.value, "risk_reason": list(risk.reasons), "evaluated_at": timestamp.isoformat(), "record_provenance": record_provenance})
        return CandidatePaperRoute(commander_id, risk, execution, None, None, None, None, PaperLineage(execution, risk), record_provenance)
    order = PaperOrder(
        commander_decision_id=commander_id,
        timestamp_utc=timestamp,
        symbol=decision.symbol,
        side=side,
        order_type=PaperOrderType.MARKET,
        requested_quantity=quantity,
        record_provenance=record_provenance,
    )
    gate = execute_paper_gate(
        commander_decision_id=commander_id,
        execution_decision=execution,
        risk_decision=risk,
        order=order,
        gate_config=PaperExecutionGateConfig(paper_execution_enabled=paper_enabled),
        simulator_config=simulator_config,
        timestamp_utc=timestamp,
        reference_price=reference_price,
        current_price=current_price,
        current_portfolio_exposure=current_portfolio_exposure,
    )
    fill = gate.fill
    effect = None
    if fill is not None:
        cash_delta = -(fill.fill_price * fill.quantity) if side is PaperSide.BUY else (fill.fill_price * fill.quantity)
        effect = PaperPortfolioEffect(
            paper_portfolio_id=paper_portfolio_id,
            paper_fill_id=fill.paper_fill_id,
            paper_order_id=order.paper_order_id,
            commander_decision_id=commander_id,
            timestamp_utc=fill.timestamp_utc,
            symbol=order.symbol,
            effect_type=PaperEffectType.OPEN,
            quantity_delta=fill.quantity,
            cash_delta=cash_delta,
            realized_pnl_delta=0,
            record_provenance=record_provenance,
        )
    lineage = PaperLineage(execution, risk, gate.updated_order if gate else order, fill, effect)
    if repository is not None and portfolio is not None:
        persisted_portfolio = portfolio
        position = None
        if fill is not None:
            persisted_portfolio = open_position(persisted_portfolio, order.symbol, order.side, fill.quantity, fill.fill_price, timestamp=fill.timestamp_utc)
        repository.persist_execution(
            portfolio=persisted_portfolio,
            paper_portfolio_id=paper_portfolio_id,
            order=gate.updated_order or order,
            fill=fill,
            position=persisted_portfolio.positions.get(order.symbol),
            effect=effect,
            events=[{"paper_event_id": f"{commander_id}|paper", "timestamp_utc": timestamp, "event_type": "CANDIDATE_PAPER_ROUTE", "commander_decision_id": commander_id, "paper_portfolio_id": paper_portfolio_id, "paper_order_id": order.paper_order_id, "paper_fill_id": None if fill is None else fill.paper_fill_id, "paper_effect_id": None if effect is None else effect.paper_effect_id, "payload": {"record_provenance": record_provenance, "paper_only": True}}],
        )
    if event_callback:
        event_callback("RISK_EVALUATED", {"commander_decision_id": commander_id, "risk_status": risk.decision.value, "risk_reason": list(risk.reasons), "evaluated_at": timestamp.isoformat(), "record_provenance": record_provenance})
    return CandidatePaperRoute(commander_id, risk, execution, gate, order, fill, effect, lineage, record_provenance)
