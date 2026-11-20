"""Deterministic paper-only risk gate.

The gate is read-only.  It evaluates explicit inputs and creates only a
PaperRiskDecision; it does not execute, persist, or mutate portfolio state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .contracts import (
    PaperRiskDecision,
    PaperRiskDecisionType,
    PaperSide,
    _decimal,
    _decimal_string,
    _required_id,
    _symbol,
    _utc,
)


MONEY_QUANT = Decimal("0.00000001")


def _money(value: Any, name: str, *, positive: bool = False, non_negative: bool = False) -> Decimal:
    return _decimal(value, name, positive=positive, non_negative=non_negative).quantize(MONEY_QUANT, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class PaperRiskConfig:
    max_position_notional: Decimal | str | int | float
    max_portfolio_exposure: Decimal | str | int | float
    max_symbol_exposure: Decimal | str | int | float
    allow_short: bool
    allow_fractional_quantity: bool

    def __post_init__(self) -> None:
        for name in ("max_position_notional", "max_portfolio_exposure", "max_symbol_exposure"):
            object.__setattr__(self, name, _money(getattr(self, name), name, positive=True))
        if not isinstance(self.allow_short, bool) or not isinstance(self.allow_fractional_quantity, bool):
            raise ValueError("boolean risk flags required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_position_notional": _decimal_string(self.max_position_notional),
            "max_portfolio_exposure": _decimal_string(self.max_portfolio_exposure),
            "max_symbol_exposure": _decimal_string(self.max_symbol_exposure),
            "allow_short": self.allow_short,
            "allow_fractional_quantity": self.allow_fractional_quantity,
        }


def _decision(
    commander_decision_id: str,
    timestamp_utc: datetime,
    decision: PaperRiskDecisionType,
    reasons: list[str],
) -> PaperRiskDecision:
    stable_key = "|".join((commander_decision_id, timestamp_utc.isoformat(), decision.value, *reasons))
    decision_id = str(uuid5(NAMESPACE_URL, f"genesis-paper-risk|{stable_key}"))
    return PaperRiskDecision(
        paper_risk_decision_id=decision_id,
        commander_decision_id=commander_decision_id,
        timestamp_utc=timestamp_utc,
        decision=decision,
        reasons=reasons,
        risk_flags=reasons,
        paper_only=True,
    )


def evaluate_paper_risk(
    *,
    commander_decision_id: str,
    symbol: str | None,
    side: PaperSide | None,
    quantity: Decimal | str | int | float | None,
    reference_price: Decimal | str | int | float | None,
    current_symbol_exposure: Decimal | str | int | float | None,
    current_portfolio_exposure: Decimal | str | int | float | None,
    available_cash: Decimal | str | int | float | None,
    config: PaperRiskConfig,
    timestamp_utc: datetime | None = None,
    commander_execution_exists: bool = False,
    opening_short: bool = False,
    short_collateral_available: Decimal | str | int | float | None = None,
) -> PaperRiskDecision:
    """Evaluate one candidate with stable, ordered reason codes.

    Required exposure inputs are absolute paper-domain exposures.  A short
    opening uses a conservative 1x collateral requirement equal to notional;
    missing collateral returns INSUFFICIENT_DATA rather than inventing margin.
    """
    commander_id = _required_id(commander_decision_id, "commander_decision_id")
    timestamp = _utc(timestamp_utc or datetime.now(timezone.utc), "timestamp_utc")
    missing: list[str] = []
    violations: list[str] = []

    if symbol is None:
        missing.append("MISSING_REQUIRED_DATA")
    else:
        try:
            _symbol(symbol)
        except ValueError:
            violations.append("INVALID_SYMBOL")

    if side is None:
        missing.append("MISSING_REQUIRED_DATA")
    elif not isinstance(side, PaperSide):
        violations.append("INVALID_SIDE")

    parsed_quantity = None
    if quantity is None:
        missing.append("MISSING_REQUIRED_DATA")
    else:
        try:
            parsed_quantity = _money(quantity, "quantity", positive=True)
        except ValueError:
            violations.append("INVALID_QUANTITY")

    parsed_price = None
    if reference_price is None:
        missing.append("MISSING_REQUIRED_DATA")
    else:
        try:
            parsed_price = _money(reference_price, "reference_price", positive=True)
        except ValueError:
            violations.append("INVALID_REFERENCE_PRICE")

    parsed_symbol_exposure = None
    if current_symbol_exposure is None:
        missing.append("MISSING_REQUIRED_DATA")
    else:
        try:
            parsed_symbol_exposure = _money(current_symbol_exposure, "current_symbol_exposure", non_negative=True)
        except ValueError:
            violations.append("INVALID_SYMBOL_EXPOSURE")

    parsed_portfolio_exposure = None
    if current_portfolio_exposure is None:
        missing.append("MISSING_REQUIRED_DATA")
    else:
        try:
            parsed_portfolio_exposure = _money(current_portfolio_exposure, "current_portfolio_exposure", non_negative=True)
        except ValueError:
            violations.append("INVALID_PORTFOLIO_EXPOSURE")

    parsed_cash = None
    if available_cash is None:
        missing.append("MISSING_REQUIRED_DATA")
    else:
        try:
            parsed_cash = _money(available_cash, "available_cash", non_negative=True)
        except ValueError:
            violations.append("INVALID_AVAILABLE_CASH")

    if missing:
        return _decision(commander_id, timestamp, PaperRiskDecisionType.INSUFFICIENT_DATA, ["MISSING_REQUIRED_DATA"])

    if parsed_quantity is not None and not config.allow_fractional_quantity and parsed_quantity != parsed_quantity.to_integral_value():
        violations.append("FRACTIONAL_QUANTITY_NOT_ALLOWED")
    if commander_execution_exists:
        violations.append("DUPLICATE_COMMANDER_EXECUTION")
    if opening_short:
        if side is not PaperSide.SELL:
            violations.append("INVALID_SHORT_SIDE")
        elif not config.allow_short:
            violations.append("SHORT_NOT_ALLOWED")
        elif short_collateral_available is None:
            return _decision(commander_id, timestamp, PaperRiskDecisionType.INSUFFICIENT_DATA, ["MISSING_REQUIRED_DATA"])

    if parsed_quantity is not None and parsed_price is not None:
        proposed_notional = _money(parsed_quantity * parsed_price, "proposed_notional")
        if proposed_notional > config.max_position_notional:
            violations.append("MAX_POSITION_NOTIONAL_EXCEEDED")
        if parsed_portfolio_exposure is not None and parsed_portfolio_exposure + proposed_notional > config.max_portfolio_exposure:
            violations.append("MAX_PORTFOLIO_EXPOSURE_EXCEEDED")
        if parsed_symbol_exposure is not None and parsed_symbol_exposure + proposed_notional > config.max_symbol_exposure:
            violations.append("MAX_SYMBOL_EXPOSURE_EXCEEDED")
        if opening_short and config.allow_short and short_collateral_available is not None:
            collateral = _money(short_collateral_available, "short_collateral_available", non_negative=True)
            if collateral < proposed_notional:
                violations.append("INSUFFICIENT_SHORT_COLLATERAL")
        elif side is PaperSide.BUY and parsed_cash is not None and parsed_cash < proposed_notional:
            violations.append("INSUFFICIENT_CASH")

    if violations:
        return _decision(commander_id, timestamp, PaperRiskDecisionType.REJECT, violations)
    return _decision(commander_id, timestamp, PaperRiskDecisionType.ALLOW, [])

