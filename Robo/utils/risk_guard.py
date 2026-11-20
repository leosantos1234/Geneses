import json
import logging
import os
from genesis_runtime import connect_operational_database, database_backend, database_path
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    val = os.getenv(name)
    if val is None or str(val).strip() == "":
        return default
    try:
        return float(val)
    except Exception:
        logging.warning("[RISK_GUARD] Invalid %s=%r. Using default=%s", name, val, default)
        return default


def _env_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None or str(val).strip() == "":
        return default
    try:
        return int(float(val))
    except Exception:
        logging.warning("[RISK_GUARD] Invalid %s=%r. Using default=%s", name, val, default)
        return default


def _scoped_limit(base_name: str, scope: str, default_num: float = 0.0) -> float:
    if scope == "symbol":
        return _env_float(f"{base_name}_SYMBOL", default_num)
    return _env_float(base_name, default_num)


def _build_query_with_symbol(base_sql: str, symbol: Optional[str]) -> Tuple[str, Tuple[Any, ...]]:
    if symbol:
        sql = base_sql + " AND symbol = ?"
        return sql, (symbol,)
    return base_sql, tuple()


def _adapt_sql(sql: str) -> str:
    if database_backend() != "postgres":
        return sql
    return sql.replace("datetime(COALESCE(close_ts, timestamp))", "CAST(COALESCE(close_ts, timestamp) AS TIMESTAMP)").replace("datetime(?)", "CAST(%s AS TIMESTAMP)").replace("?", "%s")


def _query_pl_since(db_path: str, start_iso: str, symbol: Optional[str] = None) -> Dict[str, float]:
    sql = """
    SELECT
      COALESCE(SUM(CASE WHEN COALESCE(profit_loss, 0) < 0 THEN -profit_loss ELSE 0 END), 0) AS gross_loss,
      COALESCE(SUM(COALESCE(profit_loss, 0)), 0) AS net_pnl,
      COALESCE(COUNT(*), 0) AS n
    FROM trades
    WHERE COALESCE(status, 'aberto') = 'fechado'
      AND datetime(COALESCE(close_ts, timestamp)) >= datetime(?)
    """
    try:
        sql, args_tail = _build_query_with_symbol(sql, symbol)
        sql = _adapt_sql(sql)
        conn = connect_operational_database(read_only=True)
        cur = conn.cursor()
        cur.execute(sql, (start_iso, *args_tail))
        row = cur.fetchone() or (0.0, 0.0, 0)
        conn.close()
        return {
            "gross_loss": float(row[0] or 0.0),
            "net_pnl": float(row[1] or 0.0),
            "n": int(row[2] or 0),
        }
    except Exception as e:
        logging.warning("[RISK_GUARD] Failed to query db=%s: %s", db_path, e)
        if database_backend() == "postgres":
            raise RuntimeError("PostgreSQL indisponível no risk guard") from e
        return {"gross_loss": 0.0, "net_pnl": 0.0, "n": 0}


def _query_closed_trades(
    db_path: str,
    start_iso: str,
    symbol: Optional[str] = None,
    limit: int = 10000,
) -> List[Tuple[float, str]]:
    sql = """
    SELECT COALESCE(profit_loss, 0), COALESCE(close_ts, timestamp)
    FROM trades
    WHERE COALESCE(status, 'aberto') = 'fechado'
      AND datetime(COALESCE(close_ts, timestamp)) >= datetime(?)
    """
    try:
        sql, args_tail = _build_query_with_symbol(sql, symbol)
        sql += " ORDER BY datetime(COALESCE(close_ts, timestamp)) ASC LIMIT ?"
        sql = _adapt_sql(sql)
        conn = connect_operational_database(read_only=True)
        cur = conn.cursor()
        cur.execute(sql, (start_iso, *args_tail, limit))
        rows = cur.fetchall() or []
        conn.close()
        out: List[Tuple[float, str]] = []
        for r in rows:
            try:
                out.append((float(r[0] or 0.0), str(r[1] or "")))
            except Exception:
                continue
        return out
    except Exception as e:
        logging.warning("[RISK_GUARD] Failed to query closed trades: %s", e)
        if database_backend() == "postgres":
            raise RuntimeError("PostgreSQL indisponível no risk guard") from e
        return []


