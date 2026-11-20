"""Validation utilities para tools MCP."""
from __future__ import annotations

import re
from datetime import datetime

from .models import ErrorInfo

_SAFE_TEXT = re.compile(r"^[A-Za-z0-9_.:/|+\- ]{1,256}$")


def valid_filter(value: str | None, name: str) -> ErrorInfo | None:
    if value is not None and (not value or len(value) > 256 or not _SAFE_TEXT.fullmatch(value)):
        return ErrorInfo(code="INVALID_ARGUMENT", message=f"{name} possui formato inválido")
    return None


def valid_time(value: str | None, name: str) -> ErrorInfo | None:
    invalid = valid_filter(value, name)
    if invalid or value is None:
        return invalid
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return ErrorInfo(code="INVALID_ARGUMENT", message=f"{name} deve ser ISO-8601")
    return None


def valid_limit(value: int, name: str = "limit", min_val: int = 1, max_val: int = 500) -> ErrorInfo | None:
    if value < min_val or value > max_val:
        return ErrorInfo(code="INVALID_ARGUMENT", message=f"{name} deve estar entre {min_val} e {max_val}")
    return None


def valid_offset(value: int, name: str = "offset") -> ErrorInfo | None:
    if value < 0:
        return ErrorInfo(code="INVALID_ARGUMENT", message=f"{name} não pode ser negativo")
    return None
