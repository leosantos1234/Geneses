from datetime import datetime, timezone
from pathlib import Path

from utils.breakout_observability import (
    BreakoutEvaluation,
    breakout_regime_eligible,
    deterministic_breakout_evaluation_id,
    derive_candle_identity,
    link_breakout_evaluation,
)
from utils.breakout_runtime_context import (
    build_breakout_evaluation_kwargs,
    regime_authority_metadata,
    resolve_breakout_regime_override,
    select_backtest_regime_override,
)
from regime.entry_profile import runtime_strategy_for_regime


def test_provenance_is_explicit_and_fail_closed(monkeypatch):
    jobs = []
    monkeypatch.setattr("utils.breakout_observability._enqueue", lambda kind, data: jobs.append((kind, data)))

    contexts = (
        ("PRODUCTION_RUNTIME", "PRODUCTION_RUNTIME"),
        ("BACKTEST_RUNTIME", "BACKTEST_RUNTIME"),
        ("TEST", "TEST"),
        ("UNKNOWN", "UNKNOWN"),
    )
    for provenance, expected in contexts:
        evaluation = BreakoutEvaluation(symbol="BTCUSDT", timeframe="5", regime=1,
                                        record_provenance=provenance)
        evaluation.finish("EVALUATED")
        assert jobs[-1][1]["record_provenance"] == expected
        assert jobs[-1][1]["evaluation_context"] == expected

    unknown = BreakoutEvaluation(symbol="BTCUSDT", timeframe="5", regime=1, record_provenance="bogus")
    unknown.finish("EVALUATED")
    assert jobs[-1][1]["record_provenance"] == "UNKNOWN"


def test_backtest_candle_identity_is_deterministic():
    row = {"time": "2026-10-10T12:00:00+00:00"}
    identity = derive_candle_identity(row, "5", symbol="btcusdt", candle_closed=True)
    assert identity["candle_open_time"] == datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    assert identity["candle_close_time"] == datetime(2026, 10, 10, 12, 5, tzinfo=timezone.utc)
    assert identity["candle_closed"] is True
    assert identity["analysis_key"] == "BTCUSDT|5|2026-10-10T12:00:00+00:00"


def test_regime_eligibility_and_score_input_semantics():
    assert breakout_regime_eligible(1)
    assert breakout_regime_eligible(2)
    assert not breakout_regime_eligible(3)
    assert not breakout_regime_eligible(4)

    evaluation = BreakoutEvaluation(symbol="BTCUSDT", timeframe="5", regime=2, record_provenance="TEST")
    evaluation.stage("REGIME_ELIGIBLE", "PASS")
    evaluation.stage("EMA_TREND_CONFIRMATION", "FAIL", reason_code="EMA_TREND_NOT_CONFIRMED")
    evaluation.stage("WEIGHTED_SCORE", "PASS")
    assert evaluation.stage_results["EMA_TREND_CONFIRMATION"]["result"] == "FAIL"
    assert evaluation.stage_results["WEIGHTED_SCORE"]["result"] == "PASS"


def test_final_candidate_commander_linkage(monkeypatch):
    jobs = []
    monkeypatch.setattr("utils.breakout_observability._enqueue", lambda kind, data: jobs.append((kind, data)))
    evaluation = BreakoutEvaluation(symbol="BTCUSDT", timeframe="5", regime=1, record_provenance="TEST")
    evaluation.finish("CANDIDATE_CREATED")
    link_breakout_evaluation(evaluation.breakout_evaluation_id, commander_decision_id="decision-1")
    assert jobs[-1][0] == "link"
    assert jobs[-1][1]["commander_decision_id"] == "decision-1"
    assert jobs[-1][1]["commander_invoked"] is True


def test_natural_closed_candle_production_context_is_persistable(monkeypatch):
    jobs = []
    monkeypatch.setattr("utils.breakout_observability._enqueue", lambda kind, data: jobs.append((kind, data)))
    key = "BTCUSDT|5|2026-10-10T12:00:00+00:00|PRODUCTION_RUNTIME"
    evaluation = BreakoutEvaluation(
        symbol="BTCUSDT", timeframe="5", regime=1,
        record_provenance="PRODUCTION_RUNTIME",
        evaluation_context="PRODUCTION_RUNTIME",
        run_id="worker:BTCUSDT:123",
        candle_open_time=datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc),
        candle_close_time=datetime(2026, 10, 10, 12, 5, tzinfo=timezone.utc),
        candle_closed=True,
        analysis_key=key,
        breakout_evaluation_id=deterministic_breakout_evaluation_id(key),
    )
    evaluation.finish("EVALUATED")
    row = jobs[-1][1]
    assert row["record_provenance"] == "PRODUCTION_RUNTIME"
    assert row["evaluation_context"] == "PRODUCTION_RUNTIME"
    assert row["candle_closed"] is True
    assert row["analysis_key"] == key


