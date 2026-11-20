from __future__ import annotations

from datetime import datetime, timezone

from utils.breakout_observability import (
    BreakoutEvaluation,
    aggregate_breakout_records,
)


def _record(symbol="BTCUSDT", direction="BUY", blocker=None, *, trigger=True):
    evaluation = BreakoutEvaluation(
        symbol=symbol,
        timeframe="5",
        regime=1,
        direction_candidate=direction,
        timestamp_utc=datetime(2026, 10, 10, tzinfo=timezone.utc),
        record_provenance="TEST",
    )
    for stage in (
        "REGIME_ELIGIBLE", "STRUCTURE_AVAILABLE", "PRICE_BREAKOUT",
        "VOLUME_CONFIRMATION", "DIRECTION_DETERMINED", "EMA_TREND_CONFIRMATION",
        "WEIGHTED_SCORE", "REGIME_MIN_SCORE", "RRR_VALIDATION",
    ):
        evaluation.stage(stage, "PASS")
    evaluation.stage("TRIGGER_CONFIRMATION", "PASS" if trigger else "FAIL", reason_code=None if trigger else "TRIGGER_TIMEOUT")
    evaluation.stage("FINAL_ENTRY_ELIGIBLE", "PASS" if trigger else "NOT_APPLICABLE")
    if blocker:
        evaluation.primary_blocker = blocker
    return {
        "symbol": evaluation.symbol,
        "direction": evaluation.direction_candidate,
        "stage_results": evaluation.stage_results,
        "final_state": "TRIGGER_CONFIRMED" if trigger else "QUALIFIED_SIGNAL",
        "primary_blocker": evaluation.primary_blocker,
    }


def test_evaluation_identity_symbol_and_utc(monkeypatch):
    jobs = []
    monkeypatch.setattr("utils.breakout_observability._enqueue", lambda kind, data: jobs.append((kind, data)))
    evaluation = BreakoutEvaluation(
        symbol="btcusdt", timeframe="5", regime=2,
        timestamp_utc=datetime(2026, 10, 10, 12, 0), record_provenance="TEST",
    )
    evaluation.stage("REGIME_ELIGIBLE", "FAIL", observed_value=3, threshold="1|2", operator="in", reason_code="REGIME_NOT_ELIGIBLE")
    first = evaluation.finish("EVALUATED")
    second = evaluation.finish("EVALUATED")
    assert first == second
    assert jobs[0][1]["symbol"] == "BTCUSDT"
    assert jobs[0][1]["timestamp_utc"].tzinfo == timezone.utc
    assert jobs[0][1]["stage_results"]["REGIME_ELIGIBLE"]["result"] == "FAIL"


def test_stage_status_and_primary_blocker():
    row = _record("ETHUSDT", "SELL", "VOLUME_NOT_CONFIRMED", trigger=False)
    assert row["stage_results"]["TRIGGER_CONFIRMATION"]["result"] == "FAIL"
    assert row["primary_blocker"] == "VOLUME_NOT_CONFIRMED"


def test_symbol_direction_and_blocker_aggregation():
    report = aggregate_breakout_records([
        _record("BTCUSDT", "BUY"),
        _record("BTCUSDT", "BUY", "TRIGGER_TIMEOUT", trigger=False),
        _record("XRPUSDT", "SELL"),
    ])
    btc = report["BTCUSDT:BUY"]
    assert btc["TOTAL_EVALUATIONS"] == 2
    assert btc["TRIGGER_PASS"]["count"] == 1
    assert btc["TRIGGER_PASS"]["failure_count"] == 1
    assert btc["TOP_BLOCKERS"][0]["reason"] == "TRIGGER_TIMEOUT"
    assert report["XRPUSDT:SELL"]["TOTAL_EVALUATIONS"] == 1


def test_link_retries_when_evaluation_insert_is_not_visible(monkeypatch):
    class Cursor:
        def __init__(self):
            self.rowcount = 0
            self.calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, *args):
            self.calls += 1
            self.rowcount = 1 if self.calls == 2 else 0

    class Connection:
        def __init__(self):
            self.cursor_instance = Cursor()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return self.cursor_instance

        def commit(self):
            pass

    connection = Connection()
    monkeypatch.setattr("utils.breakout_observability.connect_operational_database", lambda: connection)
    from utils.breakout_observability import _write

    _write("link", {"breakout_evaluation_id": "eval-1", "commander_decision_id": "commander-1"})
    assert connection.cursor_instance.calls == 2
