"""Deterministic, paper-only historical replay engine."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
from typing import Any, Iterable
from uuid import NAMESPACE_URL, uuid5

from .accounting import mark_position, open_position
from .contracts import (
    PaperExecutionDecision,
    PaperExecutionDecisionType,
    PaperOrder,
    PaperOrderType,
    PaperPortfolio,
    PaperRiskDecision,
    PaperRiskDecisionType,
    PaperSide,
    _decimal,
    _required_id,
    _symbol,
    _utc,
)
from .gate import PaperExecutionGateConfig, execute_paper_gate
from .risk import PaperRiskConfig, evaluate_paper_risk
from .simulator import PaperSimulatorConfig


class ReplayDataStatus(Enum):
    AVAILABLE = "AVAILABLE"
    DATA_NOT_AVAILABLE = "DATA_NOT_AVAILABLE"


@dataclass(frozen=True)
class HistoricalMarketEvent:
    timestamp_utc: datetime
    symbol: str
    reference_price: Decimal | str | int | float
    open: Decimal | str | int | float | None = None
    high: Decimal | str | int | float | None = None
    low: Decimal | str | int | float | None = None
    close: Decimal | str | int | float | None = None
    volume: Decimal | str | int | float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp_utc", _normalize_timestamp(self.timestamp_utc))
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        object.__setattr__(self, "reference_price", _decimal(self.reference_price, "reference_price", positive=True))
        for name in ("open", "high", "low", "close", "volume"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _decimal(value, name, positive=name != "volume", non_negative=name == "volume"))


@dataclass(frozen=True)
class ReplayOrderIntent:
    timestamp_utc: datetime
    commander_decision_id: str
    symbol: str
    side: PaperSide
    quantity: Decimal | str | int | float
    order_type: PaperOrderType = PaperOrderType.MARKET
    limit_price: Decimal | str | int | float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp_utc", _normalize_timestamp(self.timestamp_utc))
        object.__setattr__(self, "commander_decision_id", _required_id(self.commander_decision_id, "commander_decision_id"))
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        if not isinstance(self.side, PaperSide) or not isinstance(self.order_type, PaperOrderType):
            raise ValueError("invalid replay intent enum")
        object.__setattr__(self, "quantity", _decimal(self.quantity, "quantity", positive=True))
        if self.limit_price is not None:
            object.__setattr__(self, "limit_price", _decimal(self.limit_price, "limit_price", positive=True))
        if self.order_type is PaperOrderType.LIMIT and self.limit_price is None:
            raise ValueError("LIMIT replay intent requires limit_price")


def _normalize_timestamp(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("timestamp_utc must be datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        return _utc(value, "timestamp_utc")
    return value.astimezone(timezone.utc)


class HistoricalMarketDataView:
    def __init__(self, events: Iterable[HistoricalMarketEvent]):
        self.events = tuple(events)
        for previous, current in zip(self.events, self.events[1:]):
            if current.timestamp_utc < previous.timestamp_utc:
                raise ValueError("historical events must be non-decreasing")

    def get_price_as_of(self, symbol: str, as_of: datetime) -> tuple[ReplayDataStatus, Decimal | None]:
        symbol = _symbol(symbol)
        cutoff = _normalize_timestamp(as_of)
        candidates = [event for event in self.events if event.symbol == symbol and event.timestamp_utc <= cutoff]
        if not candidates:
            return ReplayDataStatus.DATA_NOT_AVAILABLE, None
        return ReplayDataStatus.AVAILABLE, candidates[-1].reference_price

    def get_window_as_of(self, symbol: str, as_of: datetime, lookback: int) -> tuple[HistoricalMarketEvent, ...]:
        if lookback < 0:
            raise ValueError("lookback must not be negative")
        symbol = _symbol(symbol)
        cutoff = _normalize_timestamp(as_of)
        return tuple(event for event in self.events if event.symbol == symbol and event.timestamp_utc <= cutoff)[-lookback:]


@dataclass(frozen=True)
class ReplayStep:
    timestamp_utc: datetime
    symbol: str
    reference_price: Decimal
    order_statuses: tuple[str, ...]
    fills: int
    portfolio: PaperPortfolio


@dataclass(frozen=True)
class ReplayResult:
    replay_id: str
    started_at: datetime | None
    ended_at: datetime | None
    event_count: int
    decision_count: int
    order_count: int
    fill_count: int
    steps: tuple[ReplayStep, ...]
    final_portfolio: PaperPortfolio
    paper_only: bool = True

    def economic_digest(self) -> str:
        payload = {
            "steps": [
                {"timestamp": step.timestamp_utc.isoformat(), "symbol": step.symbol, "reference_price": str(step.reference_price), "statuses": step.order_statuses, "fills": step.fills, "portfolio": step.portfolio.to_dict()}
                for step in self.steps
            ],
            "final_portfolio": self.final_portfolio.to_dict(),
        }
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class HistoricalReplayEngine:
    def __init__(self, *, events: Iterable[HistoricalMarketEvent], initial_portfolio: PaperPortfolio, simulator_config: PaperSimulatorConfig, risk_config: PaperRiskConfig, execution_enabled: bool = True):
        self.market = HistoricalMarketDataView(events)
        self.initial_portfolio = initial_portfolio
        self.simulator_config = simulator_config
        self.risk_config = risk_config
        self.gate_config = PaperExecutionGateConfig(paper_execution_enabled=execution_enabled)

    def run(self, intents: Iterable[ReplayOrderIntent] = (), *, replay_id: str = "replay-1") -> ReplayResult:
        replay_id = _required_id(replay_id, "replay_id")
        intent_items = tuple(intents)
        for previous, current in zip(intent_items, intent_items[1:]):
            if current.timestamp_utc < previous.timestamp_utc:
                raise ValueError("replay intents must be non-decreasing")
        by_timestamp: dict[datetime, list[ReplayOrderIntent]] = {}
        for intent in intent_items:
            by_timestamp.setdefault(intent.timestamp_utc, []).append(intent)

        portfolio = self.initial_portfolio
        pending: list[tuple[PaperOrder, PaperExecutionDecision, PaperRiskDecision]] = []
        steps: list[ReplayStep] = []
        decision_count = 0
        order_count = 0
        fill_count = 0

        for event in self.market.events:
            # Only the current event may mark current paper positions.
            for symbol in tuple(portfolio.positions):
                if symbol == event.symbol:
                    portfolio = mark_position(portfolio, symbol, event.reference_price)
            step_orders: list[str] = []
            step_fills = 0
            next_pending: list[tuple[PaperOrder, PaperExecutionDecision, PaperRiskDecision]] = []
            for order, execution_decision, risk_decision in pending:
                gate_result = execute_paper_gate(
                    commander_decision_id=order.commander_decision_id,
                    execution_decision=execution_decision,
                    risk_decision=risk_decision,
                    order=order,
                    gate_config=self.gate_config,
                    simulator_config=self.simulator_config,
                    timestamp_utc=event.timestamp_utc,
                    reference_price=event.reference_price,
                    current_price=event.reference_price,
                )
                step_orders.append(gate_result.updated_order.status.value if gate_result.updated_order else "BLOCKED")
                if gate_result.fill is None:
                    next_pending.append((gate_result.updated_order or order, execution_decision, risk_decision))
                else:
                    portfolio = open_position(portfolio, gate_result.fill.symbol, gate_result.fill.side, gate_result.fill.quantity, gate_result.fill.fill_price, timestamp=event.timestamp_utc)
                    fill_count += 1
                    step_fills += 1
            pending = next_pending

            for intent in by_timestamp.get(event.timestamp_utc, []):
                if intent.symbol != event.symbol:
                    continue
                order_id = str(uuid5(NAMESPACE_URL, f"{replay_id}|order|{intent.commander_decision_id}|{intent.timestamp_utc.isoformat()}"))
                execution_id = str(uuid5(NAMESPACE_URL, f"{replay_id}|execution|{intent.commander_decision_id}"))
                execution_decision = PaperExecutionDecision(paper_execution_decision_id=execution_id, commander_decision_id=intent.commander_decision_id, timestamp_utc=event.timestamp_utc, decision=PaperExecutionDecisionType.CREATE_ORDER)
                risk_decision = evaluate_paper_risk(
                    commander_decision_id=intent.commander_decision_id,
                    symbol=intent.symbol,
                    side=intent.side,
                    quantity=intent.quantity,
                    reference_price=event.reference_price,
                    current_symbol_exposure=portfolio.gross_exposure,
                    current_portfolio_exposure=portfolio.gross_exposure,
                    available_cash=portfolio.available_cash,
                    config=self.risk_config,
                    timestamp_utc=event.timestamp_utc,
                )
                order = PaperOrder(paper_order_id=order_id, commander_decision_id=intent.commander_decision_id, timestamp_utc=event.timestamp_utc, symbol=intent.symbol, side=intent.side, order_type=intent.order_type, requested_quantity=intent.quantity, requested_price=intent.limit_price)
                gate_result = execute_paper_gate(commander_decision_id=intent.commander_decision_id, execution_decision=execution_decision, risk_decision=risk_decision, order=order, gate_config=self.gate_config, simulator_config=self.simulator_config, timestamp_utc=event.timestamp_utc, reference_price=event.reference_price, current_price=event.reference_price)
                decision_count += 1
                order_count += 1
                step_orders.append(gate_result.updated_order.status.value if gate_result.updated_order else "BLOCKED")
                if gate_result.fill is not None:
                    portfolio = open_position(portfolio, gate_result.fill.symbol, gate_result.fill.side, gate_result.fill.quantity, gate_result.fill.fill_price, timestamp=event.timestamp_utc)
                    fill_count += 1
                    step_fills += 1
                elif gate_result.updated_order is not None and gate_result.updated_order.status.value == "PENDING":
                    pending.append((gate_result.updated_order, execution_decision, risk_decision))
            steps.append(ReplayStep(event.timestamp_utc, event.symbol, event.reference_price, tuple(step_orders), step_fills, portfolio))

        return ReplayResult(replay_id, self.market.events[0].timestamp_utc if self.market.events else None, self.market.events[-1].timestamp_utc if self.market.events else None, len(self.market.events), decision_count, order_count, fill_count, tuple(steps), portfolio, True)