def test_natural_runtime_context_supplies_analysis_key_once(monkeypatch):
    jobs = []
    monkeypatch.setattr("utils.breakout_observability._enqueue", lambda kind, data: jobs.append((kind, data)))
    key = "ETHUSDT|5|2026-10-10T12:00:00+00:00|PRODUCTION_RUNTIME"
    context = {
        "candle_open_time": datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc),
        "candle_close_time": datetime(2026, 10, 10, 12, 5, tzinfo=timezone.utc),
        "candle_closed": True,
        "analysis_key": key,
    }
    evaluation = BreakoutEvaluation(
        symbol="ETHUSDT", timeframe="5", regime=2,
        record_provenance="PRODUCTION_RUNTIME",
        evaluation_context="PRODUCTION_RUNTIME",
        run_id="worker:ETHUSDT:123",
        breakout_evaluation_id=deterministic_breakout_evaluation_id(key),
        **context,
    )
    evaluation.finish("EVALUATED")
    assert jobs[-1][1]["analysis_key"] == key


def test_natural_constructor_does_not_explicitly_duplicate_context_analysis_key():
    source = Path(__file__).parents[1].joinpath("estrategia_entrada", "estrategia_breakout_filtros.py").read_text(encoding="utf-8")
    natural_block_start = source.index("natural_target = natural_runtime")
    natural_block_end = source.index('evaluation.stage("REGIME_ELIGIBLE"', natural_block_start)
    natural_block = source[natural_block_start:natural_block_end]
    assert "evaluation_candle=evaluation_candle" in natural_block
    assert "analysis_key=evaluation_analysis_key" not in natural_block


def test_runtime_shaped_context_has_one_source_for_every_identity_field(monkeypatch):
    jobs = []
    monkeypatch.setattr("utils.breakout_observability._enqueue", lambda kind, data: jobs.append((kind, data)))
    key = "ETHUSDT|5|2026-10-10T12:00:00+00:00|PRODUCTION_RUNTIME"
    context = {
        "record_provenance": "PRODUCTION_RUNTIME",
        "evaluation_context": "PRODUCTION_RUNTIME",
        "run_id": "worker:ETHUSDT:123",
        "symbol": "ETHUSDT",
        "timeframe": "5",
        "regime": 2,
        "candle_open_time": datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc),
        "candle_close_time": datetime(2026, 10, 10, 12, 5, tzinfo=timezone.utc),
        "candle_closed": True,
        "analysis_key": key,
    }
    kwargs = build_breakout_evaluation_kwargs(
        symbol="ETHUSDT", timeframe="5", regime=2,
        record_provenance="PRODUCTION_RUNTIME",
        evaluation_context="PRODUCTION_RUNTIME", run_id="worker:ETHUSDT:123",
        breakout_evaluation_id=deterministic_breakout_evaluation_id(key),
        persist=True, evaluation_candle=context, natural_context=True,
    )
    evaluation = BreakoutEvaluation(**kwargs)
    evaluation.finish("EVALUATED")
    row = jobs[-1][1]
    for field in (
        "record_provenance", "evaluation_context", "run_id", "analysis_key",
        "candle_open_time", "candle_close_time", "candle_closed", "symbol", "timeframe",
    ):
        assert row[field] == context[field]


def test_backtest_context_remains_separate_from_production():
    key = "BTCUSDT|5|2026-10-10T12:00:00+00:00"
    kwargs = build_breakout_evaluation_kwargs(
        symbol="BTCUSDT", timeframe="5", regime=1,
        record_provenance="BACKTEST_RUNTIME", evaluation_context="BACKTEST_RUNTIME",
        run_id="backtest:123", breakout_evaluation_id=deterministic_breakout_evaluation_id(key),
        persist=False,
        evaluation_candle={
            "candle_open_time": datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc),
            "candle_close_time": datetime(2026, 10, 10, 12, 5, tzinfo=timezone.utc),
            "candle_closed": True, "analysis_key": key,
        },
        natural_context=False,
    )
    assert kwargs["record_provenance"] == "BACKTEST_RUNTIME"
    assert kwargs["evaluation_context"] == "BACKTEST_RUNTIME"


def test_production_canonical_regime_overrides_internal_regime():
    context = {
        "record_provenance": "PRODUCTION_RUNTIME",
        "evaluation_context": "PRODUCTION_RUNTIME",
    }
    assert resolve_breakout_regime_override(
        canonical_regime=2,
        natural_runtime_context=context,
        configured_override=None,
    ) == 2
    assert regime_authority_metadata(
        natural_runtime_context=context,
        regime_recalculated=True,
    ) == {"regime_source": "CANONICAL_RUNTIME", "regime_recalculated": False}


