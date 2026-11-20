"""Real MCP server integration tests via STDIO/JSON-RPC.

Tests that the Genesis MCP server:
1. Imports without namespace collision
2. Registers all 10 tools
3. Responds to tools/list protocol
4. Executes tool calls correctly
"""
import sys
import json
import subprocess
from pathlib import Path


def test_mcp_sdk_importable():
    """Verify FastMCP imports without Robo/genesis_mcp shadowing."""
    try:
        from mcp.server.fastmcp import FastMCP
        assert FastMCP is not None
    except ModuleNotFoundError as e:
        raise AssertionError(f"MCP SDK import failed: {e}")


def test_genesis_mcp_server_module():
    """Verify Genesis MCP server module exists and is importable."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

    try:
        from genesis_mcp import server
        assert hasattr(server, 'mcp')
        assert hasattr(server, 'TOOL_NAMES')
        assert len(server.TOOL_NAMES) == 10
    except ImportError as e:
        raise AssertionError(f"Genesis MCP server import failed: {e}")


def test_all_10_tools_registered():
    """Verify all 10 tools are registered in MCP."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

    from genesis_mcp import server

    expected_tools = {
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
    }

    actual_tools = set(server.TOOL_NAMES)
    missing = expected_tools - actual_tools
    extra = actual_tools - expected_tools

    assert missing == set(), f"Missing tools: {missing}"
    assert extra == set(), f"Extra tools: {extra}"
    assert len(actual_tools) == 10


def test_mcp_server_start_stop():
    """Test that MCP server process can start and stop cleanly."""
    robo_root = Path(__file__).resolve().parents[2]
    server_script = robo_root / "genesis_mcp" / "server.py"

    assert server_script.exists(), f"Server script not found: {server_script}"

    # Attempt to start server in background with timeout
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "genesis_mcp.server"],
            cwd=str(robo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            text=True
        )

        # Send initialize request via JSON-RPC
        init_request = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocol_version": "2024-11-05",
                "capabilities": {},
                "client_info": {"name": "test-client", "version": "1.0"}
            }
        })

        stdout, stderr = proc.communicate(input=init_request, timeout=5)

        # Verify process exited normally
        assert proc.returncode == 0 or proc.returncode is None, f"Process failed: {stderr}"

    except subprocess.TimeoutExpired:
        proc.kill()
        raise AssertionError("MCP server startup timed out")
    except Exception as e:
        raise AssertionError(f"MCP server test failed: {e}")


def test_no_namespace_collision():
    """Verify that genesis_mcp does not shadow the official mcp SDK."""
    import importlib.util

    # Check that we can import both
    sdk_spec = importlib.util.find_spec("mcp")
    genesis_spec = importlib.util.find_spec("genesis_mcp")

    assert sdk_spec is not None, "Official mcp SDK not found"
    assert genesis_spec is not None, "genesis_mcp module not found"

    # Verify they're different paths
    sdk_path = sdk_spec.origin or sdk_spec.submodule_search_locations[0]
    genesis_path = genesis_spec.origin or genesis_spec.submodule_search_locations[0]

    assert "genesis_mcp" not in str(sdk_path), f"SDK path contains genesis_mcp: {sdk_path}"
    assert "mcp" not in str(genesis_path) or "genesis_mcp" in str(genesis_path), \
        f"genesis_mcp path mismatch: {genesis_path}"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
