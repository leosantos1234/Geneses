import sys
import dataclasses
from datetime import datetime, timezone
from decimal import Decimal

import pytest

sys.path.insert(0, "Robo")

from genesis_paper import (  # noqa: E402
    PaperEffectType,
    PaperExecutionDecision,
    PaperExecutionDecisionType,
    PaperFill,
    PaperLineage,
    PaperLineageIndex,
    PaperOrder,
    PaperOrderType,
    PaperPortfolioEffect,
    PaperRiskDecision,
    PaperRiskDecisionType,
    PaperSide,
    validate_lineage_artifacts,
)


TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def artifacts(commander="decision-a", order_id="order-a", fill_id="fill-a", effect_id="effect-a"):
    execution = PaperExecutionDecision(
        paper_execution_decision_id=f"execution-{commander}",
        commander_decision_id=commander,
        timestamp_utc=TS,
        decision=PaperExecutionDecisionType.CREATE_ORDER,
    )
    risk = PaperRiskDecision(
        paper_risk_decision_id=f"risk-{commander}",
        commander_decision_id=commander,
        timestamp_utc=TS,
        decision=PaperRiskDecisionType.ALLOW,
    )
    order = PaperOrder(
        paper_order_id=order_id,
        commander_decision_id=commander,
        timestamp_utc=TS,
        symbol="BTC/USD",
        order_type=PaperOrderType.MARKET,
        requested_quantity="2",
    )
    fill = PaperFill(
        paper_fill_id=fill_id,
        paper_order_id=order_id,
        timestamp_utc=TS,
        symbol="BTC/USD",
        side=PaperSide.BUY,
        quantity="2",
        fill_price="100",
    )
    effect = PaperPortfolioEffect(
        paper_effect_id=effect_id,
        paper_portfolio_id="portfolio-1",
        paper_fill_id=fill_id,
        paper_order_id=order_id,
        commander_decision_id=commander,
        timestamp_utc=TS,
        symbol="BTC/USD",
        effect_type=PaperEffectType.OPEN,
        quantity_delta="2",
        cash_delta="-200",
    )
    return execution, risk, order, fill, effect


def test_complete_filled_lineage_and_all_roundtrips():
    values = artifacts()
    lineage = PaperLineage(*values)
    index = PaperLineageIndex().add(lineage)
    assert index.get_lineage_by_commander_decision_id("decision-a") is lineage
    assert index.get_lineage_by_paper_order_id("order-a") is lineage
    assert index.get_lineage_by_paper_fill_id("fill-a") is lineage
    assert index.get_lineage_by_paper_effect_id("effect-a") is lineage
    assert lineage.fill.paper_order_id == lineage.order.paper_order_id
    assert lineage.effect.commander_decision_id == lineage.commander_decision_id


def test_no_action_and_rejected_paths_have_no_artifacts():
    execution = PaperExecutionDecision(commander_decision_id="no-action", decision=PaperExecutionDecisionType.NO_ACTION)
    risk = PaperRiskDecision(commander_decision_id="no-action", decision=PaperRiskDecisionType.INSUFFICIENT_DATA)
    assert PaperLineage(execution, risk).order is None

    execution = PaperExecutionDecision(commander_decision_id="rejected", decision=PaperExecutionDecisionType.REJECTED)
    risk = PaperRiskDecision(commander_decision_id="rejected", decision=PaperRiskDecisionType.REJECT)
    assert PaperLineage(execution, risk).fill is None


def test_mismatched_relationships_are_rejected():
    execution, risk, order, fill, effect = artifacts()
    with pytest.raises(ValueError, match="fill order mismatch"):
        PaperLineage(execution, risk, order, PaperFill(paper_fill_id="f2", paper_order_id="other", symbol="BTC/USD", quantity=1, fill_price=1))
    with pytest.raises(ValueError, match="effect lineage mismatch"):
        PaperLineage(execution, risk, order, fill, PaperPortfolioEffect(
            paper_effect_id="effect-b", paper_portfolio_id="p", paper_fill_id=fill.paper_fill_id,
            paper_order_id=order.paper_order_id, commander_decision_id="other", symbol="BTC/USD",
        ))
    with pytest.raises(ValueError, match="risk decision commander mismatch"):
        PaperLineage(execution, PaperRiskDecision(commander_decision_id="other"), order, fill, effect)


def test_orphan_and_duplicate_validation():
    execution, risk, order, fill, effect = artifacts()
    orphan_order = PaperOrder(paper_order_id="orphan-order", commander_decision_id="missing", symbol="BTC/USD", requested_quantity=1)
    orphan_fill = PaperFill(paper_fill_id="orphan-fill", paper_order_id="missing-order", symbol="BTC/USD", quantity=1, fill_price=1)
    orphan_effect = PaperPortfolioEffect(paper_effect_id="orphan-effect", paper_portfolio_id="p", paper_fill_id="missing-fill", paper_order_id="missing-order", commander_decision_id="missing", symbol="BTC/USD")
    report = validate_lineage_artifacts([execution], [risk], [order, order, orphan_order], [fill, fill, orphan_fill], [effect, effect, orphan_effect])
    assert report.orphan_paper_order_count == 1
    assert report.orphan_paper_fill_count == 1
    assert report.orphan_paper_effect_count == 1
    assert report.duplicate_paper_order_id_count == 1
    assert report.duplicate_paper_fill_id_count == 1
    assert report.duplicate_paper_effect_id_count == 1
    assert report.duplicate_commander_execution_count == 1


def test_index_rejects_duplicate_commander_and_ids():
    first = PaperLineage(*artifacts())
    with pytest.raises(ValueError, match="commander decision"):
        PaperLineageIndex().add(first).add(first)
    second = PaperLineage(*artifacts(commander="decision-b", order_id="order-a", fill_id="fill-b", effect_id="effect-b"))
    with pytest.raises(ValueError, match="paper_order_id"):
        PaperLineageIndex().add(first).add(second)


def test_identifiers_are_immutable_and_serialization_is_deterministic():
    execution, risk, order, fill, effect = artifacts()
    with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
        order.commander_decision_id = "other"
    with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
        fill.paper_order_id = "other"
    with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
        effect.paper_fill_id = "other"
    first = PaperLineage(execution, risk, order, fill, effect).to_dict()
    second = PaperLineage(execution, risk, order, fill, effect).to_dict()
    assert first == second
    assert first["order"]["paper_only"] is True
    assert order.execution_authority.value == "NONE"
