import argparse
import json
import math
import os
import random
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
try:
    from Backtest.walk_forward_daily_report import generate_daily_report
except ModuleNotFoundError:
    from walk_forward_daily_report import generate_daily_report

from utils.indicadores import calculate_indicators
from estrategia_entrada.backtesting_geral import (
    executar_backtest_completo,
    executar_backtest_breakout,
    _backtest_early_trend,
    _backtest_market_making_lateral,
    _backtest_mean_reversion_stop_limit,
    _aplicar_custos_realistas_backtest,
)


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def _safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return default


def _load_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"CSV not found: {path}")
    try:
        from Backtest.data_quality_gate import ensure_validated_csv
    except ModuleNotFoundError:
        from data_quality_gate import ensure_validated_csv
    quality = ensure_validated_csv(path)
    print(
        f"[DATA_QUALITY] {quality['status']} | cached={quality.get('cached', False)} | "
        f"rows={quality.get('rows')} | interval={quality.get('interval_minutes', 0):.2f}m"
    )
    df = pd.read_csv(path)
    expected = {"time", "open", "high", "low", "close", "volume"}
    missing = expected.difference(df.columns)
    if missing:
        raise ValueError(f"CSV missing columns: {sorted(missing)}")
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if "turnover" not in df.columns:
        df["turnover"] = 0.0
    else:
        df["turnover"] = pd.to_numeric(df["turnover"], errors="coerce").fillna(0.0)
    df = df.dropna(subset=["time", "open", "high", "low", "close", "volume"]).copy()
    df = df.sort_values("time").reset_index(drop=True)
    return df


def _extract_metrics(res: Optional[Dict[str, Any]], capital_inicial: float) -> Dict[str, Any]:
    if not isinstance(res, dict):
        return {
            "estrategia": "NONE",
            "direcao": "AUTO",
            "saldo_inicial": capital_inicial,
            "saldo_final": capital_inicial,
            "lucro_total": 0.0,
            "retorno_pct": 0.0,
            "num_trades": 0,
            "winrate": 0.0,
            "score": 0.0,
            "score_pct": 0.0,
            "status_funil": "sem_evidencia",
            "trade_log": [],
        }

    saldo_inicial = _safe_float(res.get("saldo_inicial", capital_inicial), capital_inicial)
    saldo_final = _safe_float(res.get("saldo_final", saldo_inicial), saldo_inicial)
    lucro_total = _safe_float(res.get("lucro_total", res.get("lucro", saldo_final - saldo_inicial)), 0.0)
    retorno_pct = res.get("retorno_pct")
    if retorno_pct is None and saldo_inicial:
        retorno_pct = (lucro_total / saldo_inicial) * 100.0
    retorno_pct = _safe_float(retorno_pct, 0.0)

    trade_log = res.get("trade_log", [])
    if not isinstance(trade_log, list):
        trade_log = []
    num_trades = _safe_int(res.get("num_trades", len(trade_log)), len(trade_log))
    winrate = _safe_float(
        res.get("winrate", res.get("win_rate", res.get("win_rate (%)", 0.0))),
        0.0,
    )
    return {
        "estrategia": str(res.get("estrategia", "NONE")),
        "direcao": str(res.get("direcao", "AUTO")),
        "saldo_inicial": saldo_inicial,
        "saldo_final": saldo_final,
        "lucro_total": lucro_total,
        "retorno_pct": retorno_pct,
        "num_trades": num_trades,
        "winrate": winrate,
        "score": _safe_float(res.get("score", res.get("score_calculado", 0.0)), 0.0),
        "score_pct": _safe_float(res.get("score_pct", 0.0), 0.0),
        "status_funil": str(res.get("status_funil", "na")),
        "trade_log": trade_log,
    }


