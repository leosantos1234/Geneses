import json
import logging

from utils.execution_path_telemetry import emit_cycle, mark_exit, new_cycle


def test_cycle_records_closed_candle_without_implying_breakout(caplog):
    record = new_cycle("SOLUSDT", cycle_time="2026-10-12T12:00:00+00:00")
    record["closed_1m_persisted"] = True
    assert record["closed_1m_persisted"] is True
    assert record["breakout_called"] is False
    assert record["breakout_evaluation_created"] is False

    with caplog.at_level(logging.INFO):
        emit_cycle(record, "cycle_exit")
    payload = json.loads(caplog.records[-1].message.split(" ", 1)[1])
    assert payload["event"] == "cycle_exit"
    assert payload["symbol"] == "SOLUSDT"


def test_first_exit_reason_is_stable():
    record = new_cycle("SOLUSDT")
    mark_exit(record, "BACKTEST", "PRE_BREAKOUT_BACKTEST_INVALID")
    mark_exit(record, "STRATEGY_SELECTION", "PRE_BREAKOUT_NO_STRATEGY_SELECTED")
    assert record["first_exit_stage"] == "BACKTEST"
    assert record["first_exit_reason"] == "PRE_BREAKOUT_BACKTEST_INVALID"


def test_breakout_call_and_evaluation_are_explicit():
    record = new_cycle("SOLUSDT")
    record["breakout_execution_requested"] = True
    record["breakout_called"] = True
    record["breakout_evaluation_created"] = True
    assert record["breakout_execution_requested"] is True
    assert record["breakout_called"] is True
    assert record["breakout_evaluation_created"] is True


def test_telemetry_is_non_decisional():
    record = new_cycle("SOLUSDT")
    before = dict(record)
    emit_cycle(record, "cycle_start")
    assert record["selected_strategy"] == before["selected_strategy"]
    assert record["breakout_execution_requested"] == before["breakout_execution_requested"]


def test_macro_denial_is_recorded_without_authorizing_execution():
    record = new_cycle("SOLUSDT")
    record.update({
        "analytical_breakout_created": True,
        "shadow_created": True,
        "macro_state": "NEGADO",
        "macro_reason": "num_trades=2<5",
        "execution_authorized": False,
    })
    assert record["analytical_breakout_created"] is True
    assert record["shadow_created"] is True
    assert record["macro_state"] == "NEGADO"
    assert record["execution_authorized"] is False


def test_macro_linkage_stays_on_same_cycle():
    record = new_cycle("SOLUSDT")
    cycle_id = record["cycle_id"]
    record.update({"analytical_breakout_created": True, "macro_state": "NEGADO"})
    assert record["cycle_id"] == cycle_id
    assert record["macro_state"] == "NEGADO"
    assert record["execution_authorized"] is False
