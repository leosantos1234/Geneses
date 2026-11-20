import csv
import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from utils.risk_guard import register_critical_event


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on", "y", "sim"}


def _env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None or str(v).strip() == "":
        return default
    try:
        return float(v)
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or str(v).strip() == "":
        return default
    try:
        return int(float(v))
    except Exception:
        return default


def is_paper_trading_enabled() -> bool:
    return _env_bool("PAPER_TRADING_ENABLED", False)


def _paper_paths() -> Dict[str, str]:
    base = os.path.join("reports", "paper")
    return {
        "base": base,
        "csv": os.path.join(base, "paper_trades.csv"),
        "jsonl": os.path.join(base, "paper_trades.jsonl"),
        "phase": os.path.join(base, "paper_phase_status.json"),
    }


def _ensure_dirs() -> Dict[str, str]:
    p = _paper_paths()
    os.makedirs(p["base"], exist_ok=True)
    return p


def _paper_return_pct(entry_price: float, side: str, closes: List[float]) -> float:
    if len(closes) < 2 or entry_price <= 0:
        return 0.0
    c0 = float(closes[-2])
    c1 = float(closes[-1])
    if c0 <= 0:
        return 0.0
    move_pct = ((c1 - c0) / c0) * 100.0
    return move_pct if side.lower() == "buy" else -move_pct


def _read_csv(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    out: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            out.append(dict(row))
    return out


def _write_csv(path: str, rows: List[Dict[str, Any]], fields: List[str]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _window_rows(rows: List[Dict[str, Any]], days: int) -> List[Dict[str, Any]]:
    if not rows:
        return []
    cutoff = datetime.utcnow() - timedelta(days=max(1, days))
    out = []
    for r in rows:
        ts = str(r.get("timestamp", ""))
        try:
            d = datetime.fromisoformat(ts.replace("Z", "+00:00").replace("+00:00", ""))
        except Exception:
            continue
        if d >= cutoff:
            out.append(r)
    return out


def _to_float(v: Any) -> float:
    try:
        return float(v)
    except Exception:
        return 0.0


def _calc_max_dd_pct(start_equity: float, pnls: List[float]) -> float:
    if start_equity <= 0:
        return 0.0
    eq = start_equity
    peak = start_equity
    max_dd = 0.0
    for p in pnls:
        eq += p
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = (peak - eq) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
    return max_dd


def update_phase_status() -> Dict[str, Any]:
    paths = _ensure_dirs()
    rows = _read_csv(paths["csv"])
    lookback_days = _env_int("PAPER_LOOKBACK_DAYS", 90)
    rows_w = _window_rows(rows, lookback_days)

    start_balance = _env_float("PAPER_START_BALANCE_USDT", 10000.0)
    min_days = _env_int("PAPER_MIN_DAYS", 60)
    min_trades = _env_int("PAPER_MIN_TRADES", 100)
    min_pf = _env_float("PAPER_MIN_PF", 1.15)
    max_dd_pct = _env_float("PAPER_MAX_DD_PCT", 12.0)
    max_tracking_error_pct = _env_float("PAPER_MAX_TRACKING_ERROR_PCT", 0.50)

    pnls = [_to_float(r.get("paper_pnl_usdt")) for r in rows_w]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
    max_dd = _calc_max_dd_pct(start_balance, pnls)
    total_pnl = sum(pnls)

    bts = [_to_float(r.get("backtest_return_pct")) for r in rows_w if str(r.get("backtest_return_pct", "")).strip() != ""]
    pps = [_to_float(r.get("paper_return_pct")) for r in rows_w if str(r.get("backtest_return_pct", "")).strip() != ""]
    n_te = min(len(bts), len(pps))
    te = (sum(abs(pps[i] - bts[i]) for i in range(n_te)) / n_te) if n_te > 0 else 0.0

    dates = set()
    for r in rows_w:
        ts = str(r.get("timestamp", ""))
        try:
            d = datetime.fromisoformat(ts.replace("Z", "+00:00").replace("+00:00", "")).date().isoformat()
            dates.add(d)
        except Exception:
            continue
    active_days = len(dates)
    trades = len(rows_w)
    win_rate = (wins / trades * 100.0) if trades else 0.0

    eligible = (
        active_days >= min_days
        and trades >= min_trades
        and pf >= min_pf
        and max_dd <= max_dd_pct
        and te <= max_tracking_error_pct
    )
    next_phase = "MICRO_READY" if eligible else "PAPER"

    status = {
        "ts": datetime.utcnow().isoformat(),
        "phase_current": "PAPER",
        "phase_next": next_phase,
        "eligible_for_micro": eligible,
        "lookback_days": lookback_days,
        "metrics": {
            "active_days": active_days,
            "trades": trades,
            "total_pnl_usdt": total_pnl,
            "win_rate_pct": win_rate,
            "profit_factor": pf,
            "max_drawdown_pct": max_dd,
            "avg_tracking_error_pct": te,
        },
        "targets": {
            "min_days": min_days,
            "min_trades": min_trades,
            "min_pf": min_pf,
            "max_dd_pct": max_dd_pct,
            "max_tracking_error_pct": max_tracking_error_pct,
        },
    }
    with open(paths["phase"], "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)

    if eligible:
        register_critical_event(
            event_type="paper_phase_promoted",
            severity="INFO",
            source="paper_mode.update_phase_status",
            symbol="GLOBAL",
            message="Paper atingiu criterios de promocao para MICRO_READY.",
            details=status["metrics"],
        )
    return status


def register_paper_trade(
    symbol: str,
    side: str,
    qty: float,
    entry_price: float,
    strategy: str,
    closes: List[float],
    backtest_return_pct: Optional[float] = None,
    source: str = "unknown",
) -> Dict[str, Any]:
    paths = _ensure_dirs()
    ts = datetime.utcnow().isoformat()
    order_id = f"paper-{symbol}-{int(datetime.utcnow().timestamp())}"

    paper_ret_pct = _paper_return_pct(entry_price=entry_price, side=side, closes=closes)
    notional = max(0.0, float(qty) * float(entry_price))
    paper_pnl = notional * (paper_ret_pct / 100.0)
    tracking_error = (
        abs(float(paper_ret_pct) - float(backtest_return_pct))
        if backtest_return_pct is not None
        else None
    )

    row = {
        "timestamp": ts,
        "symbol": symbol,
        "side": side.lower(),
        "strategy": strategy,
        "qty": f"{float(qty):.8f}",
        "entry_price": f"{float(entry_price):.8f}",
        "paper_return_pct": f"{paper_ret_pct:.6f}",
        "paper_pnl_usdt": f"{paper_pnl:.6f}",
        "backtest_return_pct": "" if backtest_return_pct is None else f"{float(backtest_return_pct):.6f}",
        "tracking_error_pct": "" if tracking_error is None else f"{float(tracking_error):.6f}",
        "source": source,
        "order_id": order_id,
    }
    fields = list(row.keys())

    rows = _read_csv(paths["csv"])
    rows.append(row)
    _write_csv(paths["csv"], rows, fields)
    with open(paths["jsonl"], "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    status = update_phase_status()
    return {
        "order_id": order_id,
        "paper_return_pct": paper_ret_pct,
        "paper_pnl_usdt": paper_pnl,
        "tracking_error_pct": tracking_error,
        "phase_status": status,
    }
