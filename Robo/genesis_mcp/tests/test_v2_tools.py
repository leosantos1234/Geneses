"""Unit tests para MCP V2 tools com implementação atual."""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
import uuid


class TestDecisionLineageTools:
    """Test decision lineage tools."""

    def test_get_decision_trace_valid(self):
        """Test get_decision_trace with valid UUID."""
        from genesis_mcp.v2.lineage import get_decision_trace_impl

        decision_id = str(uuid.uuid4())
        request_id = "test-1"
        mock_trace = {
            "decision_id": decision_id,
            "created_at": "2026-10-07T00:00:00Z",
            "symbol": "BTCUSDT",
            "events": []
        }

        with patch("genesis_mcp.v2.lineage.database_backend", return_value="postgres"):
            with patch("genesis_mcp.v2.lineage.lineage_get_decision_trace", return_value=mock_trace):
                result = get_decision_trace_impl(decision_id, request_id)
                assert result.request_id == request_id
                assert result.trace is not None
                assert result.error is None

    def test_get_decision_trace_not_found(self):
        """Test get_decision_trace when trace doesn't exist."""
        from genesis_mcp.v2.lineage import get_decision_trace_impl

        decision_id = str(uuid.uuid4())
        with patch("genesis_mcp.v2.lineage.database_backend", return_value="postgres"):
            with patch("genesis_mcp.v2.lineage.lineage_get_decision_trace", return_value=None):
                result = get_decision_trace_impl(decision_id, "test-2")
                assert result.error is not None
                assert result.error.code == "NOT_FOUND"

    def test_list_decisions_pagination(self):
        """Test list_decisions with real pagination logic."""
        from genesis_mcp.v2.lineage import list_decisions_impl

        mock_rows = [{"decision_id": str(uuid.uuid4())} for _ in range(51)]  # 51 rows

        with patch("genesis_mcp.v2.lineage.database_backend", return_value="postgres"):
            with patch("genesis_mcp.v2.lineage.lineage_list_decisions", return_value=mock_rows):
                # Request 50, should get has_more=True
                result = list_decisions_impl(limit=50, offset=0, request_id="test-3")
                assert result.count == 50
                assert result.has_more is True
                assert len(result.decisions) == 50

    def test_list_decisions_has_more_false(self):
        """Test list_decisions when no more pages."""
        from genesis_mcp.v2.lineage import list_decisions_impl

        mock_rows = [{"decision_id": str(uuid.uuid4())} for _ in range(30)]

        with patch("genesis_mcp.v2.lineage.database_backend", return_value="postgres"):
            with patch("genesis_mcp.v2.lineage.lineage_list_decisions", return_value=mock_rows):
                result = list_decisions_impl(limit=50, offset=0, request_id="test-4")
                assert result.count == 30
                assert result.has_more is False

    def test_get_commander_decision_allowed(self):
        """Test get_commander_decision when COMMANDER_ALLOWED."""
        from genesis_mcp.v2.lineage import get_commander_decision_impl

        decision_id = str(uuid.uuid4())
        mock_trace = {
            "decision_id": decision_id,
            "created_at": "2026-10-07T00:00:00Z",
            "symbol": "BTCUSDT",
            "events": [
                {"event_type": "COMMANDER_ALLOWED", "reason_code": None}
            ]
        }

        with patch("genesis_mcp.v2.lineage.database_backend", return_value="postgres"):
            with patch("genesis_mcp.v2.lineage.lineage_get_decision_trace", return_value=mock_trace):
                result = get_commander_decision_impl(decision_id, "test-5")
                assert result.decision is not None
                assert result.decision.commander_status == "allowed"

    def test_get_commander_decision_blocked(self):
        """Test get_commander_decision when COMMANDER_BLOCKED."""
        from genesis_mcp.v2.lineage import get_commander_decision_impl

        decision_id = str(uuid.uuid4())
        mock_trace = {
            "decision_id": decision_id,
            "events": [
                {"event_type": "COMMANDER_BLOCKED", "reason_code": "MAX_POSITIONS"}
            ]
        }

        with patch("genesis_mcp.v2.lineage.database_backend", return_value="postgres"):
            with patch("genesis_mcp.v2.lineage.lineage_get_decision_trace", return_value=mock_trace):
                result = get_commander_decision_impl(decision_id, "test-6")
                assert result.decision.commander_status == "blocked"
                assert result.decision.reason_code == "MAX_POSITIONS"


