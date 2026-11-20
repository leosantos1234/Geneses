"""Read-only analytical memory for Genesis.

This module derives quantitative evidence from canonical PostgreSQL rows.  It
never writes to the database and never participates in strategy, risk, or
execution decisions.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from statistics import median
from typing import Any, Iterable, Sequence


@dataclass(frozen=True)
class AnalyticalTrade:
    symbol: str | None = None
    strategy: str | None = None
    setup_type: str | None = None
    side: str | None = None
    regime: str | None = None
    entry_timestamp: Any = None
    exit_timestamp: Any = None
    entry_price: float | None = None
    exit_price: float | None = None
    stop_initial: float | None = None
    take_profit_initial: float | None = None
    position_size: float | None = None
    pnl: float | None = None
    pnl_pct: float | None = None
    risk_amount: float | None = None
    r_multiple: float | None = None
    mae: float | None = None
    mae_pct: float | None = None
    mfe: float | None = None
    mfe_pct: float | None = None
    atr: float | None = None
    atr_pct: float | None = None
    volume_ratio: float | None = None
    volatility_bucket: str | None = None
    breakout_score: float | None = None
    breakout_direction: str | None = None
    impulse_reset_seen: bool | None = None
    impulse_reset_duration: float | None = None
    impulse_reset_outcome: str | None = None
    decision_id: str | None = None


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def calculate_r_multiple(side: str | None, entry: Any, stop: Any, exit: Any) -> float | None:
    """Calculate R only from the original stop geometry."""
    entry_n, stop_n, exit_n = _number(entry), _number(stop), _number(exit)
    if entry_n is None or stop_n is None or exit_n is None:
        return None
    direction = str(side or "").lower()
    risk = entry_n - stop_n if direction in {"buy", "long"} else stop_n - entry_n if direction in {"sell", "short"} else None
    if risk is None or risk <= 0:
        return None
    move = exit_n - entry_n if direction in {"buy", "long"} else entry_n - exit_n
    return move / risk


def calculate_excursions(side: str | None, entry: Any, candles: Iterable[dict[str, Any]]) -> dict[str, float | None]:
    """Calculate excursions from candles supplied strictly within the trade."""
    entry_n = _number(entry)
    if entry_n is None:
        return {"mae": None, "mae_pct": None, "mfe": None, "mfe_pct": None}
    highs = [_number(row.get("high")) for row in candles]
    lows = [_number(row.get("low")) for row in candles]
    highs = [x for x in highs if x is not None]
    lows = [x for x in lows if x is not None]
    if not highs or not lows:
        return {"mae": None, "mae_pct": None, "mfe": None, "mfe_pct": None}
    direction = str(side or "").lower()
    if direction in {"buy", "long"}:
        mfe, mae = max(highs) - entry_n, entry_n - min(lows)
    elif direction in {"sell", "short"}:
        mfe, mae = entry_n - min(lows), max(highs) - entry_n
    else:
        return {"mae": None, "mae_pct": None, "mfe": None, "mfe_pct": None}
    return {"mae": mae, "mae_pct": mae / entry_n * 100, "mfe": mfe, "mfe_pct": mfe / entry_n * 100}


def volatility_bucket(atr_pct: Any, boundaries: Sequence[float] | None = None) -> str | None:
    """Assign empirical percentile boundaries; boundaries are not strategy thresholds."""
    value = _number(atr_pct)
    if value is None:
        return None
    cuts = tuple(boundaries or (0.25, 0.50, 0.75, 1.00))
    labels = ("VERY_LOW", "LOW", "NORMAL", "HIGH", "VERY_HIGH")
    for index, boundary in enumerate(cuts):
        if value <= boundary:
            return labels[index]
    return labels[-1]


def empirical_boundaries(values: Iterable[Any]) -> tuple[float, float, float, float] | None:
    numbers = sorted(x for x in (_number(v) for v in values) if x is not None)
    if not numbers:
        return None
    def percentile(p: float) -> float:
        pos = (len(numbers) - 1) * p
        low, high = math.floor(pos), math.ceil(pos)
        if low == high:
            return numbers[low]
        return numbers[low] + (numbers[high] - numbers[low]) * (pos - low)
    return tuple(percentile(p) for p in (0.20, 0.40, 0.60, 0.80))


def sample_quality(count: int) -> str:
    return "INSUFFICIENT" if count < 20 else "LOW" if count < 50 else "MEDIUM" if count < 100 else "HIGH"


def _profit_factor(records: list[AnalyticalTrade]) -> float | None:
    wins = sum(x.pnl for x in records if x.pnl is not None and x.pnl > 0)
    losses = -sum(x.pnl for x in records if x.pnl is not None and x.pnl < 0)
    return wins / losses if losses > 0 else None


def summarize(records: Iterable[AnalyticalTrade]) -> dict[str, Any]:
    rows = list(records)
    pnl = [x.pnl for x in rows if x.pnl is not None]
    rs = [x.r_multiple for x in rows if x.r_multiple is not None]
    mae = [x.mae_pct for x in rows if x.mae_pct is not None]
    mfe = [x.mfe_pct for x in rows if x.mfe_pct is not None]
    wins = sum(1 for x in rows if x.pnl is not None and x.pnl > 0)
    return {
        "trade_count": len(rows), "wins": wins, "losses": sum(1 for x in rows if x.pnl is not None and x.pnl <= 0),
        "win_rate": wins / len(pnl) if pnl else None, "avg_pnl": sum(pnl) / len(pnl) if pnl else None,
        "avg_r": sum(rs) / len(rs) if rs else None, "median_r": median(rs) if rs else None,
        "profit_factor": _profit_factor(rows), "avg_mae_pct": sum(mae) / len(mae) if mae else None,
        "avg_mfe_pct": sum(mfe) / len(mfe) if mfe else None, "sample_quality": sample_quality(len(rows)),
    }


def deterministic_setup_key(trade: AnalyticalTrade) -> str:
    """Stable, bounded setup identity from persisted structured fields."""
    strategy = (trade.strategy or "UNKNOWN").upper()
    side = (trade.side or "UNKNOWN").upper()
    regime = (trade.regime or "UNKNOWN").upper()
    score = "UNKNOWN" if trade.breakout_score is None else f"{int(trade.breakout_score)//2*2}_{int(trade.breakout_score)//2*2+1}"
    volatility = trade.volatility_bucket or "UNKNOWN"
    return f"{strategy}|{side}|R{regime}|SCORE_{score}|VOL_{volatility}"


def similarity_vector(trade: AnalyticalTrade) -> tuple[float, ...]:
    """Features only; outcomes (PnL/R/MAE/MFE) are intentionally excluded."""
    return tuple(x if x is not None else 0.0 for x in (
        trade.atr_pct, trade.volume_ratio, trade.breakout_score,
        1.0 if trade.impulse_reset_seen else 0.0,
    ))


def rank_similar_cases(candidate: AnalyticalTrade, historical: Iterable[AnalyticalTrade], limit: int = 5, as_of: datetime | None = None) -> list[dict[str, Any]]:
    target = similarity_vector(candidate)
    ranked = []
    for row in historical:
        if as_of is not None and row.entry_timestamp is not None:
            row_ts = row.entry_timestamp if getattr(row.entry_timestamp, "tzinfo", None) else row.entry_timestamp.replace(tzinfo=timezone.utc)
            cutoff = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
            if row_ts > cutoff:
                continue
        if candidate.symbol and row.symbol and candidate.symbol.upper() != row.symbol.upper():
            continue
        vector = similarity_vector(row)
        distance = math.sqrt(sum((a - b) ** 2 for a, b in zip(target, vector)))
        ranked.append((1.0 / (1.0 + distance), row))
    ranked.sort(key=lambda item: (-item[0], str(item[1].entry_timestamp)))
    return [{"similarity_score": score, "timestamp": row.entry_timestamp, "symbol": row.symbol,
             "side": row.side, "regime": row.regime, "setup": row.setup_type,
             "r_multiple": row.r_multiple, "mfe": row.mfe, "mae": row.mae} for score, row in ranked[:limit]]


def forward_returns(closed_candles: Sequence[dict[str, Any]], entry_price: Any, side: str | None) -> dict[str, float | None]:
    """Return closed-candle forward returns; callers must provide post-event candles only."""
    entry = _number(entry_price)
    if entry is None:
        return {f"forward_return_{n}": None for n in (1, 3, 6, 12)}
    result = {}
    for n in (1, 3, 6, 12):
        if len(closed_candles) < n or _number(closed_candles[n - 1].get("close")) is None:
            result[f"forward_return_{n}"] = None
            continue
        close = _number(closed_candles[n - 1]["close"])
        move = close - entry if str(side or "").lower() in {"buy", "long"} else entry - close
        result[f"forward_return_{n}"] = move / entry * 100
    return result


def read_canonical_candles(symbol: str, *, start_time: datetime | None = None,
                           end_time: datetime | None = None, connection: Any = None) -> list[dict[str, Any]]:
    """Read only prospective closed candles captured by Analytical Telemetry V2."""
    from genesis_runtime import connect_operational_database
    owned = connection is None
    connection = connection or connect_operational_database(read_only=True)
    try:
        clauses = ["upper(symbol) = %s", "record_provenance = 'PRODUCTION_RUNTIME'", "candle_closed = TRUE"]
        params: list[Any] = [str(symbol).upper()]
        if start_time is not None:
            clauses.append("candle_open_time >= %s"); params.append(start_time)
        if end_time is not None:
            clauses.append("candle_open_time < %s"); params.append(end_time)
        with connection.cursor() as cur:
            cur.execute(f"""SELECT symbol,timeframe,candle_open_time,candle_close_time,open,high,low,
                              close,volume,atr,atr_percent,volume_ratio,body_fraction,regime,
                              winning_rule,feature_vector
                           FROM analytical_candle_telemetry
                           WHERE {' AND '.join(clauses)}
                           ORDER BY candle_open_time""", tuple(params))
            columns = [d.name for d in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        if owned:
            connection.close()


def read_event_outcomes(symbol: str | None = None, *, as_of: datetime | None = None,
                        connection: Any = None) -> list[dict[str, Any]]:
    """Read only mature prospective outcomes; never returns future-unknown rows."""
    from genesis_runtime import connect_operational_database
    owned = connection is None
    connection = connection or connect_operational_database(read_only=True)
    try:
        clauses = ["available_at IS NOT NULL"]
        params: list[Any] = []
        if symbol:
            clauses.append("upper(symbol) = %s")
            params.append(str(symbol).upper())
        if as_of is not None:
            clauses.append("available_at <= %s")
            params.append(as_of)
        with connection.cursor() as cur:
            cur.execute(f"""SELECT id,event_type,event_id,symbol,timeframe,
                              event_candle_open_time,event_candle_close_time,direction,
                              regime,blocker,feature_vector,forward_return_1,forward_return_3,
                              forward_return_6,forward_return_12,mfe_pct_1,mae_pct_1,
                              mfe_pct_3,mae_pct_3,mfe_pct_6,mae_pct_6,mfe_pct_12,mae_pct_12,
                              outcome_class,available_at,created_at
                       FROM analytical_event_outcomes
                       WHERE {' AND '.join(clauses)}
                       ORDER BY event_candle_open_time""", tuple(params))
            columns = [description.name for description in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        if owned:
            connection.close()


def read_outcome_context(symbol: str | None = None, *, as_of: datetime | None = None,
                         connection: Any = None) -> list[dict[str, Any]]:
    """Expose descriptive RAG context types without granting Commander authority."""
    from utils.analytical_outcomes import rag_outcome_context
    return rag_outcome_context(read_event_outcomes(symbol, as_of=as_of, connection=connection))


def analytical_context(records: Iterable[AnalyticalTrade], *, as_of: datetime | None = None) -> dict[str, Any]:
    rows = list(records)
    if as_of is not None:
        cutoff = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
        rows = [r for r in rows if not r.entry_timestamp or (r.entry_timestamp if getattr(r.entry_timestamp, "tzinfo", None) else r.entry_timestamp.replace(tzinfo=timezone.utc)) <= cutoff]
    by_regime: dict[str, list[AnalyticalTrade]] = {}
    by_strategy: dict[str, list[AnalyticalTrade]] = {}
    by_setup: dict[str, list[AnalyticalTrade]] = {}
    for row in rows:
        by_regime.setdefault(row.regime or "UNKNOWN", []).append(row)
        by_strategy.setdefault(row.strategy or "UNKNOWN", []).append(row)
        by_setup.setdefault(row.setup_type or deterministic_setup_key(row), []).append(row)
    return {
        "regime_performance": {key: summarize(value) for key, value in by_regime.items()},
        "strategy_performance": {key: summarize(value) for key, value in by_strategy.items()},
        "setup_performance": {key: summarize(value) for key, value in by_setup.items()},
        "sample_size": len(rows), "as_of": as_of.isoformat() if as_of else None,
    }


def trade_from_row(row: dict[str, Any]) -> AnalyticalTrade:
    strategy = row.get("strategy") or row.get("estrategia_utilizada")
    side = row.get("side")
    return AnalyticalTrade(
        symbol=row.get("symbol"), strategy=strategy, side=side, regime=row.get("regime"),
        entry_timestamp=row.get("entry_timestamp") or row.get("open_ts"), exit_timestamp=row.get("exit_timestamp") or row.get("close_ts"),
        entry_price=_number(row.get("entry_price")), exit_price=_number(row.get("exit_price")),
        stop_initial=_number(row.get("stop_loss")), take_profit_initial=_number(row.get("take_profit")),
        position_size=_number(row.get("quantity")), pnl=_number(row.get("profit_loss")),
        atr=_number(row.get("atr_entrada")), volume_ratio=_number(row.get("volume_ratio")),
        r_multiple=calculate_r_multiple(side, row.get("entry_price"), row.get("stop_loss"), row.get("exit_price")),
        decision_id=str(row.get("decision_id")) if row.get("decision_id") else None,
    )


def read_symbol_report(symbol: str) -> dict[str, Any]:
    """Build a current report from PostgreSQL using read-only transactions only."""
    from genesis_runtime import database_url
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError("PostgreSQL driver unavailable") from exc
    if not database_url():
        raise RuntimeError("PostgreSQL unavailable")

    with psycopg.connect(database_url()) as conn:
        conn.execute("SET default_transaction_read_only = on")
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM trades WHERE upper(symbol) = %s AND exit_price IS NOT NULL", (symbol.upper(),))
            columns = [desc.name for desc in cur.description]
            trades = [trade_from_row(dict(zip(columns, row))) for row in cur.fetchall()]

            cur.execute("""SELECT regime, COUNT(*) FROM trades
                           WHERE upper(symbol) = %s AND exit_price IS NOT NULL
                           GROUP BY regime ORDER BY regime""", (symbol.upper(),))
            regime_counts = {str(regime or "UNKNOWN"): count for regime, count in cur.fetchall()}

            cur.execute("""SELECT primary_blocker, COUNT(*) FROM breakout_evaluations
                           WHERE upper(symbol) = %s AND record_provenance = 'PRODUCTION_RUNTIME'
                           GROUP BY primary_blocker""", (symbol.upper(),))
            blockers = {str(blocker or "NONE"): count for blocker, count in cur.fetchall()}

            cur.execute("""SELECT atr_percent FROM regime_evaluations
                           WHERE upper(symbol) = %s AND record_provenance = 'PRODUCTION_RUNTIME'
                           AND atr_percent IS NOT NULL""", (symbol.upper(),))
            atr_values = [row[0] for row in cur.fetchall()]

            cur.execute("""SELECT COUNT(*) FROM breakout_evaluations
                           WHERE upper(symbol) = %s AND record_provenance = 'PRODUCTION_RUNTIME'
                           AND primary_blocker = 'IMPULSE_RESET_WAIT'""", (symbol.upper(),))
            impulse_count = cur.fetchone()[0]

            try:
                cur.execute("""SELECT COUNT(*), MIN(candle_open_time), MAX(candle_open_time)
                               FROM analytical_candle_telemetry
                               WHERE upper(symbol) = %s AND record_provenance = 'PRODUCTION_RUNTIME'
                                 AND candle_closed = TRUE""", (symbol.upper(),))
                canonical_candle_count, first_candle, last_candle = cur.fetchone()
            except Exception:
                canonical_candle_count, first_candle, last_candle = 0, None, None

    setup_groups: dict[str, list[AnalyticalTrade]] = {}
    regime_groups: dict[str, list[AnalyticalTrade]] = {}
    for trade in trades:
        key = deterministic_setup_key(trade)
        setup_groups.setdefault(key, []).append(trade)
        regime_groups.setdefault(trade.regime or "UNKNOWN", []).append(trade)
    valid_r = sum(1 for trade in trades if trade.r_multiple is not None)
    boundaries = empirical_boundaries(atr_values)
    volatility_distribution: dict[str, int] = {}
    for value in atr_values:
        bucket = volatility_bucket(value, boundaries)
        volatility_distribution[bucket] = volatility_distribution.get(bucket, 0) + 1
    return {
        "symbol": symbol.upper(),
        "total_completed_trades": len(trades),
        "breakout_trades": sum(1 for trade in trades if "BREAKOUT" in str(trade.strategy or "").upper()),
        "performance_by_regime": {key: summarize(value) for key, value in regime_groups.items()},
        "breakout_performance": summarize([trade for trade in trades if "BREAKOUT" in str(trade.strategy or "").upper()]),
        "setup_count": len(setup_groups),
        "top_setups_by_sample": sorted(((key, len(value)) for key, value in setup_groups.items()), key=lambda item: (-item[1], item[0]))[:10],
        "top_setups_by_avg_r": sorted(((key, summarize(value).get("avg_r")) for key, value in setup_groups.items() if summarize(value).get("avg_r") is not None), key=lambda item: (-item[1], item[0]))[:10],
        "avg_r": summarize(trades).get("avg_r"),
        "median_r": summarize(trades).get("median_r"),
        "profit_factor": summarize(trades).get("profit_factor"),
        "valid_r_count": valid_r,
        "invalid_r_count": len(trades) - valid_r,
        "canonical_candle_count": canonical_candle_count,
        "canonical_candle_first": first_candle,
        "canonical_candle_last": last_candle,
        "mae_mfe_coverage": "PROSPECTIVE_CANDLES_AVAILABLE" if canonical_candle_count else "NOT_AVAILABLE",
        "impulse_reset_count": impulse_count,
        "impulse_reset_blockers": blockers,
        "volatility_boundaries_percentiles": boundaries,
        "volatility_bucket_distribution": volatility_distribution,
        "source_classification": {
            "performance_by_regime": "DERIVABLE", "performance_by_strategy": "DERIVABLE",
            "similar_breakout": "PARTIALLY_DERIVABLE", "impulse_reset": "PARTIALLY_DERIVABLE",
            "win_rate_setup": "DERIVABLE", "average_r": "DERIVABLE",
            "mae": "PROSPECTIVE_ONLY", "mfe": "PROSPECTIVE_ONLY", "volatility_context": "DERIVABLE",
        },
        "analytical_context_available": bool(trades or blockers or atr_values),
    }


__all__ = ["AnalyticalTrade", "analytical_context", "calculate_excursions", "calculate_r_multiple",
           "deterministic_setup_key", "empirical_boundaries", "forward_returns", "rank_similar_cases",
           "read_canonical_candles", "read_event_outcomes", "read_outcome_context", "sample_quality", "summarize", "trade_from_row",
           "volatility_bucket", "read_symbol_report"]