def _query_recent_closed(
    db_path: str,
    symbol: Optional[str] = None,
    limit: int = 50,
) -> List[Tuple[float, str]]:
    sql = """
    SELECT COALESCE(profit_loss, 0), COALESCE(close_ts, timestamp)
    FROM trades
    WHERE COALESCE(status, 'aberto') = 'fechado'
    """
    try:
        sql, args_tail = _build_query_with_symbol(sql, symbol)
        sql += " ORDER BY datetime(COALESCE(close_ts, timestamp)) DESC LIMIT ?"
        sql = _adapt_sql(sql)
        conn = connect_operational_database(read_only=True)
        cur = conn.cursor()
        cur.execute(sql, (*args_tail, limit))
        rows = cur.fetchall() or []
        conn.close()
        out: List[Tuple[float, str]] = []
        for r in rows:
            try:
                out.append((float(r[0] or 0.0), str(r[1] or "")))
            except Exception:
                continue
        return out
    except Exception as e:
        logging.warning("[RISK_GUARD] Failed to query recent trades: %s", e)
        if database_backend() == "postgres":
            raise RuntimeError("PostgreSQL indisponível no risk guard") from e
        return []


def _calc_max_drawdown_pct(reference_balance: float, trades_seq: List[Tuple[float, str]]) -> float:
    if reference_balance <= 0:
        return 0.0
    equity = reference_balance
    peak = reference_balance
    max_dd = 0.0
    for pnl, _ in trades_seq:
        equity += float(pnl)
        if equity > peak:
            peak = equity
        if peak > 0:
            dd = (peak - equity) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
    return max_dd


def _consecutive_losses(recent_desc: List[Tuple[float, str]]) -> Tuple[int, Optional[str]]:
    count = 0
    last_loss_ts: Optional[str] = None
    for pnl, ts in recent_desc:
        if pnl < 0:
            count += 1
            if last_loss_ts is None:
                last_loss_ts = ts
        else:
            break
    return count, last_loss_ts


