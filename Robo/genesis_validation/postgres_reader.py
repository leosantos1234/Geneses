"""Read-only adapter from canonical PostgreSQL records to validation datasets."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
import json

from genesis_runtime import connect_operational_database

from .dataset import DatasetConfig, EvaluationDataset, PaperOutcome, build_evaluation_dataset


def _utc(value: Any) -> datetime | None:
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


PROVENANCE_VALUES = frozenset({"PRODUCTION_RUNTIME", "TEST", "ACCEPTANCE", "SYNTHETIC", "UNKNOWN"})


def read_commander_decisions(*, connection=None, record_provenance: str | None = "PRODUCTION_RUNTIME", include_unknown: bool = False) -> list[dict[str, Any]]:
    """Read canonical decisions without changing source tables."""

    owned = connection is None
    connection = connection or connect_operational_database(read_only=True)
    try:
        with connection.cursor() as cur:
            cur.execute(
                """SELECT commander_decision_id,timestamp_utc,symbol,decision_type,
                          strategy_id,strategy_name,confidence,reference_price,market_regime,
                          decision_metadata
                     FROM commander_decisions
                    ORDER BY timestamp_utc,commander_decision_id"""
            )
            keys = (
                "commander_decision_id", "timestamp_utc", "symbol", "decision",
                "strategy_id", "strategy_name", "confidence", "reference_price",
                "market_regime", "decision_metadata",
            )
            rows = []
            for row in cur.fetchall():
                item = dict(zip(keys, row))
                metadata = item.pop("decision_metadata") or {}
                if isinstance(metadata, str):
                    try:
                        metadata = json.loads(metadata)
                    except (TypeError, ValueError):
                        metadata = {}
                item["record_provenance"] = metadata.get("record_provenance", "UNKNOWN") if isinstance(metadata, dict) else "UNKNOWN"
                item["timestamp_utc"] = _utc(item["timestamp_utc"])
                if item["record_provenance"] not in PROVENANCE_VALUES:
                    item["record_provenance"] = "UNKNOWN"
                if record_provenance is None or item["record_provenance"] == record_provenance or (include_unknown and item["record_provenance"] == "UNKNOWN"):
                    rows.append(item)
            return rows
    finally:
        if owned:
            connection.close()


def read_paper_outcomes(*, connection=None) -> dict[str, PaperOutcome]:
    """Read one normalized paper outcome per Commander decision."""

    owned = connection is None
    connection = connection or connect_operational_database(read_only=True)
    try:
        with connection.cursor() as cur:
            cur.execute(
                """SELECT o.commander_decision_id,o.status,
                          f.paper_fill_id,f.fill_price,f.fee,f.slippage_cost,f.timestamp_utc,
                          e.paper_effect_id,e.realized_pnl_delta,e.timestamp_utc
                     FROM paper_orders o
                LEFT JOIN LATERAL (
                    SELECT paper_fill_id,fill_price,fee,slippage_cost,timestamp_utc
                      FROM paper_fills
                     WHERE paper_order_id=o.paper_order_id
                     ORDER BY timestamp_utc DESC LIMIT 1
                ) f ON TRUE
                LEFT JOIN LATERAL (
                    SELECT paper_effect_id,realized_pnl_delta,timestamp_utc
                      FROM paper_portfolio_effects
                     WHERE paper_order_id=o.paper_order_id
                     ORDER BY timestamp_utc DESC LIMIT 1
                ) e ON TRUE
                    ORDER BY o.created_at,o.paper_order_id"""
            )
            outcomes: dict[str, PaperOutcome] = {}
            for decision_id, order_status, fill_id, fill_price, fee, slippage, fill_ts, effect_id, pnl, effect_ts in cur.fetchall():
                filled = fill_id is not None
                outcomes[str(decision_id)] = PaperOutcome(
                    commander_decision_id=str(decision_id),
                    paper_execution_outcome="FILLED" if filled else "CREATED",
                    paper_risk_outcome="DATA_NOT_AVAILABLE",
                    paper_order_status=str(order_status or "DATA_NOT_AVAILABLE"),
                    paper_fill_status="FILLED" if filled else "NOT_APPLICABLE",
                    paper_fill_id=str(fill_id) if fill_id is not None else None,
                    paper_effect_id=str(effect_id) if effect_id is not None else None,
                    fill_price=fill_price,
                    fees_paid=fee,
                    slippage_cost=slippage,
                    net_pnl=pnl,
                    outcome_timestamp_utc=_utc(effect_ts or fill_ts),
                )
            return outcomes
    finally:
        if owned:
            connection.close()


def build_current_dataset(*, horizon: str = "H1", duration: timedelta = timedelta(minutes=5), record_provenance: str | None = "PRODUCTION_RUNTIME", include_unknown: bool = False) -> EvaluationDataset:
    """Build the current read-only dataset using canonical PostgreSQL data."""

    with connect_operational_database(read_only=True) as connection:
        decisions = read_commander_decisions(connection=connection, record_provenance=record_provenance, include_unknown=include_unknown)
        outcomes = read_paper_outcomes(connection=connection)
    return build_evaluation_dataset(decisions, outcomes, DatasetConfig({horizon: duration}))


__all__ = ["build_current_dataset", "read_commander_decisions", "read_paper_outcomes"]
