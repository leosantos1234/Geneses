import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_phase7_postgres_acceptance import (  # noqa: E402
    PASS_REQUIRED_KEYS,
    ZERO_REQUIRED_KEYS,
    final_acceptance_is_green,
)


def green_result():
    result = {key: "PASS" for key in PASS_REQUIRED_KEYS}
    result.update({key: "0" for key in ZERO_REQUIRED_KEYS})
    result["APPEND_ONLY_TEST_EVENTS_CREATED"] = "2"
    result.update({
        "RETAINED_PAPER_PORTFOLIOS": "1",
        "RETAINED_PAPER_ORDERS": "1",
        "RETAINED_PAPER_FILLS": "1",
        "RETAINED_PAPER_EFFECTS": "1",
        "RETAINED_PAPER_EVENTS": "2",
    })
    return result


def test_exact_green_acceptance_closes():
    assert final_acceptance_is_green(green_result()) is True


def test_failures_and_unknowns_cannot_close():
    result = green_result()
    result["POSTGRES_READBACK_ROUNDTRIP"] = "FAIL"
    assert final_acceptance_is_green(result) is False
    result = green_result()
    result["TEMPORARY_MUTABLE_ARTIFACT_ROWS_REMAINING"] = "1"
    assert final_acceptance_is_green(result) is False
    result = green_result()
    result["RETAINED_ACCEPTANCE_HISTORY_UNEXPLAINED_ROWS"] = "UNKNOWN"
    assert final_acceptance_is_green(result) is False


def test_retained_append_only_history_is_valid_green_state():
    result = green_result()
    result["PAPER_EVENT_PRIOR_ROW_MUTATIONS"] = "0"
    result["RETAINED_PAPER_EVENTS"] = "2"
    assert final_acceptance_is_green(result) is True