def _with_realistic_costs(res: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Aplica o mesmo modelo de custos do funil aos modos individuais."""
    return _aplicar_custos_realistas_backtest(res) if isinstance(res, dict) else res


def _run_single_backtest(
    df_raw: pd.DataFrame,
    strategy: str,
    capital_inicial: float,
    sentimento: str,
    approved_side: Optional[str],
    regime_override: Optional[int],
) -> Dict[str, Any]:
    df = calculate_indicators(df_raw.copy())
    if df is None or df.empty:
        return _extract_metrics(None, capital_inicial)
    df = df.bfill().ffill().dropna().copy()
    if len(df) < 60:
        return _extract_metrics(None, capital_inicial)

    strat = (strategy or "FUNIL").upper()
    side = (approved_side or "").lower().strip() or None
    if side not in ("buy", "sell"):
        side = None

    if strat == "FUNIL":
        melhor, _, _ = executar_backtest_completo(
            df=df,
            sentimento=sentimento,
            capital_inicial=capital_inicial,
            regime_override=regime_override,
            approved_side=side,
        )
        return _extract_metrics(melhor, capital_inicial)

    if strat == "BREAKOUT":
        res = executar_backtest_breakout(
            df.copy(),
            lado_permitido=side,
            regime_override=regime_override,
            symbol=os.getenv("ATIVOS", "").strip().upper() or None,
            capital_inicial=capital_inicial,
        )
        res = _with_realistic_costs(res)
        if isinstance(res, dict) and "estrategia" not in res:
            res["estrategia"] = "BREAKOUT"
        return _extract_metrics(res, capital_inicial)

    if strat == "MEAN_REVERSION":
        res = _backtest_mean_reversion_stop_limit(
            df.copy(),
            direcao=side or "buy",
            capital_inicial=capital_inicial,
        )
        res = _with_realistic_costs(res)
        if isinstance(res, dict) and "estrategia" not in res:
            res["estrategia"] = "MEAN_REVERSION"
        return _extract_metrics(res, capital_inicial)

    if strat == "EARLY_TREND":
        res = _backtest_early_trend(
            df.copy(),
            direcao=side or "buy",
            capital_inicial=capital_inicial,
            regime_override=regime_override,
            symbol=os.getenv("ATIVOS", "").strip().upper() or None,
        )
        res = _with_realistic_costs(res)
        if isinstance(res, dict) and "estrategia" not in res:
            res["estrategia"] = "EARLY_TREND"
        return _extract_metrics(res, capital_inicial)

    if strat == "MARKET_MAKING_LATERAL":
        res = _backtest_market_making_lateral(df.copy(), capital_inicial=capital_inicial)
        res = _with_realistic_costs(res)
        if isinstance(res, dict) and "estrategia" not in res:
            res["estrategia"] = "MARKET_MAKING_LATERAL"
        return _extract_metrics(res, capital_inicial)

    raise ValueError(f"Unknown strategy: {strategy}")


def _window_indices(n: int, train: int, test: int, step: int) -> List[Tuple[int, int, int, int]]:
    out = []
    i = 0
    while True:
        tr0 = i
        tr1 = tr0 + train
        te0 = tr1
        te1 = te0 + test
        if te1 > n:
            break
        out.append((tr0, tr1, te0, te1))
        i += step
    return out


def _calc_equity_and_dd(capital_inicial: float, pnl_list: List[float]) -> Tuple[List[float], float]:
    equity = [capital_inicial]
    peak = capital_inicial
    max_dd = 0.0
    cur = capital_inicial
    for pnl in pnl_list:
        cur += pnl
        equity.append(cur)
        if cur > peak:
            peak = cur
        if peak > 0:
            dd = (peak - cur) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
    return equity, max_dd


def _gate_summary(
    rows: List[Dict[str, Any]],
    capital_inicial: float,
    min_total_trades: int,
    min_pf: float,
    min_pos_windows_pct: float,
    max_dd_pct: float,
) -> Dict[str, Any]:
    total_trades = sum(_safe_int(r.get("test_num_trades", 0), 0) for r in rows)
    total_pnl = sum(_safe_float(r.get("test_lucro_total", 0.0), 0.0) for r in rows)
    wins = sum(1 for r in rows if _safe_float(r.get("test_lucro_total", 0.0), 0.0) > 0)
    n = len(rows)
    pos_windows_pct = (wins / n * 100.0) if n else 0.0

    gross_profit = sum(max(_safe_float(r.get("test_lucro_total", 0.0), 0.0), 0.0) for r in rows)
    gross_loss = sum(abs(min(_safe_float(r.get("test_lucro_total", 0.0), 0.0), 0.0)) for r in rows)
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (math.inf if gross_profit > 0 else 0.0)

    pnl_list = [_safe_float(r.get("test_lucro_total", 0.0), 0.0) for r in rows]
    _, max_dd = _calc_equity_and_dd(capital_inicial, pnl_list)

    by_strategy: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        s = str(r.get("test_estrategia", "NONE")).upper()
        by_strategy.setdefault(s, {"windows": 0, "trades": 0, "pnl": 0.0})
        by_strategy[s]["windows"] += 1
        by_strategy[s]["trades"] += _safe_int(r.get("test_num_trades", 0), 0)
        by_strategy[s]["pnl"] += _safe_float(r.get("test_lucro_total", 0.0), 0.0)

    checks = {
        "total_trades": total_trades >= min_total_trades,
        "profit_factor": profit_factor >= min_pf,
        "positive_windows_pct": pos_windows_pct >= min_pos_windows_pct,
        "max_drawdown_pct": max_dd <= max_dd_pct,
    }
    return {
        "approved": all(checks.values()),
        "checks": checks,
        "metrics": {
            "windows": n,
            "total_trades": total_trades,
            "total_pnl": total_pnl,
            "profit_factor": profit_factor,
            "positive_windows_pct": pos_windows_pct,
            "max_drawdown_pct": max_dd,
        },
        "by_strategy": by_strategy,
    }


def _build_mc_stats(
    pnl_list: List[float],
    capital_inicial: float,
    iterations: int,
    seed: int,
) -> Dict[str, Any]:
    if not pnl_list or iterations <= 0:
        return {
            "enabled": False,
            "iterations": 0,
            "mean_final_equity": capital_inicial,
            "median_final_equity": capital_inicial,
            "p05_final_equity": capital_inicial,
            "p95_final_equity": capital_inicial,
            "mean_max_dd_pct": 0.0,
            "p95_max_dd_pct": 0.0,
            "prob_loss_pct": 0.0,
            "seed": seed,
        }

    rng = random.Random(seed)
    finals: List[float] = []
    dds: List[float] = []
    n = len(pnl_list)
    for _ in range(iterations):
        sample = [pnl_list[rng.randrange(n)] for _ in range(n)]
        equity, max_dd = _calc_equity_and_dd(capital_inicial, sample)
        finals.append(equity[-1])
        dds.append(max_dd)

    s_final = pd.Series(finals, dtype="float64")
    s_dd = pd.Series(dds, dtype="float64")
    return {
        "enabled": True,
        "iterations": iterations,
        "mean_final_equity": _safe_float(s_final.mean(), capital_inicial),
        "median_final_equity": _safe_float(s_final.median(), capital_inicial),
        "p05_final_equity": _safe_float(s_final.quantile(0.05), capital_inicial),
        "p95_final_equity": _safe_float(s_final.quantile(0.95), capital_inicial),
        "mean_max_dd_pct": _safe_float(s_dd.mean(), 0.0),
        "p95_max_dd_pct": _safe_float(s_dd.quantile(0.95), 0.0),
        "prob_loss_pct": _safe_float((s_final < capital_inicial).mean() * 100.0, 0.0),
        "seed": seed,
    }


def _sensitivity_configs(base_train: int, base_test: int, base_step: int) -> List[Tuple[int, int, int]]:
    cfgs = {
        (base_train, base_test, base_step),
        (max(100, int(base_train * 0.8)), max(50, int(base_test * 0.8)), max(25, int(base_step * 0.8))),
        (int(base_train * 1.2), int(base_test * 1.2), int(base_step * 1.2)),
        (base_train, max(50, int(base_test * 0.7)), max(25, int(base_step * 0.7))),
        (base_train, int(base_test * 1.3), int(base_step * 1.3)),
    }
    out = []
    for tr, te, st in sorted(cfgs):
        if tr > 0 and te > 0 and st > 0:
            out.append((tr, te, st))
    return out


def _run_sensitivity(
    df: pd.DataFrame,
    strategy: str,
    base_train: int,
    base_test: int,
    base_step: int,
    capital_inicial: float,
    sentimento: str,
    approved_side: Optional[str],
    regime_override: Optional[int],
    gate_min_trades: int,
    gate_min_pf: float,
    gate_min_pos_windows_pct: float,
    gate_max_dd_pct: float,
) -> Dict[str, Any]:
    runs: List[Dict[str, Any]] = []
    for tr, te, st in _sensitivity_configs(base_train, base_test, base_step):
        rows = run_walk_forward(
            df=df,
            strategy=strategy,
            train_candles=tr,
            test_candles=te,
            step_candles=st,
            capital_inicial=capital_inicial,
            sentimento=sentimento,
            approved_side=approved_side,
            regime_override=regime_override,
        )
        if not rows:
            continue
        gate = _gate_summary(
            rows=rows,
            capital_inicial=capital_inicial,
            min_total_trades=gate_min_trades,
            min_pf=gate_min_pf,
            min_pos_windows_pct=gate_min_pos_windows_pct,
            max_dd_pct=gate_max_dd_pct,
        )
        runs.append(
            {
                "train_candles": tr,
                "test_candles": te,
                "step_candles": st,
                "windows": _safe_int(gate["metrics"].get("windows", 0)),
                "approved": bool(gate.get("approved", False)),
                "total_pnl": _safe_float(gate["metrics"].get("total_pnl", 0.0)),
                "total_trades": _safe_int(gate["metrics"].get("total_trades", 0)),
                "profit_factor": _safe_float(gate["metrics"].get("profit_factor", 0.0)),
                "positive_windows_pct": _safe_float(gate["metrics"].get("positive_windows_pct", 0.0)),
                "max_drawdown_pct": _safe_float(gate["metrics"].get("max_drawdown_pct", 0.0)),
            }
        )

    if not runs:
        return {"enabled": False, "runs": [], "summary": {}}

    approved_count = sum(1 for r in runs if r["approved"])
    return {
        "enabled": True,
        "runs": runs,
        "summary": {
            "runs_total": len(runs),
            "runs_approved": approved_count,
            "approval_rate_pct": (approved_count / len(runs)) * 100.0,
            "avg_profit_factor": float(pd.Series([r["profit_factor"] for r in runs], dtype="float64").mean()),
            "avg_total_pnl": float(pd.Series([r["total_pnl"] for r in runs], dtype="float64").mean()),
        },
    }


def _run_cost_stress(
    rows: List[Dict[str, Any]],
    capital_inicial: float,
    base_cost_pct_per_trade: float,
    multipliers: List[float],
    min_total_trades: int,
    min_pf: float,
    min_pos_windows_pct: float,
    max_dd_pct: float,
) -> Dict[str, Any]:
    if not rows:
        return {"enabled": False, "scenarios": [], "summary": {}}

    scenarios: List[Dict[str, Any]] = []
    for mult in multipliers:
        stressed_rows: List[Dict[str, Any]] = []
        for r in rows:
            rr = dict(r)
            ntr = _safe_int(rr.get("test_num_trades", 0), 0)
            extra_cost = ntr * capital_inicial * (base_cost_pct_per_trade / 100.0) * mult
            rr["test_lucro_total"] = _safe_float(rr.get("test_lucro_total", 0.0), 0.0) - extra_cost
            stressed_rows.append(rr)

        gate = _gate_summary(
            rows=stressed_rows,
            capital_inicial=capital_inicial,
            min_total_trades=min_total_trades,
            min_pf=min_pf,
            min_pos_windows_pct=min_pos_windows_pct,
            max_dd_pct=max_dd_pct,
        )
        scenarios.append(
            {
                "multiplier": mult,
                "approved": bool(gate.get("approved", False)),
                "total_pnl": _safe_float(gate["metrics"].get("total_pnl", 0.0), 0.0),
                "total_trades": _safe_int(gate["metrics"].get("total_trades", 0), 0),
                "profit_factor": _safe_float(gate["metrics"].get("profit_factor", 0.0), 0.0),
                "positive_windows_pct": _safe_float(gate["metrics"].get("positive_windows_pct", 0.0), 0.0),
                "max_drawdown_pct": _safe_float(gate["metrics"].get("max_drawdown_pct", 0.0), 0.0),
            }
        )

    base = next((s for s in scenarios if abs(s["multiplier"] - 1.0) < 1e-9), scenarios[0])
    worst = min(scenarios, key=lambda s: s["total_pnl"])
    two_x = next((s for s in scenarios if abs(s["multiplier"] - 2.0) < 1e-9), None)
    return {
        "enabled": True,
        "base_cost_pct_per_trade": base_cost_pct_per_trade,
        "scenarios": scenarios,
        "summary": {
            "base_total_pnl": base["total_pnl"],
            "worst_total_pnl": worst["total_pnl"],
            "pnl_degradation_pct": (
                ((base["total_pnl"] - worst["total_pnl"]) / abs(base["total_pnl"])) * 100.0
                if abs(base["total_pnl"]) > 1e-9
                else 0.0
            ),
            "approved_under_2x_cost": bool(two_x["approved"]) if two_x else False,
        },
    }


def run_walk_forward(
    df: pd.DataFrame,
    strategy: str,
    train_candles: int,
    test_candles: int,
    step_candles: int,
    capital_inicial: float,
    sentimento: str,
    approved_side: Optional[str],
    regime_override: Optional[int],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    windows = _window_indices(len(df), train_candles, test_candles, step_candles)
    for i, (tr0, tr1, te0, te1) in enumerate(windows, start=1):
        dtr = df.iloc[tr0:tr1].copy()
        dte = df.iloc[te0:te1].copy()

        tr_res = _run_single_backtest(
            df_raw=dtr,
            strategy=strategy,
            capital_inicial=capital_inicial,
            sentimento=sentimento,
            approved_side=approved_side,
            regime_override=regime_override,
        )
        te_res = _run_single_backtest(
            df_raw=dte,
            strategy=strategy,
            capital_inicial=capital_inicial,
            sentimento=sentimento,
            approved_side=approved_side,
            regime_override=regime_override,
        )

        rows.append(
            {
                "window_id": i,
                "train_start": str(dtr["time"].iloc[0]),
                "train_end": str(dtr["time"].iloc[-1]),
                "test_start": str(dte["time"].iloc[0]),
                "test_end": str(dte["time"].iloc[-1]),
                "train_estrategia": tr_res["estrategia"],
                "train_direcao": tr_res["direcao"],
                "train_lucro_total": tr_res["lucro_total"],
                "train_retorno_pct": tr_res["retorno_pct"],
                "train_num_trades": tr_res["num_trades"],
                "train_winrate": tr_res["winrate"],
                "test_estrategia": te_res["estrategia"],
                "test_direcao": te_res["direcao"],
                "test_lucro_total": te_res["lucro_total"],
                "test_retorno_pct": te_res["retorno_pct"],
                "test_num_trades": te_res["num_trades"],
                "test_winrate": te_res["winrate"],
                "test_status_funil": te_res["status_funil"],
                "test_score": te_res["score"],
                "test_score_pct": te_res["score_pct"],
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward runner (Genesis Quant).")
    parser.add_argument("--csv", required=True, help="CSV de candles (time,open,high,low,close,volume,turnover).")
    parser.add_argument(
        "--strategy",
        default="FUNIL",
        choices=["FUNIL", "BREAKOUT", "MEAN_REVERSION", "EARLY_TREND", "MARKET_MAKING_LATERAL"],
        help="Modo de backtest por janela.",
    )
    parser.add_argument("--train-candles", type=int, default=500)
    parser.add_argument("--test-candles", type=int, default=200)
    parser.add_argument("--step-candles", type=int, default=100)
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--sentimento", default="neutro")
    parser.add_argument("--approved-side", default="", help="buy|sell|auto")
    parser.add_argument("--regime-override", type=int, default=0, help="0 desliga, 1..4 fixa regime.")

    parser.add_argument("--gate-min-trades", type=int, default=300)
    parser.add_argument("--gate-min-pf", type=float, default=1.20)
    parser.add_argument("--gate-min-pos-windows-pct", type=float, default=60.0)
    parser.add_argument("--gate-max-dd-pct", type=float, default=10.0)
    parser.add_argument("--mc-iterations", type=int, default=1000)
    parser.add_argument("--mc-seed", type=int, default=42)
    parser.add_argument("--run-sensitivity", action="store_true")
    parser.add_argument("--run-cost-stress", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--base-cost-pct-per-trade",
        type=float,
        default=0.06,
        help="Custo base estimado por trade em %% do capital para stress de custos.",
    )
    parser.add_argument(
        "--cost-stress-multipliers",
        default="1.0,2.0",
        help="Multiplicadores de custo separados por virgula (ex: 1.0,1.5,2.0).",
    )
    parser.add_argument("--auto-daily-report", action=argparse.BooleanOptionalAction, default=True)

    parser.add_argument("--out-dir", default="reports/walk_forward")
    args = parser.parse_args()

    df = _load_csv(args.csv)
    side = (args.approved_side or "").strip().lower()
    if side not in ("buy", "sell"):
        side = None
    regime_override = args.regime_override if args.regime_override in (1, 2, 3, 4) else None

    rows = run_walk_forward(
        df=df,
        strategy=args.strategy,
        train_candles=args.train_candles,
        test_candles=args.test_candles,
        step_candles=args.step_candles,
        capital_inicial=args.capital,
        sentimento=args.sentimento,
        approved_side=side,
        regime_override=regime_override,
    )
    if not rows:
        raise RuntimeError("Nenhuma janela gerada. Ajuste train/test/step.")

    gate = _gate_summary(
        rows=rows,
        capital_inicial=args.capital,
        min_total_trades=args.gate_min_trades,
        min_pf=args.gate_min_pf,
        min_pos_windows_pct=args.gate_min_pos_windows_pct,
        max_dd_pct=args.gate_max_dd_pct,
    )
    pnl_list = [_safe_float(r.get("test_lucro_total", 0.0), 0.0) for r in rows]
    monte_carlo = _build_mc_stats(
        pnl_list=pnl_list,
        capital_inicial=args.capital,
        iterations=args.mc_iterations,
        seed=args.mc_seed,
    )
    sensitivity = (
        _run_sensitivity(
            df=df,
            strategy=args.strategy,
            base_train=args.train_candles,
            base_test=args.test_candles,
            base_step=args.step_candles,
            capital_inicial=args.capital,
            sentimento=args.sentimento,
            approved_side=side,
            regime_override=regime_override,
            gate_min_trades=args.gate_min_trades,
            gate_min_pf=args.gate_min_pf,
            gate_min_pos_windows_pct=args.gate_min_pos_windows_pct,
            gate_max_dd_pct=args.gate_max_dd_pct,
        )
        if args.run_sensitivity
        else {"enabled": False, "runs": [], "summary": {}}
    )
    multipliers: List[float] = []
    for tok in str(args.cost_stress_multipliers).split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            m = float(tok)
        except Exception:
            continue
        if m > 0:
            multipliers.append(m)
    if not multipliers:
        multipliers = [1.0, 2.0]
    if not any(abs(m - 1.0) < 1e-9 for m in multipliers):
        multipliers.insert(0, 1.0)
    cost_stress = (
        _run_cost_stress(
            rows=rows,
            capital_inicial=args.capital,
            base_cost_pct_per_trade=max(0.0, args.base_cost_pct_per_trade),
            multipliers=multipliers,
            min_total_trades=args.gate_min_trades,
            min_pf=args.gate_min_pf,
            min_pos_windows_pct=args.gate_min_pos_windows_pct,
            max_dd_pct=args.gate_max_dd_pct,
        )
        if args.run_cost_stress
        else {"enabled": False, "scenarios": [], "summary": {}}
    )

    os.makedirs(args.out_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"walk_forward_{args.strategy.lower()}_{ts}"
    csv_out = os.path.join(args.out_dir, base + ".csv")
    json_out = os.path.join(args.out_dir, base + ".json")

    pd.DataFrame(rows).to_csv(csv_out, index=False, encoding="utf-8")
    payload = {
        "config": {
            "csv": args.csv,
            "strategy": args.strategy,
            "train_candles": args.train_candles,
            "test_candles": args.test_candles,
            "step_candles": args.step_candles,
            "capital": args.capital,
            "sentimento": args.sentimento,
            "approved_side": side or "auto",
            "regime_override": regime_override,
            "gates": {
                "min_trades": args.gate_min_trades,
                "min_pf": args.gate_min_pf,
                "min_pos_windows_pct": args.gate_min_pos_windows_pct,
                "max_dd_pct": args.gate_max_dd_pct,
            },
            "monte_carlo": {
                "iterations": args.mc_iterations,
                "seed": args.mc_seed,
            },
            "sensitivity_enabled": bool(args.run_sensitivity),
            "cost_stress": {
                "enabled": bool(args.run_cost_stress),
                "base_cost_pct_per_trade": args.base_cost_pct_per_trade,
                "multipliers": multipliers,
            },
        },
        "summary": gate,
        "monte_carlo": monte_carlo,
        "sensitivity": sensitivity,
        "cost_stress": cost_stress,
        "windows": rows,
    }
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    daily_csv = None
    daily_md = None
    daily_runs = 0
    if args.auto_daily_report:
        today = datetime.now().strftime("%Y-%m-%d")
        daily_csv, daily_md, daily_runs = generate_daily_report(
            target_date=today,
            input_dir=args.out_dir,
            output_dir="reports/daily",
            min_pf=args.gate_min_pf,
            max_dd_pct=args.gate_max_dd_pct,
            max_mc_loss_prob_pct=40.0,
        )

    print("Walk-forward concluido.")
    print(f"CSV:  {csv_out}")
    print(f"JSON: {json_out}")
    if args.auto_daily_report:
        print(f"Daily CSV: {daily_csv}")
        print(f"Daily MD:  {daily_md}")
        print(f"Daily runs: {daily_runs}")
    print(
        "Resumo:",
        json.dumps(
            {
                "approved": gate["approved"],
                **gate["metrics"],
            },
            ensure_ascii=False,
        ),
    )


if __name__ == "__main__":
    main()
