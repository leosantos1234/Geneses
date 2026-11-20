"""Read-only analytical confidence calibration for Sprint 9 Phase 4."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from statistics import median
from typing import Iterable

from .contracts import DecisionType, SampleSizeStatus, ValidationObservation, classify_sample_size
from .labeling import DirectionLabel, ValidationLabelResult


class CalibrationMetricStatus(Enum):
    VALUE = "VALUE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


DEFAULT_CONFIDENCE_BANDS = (
    ("0.00-0.20", Decimal("0.00"), Decimal("0.20"), False),
    ("0.20-0.40", Decimal("0.20"), Decimal("0.40"), False),
    ("0.40-0.60", Decimal("0.40"), Decimal("0.60"), False),
    ("0.60-0.80", Decimal("0.60"), Decimal("0.80"), False),
    ("0.80-1.00", Decimal("0.80"), Decimal("1.00"), True),
)


@dataclass(frozen=True)
class ConfidenceBand:
    name: str
    lower_bound: Decimal
    upper_bound: Decimal
    upper_inclusive: bool = False

    def __post_init__(self) -> None:
        if self.lower_bound < 0 or self.upper_bound > 1 or self.lower_bound >= self.upper_bound:
            raise ValueError("confidence band bounds invalid")
        if not self.name:
            raise ValueError("confidence band name required")

    def contains(self, value: Decimal) -> bool:
        return self.lower_bound <= value <= self.upper_bound if self.upper_inclusive else self.lower_bound <= value < self.upper_bound


@dataclass(frozen=True)
class CalibrationConfig:
    bands: tuple[ConfidenceBand, ...] = tuple(ConfidenceBand(*item) for item in DEFAULT_CONFIDENCE_BANDS)
    high_confidence_threshold: Decimal = Decimal("0.50")

    def __post_init__(self) -> None:
        if not self.bands:
            raise ValueError("at least one confidence band is required")
        if not Decimal("0") <= self.high_confidence_threshold <= Decimal("1"):
            raise ValueError("high confidence threshold must be between 0 and 1")
        for left, right in zip(self.bands, self.bands[1:]):
            if left.upper_bound != right.lower_bound:
                raise ValueError("confidence bands must be contiguous")


@dataclass(frozen=True)
class ConfidenceBandSummary:
    band: str
    lower_bound: Decimal
    upper_bound: Decimal
    sample_count: int
    mean_confidence: Decimal | None
    correct_direction_count: int
    wrong_direction_count: int
    neutral_count: int
    data_not_available_count: int
    directional_accuracy: Decimal | None
    average_net_pnl: Decimal | None
    expectancy: Decimal | None
    average_return_pct: Decimal | None
    median_return_pct: Decimal | None
    sample_size_status: SampleSizeStatus
    confidence_outcome_gap: Decimal | None
    strategy_id: str | None = None
    validation_horizon: str | None = None


@dataclass(frozen=True)
class CalibrationRecord:
    observation: ValidationObservation
    label: ValidationLabelResult

    def __post_init__(self) -> None:
        identity = (self.observation.commander_decision_id, self.observation.validation_horizon)
        label_identity = (self.label.commander_decision_id, self.label.validation_horizon)
        if identity != label_identity:
            raise ValueError("observation and label identity mismatch")


@dataclass(frozen=True)
class ConfidenceCalibrationSummary:
    summaries: tuple[ConfidenceBandSummary, ...]
    missing_confidence_count: int
    non_candidate_count: int
    confidence_semantics: str
    monotonicity_status: str
    overconfidence_status: str
    underconfidence_status: str
    high_confidence_comparison: dict[str, Decimal | None | int]
    low_confidence_comparison: dict[str, Decimal | None | int]


def _mean(values: list[Decimal]) -> Decimal | None:
    return sum(values, Decimal("0")) / len(values) if values else None


def _summary(records: list[CalibrationRecord], band: ConfidenceBand, *, strategy_id: str | None, horizon: str | None) -> ConfidenceBandSummary:
    in_band = [item for item in records if item.observation.confidence is not None and band.contains(item.observation.confidence)]
    correct = sum(item.label.direction_label is DirectionLabel.CORRECT_DIRECTION for item in in_band)
    wrong = sum(item.label.direction_label is DirectionLabel.WRONG_DIRECTION for item in in_band)
    neutral = sum(item.label.direction_label is DirectionLabel.NEUTRAL for item in in_band)
    unavailable = sum(item.label.direction_label is DirectionLabel.DATA_NOT_AVAILABLE for item in in_band)
    denominator = correct + wrong
    directional_accuracy = Decimal(correct) / denominator if denominator else None
    economics = [item.observation.net_pnl for item in in_band if item.observation.net_pnl is not None]
    returns = [item.observation.return_pct for item in in_band if item.observation.return_pct is not None]
    mean_confidence = _mean([item.observation.confidence for item in in_band if item.observation.confidence is not None])
    return ConfidenceBandSummary(
        band=band.name, lower_bound=band.lower_bound, upper_bound=band.upper_bound,
        sample_count=len(in_band), mean_confidence=mean_confidence,
        correct_direction_count=correct, wrong_direction_count=wrong,
        neutral_count=neutral, data_not_available_count=unavailable,
        directional_accuracy=directional_accuracy, average_net_pnl=_mean(economics),
        expectancy=_mean(economics), average_return_pct=_mean(returns),
        median_return_pct=Decimal(str(median(returns))) if returns else None,
        sample_size_status=classify_sample_size(len(in_band)),
        confidence_outcome_gap=None if directional_accuracy is None or mean_confidence is None else directional_accuracy - mean_confidence,
        strategy_id=strategy_id, validation_horizon=horizon,
    )


def _comparison(records: list[CalibrationRecord], high: bool, threshold: Decimal) -> dict[str, Decimal | None | int]:
    selected = [item for item in records if item.observation.confidence is not None and ((item.observation.confidence >= threshold) == high)]
    correct = sum(item.label.direction_label is DirectionLabel.CORRECT_DIRECTION for item in selected)
    wrong = sum(item.label.direction_label is DirectionLabel.WRONG_DIRECTION for item in selected)
    economics = [item.observation.net_pnl for item in selected if item.observation.net_pnl is not None]
    returns = [item.observation.return_pct for item in selected if item.observation.return_pct is not None]
    return {
        "sample_count": len(selected),
        "directional_accuracy": Decimal(correct) / (correct + wrong) if correct + wrong else None,
        "average_net_pnl": _mean(economics),
        "expectancy": _mean(economics),
        "average_return_pct": _mean(returns),
    }


def _monotonicity(summaries: list[ConfidenceBandSummary]) -> str:
    populated = [item for item in summaries if item.directional_accuracy is not None and item.expectancy is not None]
    if len(populated) < 2:
        return "INSUFFICIENT_DATA"
    accuracy_non_decreasing = all(a.directional_accuracy <= b.directional_accuracy for a, b in zip(populated, populated[1:]))
    expectancy_non_decreasing = all(a.expectancy <= b.expectancy for a, b in zip(populated, populated[1:]))
    if accuracy_non_decreasing and expectancy_non_decreasing:
        return "MONOTONIC"
    if accuracy_non_decreasing or expectancy_non_decreasing:
        return "MOSTLY_MONOTONIC"
    return "NON_MONOTONIC"


def calibrate_confidence(records: Iterable[CalibrationRecord], config: CalibrationConfig = CalibrationConfig()) -> ConfidenceCalibrationSummary:
    """Analyze numeric candidate confidence; no source record is changed."""
    values = tuple(records)
    candidates = [item for item in values if item.observation.decision_type in {DecisionType.CANDIDATE_LONG, DecisionType.CANDIDATE_SHORT}]
    non_candidates = len(values) - len(candidates)
    summaries = tuple(_summary(candidates, band, strategy_id=None, horizon=None) for band in config.bands)
    high = _comparison(candidates, True, config.high_confidence_threshold)
    low = _comparison(candidates, False, config.high_confidence_threshold)
    populated = [item for item in summaries if item.confidence_outcome_gap is not None]
    over = "INSUFFICIENT_DATA" if not populated else ("POSSIBLE_OVERCONFIDENCE" if any(item.confidence_outcome_gap < 0 for item in populated if item.lower_bound >= config.high_confidence_threshold) else "NO_EVIDENCE")
    under = "INSUFFICIENT_DATA" if not populated else ("POSSIBLE_UNDERCONFIDENCE" if any(item.confidence_outcome_gap > 0 for item in populated if item.upper_bound <= config.high_confidence_threshold) else "NO_EVIDENCE")
    return ConfidenceCalibrationSummary(
        summaries=summaries,
        missing_confidence_count=sum(item.observation.confidence is None for item in candidates),
        non_candidate_count=non_candidates,
        confidence_semantics="SCORE_ONLY",
        monotonicity_status=_monotonicity(list(summaries)),
        overconfidence_status=over, underconfidence_status=under,
        high_confidence_comparison=high, low_confidence_comparison=low,
    )


def calibrate_by_segment(records: Iterable[CalibrationRecord], config: CalibrationConfig = CalibrationConfig()) -> dict[tuple[str | None, str | None, str | None], ConfidenceCalibrationSummary]:
    """Optional analytical slicing by horizon, direction and canonical strategy."""
    values = tuple(records)
    keys = sorted({(item.observation.validation_horizon, item.observation.decision_type.value, item.observation.strategy_id) for item in values})
    result = {}
    for horizon, direction, strategy in keys:
        subset = [item for item in values if (item.observation.validation_horizon, item.observation.decision_type.value, item.observation.strategy_id) == (horizon, direction, strategy)]
        result[(horizon, direction, strategy)] = calibrate_confidence(subset, config)
    return result


__all__ = [
    "CalibrationConfig", "CalibrationRecord", "ConfidenceBand", "ConfidenceBandSummary",
    "ConfidenceCalibrationSummary", "DEFAULT_CONFIDENCE_BANDS", "calibrate_by_segment",
    "calibrate_confidence",
]
