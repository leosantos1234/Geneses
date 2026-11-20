"""MCPReadOnlyClientAdapter — STDIO MCP client with a dedicated owner task.

A single long-lived worker task owns the stdio_client and ClientSession contexts.
Caller tasks never enter, exit or call the raw session; they enqueue requests and
await futures resolved by the worker.
"""

import asyncio
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp import ClientSession, StdioServerParameters
import mcp.client.stdio as _mcp_stdio
from mcp.client.stdio import stdio_client
from mcp.os.win32.utilities import FallbackProcess

logger = logging.getLogger(__name__)


async def _create_windows_stdio_process_compat(
    command: str,
    args: list[str],
    env: Optional[Dict[str, str]] = None,
    errlog=None,
    cwd=None,
):
    """Use Popen-backed async streams when Windows async pipes return WinError 5.

    Some Windows configurations deny the overlapped-pipe handles created by
    AnyIO's Proactor subprocess transport. The MCP SDK already ships
    ``FallbackProcess`` for this case; use it proactively for this adapter.
    """
    if os.name != "nt":
        return await _mcp_stdio._create_platform_compatible_process(
            command=command, args=args, env=env, errlog=errlog, cwd=cwd
        )

    popen_kwargs = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": errlog,
        "env": env,
        "cwd": cwd,
        "bufsize": 0,
    }
    try:
        process = subprocess.Popen(
            [command, *args],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            **popen_kwargs,
        )
    except OSError:
        # A restricted Windows policy may reject CREATE_NO_WINDOW while still
        # allowing the normal child process creation path.
        process = subprocess.Popen([command, *args], **popen_kwargs)
    return FallbackProcess(process)


# stdio_client resolves its process factory from the MCP module globals. Patch
# only that factory on Windows; the MCP protocol and allowed tools remain
# unchanged.
if os.name == "nt":
    _mcp_stdio._create_platform_compatible_process = _create_windows_stdio_process_compat

_CALL = "CALL"
_STOP = "STOP"


@dataclass
class MCPToolResult:
    """Normalized MCP tool response."""
    success: bool
    tool_name: str
    data: Any = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    request_id: Optional[str] = None


class MCPWorkerError(RuntimeError):
    """MCP owner task failed or is unavailable."""


@dataclass
class _Request:
    kind: str
    tool_name: Optional[str] = None
    arguments: Optional[Dict[str, Any]] = None
    future: Optional[asyncio.Future] = None


def _settle(future: asyncio.Future, result: Any = None, exc: Optional[BaseException] = None) -> None:
    if future.done():
        return
    if exc is not None:
        future.set_exception(exc)
    else:
        future.set_result(result)