def evaluate_risk_limits(db_path: str = str(database_path()), symbol: Optional[str] = None) -> Dict[str, Any]:
    if database_backend() == "postgres":
        # Fail closed even when the guard is disabled: no DB means no safe state.
        conn = connect_operational_database(read_only=True)
        conn.execute("SELECT 1").fetchone()
        conn.close()
    enabled = _env_bool("RISK_GUARD_ENABLE", True)
    if not enabled:
        return {"blocked": False, "reason": "disabled"}

    now = datetime.now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = day_start - timedelta(days=day_start.weekday())

    # Global and per-symbol are evaluated independently.
    day_stats_global = _query_pl_since(db_path, day_start.isoformat(), symbol=None)
    week_stats_global = _query_pl_since(db_path, week_start.isoformat(), symbol=None)
    day_stats_symbol = _query_pl_since(db_path, day_start.isoformat(), symbol=symbol) if symbol else {"gross_loss": 0.0, "net_pnl": 0.0, "n": 0}
    week_stats_symbol = _query_pl_since(db_path, week_start.isoformat(), symbol=symbol) if symbol else {"gross_loss": 0.0, "net_pnl": 0.0, "n": 0}

    ref_balance = _env_float("RISK_GUARD_REFERENCE_BALANCE", 0.0)
    rolling_window_hours = _env_int("ROLLING_WINDOW_HOURS", 24)
    rolling_start = now - timedelta(hours=max(1, rolling_window_hours))

    day_trades_global = _query_closed_trades(db_path, day_start.isoformat(), symbol=None, limit=50000)
    day_trades_symbol = _query_closed_trades(db_path, day_start.isoformat(), symbol=symbol, limit=50000) if symbol else []
    rolling_stats_global = _query_pl_since(db_path, rolling_start.isoformat(), symbol=None)
    rolling_stats_symbol = _query_pl_since(db_path, rolling_start.isoformat(), symbol=symbol) if symbol else {"gross_loss": 0.0, "net_pnl": 0.0, "n": 0}

    recent_global = _query_recent_closed(db_path, symbol=None, limit=50)
    recent_symbol = _query_recent_closed(db_path, symbol=symbol, limit=50) if symbol else []
    cons_losses_global, last_loss_ts_global = _consecutive_losses(recent_global)
    cons_losses_symbol, last_loss_ts_symbol = _consecutive_losses(recent_symbol)

    day_dd_global = _calc_max_drawdown_pct(ref_balance, day_trades_global)
    day_dd_symbol = _calc_max_drawdown_pct(ref_balance, day_trades_symbol) if symbol else 0.0

    reasons: List[str] = []

    def _check_scope(scope: str, stats_day: Dict[str, float], stats_week: Dict[str, float], stats_rolling: Dict[str, float], day_dd: float, cons_losses: int, last_loss_ts: Optional[str]) -> None:
        nonlocal reasons
        max_daily_loss_usdt = _scoped_limit("MAX_DAILY_LOSS_USDT", scope, 0.0)
        max_weekly_loss_usdt = _scoped_limit("MAX_WEEKLY_LOSS_USDT", scope, 0.0)
        max_daily_loss_pct = _scoped_limit("MAX_DAILY_LOSS_PCT", scope, 0.0)
        max_weekly_loss_pct = _scoped_limit("MAX_WEEKLY_LOSS_PCT", scope, 0.0)
        max_daily_dd_pct = _scoped_limit("MAX_DAILY_DRAWDOWN_PCT", scope, 0.0)
        max_rolling_loss_usdt = _scoped_limit("MAX_ROLLING_LOSS_USDT", scope, 0.0)
        max_trades_day = _env_int("MAX_TRADES_PER_DAY_SYMBOL", 0) if scope == "symbol" else _env_int("RISK_MAX_TRADES_PER_DAY", _env_int("MAX_TRADES_PER_DAY", 0))
        max_consecutive_losses = _env_int("MAX_CONSECUTIVE_LOSSES_SYMBOL", 0) if scope == "symbol" else _env_int("MAX_CONSECUTIVE_LOSSES", 0)
        cooldown_minutes = _env_int("COOLDOWN_AFTER_LOSS_MINUTES", 0)

        day_loss = float(stats_day["gross_loss"])
        week_loss = float(stats_week["gross_loss"])
        day_loss_pct = (day_loss / ref_balance * 100.0) if ref_balance > 0 else 0.0
        week_loss_pct = (week_loss / ref_balance * 100.0) if ref_balance > 0 else 0.0
        rolling_loss = float(stats_rolling["gross_loss"])
        day_trades_count = int(stats_day["n"])

        tag = "SYMBOL" if scope == "symbol" else "GLOBAL"
        if max_daily_loss_usdt > 0 and day_loss >= max_daily_loss_usdt:
            reasons.append(f"{tag}:daily_loss_usdt {day_loss:.2f}>={max_daily_loss_usdt:.2f}")
        if max_weekly_loss_usdt > 0 and week_loss >= max_weekly_loss_usdt:
            reasons.append(f"{tag}:weekly_loss_usdt {week_loss:.2f}>={max_weekly_loss_usdt:.2f}")
        if ref_balance > 0 and max_daily_loss_pct > 0 and day_loss_pct >= max_daily_loss_pct:
            reasons.append(f"{tag}:daily_loss_pct {day_loss_pct:.2f}%>={max_daily_loss_pct:.2f}%")
        if ref_balance > 0 and max_weekly_loss_pct > 0 and week_loss_pct >= max_weekly_loss_pct:
            reasons.append(f"{tag}:weekly_loss_pct {week_loss_pct:.2f}%>={max_weekly_loss_pct:.2f}%")
        if max_daily_dd_pct > 0 and day_dd >= max_daily_dd_pct:
            reasons.append(f"{tag}:daily_drawdown_pct {day_dd:.2f}%>={max_daily_dd_pct:.2f}%")
        if max_rolling_loss_usdt > 0 and rolling_loss >= max_rolling_loss_usdt:
            reasons.append(f"{tag}:rolling_loss_usdt {rolling_loss:.2f}>={max_rolling_loss_usdt:.2f} ({rolling_window_hours}h)")
        if max_trades_day > 0 and day_trades_count >= max_trades_day:
            reasons.append(f"{tag}:max_trades_day {day_trades_count}>={max_trades_day}")

        if max_consecutive_losses > 0 and cons_losses >= max_consecutive_losses:
            if cooldown_minutes > 0 and last_loss_ts:
                try:
                    ts_loss = datetime.fromisoformat(last_loss_ts.replace("Z", "+00:00").replace("+00:00", ""))
                    blocked_until = ts_loss + timedelta(minutes=cooldown_minutes)
                    if datetime.now() <= blocked_until:
                        reasons.append(
                            f"{tag}:consecutive_losses {cons_losses}>={max_consecutive_losses} cooldown_until={blocked_until.isoformat(timespec='seconds')}"
                        )
                except Exception:
                    reasons.append(f"{tag}:consecutive_losses {cons_losses}>={max_consecutive_losses}")
            else:
                reasons.append(f"{tag}:consecutive_losses {cons_losses}>={max_consecutive_losses}")

    # Always check global limits.
    _check_scope("global", day_stats_global, week_stats_global, rolling_stats_global, day_dd_global, cons_losses_global, last_loss_ts_global)
    # Check per-symbol limits only when symbol is provided.
    if symbol:
        _check_scope("symbol", day_stats_symbol, week_stats_symbol, rolling_stats_symbol, day_dd_symbol, cons_losses_symbol, last_loss_ts_symbol)

    blocked = len(reasons) > 0
    return {
        "blocked": blocked,
        "reason": "; ".join(reasons) if reasons else "ok",
        "symbol": symbol,
        "day_loss_usdt_global": day_stats_global["gross_loss"],
        "week_loss_usdt_global": week_stats_global["gross_loss"],
        "day_net_pnl_global": day_stats_global["net_pnl"],
        "week_net_pnl_global": week_stats_global["net_pnl"],
        "day_trades_global": day_stats_global["n"],
        "week_trades_global": week_stats_global["n"],
        "rolling_loss_usdt_global": rolling_stats_global["gross_loss"],
        "day_drawdown_pct_global": day_dd_global,
        "consecutive_losses_global": cons_losses_global,
        "day_loss_usdt_symbol": day_stats_symbol["gross_loss"] if symbol else 0.0,
        "week_loss_usdt_symbol": week_stats_symbol["gross_loss"] if symbol else 0.0,
        "day_net_pnl_symbol": day_stats_symbol["net_pnl"] if symbol else 0.0,
        "week_net_pnl_symbol": week_stats_symbol["net_pnl"] if symbol else 0.0,
        "day_trades_symbol": day_stats_symbol["n"] if symbol else 0,
        "week_trades_symbol": week_stats_symbol["n"] if symbol else 0,
        "rolling_loss_usdt_symbol": rolling_stats_symbol["gross_loss"] if symbol else 0.0,
        "day_drawdown_pct_symbol": day_dd_symbol if symbol else 0.0,
        "consecutive_losses_symbol": cons_losses_symbol if symbol else 0,
        "rolling_window_hours": rolling_window_hours,
        "reference_balance": ref_balance,
    }


