import json
import os
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, Optional


AUDIT_JSONL = os.path.join("logs", "strategy_audit.jsonl")
AUDIT_LAST = os.path.join("reports", "audit", "strategy_audit_latest.json")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_git_rev() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
        out = (proc.stdout or "").strip()
        return out if out else "nogit"
    except Exception:
        return "nogit"


def _safe_git_dirty() -> Optional[bool]:
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
        if proc.returncode != 0:
            return None
        return bool((proc.stdout or "").strip())
    except Exception:
        return None


def _write_jsonl(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _write_last(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def register_strategy_audit(
    event_type: str,
    symbol: str,
    strategy: str,
    side: str,
    params: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "ts": _utc_now(),
        "event_type": str(event_type or "strategy_event"),
        "symbol": str(symbol or "UNKNOWN").upper(),
        "strategy": str(strategy or "unknown"),
        "side": str(side or "unknown").lower(),
        "version": {
            "bot_version": os.getenv("BOT_VERSION", "dev"),
            "strategy_version": os.getenv("STRATEGY_VERSION", "dev"),
            "params_version": os.getenv("PARAMS_VERSION", "dev"),
            "git_rev": _safe_git_rev(),
            "git_dirty": _safe_git_dirty(),
            "env_file": os.getenv("ENV_FILE", ""),
        },
        "params": params or {},
        "context": context or {},
    }
    _write_jsonl(AUDIT_JSONL, payload)
    _write_last(AUDIT_LAST, payload)
    return payload

