"""Minimal paper-only concurrency and idempotency boundary.

The coordinator is intentionally local to one paper execution process.  It
serializes the check-and-create critical section; PostgreSQL deployments must
perform the same section in one transaction with ``SELECT ... FOR UPDATE`` on
the portfolio row, followed by the existing unique constraints.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from threading import RLock
from typing import Callable
from uuid import NAMESPACE_URL, uuid5

from .contracts import PaperFill, PaperOrder, PaperOrderStatus, PaperPortfolio, _decimal, _required_id, _symbol
from .lineage import PaperPortfolioEffect


PORTFOLIO_CONCURRENCY_CONTROL = "PROCESS_RLOCK; POSTGRES_SELECT_FOR_UPDATE"
SAME_DECISION_POLICY = "FIRST_COMMIT_WINS; RETRIES_RETURN_EXISTING_RESULT"


@dataclass(frozen=True)
class ConcurrentPaperResult:
    accepted: bool
    duplicate: bool
    reason: str
    order: PaperOrder | None = None
    fill: PaperFill | None = None
    effect: PaperPortfolioEffect | None = None
    paper_event_id: str | None = None


@dataclass(frozen=True)
class TerminalTransitionResult:
    order: PaperOrder
    won: bool


class PaperConcurrencyCoordinator:
    """Atomically reserve paper portfolio capacity and create artifacts."""

    def __init__(
        self,
        portfolio: PaperPortfolio,
        *,
        max_portfolio_exposure: Decimal | str | int | float | None = None,
        max_symbol_exposure: Decimal | str | int | float | None = None,
    ) -> None:
        if portfolio.paper_only is not True:
            raise ValueError("portfolio must be paper-only")
        self._lock = RLock()
        self._portfolio = portfolio
        self._max_portfolio_exposure = None if max_portfolio_exposure is None else _decimal(max_portfolio_exposure, "max_portfolio_exposure", positive=True)
        self._max_symbol_exposure = None if max_symbol_exposure is None else _decimal(max_symbol_exposure, "max_symbol_exposure", positive=True)
        self._by_decision: dict[str, ConcurrentPaperResult] = {}
        self._orders: dict[str, PaperOrder] = {}
        self._fills: dict[str, PaperFill] = {}
        self._effects: dict[str, PaperPortfolioEffect] = {}
        self._symbol_exposure: dict[str, Decimal] = {}

    @property
    def portfolio(self) -> PaperPortfolio:
        with self._lock:
            return self._portfolio

    def submit(
        self,
        *,
        commander_decision_id: str,
        symbol: str,
        requested_cash: Decimal | str | int | float,
        requested_exposure: Decimal | str | int | float,
        order_factory: Callable[[], PaperOrder],
        fill_factory: Callable[[PaperOrder], PaperFill | None] | None = None,
        effect_factory: Callable[[PaperOrder, PaperFill], PaperPortfolioEffect] | None = None,
    ) -> ConcurrentPaperResult:
        decision_id = _required_id(commander_decision_id, "commander_decision_id")
        symbol = _symbol(symbol)
        cash = _decimal(requested_cash, "requested_cash", positive=True)
        exposure = _decimal(requested_exposure, "requested_exposure", positive=True)
        with self._lock:
            existing = self._by_decision.get(decision_id)
            if existing is not None:
                return replace(existing, accepted=False, duplicate=True, reason="DUPLICATE_EXECUTION")
            if self._portfolio.available_cash < cash:
                result = ConcurrentPaperResult(False, False, "INSUFFICIENT_CASH")
                self._by_decision[decision_id] = result
                return result
            if self._max_portfolio_exposure is not None and self._portfolio.gross_exposure + exposure > self._max_portfolio_exposure:
                result = ConcurrentPaperResult(False, False, "MAX_PORTFOLIO_EXPOSURE_EXCEEDED")
                self._by_decision[decision_id] = result
                return result
            symbol_total = self._symbol_exposure.get(symbol, Decimal("0")) + exposure
            if self._max_symbol_exposure is not None and symbol_total > self._max_symbol_exposure:
                result = ConcurrentPaperResult(False, False, "MAX_SYMBOL_EXPOSURE_EXCEEDED")
                self._by_decision[decision_id] = result
                return result

            order = order_factory()
            if order.commander_decision_id != decision_id or order.symbol != symbol or order.paper_only is not True:
                raise ValueError("order factory returned invalid paper lineage")
            fill = None if fill_factory is None else fill_factory(order)
            if fill is not None and (fill.paper_order_id != order.paper_order_id or fill.symbol != order.symbol or fill.paper_only is not True):
                raise ValueError("fill factory returned invalid paper lineage")
            effect = None if fill is None or effect_factory is None else effect_factory(order, fill)
            if effect is not None and (effect.paper_order_id != order.paper_order_id or effect.paper_fill_id != fill.paper_fill_id or effect.commander_decision_id != decision_id):
                raise ValueError("effect factory returned invalid paper lineage")
            event_id = str(uuid5(NAMESPACE_URL, f"genesis-paper-event|{decision_id}|{order.paper_order_id}|{None if fill is None else fill.paper_fill_id}"))
            self._portfolio = replace(
                self._portfolio,
                available_cash=self._portfolio.available_cash - cash,
                equity=self._portfolio.equity - cash,
                gross_exposure=self._portfolio.gross_exposure + exposure,
            )
            self._symbol_exposure[symbol] = symbol_total
            self._orders[order.paper_order_id] = order
            if fill is not None:
                self._fills[fill.paper_fill_id] = fill
            if effect is not None:
                self._effects[effect.paper_effect_id] = effect
            result = ConcurrentPaperResult(True, False, "COMMITTED", order, fill, effect, event_id)
            self._by_decision[decision_id] = result
            return result

    def transition_terminal(self, paper_order_id: str, target: PaperOrderStatus) -> TerminalTransitionResult:
        if target not in {PaperOrderStatus.FILLED, PaperOrderStatus.CANCELLED, PaperOrderStatus.EXPIRED}:
            raise ValueError("target must be a terminal order status")
        with self._lock:
            order = self._orders[_required_id(paper_order_id, "paper_order_id")]
            if order.status in {PaperOrderStatus.FILLED, PaperOrderStatus.CANCELLED, PaperOrderStatus.EXPIRED}:
                return TerminalTransitionResult(order, False)
            if order.status is not PaperOrderStatus.PENDING:
                raise ValueError("only pending orders may transition terminally")
            updated = replace(order, status=target)
            self._orders[paper_order_id] = updated
            existing = self._by_decision[order.commander_decision_id]
            self._by_decision[order.commander_decision_id] = replace(existing, order=updated)
            return TerminalTransitionResult(updated, True)

    def artifact_counts(self) -> dict[str, int]:
        with self._lock:
            return {"orders": len(self._orders), "fills": len(self._fills), "effects": len(self._effects), "decisions": len(self._by_decision)}


__all__ = ["PORTFOLIO_CONCURRENCY_CONTROL", "SAME_DECISION_POLICY", "ConcurrentPaperResult", "TerminalTransitionResult", "PaperConcurrencyCoordinator"]
