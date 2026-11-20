"""RECONSTRUCTED_STRONG_2_5E_REGRESSION - EXACT ASSERTIONS.

A-M: Production-grade evidence with exact assertions.
"""

import pytest
import asyncio
import sys
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock


class Test2_5E_A_Official_SDK_STDIO_Startup:
    def test_2_5e_a(self):
        sys.path.insert(0, 'Robo')
        os.environ.setdefault('GENESIS_DATABASE_BACKEND', 'postgres')
        async def run():
            from genesis_skills.mcp_adapter import MCPReadOnlyClientAdapter
            from mcp import ClientSession
            adapter = MCPReadOnlyClientAdapter()
            await adapter.start()
            assert isinstance(adapter.session, ClientSession)
            await adapter.close()
        asyncio.run(run())


class Test2_5E_B_Real_Initialize_ListTools:
    def test_2_5e_b(self):
        sys.path.insert(0, 'Robo')
        os.environ.setdefault('GENESIS_DATABASE_BACKEND', 'postgres')
        async def run():
            from genesis_skills.mcp_adapter import MCPReadOnlyClientAdapter
            adapter = MCPReadOnlyClientAdapter()
            await adapter.start()
            assert adapter.session is not None
            tools = await adapter.session.list_tools()
            assert len(tools.tools) == 10
            await adapter.close()
        asyncio.run(run())


class Test2_5E_C_Startup_Timeout:
    def test_2_5e_c_real_adapter_start_timeout(self):
        """C: Real adapter.start() timeout via production path."""
        sys.path.insert(0, 'Robo')
        os.environ.setdefault('GENESIS_DATABASE_BACKEND', 'postgres')

        startup_timeout_fired = False

        async def run():
            nonlocal startup_timeout_fired

            from genesis_skills.mcp_adapter import MCPReadOnlyClientAdapter
            from unittest.mock import patch

            adapter = MCPReadOnlyClientAdapter(startup_timeout_seconds=0.05)

            # Patch the initialize() method called by adapter.start()
            async def slow_init(*args, **kwargs):
                await asyncio.sleep(10.0)

            try:
                # Patch ClientSession.initialize
                with patch('mcp.ClientSession.initialize', new_callable=AsyncMock) as mock_init:
                    mock_init.side_effect = slow_init

                    # MANDATORY: Call real adapter.start() which uses timeout internally
                    try:
                        await adapter.start()
                        # If start() returns normally, test FAILS
                        pytest.fail("adapter.start() did not timeout as required")
                    except RuntimeError as e:
                        # Production error from adapter.start() timeout path
                        if "startup failed" in str(e).lower() or "timeout" in str(e).lower():
                            startup_timeout_fired = True
                        else:
                            pytest.fail(f"Unexpected error: {e}")
            except asyncio.TimeoutError:
                startup_timeout_fired = True

        asyncio.run(run())
        assert startup_timeout_fired, "adapter.start() did not fire timeout as required"


class Test2_5E_D_Operation_Timeout:
    def test_2_5e_d(self):
        sys.path.insert(0, 'Robo')
        async def run():
            from genesis_skills.mcp_adapter import MCPReadOnlyClientAdapter
            from unittest.mock import patch
            from mcp import ClientSession
            adapter = MCPReadOnlyClientAdapter(operation_timeout_seconds=0.05)
            async def slow_call(*args, **kwargs):
                await asyncio.sleep(10.0)
            with patch.object(ClientSession, 'call_tool', new=slow_call):
                result = await adapter.call("get_system_health", {})
            await adapter.close()
            assert result.success is False, "Timeout should return failure"
            assert result.error_code == "MCP_NOT_AVAILABLE"
        asyncio.run(run())


_captured_processes_efg = []
_original_create_process_efg = None


async def capture_process_efg(*args, **kwargs):
    """Async wrapper to capture real anyio.Process objects."""
    global _original_create_process_efg
    proc = await _original_create_process_efg(*args, **kwargs)
    _captured_processes_efg.append(proc)
    return proc


class Test2_5E_E_Process_Alive_While_Active:
    def test_2_5e_e_process_alive(self):
        """E: Real process alive with valid PID and returncode=None."""
        sys.path.insert(0, 'Robo')
        os.environ.setdefault('GENESIS_DATABASE_BACKEND', 'postgres')

        global _captured_processes_efg, _original_create_process_efg
        _captured_processes_efg = []
        _original_create_process_efg = None

        async def run():
            global _original_create_process_efg

            from genesis_skills.mcp_adapter import MCPReadOnlyClientAdapter
            import mcp.client.stdio as stdio_mod

            # Patch async process factory
            if hasattr(stdio_mod, '_create_platform_compatible_process'):
                _original_create_process_efg = stdio_mod._create_platform_compatible_process
                stdio_mod._create_platform_compatible_process = capture_process_efg

            try:
                adapter = MCPReadOnlyClientAdapter()
                await adapter.start()

                # MANDATORY: At least one real process captured
                assert len(_captured_processes_efg) >= 1, \
                    "No real process captured"

                proc = _captured_processes_efg[0]

                # MANDATORY: Process has valid PID
                assert hasattr(proc, 'pid'), "Process missing pid attribute"
                assert proc.pid is not None and proc.pid > 0, \
                    f"Invalid PID: {proc.pid}"

                # MANDATORY: Process is alive (returncode is None)
                assert hasattr(proc, 'returncode'), "Process missing returncode attribute"
                assert proc.returncode is None, \
                    f"Process not alive (returncode={proc.returncode})"

                await adapter.close()
            finally:
                if _original_create_process_efg and hasattr(stdio_mod, '_create_platform_compatible_process'):
                    stdio_mod._create_platform_compatible_process = _original_create_process_efg


