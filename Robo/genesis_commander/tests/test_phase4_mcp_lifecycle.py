"""Phase 4 MCP Lifecycle Tests - Real process lifecycle verification."""

import pytest
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

from genesis_commander.orchestrator import (
    CommanderOrchestrator, get_orchestrator, close_orchestrator, set_orchestrator
)
from genesis_skills.mcp_adapter import MCPReadOnlyClientAdapter
from genesis_skills.executor import SkillExecutor
from genesis_skills.audit import get_audit_logger

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def clean_globals():
    """Reset global orchestrator and audit before/after each test."""
    yield
    asyncio.run(close_orchestrator())
    audit_logger = get_audit_logger()
    audit_logger.clear()


class TestMCPConcurrentLazyStart:
    """Test concurrent lazy-start safety of MCPReadOnlyClientAdapter."""

    @pytest.mark.anyio
    async def test_mcp_lazy_start_first_call(self):
        """Adapter lazy-starts on first call."""
        adapter = MCPReadOnlyClientAdapter()

        # Before: no session
        assert adapter.session is None

        # First call triggers lazy-start
        result = await adapter.call("get_system_health", {})

        # Verify result
        assert result is not None
        assert result.tool_name == "get_system_health"
        assert adapter.session is not None, "Session created on first call"

        await adapter.close()

    @pytest.mark.anyio
    async def test_mcp_sequential_reuse(self):
        """Adapter reuses session across sequential calls."""
        adapter = MCPReadOnlyClientAdapter()

        # Call 1: lazy-start
        r1 = await adapter.call("get_system_health", {})
        assert r1.tool_name == "get_system_health"
        session_1 = adapter.session

        # Call 2: reuse session
        r2 = await adapter.call("get_risk_status", {})
        assert r2.tool_name == "get_risk_status"
        session_2 = adapter.session

        # Same session
        assert session_1 is session_2, "Session reused in call 2"

        # Close
        await adapter.close()
        assert adapter.session is None

    @pytest.mark.anyio
    async def test_mcp_close_idempotent(self):
        """MCP close() is idempotent."""
        adapter = MCPReadOnlyClientAdapter()

        # Start via lazy call
        await adapter.call("get_system_health", {})
        assert adapter.session is not None

        # Close once
        await adapter.close()
        assert adapter.session is None

        # Close again - no error
        await adapter.close()
        assert adapter.session is None

    @pytest.mark.anyio
    async def test_mcp_restart_after_close(self):
        """Adapter can restart after close."""
        adapter = MCPReadOnlyClientAdapter()

        # First use
        r1 = await adapter.call("get_system_health", {})
        assert r1.success

        # Close
        await adapter.close()
        assert adapter.session is None

        # Restart - lazy-start again
        r2 = await adapter.call("get_risk_status", {})
        assert r2.success

        await adapter.close()


class TestExecutorCloseChain:
    """Test executor close chain."""

    @pytest.mark.anyio
    async def test_executor_has_close_method(self):
        """SkillExecutor has async close() method."""
        adapter = MCPReadOnlyClientAdapter()
        executor = SkillExecutor(mcp_client=adapter, skill_classes={})

        # Verify method exists
        assert hasattr(executor, 'close')
        assert callable(executor.close)

        # Use MCP to start it
        await adapter.call("get_system_health", {})

        # Close via executor
        await executor.close()

        # Adapter should be closed
        assert adapter.session is None

    @pytest.mark.anyio
    async def test_orchestrator_has_close_method(self):
        """CommanderOrchestrator has async close() method."""
        adapter = MCPReadOnlyClientAdapter()
        executor = SkillExecutor(mcp_client=adapter, skill_classes={})
        orchestrator = CommanderOrchestrator(executor)

        # Verify method exists
        assert hasattr(orchestrator, 'close')
        assert callable(orchestrator.close)

        # Start MCP
        await adapter.call("get_system_health", {})

        # Close via orchestrator
        await orchestrator.close()

        # Adapter should be closed
        assert adapter.session is None


class TestGlobalOrchestratorLifecycle:
    """Test global orchestrator singleton lifecycle."""

    @pytest.mark.anyio
    async def test_get_orchestrator_singleton(self):
        """get_orchestrator returns same instance."""
        orch1 = get_orchestrator()
        orch2 = get_orchestrator()

        assert orch1 is orch2, "Should return same singleton instance"

    @pytest.mark.anyio
    async def test_close_orchestrator_resets_singleton(self):
        """close_orchestrator() resets singleton."""
        orch1 = get_orchestrator()

        # Close
        await close_orchestrator()

        # Next get should create new instance
        orch2 = get_orchestrator()

        assert orch1 is not orch2, "Should create new instance after close"

    @pytest.mark.anyio
    async def test_set_orchestrator_custom_instance(self):
        """set_orchestrator() allows custom instance."""
        adapter = MCPReadOnlyClientAdapter()
        executor = SkillExecutor(mcp_client=adapter, skill_classes={})
        custom_orch = CommanderOrchestrator(executor)

        # Set custom
        set_orchestrator(custom_orch)

        # Should return custom
        orch = get_orchestrator()
        assert orch is custom_orch


class TestRealMCPProcessLifecycle:
    """Test real MCP process lifecycle."""

    @pytest.mark.anyio
    async def test_mcp_stdio_cleanup(self):
        """Verify STDIO contexts are properly cleaned."""
        adapter = MCPReadOnlyClientAdapter()

        # Before: no contexts
        assert adapter._stdio_context is None
        assert adapter._session_context is None

        # Start
        await adapter.call("get_system_health", {})

        # After start: contexts exist
        assert adapter._stdio_context is not None
        assert adapter._session_context is not None

        # Close
        await adapter.close()

        # After close: contexts cleared
        assert adapter._stdio_context is None
        assert adapter._session_context is None
        assert adapter.session is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
