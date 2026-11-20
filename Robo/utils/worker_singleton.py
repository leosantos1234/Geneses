"""Cross-platform singleton lock for one trading worker per symbol."""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import TextIO


class WorkerAlreadyRunning(RuntimeError):
    """Raised when another worker already owns the symbol lock."""


class WorkerInstanceLock:
    """Hold an OS-level lock for the lifetime of one symbol worker."""

    def __init__(self, symbol: str, lock_dir: str | None = None) -> None:
        normalized = "".join(
            ch for ch in str(symbol or "UNKNOWN").upper()
            if ch.isalnum() or ch in ("-", "_")
        )
        self.symbol = normalized or "UNKNOWN"
        configured_dir = lock_dir or os.getenv("GENESIS_WORKER_LOCK_DIR")
        base = Path(configured_dir) if configured_dir else Path(__file__).resolve().parents[1] / "pids"
        self.path = base / f"genesis_worker_{self.symbol}.lock"
        self._handle: TextIO | None = None
        self._locked = False

    def acquire(self) -> "WorkerInstanceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        handle.seek(0)
        if self.path.stat().st_size == 0:
            handle.write(" ")
            handle.flush()
            handle.seek(0)

        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError) as exc:
            handle.close()
            raise WorkerAlreadyRunning(
                f"worker do ativo {self.symbol} já está em execução (lock: {self.path})"
            ) from exc

        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()} host={socket.gethostname()}\n")
        handle.flush()
        self._handle = handle
        self._locked = True
        return self

    def release(self) -> None:
        if not self._handle or not self._locked:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._locked = False
            self._handle.close()
            self._handle = None

