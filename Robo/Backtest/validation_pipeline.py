"""Validação sem vazamento do Genesis Quant.

O pipeline roda as estratégias candidatas em janelas walk-forward. Em cada
janela, a estratégia é escolhida somente com os candles de calibração; o
resultado publicado vem exclusivamente dos candles seguintes (teste/OOS).
Também agrega os registros do modo paper para comparação operacional.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

# Permite executar tanto `python -m Backtest.validation_pipeline` a partir de
# Robo quanto `python Robo/Backtest/validation_pipeline.py` a partir da raiz.
_ROBO_ROOT = Path(__file__).resolve().parents[1]
if str(_ROBO_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROBO_ROOT))

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - permite uso sem python-dotenv
    load_dotenv = None

STRATEGIES = (
    "FUNIL",
    "BREAKOUT",
    "MEAN_REVERSION",
    "EARLY_TREND",
    "MARKET_MAKING_LATERAL",
)

DEFAULT_MIN_CALIBRATION_OPERATIONS = 5


def _get_runner():
    """Importa o runner somente depois do .env estar carregado."""
    try:
        from Backtest.walk_forward_runner import _load_csv, run_walk_forward
    except ModuleNotFoundError:
        from walk_forward_runner import _load_csv, run_walk_forward
    return _load_csv, run_walk_forward


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _profit_factor(pnls: Iterable[float]) -> float:
    values = list(pnls)
    gross_profit = sum(p for p in values if p > 0)
    gross_loss = abs(sum(p for p in values if p < 0))
    if gross_loss > 0:
        return gross_profit / gross_loss
    return math.inf if gross_profit > 0 else 0.0


def _drawdown_pct(start: float, pnls: Iterable[float]) -> float:
    if start <= 0:
        return 0.0
    equity = start
    peak = start
    maximum = 0.0
    for pnl in pnls:
        equity += float(pnl)
        peak = max(peak, equity)
        if peak > 0:
            maximum = max(maximum, (peak - equity) / peak * 100.0)
    return maximum


def _row_pnls(row: Dict[str, Any], prefix: str) -> List[float]:
    """Extrai PnL por operação quando o backtest o fornece.

    Alguns adaptadores antigos retornam apenas PnL por janela. Nesse caso o
    chamador usa o PnL da janela como uma observação, preservando a métrica sem
    inventar operações.
    """
    log = row.get(f"{prefix}_trade_log")
    if isinstance(log, list):
        values: List[float] = []
        for trade in log:
            if not isinstance(trade, dict):
                continue
            for key in ("profit_loss", "pnl", "lucro", "lucro_total", "profit"):
                if key in trade:
                    values.append(_float(trade.get(key)))
                    break
        if values:
            return values
    return [_float(row.get(f"{prefix}_lucro_total"))]


def _aggregate(rows: List[Dict[str, Any]], prefix: str, capital: float) -> Dict[str, Any]:
    pnls: List[float] = []
    trades = 0
    for row in rows:
        pnls.extend(_row_pnls(row, prefix))
        trades += _int(row.get(f"{prefix}_num_trades"), 0)
    total_pnl = sum(pnls)
    return {
        "windows": len(rows),
        "operations": trades,
        "pnl_observations": len(pnls),
        "net_pnl_usdt": total_pnl,
        "net_return_pct": (total_pnl / capital * 100.0) if capital else 0.0,
        "profit_factor": _profit_factor(pnls),
        "max_drawdown_pct": _drawdown_pct(capital, pnls),
    }


def _calibration_score(
    row: Dict[str, Any],
    capital: float,
    min_operations: int = DEFAULT_MIN_CALIBRATION_OPERATIONS,
) -> Tuple[int, float, float, float, float]:
    pnl = _float(row.get("train_lucro_total"))
    pf = _profit_factor(_row_pnls(row, "train"))
    pf_rank = min(pf, 5.0) if math.isfinite(pf) else 5.0
    dd = _drawdown_pct(capital, _row_pnls(row, "train"))
    trades = _int(row.get("train_num_trades"), 0)
    # A estratégia só é promovida pelo treino; teste nunca participa do score.
    eligible = trades >= min_operations and pnl > 0.0
    return (1 if eligible else 0, pnl / capital if capital else pnl, pf_rank, -dd, float(trades))


def _calibration_candidate(
    row: Dict[str, Any], capital: float, min_operations: int
) -> Dict[str, Any]:
    pnl = _float(row.get("train_lucro_total"))
    trades = _int(row.get("train_num_trades"), 0)
    pf = _profit_factor(_row_pnls(row, "train"))
    dd = _drawdown_pct(capital, _row_pnls(row, "train"))
    reasons: List[str] = []
    if trades < min_operations:
        reasons.append(f"operações insuficientes ({trades} < {min_operations})")
    if pnl <= 0:
        reasons.append("PnL de calibração não positivo")
    return {
        "strategy": row.get("train_estrategia", "NONE"),
        "operations": trades,
        "pnl_usdt": pnl,
        "return_pct": _float(row.get("train_retorno_pct")),
        "profit_factor": pf,
        "max_drawdown_pct": dd,
        "eligible": not reasons,
        "reason": "elegível" if not reasons else "; ".join(reasons),
        "score": list(_calibration_score(row, capital, min_operations)),
    }


def _read_paper_csv(path: str) -> List[Dict[str, Any]]:
    if not path or not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _paper_summary(rows: List[Dict[str, Any]], capital: float) -> Dict[str, Any]:
    pnls = [_float(row.get("paper_pnl_usdt")) for row in rows]
    returns = [_float(row.get("paper_return_pct")) for row in rows]
    tracking = [
        abs(_float(row.get("tracking_error_pct")))
        for row in rows
        if str(row.get("tracking_error_pct", "")).strip() != ""
    ]
    return {
        "available": bool(rows),
        "operations": len(rows),
        "net_pnl_usdt": sum(pnls),
        "net_return_pct_on_reference_capital": (sum(pnls) / capital * 100.0) if capital else 0.0,
        "profit_factor": _profit_factor(pnls),
        "max_drawdown_pct": _drawdown_pct(capital, pnls),
        "win_rate_pct": (sum(p > 0 for p in pnls) / len(pnls) * 100.0) if pnls else 0.0,
        "avg_tracking_error_pct": (sum(tracking) / len(tracking)) if tracking else None,
    }


def run_validation(
    df: pd.DataFrame,
    capital: float = 10000.0,
    train_candles: int = 500,
    test_candles: int = 200,
    step_candles: int = 100,
    sentimento: str = "neutro",
    approved_side: Optional[str] = None,
    regime_override: Optional[int] = None,
    paper_rows: Optional[List[Dict[str, Any]]] = None,
    min_calibration_operations: int = DEFAULT_MIN_CALIBRATION_OPERATIONS,
) -> Dict[str, Any]:
    _, run_walk_forward = _get_runner()
    runs: Dict[str, List[Dict[str, Any]]] = {}
    for strategy in STRATEGIES:
        runs[strategy] = run_walk_forward(
            df=df,
            strategy=strategy,
            train_candles=train_candles,
            test_candles=test_candles,
            step_candles=step_candles,
            capital_inicial=capital,
            sentimento=sentimento,
            approved_side=approved_side,
            regime_override=regime_override,
        )

    windows = max((len(rows) for rows in runs.values()), default=0)
    selected: List[Dict[str, Any]] = []
    for index in range(windows):
        candidates = [
            rows[index]
            for rows in runs.values()
            if index < len(rows)
        ]
        if not candidates:
            continue
        candidate_report = [
            _calibration_candidate(row, capital, min_calibration_operations)
            for row in candidates
        ]
        eligible = [
            row for row in candidates
            if _calibration_score(row, capital, min_calibration_operations)[0] == 1
        ]
        winner = (
            max(eligible, key=lambda row: _calibration_score(row, capital, min_calibration_operations))
            if eligible else None
        )
        reference = winner or candidates[0]
        selected_strategy = winner.get("train_estrategia", "NONE") if winner else "NONE"
        selected.append(
            {
                "window_id": _int(reference.get("window_id"), index + 1),
                "calibration_start": reference.get("train_start"),
                "calibration_end": reference.get("train_end"),
                "test_start": reference.get("test_start"),
                "test_end": reference.get("test_end"),
                "selected_strategy": selected_strategy,
                "calibration_pnl_usdt": _float(winner.get("train_lucro_total")) if winner else 0.0,
                "calibration_return_pct": _float(winner.get("train_retorno_pct")) if winner else 0.0,
                "calibration_operations": _int(winner.get("train_num_trades")) if winner else 0,
                "test_pnl_usdt": _float(winner.get("test_lucro_total")) if winner else 0.0,
                "test_return_pct": _float(winner.get("test_retorno_pct")) if winner else 0.0,
                "test_operations": _int(winner.get("test_num_trades")) if winner else 0,
                "test_winrate_pct": _float(winner.get("test_winrate")) if winner else 0.0,
                "selection_score": list(_calibration_score(winner, capital, min_calibration_operations)) if winner else [0, 0.0, 0.0, 0.0, 0.0],
                "calibration_candidates": candidate_report,
                "selection_reason": "melhor candidata elegível" if winner else "nenhuma candidata atingiu o mínimo de operações e PnL positivo",
            }
        )

    test_pnls = [row["test_pnl_usdt"] for row in selected]
    selected_metrics = {
        "windows": len(selected),
        "operations": sum(row["test_operations"] for row in selected),
        "net_pnl_usdt": sum(test_pnls),
        "net_return_pct": (sum(test_pnls) / capital * 100.0) if capital else 0.0,
        "profit_factor": _profit_factor(test_pnls),
        "max_drawdown_pct": _drawdown_pct(capital, test_pnls),
    }
    strategy_counts: Dict[str, int] = {}
    for row in selected:
        name = str(row["selected_strategy"])
        strategy_counts[name] = strategy_counts.get(name, 0) + 1

    paper = _paper_summary(paper_rows or [], capital)
    return {
        "generated_at": datetime.utcnow().isoformat(),
        "method": "walk_forward_calibration_then_out_of_sample_test",
        "config": {
            "capital": capital,
            "train_candles": train_candles,
            "test_candles": test_candles,
            "step_candles": step_candles,
            "strategies": list(STRATEGIES),
            "calibration_is_used_for_selection": True,
            "test_is_never_used_for_selection": True,
            "min_calibration_operations": min_calibration_operations,
            "requires_positive_calibration_pnl": True,
            "profit_factor_cap": 5.0,
        },
        "calibration": {
            "windows": windows,
            "strategies_evaluated": len(STRATEGIES),
            "selected_strategy_by_window": strategy_counts,
        },
        "out_of_sample_test": selected_metrics,
        "paper_trading": paper,
        "comparison": {
            "net_pnl_difference_paper_minus_test": paper["net_pnl_usdt"] - selected_metrics["net_pnl_usdt"],
            "profit_factor_difference_paper_minus_test": (
                paper["profit_factor"] - selected_metrics["profit_factor"]
                if math.isfinite(paper["profit_factor"]) and math.isfinite(selected_metrics["profit_factor"])
                else None
            ),
            "max_drawdown_difference_paper_minus_test": paper["max_drawdown_pct"] - selected_metrics["max_drawdown_pct"],
            "tracking_error_available": paper["avg_tracking_error_pct"] is not None,
        },
        "windows": selected,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validação Genesis: calibração, walk-forward, OOS e paper.")
    parser.add_argument("--csv", required=True, help="CSV de candles com time,open,high,low,close,volume.")
    parser.add_argument("--env-file", default="", help="Arquivo .env opcional para carregar antes do backtest.")
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--train-candles", type=int, default=500)
    parser.add_argument("--test-candles", type=int, default=200)
    parser.add_argument("--step-candles", type=int, default=100)
    parser.add_argument("--sentimento", default="neutro")
    parser.add_argument("--approved-side", default="auto")
    parser.add_argument("--regime-override", type=int, default=0)
    parser.add_argument("--min-calibration-operations", type=int, default=DEFAULT_MIN_CALIBRATION_OPERATIONS)
    parser.add_argument("--paper-csv", default="reports/paper/paper_trades.csv")
    parser.add_argument("--out-dir", default="reports/validation")
    parser.add_argument("--force-data-validation", action="store_true", help="Força nova validação mesmo se o arquivo não mudou.")
    args = parser.parse_args()

    if args.env_file and load_dotenv is not None:
        load_dotenv(args.env_file, override=False)
    if args.force_data_validation:
        os.environ["DATA_QUALITY_FORCE"] = "true"

    load_csv, _ = _get_runner()
    df = load_csv(args.csv)
    side = args.approved_side.strip().lower()
    side = side if side in {"buy", "sell"} else None
    regime = args.regime_override if args.regime_override in {1, 2, 3, 4} else None
    payload = run_validation(
        df=df,
        capital=args.capital,
        train_candles=args.train_candles,
        test_candles=args.test_candles,
        step_candles=args.step_candles,
        sentimento=args.sentimento,
        approved_side=side,
        regime_override=regime,
        paper_rows=_read_paper_csv(args.paper_csv),
        min_calibration_operations=max(1, args.min_calibration_operations),
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"validation_{stamp}"
    json_path = out_dir / f"{stem}.json"
    csv_path = out_dir / f"{stem}_windows.csv"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    pd.DataFrame(payload["windows"]).to_csv(csv_path, index=False, encoding="utf-8")
    print(json.dumps({"json": str(json_path), "windows_csv": str(csv_path), "oos": payload["out_of_sample_test"], "paper": payload["paper_trading"]}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
