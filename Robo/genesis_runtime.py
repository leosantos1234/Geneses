"""Contratos de runtime do Genesis; sem lógica quantitativa ou de trading."""

from __future__ import annotations

import os
import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from utils.resource_monitor import start_resource_monitor
except ImportError:  # Importação como pacote a partir da raiz do projeto.
    from Robo.utils.resource_monitor import start_resource_monitor
try:
    from utils.decision_metrics import start_decision_metrics_monitor
except ImportError:  # Importação como pacote a partir da raiz do projeto.
    from Robo.utils.decision_metrics import start_decision_metrics_monitor
try:
    from utils.postgres_metrics import start_postgres_metrics_monitor
except ImportError:  # Importação como pacote a partir da raiz do projeto.
    from Robo.utils.postgres_metrics import start_postgres_metrics_monitor
try:
    from utils.position_reconciliation import start_position_reconciliation_monitor
except ImportError:
    from Robo.utils.position_reconciliation import start_position_reconciliation_monitor
try:
    from utils.process_audit import start_process_audit_monitor
except ImportError:
    from Robo.utils.process_audit import start_process_audit_monitor


ROBO_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROBO_ROOT.parent
CANONICAL_WORKER = ROBO_ROOT / "trading_bot_geneses.py"
CANONICAL_SQLITE_DATABASE = ROBO_ROOT / "trades.db"
STARTUP_EVENTS_DIR = ROBO_ROOT / "runtime" / "startups"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "sim"}


def database_backend() -> str:
    """Resolve o único backend operacional suportado: PostgreSQL."""
    raw = str(os.getenv("GENESIS_DATABASE_BACKEND", "postgres")).strip().lower()
    backend = {"postgresql": "postgres", "psql": "postgres", "sqlite3": "sqlite"}.get(raw, raw)
    if backend != "postgres":
        raise RuntimeError("SQLite não é suportado pelo runtime operacional; configure GENESIS_DATABASE_BACKEND=postgres.")
    return backend


def database_url() -> str | None:
    if database_backend() != "postgres":
        return None
    value = str(os.getenv("GENESIS_DATABASE_URL", "")).strip()
    if not value:
        raise RuntimeError("GENESIS_DATABASE_BACKEND=postgres exige GENESIS_DATABASE_URL.")
    return value


def database_path() -> Path:
    raw = str(os.getenv("GENESIS_SQLITE_PATH", "")).strip()
    path = Path(raw) if raw else CANONICAL_SQLITE_DATABASE
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def connect_operational_database(*, read_only: bool = False):
    """Abre o banco operacional PostgreSQL; nunca faz fallback para SQLite."""
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("PostgreSQL configurado, mas o driver psycopg não está instalado.") from exc
    options = "-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000" if read_only else None
    kwargs = {"connect_timeout": int(os.getenv("GENESIS_DB_CONNECT_TIMEOUT", "5"))}
    if options:
        kwargs["options"] = options
    return psycopg.connect(database_url(), **kwargs)


def database_health() -> dict[str, Any]:
    """Healthcheck read-only do banco operacional, sem expor credenciais."""
    backend = database_backend()
    target = runtime_snapshot()["database_target"]
    try:
        conn = connect_operational_database(read_only=True)
        conn.execute("SELECT 1").fetchone()
        conn.close()
        return {"backend": backend, "target": target, "status": "healthy", "canonical": backend == "postgres"}
    except Exception as exc:
        return {"backend": backend, "target": target, "status": "unavailable", "canonical": backend == "postgres", "error": str(exc)}


def agent_api_trading_enabled() -> bool:
    return _env_bool("ENABLE_AGENT_API_TRADING", False)


def exchange_write_enabled() -> bool:
    return _env_bool("ALLOW_EXCHANGE_WRITE", False)


def allowed_agent_origins() -> list[str]:
    raw = os.getenv(
        "GENESIS_AGENT_ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:5173",
    )
    return [item.strip() for item in raw.split(",") if item.strip()]


