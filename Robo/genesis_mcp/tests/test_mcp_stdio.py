"""Bounded real STDIO integration tests using the official MCP Python SDK."""

import asyncio
import os
import sys
from datetime import timedelta
from pathlib import Path

import mcp.client.stdio as stdio_module
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


TEST_TIMEOUT_SECONDS = 8
EXPECTED_TOOLS = {
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


def _server_parameters() -> StdioServerParameters:
    robo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(robo_root) + os.pathsep + env.get("PYTHONPATH", "")
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "genesis_mcp.server"],
        env=env,
        cwd=str(robo_root),
    )


async def _reap_processes(processes) -> None:
    """Terminate and reap only processes created by this test invocation."""
    for process in processes:
        if process.returncode is None:
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except asyncio.TimeoutError:
            if process.returncode is None:
                process.kill()
            await asyncio.wait_for(process.wait(), timeout=2)


async def _run_sdk_session(operation):
    processes = []
    original_spawn = stdio_module._create_platform_compatible_process

    async def tracked_spawn(*args, **kwargs):
        process = await original_spawn(*args, **kwargs)
        processes.append(process)
        return process

    stdio_module._create_platform_compatible_process = tracked_spawn
    try:
        async def session_operation():
            async with stdio_client(_server_parameters()) as (read_stream, write_stream):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=TEST_TIMEOUT_SECONDS),
                ) as session:
                    await asyncio.wait_for(
                        session.initialize(), timeout=TEST_TIMEOUT_SECONDS
                    )
                    return await operation(session)

        result = await asyncio.wait_for(
            session_operation(), timeout=TEST_TIMEOUT_SECONDS
        )
    finally:
        await _reap_processes(processes)
        stdio_module._create_platform_compatible_process = original_spawn

    assert processes, "The real MCP STDIO child process was not created"
    assert all(process.returncode is not None for process in processes), (
        "MCP STDIO child process was not reaped"
    )
    return result


def test_mcp_stdio_tools_list():
    """Real initialize + tools/list exposes exactly the canonical ten tools."""

    async def list_tools(session):
        return await asyncio.wait_for(session.list_tools(), timeout=TEST_TIMEOUT_SECONDS)

    tools_response = asyncio.run(_run_sdk_session(list_tools))
    tools = tools_response.tools
    tool_names = {tool.name for tool in tools}
    assert len(tools) == 10
    assert tool_names == EXPECTED_TOOLS


def test_mcp_stdio_tool_call():
    """Real initialize + tools/call returns a structured system-health result."""

    async def call_system_health(session):
        return await asyncio.wait_for(
            session.call_tool("get_system_health", {}),
            timeout=TEST_TIMEOUT_SECONDS,
        )

    response = asyncio.run(_run_sdk_session(call_system_health))
    assert hasattr(response, "content")
    assert response.content is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
