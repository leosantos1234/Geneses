"""Phase 4 MCP owner-task acceptance tests.

Real MCP STDIO server, real ClientSession, real stdio_client. Instrumentation wraps the
real SDK callables (counts and process capture); it does not replace their behaviour.
Failure injection is limited to tests that name the injected failure explicitly.
"""

import asyncio
from unittest.mock import patch

import pytest
from mcp import ClientSession
import mcp.client.stdio as stdio_mod

import genesis_skills.mcp_adapter as adapter_mod
from genesis_skills.mcp_adapter import MCPReadOnlyClientAdapter, MCPWorkerError


@pytest.fixture
def instrumented(monkeypatch):
    counts = {"stdio": 0, "session": 0}
    processes = []

    real_create_process = stdio_mod._create_platform_compatible_process
    real_stdio_client = adapter_mod.stdio_client
    real_session_cls = adapter_mod.ClientSession

    async def capture_process(*args, **kwargs):
        proc = await real_create_process(*args, **kwargs)
        processes.append(proc)
        return proc

    class CountingSession(real_session_cls):
        def __init__(self, *args, **kwargs):
            counts["session"] += 1
            super().__init__(*args, **kwargs)

    def counting_stdio_client(*args, **kwargs):
        counts["stdio"] += 1
        return real_stdio_client(*args, **kwargs)

    monkeypatch.setattr(stdio_mod, "_create_platform_compatible_process", capture_process)
    monkeypatch.setattr(adapter_mod, "ClientSession", CountingSession)
    monkeypatch.setattr(adapter_mod, "stdio_client", counting_stdio_client)
    return counts, processes


def _assert_no_affinity_error(results):
    for r in results:
        msg = (r.error_message or "").lower()
        assert "different task" not in msg, f"task affinity error: {r.error_message}"
        assert "cancel scope" not in msg, f"task affinity error: {r.error_message}"


@pytest.mark.anyio
async def test_concurrent_first_calls_share_one_owner(instrumented):
    counts, processes = instrumented
    adapter = MCPReadOnlyClientAdapter()

    results = await asyncio.gather(
        adapter.call("get_system_health", {}),
        adapter.call("get_risk_status", {}),
        adapter.call("get_model_inventory", {}),
    )

    assert all(r.success for r in results), [r.error_message for r in results]
    _assert_no_affinity_error(results)
    assert adapter.worker_starts == 1
    assert counts["stdio"] == 1
    assert counts["session"] == 1
    assert len(processes) == 1
    assert adapter.is_running is True

    await adapter.close()

    assert adapter.is_running is False
    assert adapter._worker_task is None
    assert adapter.session is None
    assert processes[0].returncode is not None, "STDIO server process still running after close"


@pytest.mark.anyio
async def test_close_before_start_is_safe():
    adapter = MCPReadOnlyClientAdapter()
    await adapter.close()
    await adapter.close()
    assert adapter.is_running is False


@pytest.mark.anyio
async def test_close_after_use_and_twice(instrumented):
    _, processes = instrumented
    adapter = MCPReadOnlyClientAdapter()
    r = await adapter.call("get_system_health", {})
    assert r.success
    await adapter.close()
    await adapter.close()
    assert processes[0].returncode is not None


@pytest.mark.anyio
async def test_two_concurrent_closes_are_safe(instrumented):
    _, processes = instrumented
    adapter = MCPReadOnlyClientAdapter()
    assert (await adapter.call("get_system_health", {})).success
    await asyncio.gather(adapter.close(), adapter.close())
    assert adapter.is_running is False
    assert processes[0].returncode is not None


@pytest.mark.anyio
async def test_restart_after_close(instrumented):
    counts, processes = instrumented
    adapter = MCPReadOnlyClientAdapter()
    assert (await adapter.call("get_system_health", {})).success
    await adapter.close()
    r = await adapter.call("get_risk_status", {})
    assert r.success
    assert adapter.worker_starts == 2
    assert counts["stdio"] == 2
    await adapter.close()
    assert all(p.returncode is not None for p in processes)


@pytest.mark.anyio
async def test_startup_failure_is_observable_and_leaves_no_process(instrumented):
    _, processes = instrumented

    async def hanging_initialize(self, *args, **kwargs):
        await asyncio.sleep(10)

    adapter = MCPReadOnlyClientAdapter(startup_timeout_seconds=0.2)
    with patch.object(ClientSession, "initialize", new=hanging_initialize):
        with pytest.raises(MCPWorkerError, match="startup failed"):
            await adapter.call("get_system_health", {})

    assert adapter.is_running is False
    assert adapter._worker_task is None
    assert len(processes) == 1
    assert processes[0].returncode is not None, "STDIO process leaked after startup failure"
    await adapter.close()


@pytest.mark.anyio
async def test_tool_call_failure_is_normalized_and_worker_survives(instrumented):
    adapter = MCPReadOnlyClientAdapter()
    assert (await adapter.call("get_system_health", {})).success

    async def failing_call(self, *args, **kwargs):
        raise RuntimeError("injected tool failure")

    with patch.object(ClientSession, "call_tool", new=failing_call):
        failed = await adapter.call("get_risk_status", {})

    assert failed.success is False
    assert failed.error_code == "INTERNAL_ERROR"
    assert "injected tool failure" in failed.error_message
    assert adapter.is_running is True

    ok = await adapter.call("get_risk_status", {})
    assert ok.success
    await adapter.close()


@pytest.mark.anyio
async def test_caller_cancellation_does_not_break_worker(instrumented):
    _, processes = instrumented
    adapter = MCPReadOnlyClientAdapter()
    warm = await adapter.call("get_system_health", {})
    assert warm.success, warm.error_message
    adapter.operation_timeout_seconds = 0.5

    async def slow_call(self, *args, **kwargs):
        await asyncio.sleep(10)

    with patch.object(ClientSession, "call_tool", new=slow_call):
        task = asyncio.create_task(adapter.call("get_risk_status", {}))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert adapter.is_running is True
    r = await adapter.call("get_risk_status", {})
    assert r.success, r.error_message
    await adapter.close()
    assert adapter.is_running is False
    assert all(p.returncode is not None for p in processes), "STDIO process leaked"


@pytest.mark.anyio
async def test_worker_death_fails_pending_calls_deterministically(instrumented):
    _, processes = instrumented
    adapter = MCPReadOnlyClientAdapter()
    assert (await adapter.call("get_system_health", {})).success

    async def slow_call(self, *args, **kwargs):
        await asyncio.sleep(10)

    with patch.object(ClientSession, "call_tool", new=slow_call):
        pending = asyncio.gather(
            adapter.call("get_risk_status", {}),
            adapter.call("get_model_inventory", {}),
            adapter.call("list_trades", {}),
            return_exceptions=True,
        )
        await asyncio.sleep(0.2)
        adapter._worker_task.cancel()
        results = await asyncio.wait_for(pending, timeout=15)

    assert all(r.success is False for r in results), "pending calls must fail, not hang"
    assert adapter.is_running is False

    recovered = await adapter.call("get_risk_status", {})
    assert recovered.success
    await adapter.close()
    assert all(p.returncode is not None for p in processes)


@pytest.mark.anyio
async def test_call_rejects_tool_outside_approved_surface():
    adapter = MCPReadOnlyClientAdapter()
    r = await adapter.call("place_order", {})
    assert r.success is False
    assert r.error_code == "TOOL_NOT_FOUND"
    assert adapter.is_running is False
    assert adapter.worker_starts == 0
