"""Integration tests for MCP tools and common modules.

Note: Full MCP server integration requires mcp SDK (FastMCP), which is external.
These tests validate that all components work together without the SDK.
"""
import sys
from pathlib import Path

robo_root = Path(__file__).resolve().parents[2]
if str(robo_root) not in sys.path:
    sys.path.insert(0, str(robo_root))


def test_server_file_exists():
    """Test that server.py file exists and is syntactically valid."""
    server_file = Path(__file__).resolve().parent.parent / "server.py"
    assert server_file.exists(), f"server.py not found at {server_file}"

    # Validate Python syntax
    try:
        import py_compile
        py_compile.compile(str(server_file), doraise=True)
    except Exception as e:
        raise AssertionError(f"server.py has syntax errors: {e}")


def test_server_tool_names_defined():
    """Test that server.py defines TOOL_NAMES with 10 tools."""
    server_file = Path(__file__).resolve().parent.parent / "server.py"
    content = server_file.read_text()

    # Check that TOOL_NAMES is defined
    assert "TOOL_NAMES = [" in content, "TOOL_NAMES not defined"

    # Check that all 10 tools are mentioned
    expected_tools = [
        "get_system_health",
        "list_open_positions",
        "list_trades",
        "get_trade_detail",
        "get_risk_status",
        "get_decision_trace",
        "list_decisions",
        "get_lstm_predictions",
        "get_model_inventory",
        "get_commander_decision",
    ]

    for tool_name in expected_tools:
        assert f'"{tool_name}"' in content or f"'{tool_name}'" in content, f"Tool {tool_name} not found in server.py"


def test_v2_implementations_importable():
    """Test that V2 tool implementations can be imported."""
    from genesis_mcp.v2 import (
        get_decision_trace_impl,
        list_decisions_impl,
        get_lstm_predictions_impl,
        get_model_inventory_impl,
        get_commander_decision_impl,
    )

    assert get_decision_trace_impl is not None
    assert list_decisions_impl is not None
    assert get_lstm_predictions_impl is not None
    assert get_model_inventory_impl is not None
    assert get_commander_decision_impl is not None


def test_common_models_importable():
    """Test that common models can be imported."""
    from genesis_mcp.common import (
        ErrorInfo,
        HealthResponse,
        PositionsResponse,
        TradesResponse,
        TradeDetailResponse,
        RiskResponse,
        DecisionTraceResponse,
        ListDecisionsResponse,
        LSTMPredictionsResponse,
        ModelInventoryResponse,
        CommanderDecisionResponse,
    )

    assert ErrorInfo is not None
    assert HealthResponse is not None
    assert PositionsResponse is not None
    assert TradesResponse is not None
    assert TradeDetailResponse is not None
    assert RiskResponse is not None
    assert DecisionTraceResponse is not None
    assert ListDecisionsResponse is not None
    assert LSTMPredictionsResponse is not None
    assert ModelInventoryResponse is not None
    assert CommanderDecisionResponse is not None


def test_validators_importable():
    """Test that validators are properly exported."""
    from genesis_mcp.common import (
        valid_filter,
        valid_limit,
        valid_offset,
        valid_time,
    )

    assert valid_filter is not None
    assert valid_limit is not None
    assert valid_offset is not None
    assert valid_time is not None


def test_audit_logging_importable():
    """Test that audit logging is properly exported."""
    from genesis_mcp.common import LOGGER, audit, call_tool

    assert LOGGER is not None
    assert audit is not None
    assert call_tool is not None


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
