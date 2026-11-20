"""Caminhos e gravação segura dos caches do Robo."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


ROBO_ROOT = Path(__file__).resolve().parents[1]
_configured_cache_dir = os.getenv("GENESIS_CACHE_DIR", "").strip()
if _configured_cache_dir:
    CACHE_DIR = Path(_configured_cache_dir).expanduser()
    if not CACHE_DIR.is_absolute():
        CACHE_DIR = ROBO_ROOT / CACHE_DIR
else:
    CACHE_DIR = ROBO_ROOT / "cache"
CACHE_DIR = CACHE_DIR.resolve()
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def cache_path(name: str) -> Path:
    """Retorna o caminho canônico de um arquivo de cache."""
    return CACHE_DIR / Path(name).name


def _legacy_path(path: Path) -> Path | None:
    """Permite ler caches antigos que ficavam diretamente em Robo."""
    try:
        if path.parent.resolve() == CACHE_DIR:
            legacy = ROBO_ROOT / path.name
            return legacy if legacy.exists() else None
    except OSError:
        pass
    return None


def _read_path(path: Path) -> Path:
    if path.exists():
        return path
    return _legacy_path(path) or path


def load_json(path: str | Path) -> Any:
    target = _read_path(Path(path))
    if not target.exists():
        return None
    return json.loads(target.read_text(encoding="utf-8"))


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def save_json(data: Any, path: str | Path) -> None:
    _atomic_write(Path(path), json.dumps(data, ensure_ascii=False, indent=4))


def load_text(path: str | Path) -> str | None:
    target = _read_path(Path(path))
    if not target.exists():
        return None
    return target.read_text(encoding="utf-8")


def save_text(value: str, path: str | Path) -> None:
    _atomic_write(Path(path), value)
