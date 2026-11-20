import pytest

from utils.lstm_persistence import map_prediction


def test_runtime_numeric_prediction_survives_mapping():
    mapped = map_prediction(111.25, None, 110.0)
    assert mapped["predicted_price"] == pytest.approx(111.25)
    assert mapped["predicted_price"] != 0
    assert mapped["direction_predicted"] == "buy"
    assert mapped["confidence_score"] is None


def test_invalid_prediction_does_not_silently_become_zero():
    with pytest.raises(ValueError):
        map_prediction(None, None, 110.0)