def _active_symbols() -> list[str]:
    """Resolve os ativos configurados sem expor outras variáveis do ambiente."""
    raw = os.getenv("ATIVOS", os.getenv("ACTIVE_SYMBOL", ""))
    symbols = [item.strip().upper() for item in raw.split(",") if item.strip()]
    return symbols or ["UNKNOWN"]


def runtime_snapshot() -> dict[str, Any]:
    backend = database_backend()
    url = database_url()
    if url:
        database_target = url.split("@", 1)[-1]
        if "@" not in url:
            database_target = "<configured-url-without-host>"
    else:
        database_target = str(database_path())
    active_symbols = _active_symbols()
    return {
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "active_symbol": active_symbols[0],
        "active_symbols": active_symbols,
        "environment": os.getenv("GENESIS_ENVIRONMENT", os.getenv("ENVIRONMENT", "local")),
        "canonical_worker": str(CANONICAL_WORKER),
        "database_backend": backend,
        "database_target": database_target,
        "exchange": os.getenv("ACTIVE_EXCHANGE", "unknown"),
        "live_trading_enabled": _env_bool("ENABLE_LIVE_TRADING", False),
        "agent_api_trading_enabled": agent_api_trading_enabled(),
        "exchange_write_gate": exchange_write_enabled(),
        "risk_kill_switch": bool(os.getenv("RISK_KILL_SWITCH", "")),
        "version": os.getenv("GENESIS_VERSION", "unversioned"),
    }


def _persist_startup_event(snapshot: dict[str, Any], role: str) -> None:
    """Persiste uma linha JSON por startup para auditoria pós-execução."""
    STARTUP_EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    event = {
        "event": "GENESIS_STARTUP",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "role": role,
        "pid": snapshot["pid"],
        "hostname": snapshot["hostname"],
        "active_symbol": snapshot["active_symbol"],
        "active_symbols": snapshot["active_symbols"],
        "environment": snapshot["environment"],
        "version": snapshot["version"],
        "exchange": snapshot["exchange"],
        "canonical_worker": snapshot["canonical_worker"],
        "database_backend": snapshot["database_backend"],
        "database_target": snapshot["database_target"],
    }
    event_path = STARTUP_EVENTS_DIR / "startup_events.jsonl"
    with event_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def log_runtime_startup(logger, *, role: str = "runtime") -> None:
    snapshot = runtime_snapshot()
    os.environ.setdefault("GENESIS_PROCESS_ROLE", role)
    logger.info(
        "[GENESIS_STARTUP] role=%s pid=%s active_symbol=%s active_symbols=%s environment=%s version=%s exchange=%s",
        role, snapshot["pid"], snapshot["active_symbol"], ",".join(snapshot["active_symbols"]),
        snapshot["environment"], snapshot["version"], snapshot["exchange"],
    )
    logger.info("[GENESIS_STARTUP] canonical_worker=%s", snapshot["canonical_worker"])
    logger.info("[GENESIS_STARTUP] database_backend=%s database_target=%s", snapshot["database_backend"], snapshot["database_target"])
    logger.info(
        "[GENESIS_STARTUP] exchange=%s live_trading_enabled=%s agent_api_trading_enabled=%s exchange_write_gate=%s risk_kill_switch=%s version=%s",
        snapshot["exchange"], snapshot["live_trading_enabled"], snapshot["agent_api_trading_enabled"],
        snapshot["exchange_write_gate"], snapshot["risk_kill_switch"], snapshot["version"],
    )
    try:
        _persist_startup_event(snapshot, role)
    except Exception as exc:
        logger.warning("[GENESIS_STARTUP] persist_failed=%s", exc)
    start_resource_monitor(snapshot, role=role, symbol=snapshot["active_symbol"], logger=logger)
    start_decision_metrics_monitor(snapshot, role=role, symbol=snapshot["active_symbol"], logger=logger)
    start_postgres_metrics_monitor(snapshot, role=role, symbol=snapshot["active_symbol"], logger=logger)
    start_position_reconciliation_monitor(snapshot, role=role, symbol=snapshot["active_symbol"], logger=logger)
    start_process_audit_monitor(snapshot, role=role, symbol=snapshot["active_symbol"], logger=logger)
