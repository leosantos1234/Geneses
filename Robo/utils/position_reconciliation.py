"""Reconciliação read-only de posições: exchange, PostgreSQL e dashboard."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROBO_ROOT = Path(__file__).resolve().parents[1]
RECONCILIATION_DIR = Path(os.getenv("GENESIS_POSITION_RECONCILIATION_DIR", str(ROBO_ROOT / "runtime" / "metrics"))).expanduser()
if not RECONCILIATION_DIR.is_absolute():
    RECONCILIATION_DIR = (ROBO_ROOT / RECONCILIATION_DIR).resolve()
_MONITOR_LOCK = threading.Lock()
_MONITOR_STARTED = False


def _symbol(value: Any) -> str:
    return str(value or "UNKNOWN").upper().replace("/", "").replace("-SWAP", "").replace("-", "")


def _side(value: Any) -> str:
    raw = str(value or "NET").strip().lower()
    return {"buy": "long", "sell": "short", "long": "long", "short": "short"}.get(raw, raw or "net")


def _key(position: dict[str, Any]) -> tuple[str, str]:
    return _symbol(position.get("symbol")), _side(position.get("side"))


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _exchange_positions() -> list[dict[str, Any]]:
    try:
        from exchange.factory import active_exchange_name, create_exchange_client
    except ImportError:
        from Robo.exchange.factory import active_exchange_name, create_exchange_client
    exchange_name = active_exchange_name().lower()
    client = create_exchange_client()
    response = client.get_positions(category="linear") if exchange_name == "okx" else client.get_positions(category="linear", settleCoin="USDT")
    if exchange_name == "okx":
        rows = response.get("result", {}).get("list", [])
    else:
        if response.get("retCode") not in (None, 0):
            raise RuntimeError(response.get("retMsg") or "exchange position query failed")
        rows = response.get("result", {}).get("list", [])
    positions = []
    for row in rows or []:
        size = _float(row.get("pos") or row.get("size"))
        if abs(size) <= 0:
            continue
        positions.append({
            "symbol": _symbol(row.get("symbol") or row.get("instId")),
            "side": _side(row.get("side") or row.get("posSide") or ("short" if size < 0 else "long")),
            "quantity": abs(size),
            "entry_price": _float(row.get("avgPx") or row.get("entryPrice") or row.get("avgEntryPrice")),
            "source": f"{exchange_name}.positions",
        })
    return positions


def _database_positions() -> list[dict[str, Any]]:
    try:
        from genesis_runtime import connect_operational_database
    except ImportError:
        from Robo.genesis_runtime import connect_operational_database
    query = """
        SELECT symbol, side, quantity, entry_price
        FROM trades
        WHERE LOWER(TRIM(COALESCE(status, ''))) IN ('aberto', 'open', 'pending', 'partially_filled')
          AND COALESCE(quantity, 0) > 0
    """
    aggregates: dict[tuple[str, str], dict[str, Any]] = {}
    with connect_operational_database(read_only=True) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            for symbol, side, quantity, entry_price in cur.fetchall():
                key = (_symbol(symbol), _side(side))
                item = aggregates.setdefault(key, {"symbol": key[0], "side": key[1], "quantity": 0.0, "entry_price": 0.0, "source": "postgres.trades"})
                qty = abs(_float(quantity))
                old_qty = item["quantity"]
                item["entry_price"] = ((item["entry_price"] * old_qty) + (_float(entry_price) * qty)) / (old_qty + qty) if old_qty + qty else 0.0
                item["quantity"] += qty
    return list(aggregates.values())


def _close_orphan_database_positions(exchange_positions: list[dict[str, Any]]) -> int:
    """Encerra trades ativos sem posicao correspondente na exchange."""
    enabled = str(os.getenv("GENESIS_AUTO_CLOSE_ORPHAN_TRADES", "true")).strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return 0
    try:
        from genesis_runtime import connect_operational_database
    except ImportError:
        from Robo.genesis_runtime import connect_operational_database

    exchange_keys = {_key(item) for item in exchange_positions}
    active_sql = """
        SELECT trade_id, symbol, side
        FROM trades
        WHERE LOWER(TRIM(COALESCE(status, ''))) IN ('aberto', 'open', 'pending', 'partially_filled')
          AND COALESCE(quantity, 0) > 0
    """
    update_sql = """
        UPDATE trades
        SET exit_price = COALESCE(exit_price, entry_price),
            profit_loss = COALESCE(profit_loss, 0),
            close_ts = COALESCE(close_ts, CURRENT_TIMESTAMP::text),
            status = 'fechado'
        WHERE trade_id = %s
          AND LOWER(TRIM(COALESCE(status, ''))) IN ('aberto', 'open', 'pending', 'partially_filled')
    """
    closed = 0
    with connect_operational_database() as conn:
        with conn.cursor() as cur:
            cur.execute(active_sql)
            for trade_id, symbol, side in cur.fetchall():
                if (_symbol(symbol), _side(side)) in exchange_keys:
                    continue
                cur.execute(update_sql, (trade_id,))
                closed += cur.rowcount
        conn.commit()
    return closed


def _compare(left: list[dict[str, Any]], right: list[dict[str, Any]], *, left_name: str, right_name: str) -> list[dict[str, Any]]:
    quantity_tolerance = float(os.getenv("GENESIS_POSITION_QTY_TOLERANCE", "0.00000001"))
    price_tolerance = float(os.getenv("GENESIS_POSITION_PRICE_TOLERANCE", "0.000001"))
    left_map = {_key(item): item for item in left}
    right_map = {_key(item): item for item in right}
    issues = []
    for key in sorted(set(left_map) | set(right_map)):
        a, b = left_map.get(key), right_map.get(key)
        if a is None or b is None:
            issues.append({"type": "missing_position", "key": key, left_name: bool(a), right_name: bool(b)})
            continue
        quantity_delta = abs(_float(a.get("quantity")) - _float(b.get("quantity")))
        price_delta = abs(_float(a.get("entry_price")) - _float(b.get("entry_price")))
        if quantity_delta > quantity_tolerance or price_delta > price_tolerance:
            issues.append({"type": "position_mismatch", "key": key, "quantity_delta": quantity_delta, "entry_price_delta": price_delta, left_name: a, right_name: b})
    return issues


def reconcile_positions() -> dict[str, Any]:
    exchange = _exchange_positions()
    _close_orphan_database_positions(exchange)
    database = _database_positions()
    dashboard = [dict(item, source="dashboard.exchange_projection") for item in exchange]
    issues = _compare(exchange, database, left_name="exchange", right_name="database")
    issues.extend(_compare(exchange, dashboard, left_name="exchange", right_name="dashboard"))
    return {"status": "matched" if not issues else "divergent", "exchange": exchange, "database": database, "dashboard": dashboard, "issues": issues}


def record_position_reconciliation(snapshot: dict[str, Any], *, role: str, symbol: str, logger: logging.Logger) -> None:
    try:
        result = reconcile_positions()
        error_type = None
    except Exception as exc:
        result = {"status": "unavailable", "exchange": [], "database": [], "dashboard": [], "issues": []}
        error_type = type(exc).__name__
        logger.warning("[GENESIS_POSITIONS] reconciliation_failed=%s", error_type)
    payload = {"event": "GENESIS_POSITION_RECONCILIATION", "timestamp_utc": datetime.now(timezone.utc).isoformat(), "pid": snapshot.get("pid", os.getpid()), "role": role, "active_symbol": symbol, "environment": snapshot.get("environment"), "version": snapshot.get("version"), **result}
    if error_type:
        payload["error_type"] = error_type
    try:
        RECONCILIATION_DIR.mkdir(parents=True, exist_ok=True)
        target = RECONCILIATION_DIR / f"positions_{payload['pid']}.jsonl"
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception as exc:
        logger.warning("[GENESIS_POSITIONS] metrics_write_failed=%s", type(exc).__name__)


def start_position_reconciliation_monitor(snapshot: dict[str, Any], *, role: str, symbol: str, logger: logging.Logger) -> None:
    global _MONITOR_STARTED
    if str(os.getenv("GENESIS_POSITION_RECONCILIATION_ENABLED", "true")).strip().lower() not in {"1", "true", "yes", "on", "sim"}:
        return
    with _MONITOR_LOCK:
        if _MONITOR_STARTED:
            return
        _MONITOR_STARTED = True
    interval = max(5.0, float(os.getenv("GENESIS_POSITION_RECONCILIATION_INTERVAL_SECONDS", "300")))

    def monitor() -> None:
        while True:
            record_position_reconciliation(snapshot, role=role, symbol=symbol, logger=logger)
            time.sleep(interval)

    threading.Thread(target=monitor, name="genesis-position-reconciliation", daemon=True).start()
    logger.info("[GENESIS_POSITIONS] reconciliation_enabled interval_seconds=%s output=%s", interval, RECONCILIATION_DIR)
