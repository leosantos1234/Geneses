"""Load only the canonical PostgreSQL settings from Robo/.env-btc; values are never printed."""

import os
from pathlib import Path

_CANONICAL_DB_KEYS = ("GENESIS_DATABASE_BACKEND", "GENESIS_DATABASE_URL")


def _load_canonical_db_settings() -> None:
    env_file = Path(__file__).resolve().parent / ".env-btc"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and key in _CANONICAL_DB_KEYS and not os.environ.get(key):
            os.environ[key] = value.strip().strip('"').strip("'")


_load_canonical_db_settings()