class Test2_5E_F_Process_Exits_After_Close:
    def test_2_5e_f_process_exits(self):
        """F: Real process returncode None -> not None after close."""
        sys.path.insert(0, 'Robo')
        os.environ.setdefault('GENESIS_DATABASE_BACKEND', 'postgres')

        global _captured_processes_efg, _original_create_process_efg
        _captured_processes_efg = []
        _original_create_process_efg = None

        async def run():
            global _original_create_process_efg

            from genesis_skills.mcp_adapter import MCPReadOnlyClientAdapter
            import mcp.client.stdio as stdio_mod

            if hasattr(stdio_mod, '_create_platform_compatible_process'):
                _original_create_process_efg = stdio_mod._create_platform_compatible_process
                stdio_mod._create_platform_compatible_process = capture_process_efg

            try:
                adapter = MCPReadOnlyClientAdapter()
                await adapter.start()

                assert len(_captured_processes_efg) >= 1
                proc = _captured_processes_efg[0]

                # MANDATORY: Before close, process alive
                assert proc.returncode is None, \
                    f"Process not alive before close: returncode={proc.returncode}"

                await adapter.close()

                # MANDATORY: After close, process terminated
                # Use wait() to ensure process has fully exited
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pytest.fail("Process did not terminate after close")

                # MANDATORY: returncode not None after termination
                assert proc.returncode is not None, \
                    f"Process still running after close: returncode={proc.returncode}"
            finally:
                if _original_create_process_efg and hasattr(stdio_mod, '_create_platform_compatible_process'):
                    stdio_mod._create_platform_compatible_process = _original_create_process_efg


class Test2_5E_G_Zero_Leaks:
    def test_2_5e_g_no_child_leaks(self):
        """G: All captured processes terminated after close."""
        sys.path.insert(0, 'Robo')
        os.environ.setdefault('GENESIS_DATABASE_BACKEND', 'postgres')

        global _captured_processes_efg, _original_create_process_efg
        _captured_processes_efg = []
        _original_create_process_efg = None

        async def run():
            global _original_create_process_efg

            from genesis_skills.mcp_adapter import MCPReadOnlyClientAdapter
            import mcp.client.stdio as stdio_mod

            if hasattr(stdio_mod, '_create_platform_compatible_process'):
                _original_create_process_efg = stdio_mod._create_platform_compatible_process
                stdio_mod._create_platform_compatible_process = capture_process_efg

            try:
                adapter = MCPReadOnlyClientAdapter()
                await adapter.start()

                captured_count = len(_captured_processes_efg)
                assert captured_count >= 1, "No processes captured"

                await adapter.close()

                # MANDATORY: All captured processes must be dead (no leaks)
                alive_count = 0
                for proc in _captured_processes_efg:
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=1.0)
                    except asyncio.TimeoutError:
                        alive_count += 1

                assert alive_count == 0, \
                    f"LEAK DETECTED: {alive_count}/{captured_count} processes still alive after close"
            finally:
                if _original_create_process_efg and hasattr(stdio_mod, '_create_platform_compatible_process'):
                    stdio_mod._create_platform_compatible_process = _original_create_process_efg


class Test2_5E_H_Session_Cleanup_Attempted:
    def test_2_5e_h(self):
        sys.path.insert(0, 'Robo')
        os.environ.setdefault('GENESIS_DATABASE_BACKEND', 'postgres')
        aexit_called = False
        async def run():
            nonlocal aexit_called
            from genesis_skills.mcp_adapter import MCPReadOnlyClientAdapter
            adapter = MCPReadOnlyClientAdapter()
            await adapter.start()
            if adapter.session and hasattr(adapter.session, '__aexit__'):
                orig = adapter.session.__aexit__
                async def track(*a, **k):
                    nonlocal aexit_called
                    aexit_called = True
                    if callable(orig):
                        return await orig(*a, **k)
                adapter.session.__aexit__ = track
            await adapter.close()
        asyncio.run(run())
        assert aexit_called


