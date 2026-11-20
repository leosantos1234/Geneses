from datetime import datetime, timezone
from pathlib import Path
import re

from utils.shadow_decision_telemetry import (
    SHADOW_POLICY,
    _forensic_db_values,
    build_comparison_payload,
    telemetry_id,
)


def _snapshot(*, blocker=None, price="PASS", trend="PASS", score="PASS", final="PASS",
              raw=False, direction=None):
    return {
        "symbol": "SOLUSDT",
        "timeframe": "5m",
        "candle_open_time": datetime(2026, 10, 11, 18, 0, tzinfo=timezone.utc),
        "candle_close_time": datetime(2026, 10, 11, 18, 5, tzinfo=timezone.utc),
        "regime": 2,
        "primary_blocker": blocker,
        "raw_breakout": raw,
        "direction_candidate": direction,
        "stage_results": {
            "PRICE_BREAKOUT": {"result": price},
            "EMA_TREND_CONFIRMATION": {"result": trend},
            "WEIGHTED_SCORE": {"result": score},
            "FINAL_ENTRY_ELIGIBLE": {"result": final},
        },
        "feature_vector": {"close": 110.0},
    }


def test_identity_is_deterministic_and_policy_scoped():
    ts = datetime(2026, 10, 11, 18, 0, tzinfo=timezone.utc)
    assert telemetry_id("solusdt", "5m", ts) == telemetry_id("SOLUSDT", "5m", ts)
    assert telemetry_id("SOLUSDT", "5m", ts) != telemetry_id("SOLUSDT", "5m", ts, "OTHER_POLICY")


def test_divergence_and_missing_direction_are_not_invented():
    payload = build_comparison_payload(
        current=_snapshot(blocker="IMPULSE_RESET_WAIT", price="NOT_REACHED", trend="NOT_REACHED", score="NOT_REACHED", final="NOT_REACHED"),
        shadow=_snapshot(blocker="PRICE_NOT_BROKEN", price="FAIL", trend="NOT_REACHED", score="NOT_REACHED", final="NOT_REACHED", raw=True),
    )
    assert payload["shadow_policy"] == SHADOW_POLICY
    assert payload["impulse_reset_divergence"] is True
    assert payload["current_direction"] is None
    assert payload["shadow_direction"] is None
    assert payload["shadow_trade_created"] is False
    assert payload["shadow_entry_price"] is None


def test_candidate_flag_comes_only_from_final_entry_stage():
    payload = build_comparison_payload(
        current=_snapshot(),
        shadow=_snapshot(final="FAIL"),
    )
    assert payload["current_candidate"] is True
    assert payload["shadow_candidate"] is False


def test_price_failure_does_not_fabricate_later_passes():
    p = build_comparison_payload(current=_snapshot(price="FAIL", trend="NOT_REACHED", score="NOT_REACHED", final="NOT_REACHED"), shadow=_snapshot())
    assert p["current_stage_results"] == {"PRICE_BREAKOUT": "FAIL", "TREND": "NOT_REACHED", "SCORE": "NOT_REACHED", "FINAL_ENTRY_ELIGIBLE": "NOT_REACHED"}
    assert p["current_first_failed_stage"] == "PRICE_BREAKOUT"


def test_trend_failure_forces_final_candidate_false():
    p = build_comparison_payload(current=_snapshot(trend="FAIL", score="NOT_REACHED", final="NOT_REACHED"), shadow=_snapshot())
    assert p["current_candidate"] is False
    assert p["current_final_blocker"] == "TREND"


def test_score_failure_forces_final_candidate_false():
    p = build_comparison_payload(current=_snapshot(score="FAIL", final="NOT_REACHED"), shadow=_snapshot())
    assert p["current_candidate"] is False
    assert p["current_first_failed_stage"] == "SCORE"


def test_all_required_stages_pass_create_final_candidate():
    p = build_comparison_payload(current=_snapshot(), shadow=_snapshot())
    assert p["current_candidate"] is True
    assert p["shadow_final_candidate"] is True
    assert p["shadow_raw_candidate"] is False


def test_raw_candidate_is_separate_from_final_candidate():
    p = build_comparison_payload(current=_snapshot(price="FAIL", trend="NOT_REACHED", score="NOT_REACHED", final="NOT_REACHED", raw=True), shadow=_snapshot(price="PASS", raw=True))
    assert p["current_raw_breakout"] is True
    assert p["current_candidate"] is False
    assert p["shadow_raw_candidate"] is True
    assert p["shadow_final_candidate"] is True


def test_missing_required_stages_are_not_reached():
    base = _snapshot()
    base["stage_results"] = {"FINAL_ENTRY_ELIGIBLE": {"result": "PASS"}}
    p = build_comparison_payload(current=base, shadow=base)
    assert p["current_stage_results"]["PRICE_BREAKOUT"] == "NOT_REACHED"
    assert p["current_candidate"] is False


def test_v2_stage_lists_are_complete_and_consistent():
    p = build_comparison_payload(current=_snapshot(), shadow=_snapshot(trend="FAIL", score="NOT_REACHED", final="NOT_REACHED"))
    assert p["telemetry_version"] == "V2"
    assert p["current_stage_passed"] == ["PRICE_BREAKOUT", "TREND", "SCORE", "FINAL_ENTRY_ELIGIBLE"]
    assert p["shadow_stage_passed"] == ["PRICE_BREAKOUT"]
    assert p["shadow_first_failed_stage"] == "TREND"


def test_same_candle_identity_is_preserved():
    p = build_comparison_payload(current=_snapshot(), shadow=_snapshot())
    assert p["symbol"] == "SOLUSDT"
    assert p["timeframe"] == "5m"
    assert p["candle_open_time"] == datetime(2026, 10, 11, 18, 0, tzinfo=timezone.utc)


def test_provenance_is_observational_and_policy_is_shadow():
    p = build_comparison_payload(current=_snapshot(), shadow=_snapshot())
    assert p["record_provenance"] == "SHADOW_PAPER"
    assert p["current_policy"] == "CURRENT"
    assert p["shadow_policy"] == SHADOW_POLICY


def test_forensics_parameters_are_psycopg_safe():
    class Scalar:
        def __init__(self, value):
            self.value = value

        def item(self):
            return self.value

    forensic = {
        "trend_direction_expected": "SELL",
        "trend_ema_short_period": Scalar(50),
        "trend_ema_medium_period": Scalar(100),
        "trend_ema_long_period": Scalar(200),
        "trend_ema_short_value": Scalar(110.25),
        "trend_condition_final": Scalar(False),
        "trend_distance_short_medium_pct": Scalar(0.12),
    }
    row = {
        "symbol": "SOLUSDT", "timeframe": "5m",
        "candle_open_time": datetime(2026, 10, 11, 18, 0, tzinfo=timezone.utc),
        "candle_close_time": datetime(2026, 10, 11, 18, 5, tzinfo=timezone.utc),
        "record_provenance": "SHADOW_PAPER",
    }
    values = _forensic_db_values(forensic, row, "evaluation-id", SHADOW_POLICY)
    assert values[0] == "evaluation-id"
    assert values[10] == 110.25
    assert isinstance(values[10], float)
    assert values[18] is False


def test_shadow_forensics_sql_has_one_placeholder_per_column():
    source = Path(__file__).parents[1] / "utils" / "shadow_decision_telemetry.py"
    text = source.read_text(encoding="utf-8")
    block = re.search(r"INSERT INTO breakout_trend_forensics.*?ON CONFLICT", text, re.S).group(0)
    assert block.split("VALUES", 1)[1].split("ON CONFLICT", 1)[0].count("%s") == 24