class MCPReadOnlyClientAdapter:
    """Real MCP STDIO adapter; all MCP SDK contexts are owned by one worker task."""

    ALLOWED_TOOLS = {
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

    def __init__(self, startup_timeout_seconds: int = 30, operation_timeout_seconds: int = 30):
        """Initialize — does NOT start server yet.

        Args:
            startup_timeout_seconds: Timeout for server startup/initialize in seconds (default: 30)
            operation_timeout_seconds: Timeout for individual tool calls in seconds (default: 30)
        """
        self.session = None
        self._stdio_context = None
        self._session_context = None
        self.startup_timeout_seconds = startup_timeout_seconds
        self.operation_timeout_seconds = operation_timeout_seconds
        self._lifecycle_lock: Optional[asyncio.Lock] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._queue: Optional[asyncio.Queue] = None
        self._accepting = False
        self._cleanup_errors: List[BaseException] = []
        self.worker_starts = 0

    @property
    def is_running(self) -> bool:
        return self._worker_alive()

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    def _get_lock(self) -> asyncio.Lock:
        if self._lifecycle_lock is None:
            self._lifecycle_lock = asyncio.Lock()
        return self._lifecycle_lock

    def _worker_alive(self) -> bool:
        return (
            self._accepting
            and self._worker_task is not None
            and not self._worker_task.done()
        )

    def _server_params(self) -> StdioServerParameters:
        robo_root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        env['PYTHONPATH'] = str(robo_root) + os.pathsep + env.get('PYTHONPATH', '')
        return StdioServerParameters(
            command=sys.executable,
            args=["-m", "genesis_mcp.server"],
            env=env,
            cwd=str(robo_root),
        )

    async def start(self):
        """Start the owner worker if it is not already running. Idempotent."""
        async with self._get_lock():
            if self._worker_alive():
                return
            await self._spawn_worker()

    async def _spawn_worker(self):
        await self._reap_worker()
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        ready = loop.create_future()
        self._queue = queue
        self._accepting = True
        self.worker_starts += 1
        self._worker_task = asyncio.create_task(self._worker_main(queue, ready))
        try:
            await asyncio.wait_for(
                asyncio.shield(ready),
                timeout=self.startup_timeout_seconds + 10,
            )
        except asyncio.CancelledError:
            await self._reap_worker()
            raise
        except Exception as exc:
            await self._reap_worker()
            raise MCPWorkerError(
                f"MCP startup failed: {str(exc) or type(exc).__name__}"
            ) from exc

    async def _worker_main(self, queue: asyncio.Queue, ready: asyncio.Future):
        stdio_cm = None
        session_cm = None
        stdio_entered = False
        session_entered = False
        inflight: Optional[_Request] = None
        fatal: Optional[BaseException] = None
        try:
            stdio_cm = stdio_client(self._server_params())
            read_stream, write_stream = await stdio_cm.__aenter__()
            stdio_entered = True
            self._stdio_context = stdio_cm

            session_cm = ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=self.startup_timeout_seconds),
            )
            session = await session_cm.__aenter__()
            session_entered = True
            self._session_context = session_cm
            self.session = session

            await asyncio.wait_for(session.initialize(), timeout=self.startup_timeout_seconds)
            tools_resp = await asyncio.wait_for(session.list_tools(), timeout=self.startup_timeout_seconds)
            actual_tools = {t.name for t in tools_resp.tools} if tools_resp else set()
            if actual_tools != self.ALLOWED_TOOLS:
                raise MCPWorkerError(
                    f"Tool mismatch: expected {self.ALLOWED_TOOLS}, got {actual_tools}"
                )

            _settle(ready, result=None)

            while True:
                req = await queue.get()
                if req.kind == _STOP:
                    break
                if req.future.done():
                    continue
                inflight = req
                try:
                    result = await asyncio.wait_for(
                        session.call_tool(req.tool_name, req.arguments),
                        timeout=self.operation_timeout_seconds,
                    )
                except Exception as exc:
                    _settle(req.future, exc=exc)
                else:
                    _settle(req.future, result=result)
                inflight = None
        except asyncio.CancelledError:
            fatal = MCPWorkerError("MCP worker cancelled")
            raise
        except Exception as exc:
            fatal = MCPWorkerError(f"MCP worker failed: {str(exc) or type(exc).__name__}")
            logger.warning("MCP worker terminated: %s", fatal)
        finally:
            self._accepting = False
            failure = fatal or MCPWorkerError("MCP worker stopped")
            if inflight is not None:
                _settle(inflight.future, exc=failure)
            while True:
                try:
                    pending = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if pending.kind == _CALL:
                    _settle(pending.future, exc=failure)
            _settle(ready, exc=failure)

            if session_entered:
                try:
                    await session_cm.__aexit__(None, None, None)
                except BaseException as exc:
                    self._cleanup_errors.append(exc)
            if stdio_entered:
                try:
                    await stdio_cm.__aexit__(None, None, None)
                except BaseException as exc:
                    self._cleanup_errors.append(exc)
            self.session = None
            self._session_context = None
            self._stdio_context = None

    async def _reap_worker(self):
        task = self._worker_task
        self._accepting = False
        if task is not None and not task.done():
            done, _ = await asyncio.wait({task}, timeout=self.startup_timeout_seconds)
            if not done:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self._worker_task = None
        self._queue = None

    async def call(self, tool_name: str, arguments: Dict[str, Any]) -> MCPToolResult:
        """Call an allowed MCP tool through the owner worker."""
        if tool_name not in self.ALLOWED_TOOLS:
            return MCPToolResult(
                success=False,
                tool_name=tool_name,
                error_code="TOOL_NOT_FOUND",
                error_message=f"Tool {tool_name} not found"
            )

        await self.start()
        if not self._accepting or self._queue is None:
            raise MCPWorkerError("MCP worker is not accepting requests")

        future = asyncio.get_running_loop().create_future()
        self._queue.put_nowait(_Request(kind=_CALL, tool_name=tool_name, arguments=arguments, future=future))

        try:
            response = await future
        except asyncio.TimeoutError:
            return MCPToolResult(
                success=False,
                tool_name=tool_name,
                error_code="MCP_NOT_AVAILABLE",
                error_message="MCP tool call timed out"
            )
        except Exception as e:
            error_str = str(e)

            error_code = "INTERNAL_ERROR"
            if "NOT_FOUND" in error_str:
                error_code = "NOT_FOUND"
            elif "DATABASE" in error_str:
                error_code = "DATABASE_UNAVAILABLE"
            elif "SERVICE" in error_str:
                error_code = "SERVICE_UNAVAILABLE"
            elif "READ_ONLY" in error_str:
                error_code = "READ_ONLY_VIOLATION"
            elif "timeout" in error_str.lower():
                error_code = "MCP_NOT_AVAILABLE"

            return MCPToolResult(
                success=False,
                tool_name=tool_name,
                error_code=error_code,
                error_message=error_str
            )

        if response and hasattr(response, 'content'):
            content = response.content
            if content:
                text_content = None
                for block in content:
                    if hasattr(block, 'text'):
                        text_content = block.text
                        break

                if text_content:
                    return MCPToolResult(
                        success=True,
                        tool_name=tool_name,
                        data={"text": text_content}
                    )

        return MCPToolResult(
            success=True,
            tool_name=tool_name,
            data=response
        )

    async def close(self):
        """Stop the owner worker; the worker exits its own MCP contexts. Idempotent."""
        async with self._get_lock():
            task = self._worker_task
            if task is None:
                return
            if self._accepting and self._queue is not None:
                self._accepting = False
                self._queue.put_nowait(_Request(kind=_STOP))
            done, _ = await asyncio.wait({task}, timeout=self.startup_timeout_seconds)
            timed_out = not done
            if timed_out:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            self._worker_task = None
            self._queue = None
            errors, self._cleanup_errors = self._cleanup_errors, []
            if timed_out:
                raise MCPWorkerError("MCP worker did not stop within the shutdown grace period")
            if errors:
                raise errors[0]