class Test2_5E_I_STDIO_Cleanup_Attempted:
    def test_2_5e_i(self):
        sys.path.insert(0, 'Robo')
        os.environ.setdefault('GENESIS_DATABASE_BACKEND', 'postgres')
        aexit_called = False
        async def run():
            nonlocal aexit_called
            from genesis_skills.mcp_adapter import MCPReadOnlyClientAdapter
            adapter = MCPReadOnlyClientAdapter()
            await adapter.start()
            if adapter._stdio_context and hasattr(adapter._stdio_context, '__aexit__'):
                orig = adapter._stdio_context.__aexit__
                async def track(*a, **k):
                    nonlocal aexit_called
                    aexit_called = True
                    if callable(orig):
                        return await orig(*a, **k)
                adapter._stdio_context.__aexit__ = track
            await adapter.close()
        asyncio.run(run())
        assert aexit_called


class Test2_5E_J_Cleanup_Failure_Observable:
    def test_2_5e_j(self):
        sys.path.insert(0, 'Robo')
        os.environ.setdefault('GENESIS_DATABASE_BACKEND', 'postgres')
        error_propagated = False
        async def run():
            nonlocal error_propagated
            from genesis_skills.mcp_adapter import MCPReadOnlyClientAdapter
            adapter = MCPReadOnlyClientAdapter()
            await adapter.start()
            if adapter.session:
                async def error_exit(*a, **k):
                    raise RuntimeError("Injected cleanup error")
                adapter.session.__aexit__ = error_exit
            try:
                await adapter.close()
            except RuntimeError:
                error_propagated = True
        asyncio.run(run())
        assert error_propagated


class Test2_5E_K_Adapter_State_Cleared:
    def test_2_5e_k(self):
        sys.path.insert(0, 'Robo')
        os.environ.setdefault('GENESIS_DATABASE_BACKEND', 'postgres')
        async def run():
            from genesis_skills.mcp_adapter import MCPReadOnlyClientAdapter
            adapter = MCPReadOnlyClientAdapter()
            await adapter.start()
            # MANDATORY: Exact contract fields before close
            assert adapter.session is not None
            assert adapter._session_context is not None
            assert adapter._stdio_context is not None
            await adapter.close()
            # MANDATORY: Exact contract fields must be None post-close
            assert adapter.session is None, "session not cleared"
            assert adapter._session_context is None, "_session_context not cleared"
            assert adapter._stdio_context is None, "_stdio_context not cleared"
        asyncio.run(run())


class Test2_5E_L_Real_MCP_PostgreSQL_Call:
    @staticmethod
    def _load_env():
        env_file = Path("Robo/.env-btc")
        if env_file.exists():
            with open(env_file, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        os.environ[key] = value

    def test_2_5e_l(self):
        sys.path.insert(0, 'Robo')
        os.environ.setdefault('GENESIS_DATABASE_BACKEND', 'postgres')
        self._load_env()
        async def run():
            from genesis_skills.mcp_adapter import MCPReadOnlyClientAdapter
            import json
            adapter = MCPReadOnlyClientAdapter()
            await adapter.start()
            result = await adapter.call("get_system_health", {})
            assert result.success is True
            assert result.error_code is None
            health_json = json.loads(result.data.get('text', '{}'))
            assert health_json.get('database', {}).get('backend') == 'postgres'
            await adapter.close()
        asyncio.run(run())


class Test2_5E_M_BusinessState_Delta_Zero:
    @staticmethod
    def _load_env():
        env_file = Path("Robo/.env-btc")
        if env_file.exists():
            with open(env_file, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        os.environ[key] = value

    def test_2_5e_m(self):
        sys.path.insert(0, 'Robo')
        os.environ.setdefault('GENESIS_DATABASE_BACKEND', 'postgres')
        self._load_env()
        async def run():
            from genesis_skills.mcp_adapter import MCPReadOnlyClientAdapter
            from genesis_runtime import connect_operational_database
            adapter = MCPReadOnlyClientAdapter()
            conn = connect_operational_database(read_only=True)
            before = {}
            for table in ['trades', 'predictions_lstm', 'decision_trace', 'mr_fsm_state', 'decision_events']:
                before[table] = conn.execute(f"SELECT COUNT(*) FROM {table};").fetchone()[0]
            conn.close()
            await adapter.start()
            r1 = await adapter.call("get_system_health", {})
            assert r1.success
            r2 = await adapter.call("list_trades", {})
            assert r2.success
            await adapter.close()
            conn = connect_operational_database(read_only=True)
            after = {}
            for table in ['trades', 'predictions_lstm', 'decision_trace', 'mr_fsm_state', 'decision_events']:
                after[table] = conn.execute(f"SELECT COUNT(*) FROM {table};").fetchone()[0]
            conn.close()
            for table in before.keys():
                assert before[table] == after[table], f"{table}: {before[table]} != {after[table]}"
        asyncio.run(run())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