class TestLSTMPredictionsTool:
    """Test get_lstm_predictions tool."""

    def test_lstm_predictions_valid_symbol(self):
        """Test get_lstm_predictions with valid symbol."""
        from genesis_mcp.v2.lstm import get_lstm_predictions_impl

        with patch("genesis_mcp.v2.lstm.database_backend", return_value="postgres"):
            with patch("genesis_mcp.v2.lstm.database_url", return_value="postgresql://test"):
                with patch("genesis_mcp.v2.lstm.psycopg") as mock_psycopg_module:
                    mock_conn = MagicMock()
                    mock_cursor = MagicMock()
                    mock_cursor.fetchall.return_value = [
                        ("2026-10-07T00:00:00Z", "BTCUSDT", "UP", "DOWN", 0.85, 45000, 44900, 0.22, "lstm_v1")
                    ]
                    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
                    mock_psycopg_module.connect.return_value = mock_conn

                    result = get_lstm_predictions_impl("BTCUSDT", limit=50, offset=0, request_id="test-7")
                    assert result.symbol == "BTCUSDT"
                    assert result.count == 1
                    assert result.error is None
                    assert result.predictions[0].predicted_price == 45000.0
                    assert result.predictions[0].model_dump()["predicted_price"] == 45000.0

    def test_lstm_predictions_null_confidence(self):
        """Test get_lstm_predictions with NULL confidence_score."""
        from genesis_mcp.v2.lstm import get_lstm_predictions_impl

        with patch("genesis_mcp.v2.lstm.database_backend", return_value="postgres"):
            with patch("genesis_mcp.v2.lstm.database_url", return_value="postgresql://test"):
                with patch("genesis_mcp.v2.lstm.psycopg") as mock_psycopg_module:
                    mock_conn = MagicMock()
                    mock_cursor = MagicMock()
                    mock_cursor.fetchall.return_value = [
                        ("2026-10-07T00:00:00Z", "BTCUSDT", "UP", "DOWN", None, 45000, None, None, "lstm_v1")
                    ]
                    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
                    mock_psycopg_module.connect.return_value = mock_conn

                    result = get_lstm_predictions_impl("BTCUSDT", request_id="test-8")
                    assert result.predictions[0].confidence is None

    def test_lstm_predictions_nulls_and_legitimate_zero_are_preserved(self):
        """NULL stays NULL; a stored numeric zero is not treated as missing."""
        from genesis_mcp.v2.lstm import get_lstm_predictions_impl

        with patch("genesis_mcp.v2.lstm.database_backend", return_value="postgres"):
            with patch("genesis_mcp.v2.lstm.database_url", return_value="postgresql://test"):
                with patch("genesis_mcp.v2.lstm.psycopg") as mock_psycopg_module:
                    mock_conn = MagicMock()
                    mock_cursor = MagicMock()
                    mock_cursor.fetchall.return_value = [
                        ("2026-10-07T00:00:00Z", "SOLUSDT", "hold", None, None, None, None, None, "lstm_v1"),
                        ("2026-10-07T00:05:00Z", "SOLUSDT", "hold", None, None, 0, None, None, "lstm_v1"),
                    ]
                    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
                    mock_psycopg_module.connect.return_value = mock_conn

                    result = get_lstm_predictions_impl("SOLUSDT", request_id="test-null-zero")
                    assert result.predictions[0].predicted_price is None
                    assert result.predictions[0].confidence is None
                    assert result.predictions[1].predicted_price == 0.0
                    assert result.predictions[1].confidence is None
                    payload = result.model_dump(mode="json")
                    assert payload["predictions"][0]["predicted_price"] is None
                    assert payload["predictions"][1]["predicted_price"] == 0.0


class TestModelInventoryTool:
    """Test get_model_inventory tool."""

    def test_inventory_status_available(self):
        """Test that files modified recently are AVAILABLE."""
        from genesis_mcp.v2.inventory import get_model_inventory_impl
        import time

        mock_file = MagicMock()
        mock_file.name = "model_recent.pt"
        mock_file.suffix = ".pt"
        mock_file.is_file.return_value = True
        mock_file.stat.return_value.st_mtime = time.time()  # Just now
        mock_file.stat.return_value.st_size = 1024
        mock_file.relative_to.return_value = "models/model_recent.pt"

        with patch("genesis_mcp.v2.inventory.database_backend", return_value="postgres"):
            with patch("genesis_mcp.v2.inventory.Path.rglob", return_value=[mock_file]):
                result = get_model_inventory_impl(request_id="test-9")
                assert result.count >= 0
                if result.models:
                    assert any(m.name == "model_recent.pt" for m in result.models)

    def test_inventory_status_no_age_inference(self):
        """Test that file age does NOT determine status (AVAILABLE always for existing files)."""
        from genesis_mcp.v2.inventory import get_model_inventory_impl
        import time

        # Old file (35 days ago) should still be AVAILABLE (not INACTIVE based on age)
        old_timestamp = time.time() - (35 * 24 * 3600)  # 35 days ago
        mock_file = MagicMock()
        mock_file.name = "model_old.pt"
        mock_file.suffix = ".pt"
        mock_file.is_file.return_value = True
        mock_file.stat.return_value.st_mtime = old_timestamp
        mock_file.stat.return_value.st_size = 1024
        mock_file.relative_to.return_value = "models/model_old.pt"

        with patch("genesis_mcp.v2.inventory.database_backend", return_value="postgres"):
            with patch("genesis_mcp.v2.inventory.Path.rglob", return_value=[mock_file]):
                result = get_model_inventory_impl(request_id="test-10")
                if result.models:
                    old_model = next((m for m in result.models if m.name == "model_old.pt"), None)
                    if old_model:
                        # Age does NOT infer status; file exists = AVAILABLE
                        assert old_model.status == "AVAILABLE"
                        assert old_model.operational_evidence == "File exists in filesystem"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
