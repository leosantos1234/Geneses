"""Exchange state mutation guard.

Live writes are enabled by default for the configured production account.
Set ``OKX_READ_ONLY=true`` or ``EXCHANGE_WRITE_DISABLED=true`` to block
mutations explicitly.
"""

from __future__ import annotations

import functools
import os
from typing import Any, Callable


class ExchangeWriteBlocked(RuntimeError):
    """Raised when an exchange mutation is not explicitly authorized."""


_MUTATING_METHODS = frozenset(
    {
        "place_order",
        "place_algo_order",
        "create_order",
        "cancel_order",
        "cancel_batch_orders",
        "amend_order",
        "set_leverage",
        "set_trading_stop",
        "set_trailing_stop",
        "set_position_mode",
        "close_positions",
        "transfer",
        "withdraw",
    }
)


def _enabled(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on", "sim"}


def live_write_authorized() -> bool:
    """Return true unless an explicit write-blocking flag is enabled."""

    return not (
        _enabled("OKX_READ_ONLY")
        or _enabled("EXCHANGE_WRITE_DISABLED")
    )


def assert_exchange_write_allowed(operation: str) -> None:
    if not live_write_authorized():
        raise ExchangeWriteBlocked(
            f"Operacao {operation} bloqueada por configuracao explicita de somente leitura."
        )


class GuardedExchangeProxy:
    """Proxy that blocks mutating methods on legacy exchange clients."""

    def __init__(self, client: Any):
        self._client = client

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._client, name)
        if name not in _MUTATING_METHODS or not callable(value):
            return value

        @functools.wraps(value)
        def guarded(*args: Any, **kwargs: Any) -> Any:
            assert_exchange_write_allowed(name)
            return value(*args, **kwargs)

        return guarded