def test_production_canonical_regime3_remains_ineligible():
    context = {
        "record_provenance": "PRODUCTION_RUNTIME",
        "evaluation_context": "PRODUCTION_RUNTIME",
    }
    regime = resolve_breakout_regime_override(
        canonical_regime=3,
        natural_runtime_context=context,
        configured_override=2,
    )
    assert regime == 3
    assert not breakout_regime_eligible(regime)


def test_backtest_dynamic_regime_override_remains_unmodified():
    assert resolve_breakout_regime_override(
        canonical_regime=2,
        natural_runtime_context=None,
        configured_override=None,
    ) is None
    assert regime_authority_metadata(
        natural_runtime_context=None,
        regime_recalculated=True,
    ) == {"regime_source": "DYNAMIC_BACKTEST", "regime_recalculated": True}


def test_production_runtime_preserves_canonical_override_when_dynamic_backtest_is_enabled():
    context = {
        "record_provenance": "PRODUCTION_RUNTIME",
        "evaluation_context": "PRODUCTION_RUNTIME",
    }
    assert select_backtest_regime_override(
        regime=2, natural_runtime_context=context, dynamic_regime_enabled=True,
    ) == 2


def test_backtest_runtime_keeps_dynamic_recalculation_behavior():
    assert select_backtest_regime_override(
        regime=2, natural_runtime_context=None, dynamic_regime_enabled=True,
    ) is None
    assert select_backtest_regime_override(
        regime=2, natural_runtime_context=None, dynamic_regime_enabled=False,
    ) == 2


def test_production_candle_identity_is_propagated_without_regime_recalculation():
    key = "ETHUSDT|5|2026-10-10T12:00:00+00:00|PRODUCTION_RUNTIME"
    context = {
        "record_provenance": "PRODUCTION_RUNTIME",
        "evaluation_context": "PRODUCTION_RUNTIME",
        "canonical_regime": 2,
        "regime_source": "CANONICAL_RUNTIME",
        "regime_recalculated": False,
        "candle_open_time": datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc),
        "candle_close_time": datetime(2026, 10, 10, 12, 5, tzinfo=timezone.utc),
        "candle_closed": True,
        "analysis_key": key,
    }
    kwargs = build_breakout_evaluation_kwargs(
        symbol="ETHUSDT", timeframe="5", regime=2,
        record_provenance="PRODUCTION_RUNTIME", evaluation_context="PRODUCTION_RUNTIME",
        run_id="worker:ETHUSDT:123",
        breakout_evaluation_id=deterministic_breakout_evaluation_id(key),
        persist=True, evaluation_candle=context, natural_context=True,
    )
    assert kwargs["candle_open_time"] == context["candle_open_time"]
    assert kwargs["candle_close_time"] == context["candle_close_time"]
    assert kwargs["candle_closed"] is True
    assert "canonical_regime" not in kwargs


def test_regime_authority_observability_is_persisted_in_stage_ledger(monkeypatch):
    jobs = []
    monkeypatch.setattr("utils.breakout_observability._enqueue", lambda kind, data: jobs.append((kind, data)))
    evaluation = BreakoutEvaluation(
        symbol="ETHUSDT", timeframe="5", regime=2,
        record_provenance="PRODUCTION_RUNTIME", evaluation_context="PRODUCTION_RUNTIME",
    )
    authority = regime_authority_metadata(
        natural_runtime_context={
            "record_provenance": "PRODUCTION_RUNTIME",
            "evaluation_context": "PRODUCTION_RUNTIME",
        },
        regime_recalculated=False,
    )
    evaluation.stage_results["REGIME_AUTHORITY"] = {
        "result": "PASS", "observed_value": 2, **authority,
    }
    evaluation.finish("EVALUATED")
    assert jobs[-1][1]["stage_results"]["REGIME_AUTHORITY"]["regime_source"] == "CANONICAL_RUNTIME"
    assert jobs[-1][1]["stage_results"]["REGIME_AUTHORITY"]["regime_recalculated"] is False


def test_same_natural_candle_has_same_idempotency_key():
    key = "ETHUSDT|5|2026-10-10T12:00:00+00:00|PRODUCTION_RUNTIME"
    assert deterministic_breakout_evaluation_id(key) == deterministic_breakout_evaluation_id(key)


def test_breakout_only_regime3_does_not_route_market_making():
    assert runtime_strategy_for_regime(3, breakout_only=1) is None
    assert runtime_strategy_for_regime(4, breakout_only=1) is None


def test_natural_runtime_never_defaults_to_backtest():
    evaluation = BreakoutEvaluation(
        symbol="SOLUSDT", timeframe="5", regime=2,
        record_provenance="PRODUCTION_RUNTIME",
        evaluation_context="PRODUCTION_RUNTIME",
    )
    assert evaluation.record_provenance != "BACKTEST_RUNTIME"
