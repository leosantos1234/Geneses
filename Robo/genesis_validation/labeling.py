"""Deterministic outcome labeling for Phase 3.

Labels are derived only from a Phase 2 observation.  The observation and all
economic/confidence/strategy fields remain untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .contracts import DATA_NOT_AVAILABLE, DecisionType, ValidationObservation
from .dataset import NOT_APPLICABLE


class DirectionLabel(Enum):
    CORRECT_DIRECTION = "CORRECT_DIRECTION"
    WRONG_DIRECTION = "WRONG_DIRECTION"
    NEUTRAL = "NEUTRAL"
    NOT_APPLICABLE = NOT_APPLICABLE
    DATA_NOT_AVAILABLE = DATA_NOT_AVAILABLE


class ExecutionLabel(Enum):
    EXECUTED = "EXECUTED"
    NOT_EXECUTED = "NOT_EXECUTED"
    NOT_APPLICABLE = NOT_APPLICABLE
    DATA_NOT_AVAILABLE = DATA_NOT_AVAILABLE


@dataclass(frozen=True)
class ValidationLabelResult:
    commander_decision_id: str
    validation_horizon: str
    decision_type: DecisionType
    direction_label: DirectionLabel
    execution_label: ExecutionLabel
    economic_outcome_status: str
    source_data_status: str
    paper_only: bool = True

    def __post_init__(self) -> None:
        if not self.commander_decision_id or not self.validation_horizon:
            raise ValueError("label identity required")
        if not isinstance(self.decision_type, DecisionType):
            raise ValueError("decision_type invalid")
        if not isinstance(self.direction_label, DirectionLabel):
            raise ValueError("direction_label invalid")
        if not isinstance(self.execution_label, ExecutionLabel):
            raise ValueError("execution_label invalid")
        if self.paper_only is not True:
            raise ValueError("paper_only must be True")

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "commander_decision_id": self.commander_decision_id,
            "validation_horizon": self.validation_horizon,
            "decision_type": self.decision_type.value,
            "direction_label": self.direction_label.value,
            "execution_label": self.execution_label.value,
            "economic_outcome_status": self.economic_outcome_status,
            "source_data_status": self.source_data_status,
            "paper_only": self.paper_only,
        }


def _direction_label(observation: ValidationObservation) -> DirectionLabel:
    if observation.decision_type in {DecisionType.NO_ACTION, DecisionType.WATCH}:
        return DirectionLabel.NOT_APPLICABLE
    if observation.decision_type is DecisionType.INSUFFICIENT_DATA:
        return DirectionLabel.DATA_NOT_AVAILABLE
    if observation.return_pct is None:
        return DirectionLabel.DATA_NOT_AVAILABLE
    if observation.return_pct == 0:
        return DirectionLabel.NEUTRAL
    if observation.decision_type is DecisionType.CANDIDATE_LONG:
        return DirectionLabel.CORRECT_DIRECTION if observation.return_pct > 0 else DirectionLabel.WRONG_DIRECTION
    return DirectionLabel.CORRECT_DIRECTION if observation.return_pct < 0 else DirectionLabel.WRONG_DIRECTION


def _execution_label(observation: ValidationObservation) -> ExecutionLabel:
    if observation.decision_type in {DecisionType.NO_ACTION, DecisionType.WATCH, DecisionType.INSUFFICIENT_DATA}:
        return ExecutionLabel.NOT_APPLICABLE
    if observation.paper_execution_outcome == DATA_NOT_AVAILABLE:
        return ExecutionLabel.DATA_NOT_AVAILABLE
    status = observation.fill_outcome.upper()
    if status == "FILLED" or observation.paper_execution_outcome.upper() == "FILLED":
        return ExecutionLabel.EXECUTED
    if status in {"PENDING", "EXPIRED", "CANCELLED", "REJECTED", NOT_APPLICABLE}:
        return ExecutionLabel.NOT_EXECUTED
    if observation.risk_outcome.upper() in {"REJECT", "REJECTED"}:
        return ExecutionLabel.NOT_EXECUTED
    return ExecutionLabel.DATA_NOT_AVAILABLE


def label_observation(observation: ValidationObservation) -> ValidationLabelResult:
    """Label one observation without mutating it or its economic fields."""
    if not isinstance(observation, ValidationObservation):
        raise TypeError("observation must be ValidationObservation")
    return ValidationLabelResult(
        commander_decision_id=observation.commander_decision_id,
        validation_horizon=observation.validation_horizon,
        decision_type=observation.decision_type,
        direction_label=_direction_label(observation),
        execution_label=_execution_label(observation),
        economic_outcome_status=("AVAILABLE" if observation.return_pct is not None else DATA_NOT_AVAILABLE),
        source_data_status=observation.source_data_status,
    )


def label_dataset(observations: Iterable[ValidationObservation]) -> tuple[ValidationLabelResult, ...]:
    """Label a batch in stable input order, preserving one output per input."""
    rows = tuple(observations)
    results = tuple(label_observation(item) for item in rows)
    identities = [(item.commander_decision_id, item.validation_horizon) for item in results]
    if len(set(identities)) != len(identities):
        raise ValueError("duplicate label output identity")
    return results


def summarize_labels(results: Iterable[ValidationLabelResult]) -> dict[str, int]:
    values = tuple(results)
    return {
        "correct_direction_count": sum(item.direction_label is DirectionLabel.CORRECT_DIRECTION for item in values),
        "wrong_direction_count": sum(item.direction_label is DirectionLabel.WRONG_DIRECTION for item in values),
        "neutral_count": sum(item.direction_label is DirectionLabel.NEUTRAL for item in values),
        "not_executed_count": sum(item.execution_label is ExecutionLabel.NOT_EXECUTED for item in values),
        "not_applicable_count": sum(item.direction_label is DirectionLabel.NOT_APPLICABLE for item in values),
        "data_not_available_count": sum(item.direction_label is DirectionLabel.DATA_NOT_AVAILABLE for item in values),
    }


__all__ = [
    "DirectionLabel", "ExecutionLabel", "ValidationLabelResult",
    "label_dataset", "label_observation", "summarize_labels",
]
