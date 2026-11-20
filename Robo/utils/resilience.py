"""Circuit breaker e retry com backoff para integrações externas."""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from collections.abc import Callable
from typing import Any, TypeVar

try:
    from utils.performance_metrics import record_timing
except ImportError:
    from Robo.utils.performance_metrics import record_timing


T = TypeVar("T")
logger = logging.getLogger(__name__)


def _float_env(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


class CircuitOpenError(RuntimeError):
    """Indica que o serviço está temporariamente bloqueado."""


class CircuitBreaker:
    def __init__(self, name: str):
        self.name = name
        self.failure_threshold = max(1, int(os.getenv("GENESIS_CB_FAILURE_THRESHOLD", "3")))
        self.reset_seconds = _float_env("GENESIS_CB_RESET_SECONDS", 30.0)
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_probe = False
        self._lock = threading.Lock()

    def _allow(self) -> None:
        with self._lock:
            if not self._opened_at:
                return
            elapsed = time.monotonic() - self._opened_at
            if elapsed < self.reset_seconds:
                raise CircuitOpenError(
                    f"circuit_open service={self.name} retry_after={self.reset_seconds - elapsed:.1f}s"
                )
            if self._half_open_probe:
                raise CircuitOpenError(f"circuit_half_open service={self.name}")
            self._half_open_probe = True

    def success(self) -> None:
        with self._lock:
            was_open = bool(self._opened_at)
            self._failures = 0
            self._opened_at = 0.0
            self._half_open_probe = False
        if was_open:
            logger.info("CIRCUIT_CLOSED service=%s", self.name)

    def failure(self) -> None:
        with self._lock:
            self._half_open_probe = False
            self._failures += 1
            if self._failures >= self.failure_threshold and not self._opened_at:
                self._opened_at = time.monotonic()
                logger.error(
                    "CIRCUIT_OPEN service=%s failures=%d reset_seconds=%s",
                    self.name, self._failures, self.reset_seconds,
                )

    def call(self, operation: Callable[[], T]) -> T:
        self._allow()
        try:
            result = operation()
        except Exception:
            self.failure()
            raise
        self.success()
        return result


_BREAKERS: dict[str, CircuitBreaker] = {}
_BREAKERS_LOCK = threading.Lock()


def breaker_for(name: str) -> CircuitBreaker:
    with _BREAKERS_LOCK:
        if name not in _BREAKERS:
            _BREAKERS[name] = CircuitBreaker(name)
        return _BREAKERS[name]


def is_retryable_error(error: Exception) -> bool:
    text = str(error).lower()
    transient_markers = (
        "timeout", "timed out", "connection", "temporarily", "rate limit",
        "too many", "http 429", "http 500", "http 502", "http 503", "http 504",
    )
    return any(marker in text for marker in transient_markers)


def call_with_backoff(
    service: str,
    operation: Callable[[], T],
    *,
    retry: bool = True,
    max_retries: int | None = None,
    operation_name: str = "external_call",
) -> T:
    """Executa operação com breaker e backoff exponencial com jitter."""
    started = time.perf_counter()
    status = "success"
    error_type: str | None = None
    retries = (
        max(0, int(max_retries))
        if max_retries is not None
        else max(0, int(os.getenv("GENESIS_HTTP_MAX_RETRIES", "2")))
    )
    base = _float_env("GENESIS_HTTP_BACKOFF_BASE_SECONDS", 0.5)
    maximum = _float_env("GENESIS_HTTP_BACKOFF_MAX_SECONDS", 8.0)
    breaker = breaker_for(service)
    attempts = retries + 1 if retry else 1

    try:
        for attempt in range(attempts):
            try:
                return breaker.call(operation)
            except CircuitOpenError:
                raise
            except Exception as exc:
                if attempt >= attempts - 1 or not retry or not is_retryable_error(exc):
                    raise
                delay = min(maximum, base * (2 ** attempt))
                delay += random.uniform(0.0, delay * 0.25)
                logger.warning(
                    "EXTERNAL_RETRY service=%s attempt=%d/%d delay=%.2fs error=%s",
                    service, attempt + 1, attempts - 1, delay, type(exc).__name__,
                )
                time.sleep(delay)
        raise RuntimeError("unreachable")
    except Exception as exc:
        status = "error"
        error_type = type(exc).__name__
        raise
    finally:
        try:
            record_timing(
                "GENESIS_EXTERNAL_CALL",
                time.perf_counter() - started,
                role=os.getenv("GENESIS_PROCESS_ROLE", "runtime"),
                symbol=(os.getenv("ATIVOS", "UNKNOWN").split(",")[0].strip().upper() or "UNKNOWN"),
                operation=operation_name,
                service=service,
                status=status,
                error_type=error_type,
                extra={"retry_enabled": retry, "max_attempts": attempts},
            )
        except Exception as metrics_exc:
            logger.warning("PERFORMANCE_METRICS_WRITE_FAILED error=%s", metrics_exc)
