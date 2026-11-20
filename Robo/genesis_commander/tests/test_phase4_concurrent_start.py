"""Phase 4 Concurrent Lazy-Start Test - Verify asyncio.Lock protection."""

import pytest
import asyncio

from genesis_skills.mcp_adapter import MCPReadOnlyClientAdapter


class TestConcurrentLazyStartWithLock:
    """Verify asyncio.Lock prevents duplicate STDIO startup under concurrent first calls."""

    @pytest.mark.anyio
    async def test_concurrent_first_calls_single_session(self):
        """
        Two simultaneous first calls must share ONE session.

        asyncio.Lock in MCPReadOnlyClientAdapter.start() ensures:
        - Both tasks enter start()
        - Lock serializes actual STDIO/session creation
        - First task creates STDIO+session
        - Second task acquires lock, checks self.session (exists), returns
        - Both calls succeed
        - Only one session object exists
        """
        adapter = MCPReadOnlyClientAdapter()

        # Precondition: session is None
        assert adapter.session is None

        # Launch two simultaneous calls
        results = []

        async def call_tool(tool_name):
            result = await adapter.call(tool_name, {})
            results.append(result)

        # Both start from session=None
        task1 = asyncio.create_task(call_tool("get_system_health"))
        task2 = asyncio.create_task(call_tool("get_risk_status"))

        await asyncio.gather(task1, task2)

        # Verify: both succeeded
        assert len(results) == 2
        assert all(r.success for r in results)

        # Verify: only one session exists (not two)
        assert adapter.session is not None

        # Cleanup
        await adapter.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
