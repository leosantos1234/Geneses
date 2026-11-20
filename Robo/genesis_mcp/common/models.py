"""Pydantic models compartilhados entre tools V1 e V2."""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class ErrorInfo(BaseModel):
    code: str
    message: str


class OperationalResponse(BaseModel):
    """Common status fields for read-only operational responses."""

    status: str = "ok"
    backend_ok: bool = True
    query_ok: bool = True

    def model_post_init(self, __context: Any) -> None:
        if getattr(self, "error", None) is not None:
            self.status = "error"
            self.backend_ok = False
            self.query_ok = False


class DatabaseHealth(BaseModel):
    backend: str
    status: str
    canonical: bool
    target: str | None = None


class WorkerHealth(BaseModel):
    status: str


class RiskHealth(BaseModel):
    status: str


class HealthResponse(BaseModel):
    request_id: str
    status: str
    database: DatabaseHealth | None = None
    worker: WorkerHealth
    risk: RiskHealth
    environment: str
    timestamp: str
    error: ErrorInfo | None = None


class Position(BaseModel):
    trade_id: str | None = None
    timestamp: Any = None
    symbol: str | None = None
    side: str | None = None
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    trailing_stop: float | None = None
    strategy: str | None = None
    status: str | None = None


class PositionsResponse(OperationalResponse):
    request_id: str
    source: str
    count: int
    positions: list[Position]
    error: ErrorInfo | None = None


class Trade(BaseModel):
    trade_id: str | None = None
    timestamp: Any = None
    symbol: str | None = None
    side: str | None = None
    entry_price: float | None = None
    exit_price: float | None = None
    profit_loss: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    trailing_stop: float | None = None
    strategy: str | None = None
    decision_source: str | None = None
    regime: str | None = None
    tendencia: str | None = None
    status: str | None = None


class TradesResponse(OperationalResponse):
    request_id: str
    source: str
    filters: dict[str, Any]
    count: int
    total: int
    has_more: bool
    trades: list[Trade]
    error: ErrorInfo | None = None


class TradeDetailResponse(OperationalResponse):
    request_id: str
    source: str
    trade: dict[str, Any] | None = None
    error: ErrorInfo | None = None


class RiskResponse(OperationalResponse):
    request_id: str
    source: str
    status: str
    kill_switch: bool
    open_positions: int | None = None
    exposure: float | None = None
    daily_pnl: float | None = None
    daily_loss: float | None = None
    limits: dict[str, float | int | str] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    error: ErrorInfo | None = None


class DecisionEvent(BaseModel):
    event_id: int | None = None
    event_ts: str | None = None
    stage: str | None = None
    event_type: str | None = None
    component: str | None = None
    status: str | None = None
    reason_code: str | None = None
    duration_ms: float | None = None
    payload: dict[str, Any] | None = None
    schema_version: str | None = None


class DecisionTrace(BaseModel):
    decision_id: str
    created_at: str | None = None
    updated_at: str | None = None
    symbol: str | None = None
    timeframe: str | None = None
    strategy: str | None = None
    environment: str | None = None
    mode: str | None = None
    final_status: str | None = None
    trade_id: str | None = None
    order_id: str | None = None
    position_id: str | None = None
    schema_version: str | None = None
    decision_context: dict[str, Any] | None = None
    config_fingerprint: str | None = None
    code_version: str | None = None
    strategy_version: str | None = None
    model_version: str | None = None
    reason_code: str | None = None
    reason_message: str | None = None
    legacy: bool | None = None
    lineage_quality: str | None = None
    events: list[DecisionEvent] = Field(default_factory=list)


class DecisionTraceResponse(OperationalResponse):
    request_id: str
    source: str
    trace: DecisionTrace | None = None
    error: ErrorInfo | None = None


class ListDecisionsResponse(OperationalResponse):
    request_id: str
    source: str
    filters: dict[str, Any]
    count: int
    total: int | None  # None when total count not calculated (using LIMIT+1 strategy)
    has_more: bool
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    error: ErrorInfo | None = None


class LSTMPrediction(BaseModel):
    timestamp: str | None = None
    symbol: str | None = None
    direction: str | None = None
    predicted_price: float | None = None
    confidence: float | None = None
    prediction_ts: str | None = None


class LSTMPredictionsResponse(OperationalResponse):
    request_id: str
    source: str
    symbol: str
    count: int
    predictions: list[LSTMPrediction] = Field(default_factory=list)
    error: ErrorInfo | None = None


class ModelArtifact(BaseModel):
    name: str
    version: str | None = None
    path: str
    size_bytes: int | None = None
    modified_at: str | None = None
    model_type: str | None = None
    category: str = "artifact"
    symbol: str | None = None
    status: str = "AVAILABLE"  # AVAILABLE, ACTIVE_IN_LIVE, INACTIVE, UNKNOWN
    operational_evidence: str | None = None  # Why the status was assigned


class ModelInventoryResponse(OperationalResponse):
    request_id: str
    source: str
    count: int
    models: list[ModelArtifact] = Field(default_factory=list)
    error: ErrorInfo | None = None


class CommanderDecision(BaseModel):
    decision_id: str
    created_at: str | None = None
    symbol: str | None = None
    strategy: str | None = None
    commander_status: str | None = None
    reason_code: str | None = None
    trade_id: str | None = None


class CommanderDecisionResponse(BaseModel):
    request_id: str
    source: str
    decision: CommanderDecision | None = None
    error: ErrorInfo | None = None