def write_kill_switch_flag(state: Dict[str, Any], path: str = "risk_kill.flag") -> None:
    try:
        payload = dict(state or {})
        payload["ts"] = datetime.utcnow().isoformat()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.warning("[RISK_GUARD] Failed to write %s: %s", path, e)


def _append_critical_event(event: Dict[str, Any], path: str = "logs/critical_events.jsonl") -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:
        logging.warning("[RISK_GUARD] Failed to append critical event: %s", e)


def _read_transition_state(path: str = "logs/risk_transition_state.json") -> Dict[str, Any]:
    try:
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_transition_state(state: Dict[str, Any], path: str = "logs/risk_transition_state.json") -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.warning("[RISK_GUARD] Failed to persist transition state: %s", e)


def _send_risk_transition_telegram(message: str) -> None:
    try:
        from utils.utils_telegram import send_telegram_message  # type: ignore
    except Exception:
        try:
            from leotelegram import send_telegram_message  # type: ignore
        except Exception:
            return
    try:
        send_telegram_message(message)
    except Exception as e:
        logging.warning("[RISK_GUARD] Telegram transition notify failed: %s", e)


def process_risk_transition(
    risk_state: Dict[str, Any],
    symbol: Optional[str] = None,
    source: str = "unknown",
) -> Dict[str, Any]:
    """
    Detecta transição bloqueado<->desbloqueado por símbolo e:
    - grava evento estruturado em logs/critical_events.jsonl
    - opcionalmente envia alerta Telegram
    - persiste estado em logs/risk_transition_state.json
    """
    state = dict(risk_state or {})
    blocked = bool(state.get("blocked", False))
    sym = (symbol or state.get("symbol") or "GLOBAL").upper()
    key = f"risk_guard::{sym}"
    now_iso = datetime.utcnow().isoformat()

    transition_state = _read_transition_state()
    prev = transition_state.get(key, {})
    prev_blocked = bool(prev.get("blocked", False)) if isinstance(prev, dict) else False
    changed = prev_blocked != blocked

    transition_state[key] = {
        "blocked": blocked,
        "reason": str(state.get("reason", "")),
        "updated_at": now_iso,
        "source": source,
    }
    _write_transition_state(transition_state)

    if changed:
        evt_type = "risk_blocked" if blocked else "risk_unblocked"
        msg = (
            f"{evt_type.upper()} | symbol={sym} | source={source} | "
            f"reason={state.get('reason', 'ok')}"
        )
        event = {
            "ts": now_iso,
            "event_type": evt_type,
            "severity": "CRITICAL" if blocked else "INFO",
            "source": source,
            "symbol": sym,
            "message": msg,
            "risk_state": state,
        }
        _append_critical_event(event)
        if os.getenv("RISK_GUARD_TELEGRAM_ALERTS", "1").strip().lower() in {"1", "true", "yes", "on"}:
            _send_risk_transition_telegram(f"[RISK_GUARD] {msg}")

    return {"changed": changed, "blocked": blocked, "prev_blocked": prev_blocked, "key": key}


def register_critical_event(
    event_type: str,
    message: str,
    severity: str = "WARNING",
    source: str = "unknown",
    symbol: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    payload = {
        "ts": datetime.utcnow().isoformat(),
        "event_type": str(event_type or "event").strip().lower(),
        "severity": str(severity or "WARNING").upper(),
        "source": source,
        "symbol": (symbol or "GLOBAL").upper(),
        "message": str(message),
        "details": details or {},
    }
    _append_critical_event(payload)
