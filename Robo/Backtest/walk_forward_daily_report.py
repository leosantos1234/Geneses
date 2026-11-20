import argparse
import csv
import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional


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


def _safe_bool(x: Any, default: bool = False) -> bool:
    if isinstance(x, bool):
        return x
    if isinstance(x, str):
        return x.strip().lower() in ("1", "true", "yes", "y", "sim")
    return default


def _parse_run_date(path: str) -> Optional[str]:
    name = os.path.basename(path)
    # expected suffix: ..._YYYYMMDD_HHMMSS.json
    parts = name.rsplit("_", 2)
    if len(parts) >= 3:
        dt_part = parts[-2]
        if len(dt_part) == 8 and dt_part.isdigit():
            return f"{dt_part[:4]}-{dt_part[4:6]}-{dt_part[6:8]}"
    return None


@dataclass
class RunRow:
    file: str
    run_date: str
    strategy: str
    approved: bool
    windows: int
    total_trades: int
    total_pnl: float
    profit_factor: float
    positive_windows_pct: float
    max_drawdown_pct: float
    mc_prob_loss_pct: float
    mc_p05_final_equity: float
    mc_mean_final_equity: float
    sensitivity_approval_rate_pct: float
    cost_stress_2x_pnl: float
    cost_stress_2x_pf: float
    cost_stress_2x_approved: bool


def _load_run(path: str) -> Optional[RunRow]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    summary = data.get("summary", {})
    metrics = summary.get("metrics", {})
    config = data.get("config", {})
    mc = data.get("monte_carlo", {})
    sens = data.get("sensitivity", {})
    sens_summary = sens.get("summary", {}) if isinstance(sens, dict) else {}
    cost_stress = data.get("cost_stress", {})
    cost_scenarios = cost_stress.get("scenarios", []) if isinstance(cost_stress, dict) else []
    cost_2x = None
    for s in cost_scenarios:
        if abs(_safe_float(s.get("multiplier", 0.0), 0.0) - 2.0) < 1e-9:
            cost_2x = s
            break
    if not cost_2x:
        cost_2x = {"total_pnl": 0.0, "profit_factor": 0.0, "approved": False}

    run_date = _parse_run_date(path)
    if not run_date:
        ts = datetime.fromtimestamp(os.path.getmtime(path))
        run_date = ts.strftime("%Y-%m-%d")

    return RunRow(
        file=os.path.basename(path),
        run_date=run_date,
        strategy=str(config.get("strategy", "UNKNOWN")).upper(),
        approved=_safe_bool(summary.get("approved", False), False),
        windows=_safe_int(metrics.get("windows", 0), 0),
        total_trades=_safe_int(metrics.get("total_trades", 0), 0),
        total_pnl=_safe_float(metrics.get("total_pnl", 0.0), 0.0),
        profit_factor=_safe_float(metrics.get("profit_factor", 0.0), 0.0),
        positive_windows_pct=_safe_float(metrics.get("positive_windows_pct", 0.0), 0.0),
        max_drawdown_pct=_safe_float(metrics.get("max_drawdown_pct", 0.0), 0.0),
        mc_prob_loss_pct=_safe_float(mc.get("prob_loss_pct", 0.0), 0.0),
        mc_p05_final_equity=_safe_float(mc.get("p05_final_equity", 0.0), 0.0),
        mc_mean_final_equity=_safe_float(mc.get("mean_final_equity", 0.0), 0.0),
        sensitivity_approval_rate_pct=_safe_float(sens_summary.get("approval_rate_pct", 0.0), 0.0),
        cost_stress_2x_pnl=_safe_float(cost_2x.get("total_pnl", 0.0), 0.0),
        cost_stress_2x_pf=_safe_float(cost_2x.get("profit_factor", 0.0), 0.0),
        cost_stress_2x_approved=_safe_bool(cost_2x.get("approved", False), False),
    )


def _list_runs(input_dir: str, target_date: str) -> List[RunRow]:
    if not os.path.isdir(input_dir):
        return []
    rows: List[RunRow] = []
    for name in os.listdir(input_dir):
        if not name.endswith(".json"):
            continue
        path = os.path.join(input_dir, name)
        row = _load_run(path)
        if not row:
            continue
        if row.run_date == target_date:
            rows.append(row)
    rows.sort(key=lambda r: r.file)
    return rows


def _write_csv(rows: List[RunRow], out_path: str) -> None:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fields = [
        "file",
        "run_date",
        "strategy",
        "approved",
        "windows",
        "total_trades",
        "total_pnl",
        "profit_factor",
        "positive_windows_pct",
        "max_drawdown_pct",
        "mc_prob_loss_pct",
        "mc_p05_final_equity",
        "mc_mean_final_equity",
        "sensitivity_approval_rate_pct",
        "cost_stress_2x_pnl",
        "cost_stress_2x_pf",
        "cost_stress_2x_approved",
    ]
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {
                    "file": r.file,
                    "run_date": r.run_date,
                    "strategy": r.strategy,
                    "approved": r.approved,
                    "windows": r.windows,
                    "total_trades": r.total_trades,
                    "total_pnl": f"{r.total_pnl:.6f}",
                    "profit_factor": f"{r.profit_factor:.4f}",
                    "positive_windows_pct": f"{r.positive_windows_pct:.2f}",
                    "max_drawdown_pct": f"{r.max_drawdown_pct:.2f}",
                    "mc_prob_loss_pct": f"{r.mc_prob_loss_pct:.2f}",
                    "mc_p05_final_equity": f"{r.mc_p05_final_equity:.2f}",
                    "mc_mean_final_equity": f"{r.mc_mean_final_equity:.2f}",
                    "sensitivity_approval_rate_pct": f"{r.sensitivity_approval_rate_pct:.2f}",
                    "cost_stress_2x_pnl": f"{r.cost_stress_2x_pnl:.6f}",
                    "cost_stress_2x_pf": f"{r.cost_stress_2x_pf:.4f}",
                    "cost_stress_2x_approved": r.cost_stress_2x_approved,
                }
            )


def _overall_status(
    rows: List[RunRow],
    min_pf: float,
    max_dd: float,
    max_mc_loss_prob: float,
) -> str:
    if not rows:
        return "SEM_DADOS"
    avg_pf = sum(r.profit_factor for r in rows) / len(rows)
    worst_dd = max(r.max_drawdown_pct for r in rows)
    worst_mc = max(r.mc_prob_loss_pct for r in rows)
    approved_rate = (sum(1 for r in rows if r.approved) / len(rows)) * 100.0
    if avg_pf >= min_pf and worst_dd <= max_dd and worst_mc <= max_mc_loss_prob and approved_rate >= 60.0:
        return "OPERACIONAL_SAUDAVEL"
    if avg_pf >= 1.0 and worst_dd <= (max_dd * 1.25):
        return "ATENCAO"
    return "RISCO_ELEVADO"


def _write_md(
    rows: List[RunRow],
    out_path: str,
    target_date: str,
    min_pf: float,
    max_dd: float,
    max_mc_loss_prob: float,
) -> None:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    status = _overall_status(rows, min_pf=min_pf, max_dd=max_dd, max_mc_loss_prob=max_mc_loss_prob)
    n = len(rows)
    total_pnl = sum(r.total_pnl for r in rows)
    total_trades = sum(r.total_trades for r in rows)
    avg_pf = (sum(r.profit_factor for r in rows) / n) if n else 0.0
    avg_pos = (sum(r.positive_windows_pct for r in rows) / n) if n else 0.0
    worst_dd = max((r.max_drawdown_pct for r in rows), default=0.0)
    worst_mc = max((r.mc_prob_loss_pct for r in rows), default=0.0)
    avg_cost_2x_pf = (sum(r.cost_stress_2x_pf for r in rows) / n) if n else 0.0
    cost_2x_approved_rate = (sum(1 for r in rows if r.cost_stress_2x_approved) / n * 100.0) if n else 0.0
    approved = sum(1 for r in rows if r.approved)
    approval_rate = (approved / n * 100.0) if n else 0.0

    best = max(rows, key=lambda x: x.total_pnl) if rows else None
    worst = min(rows, key=lambda x: x.total_pnl) if rows else None

    alerts: List[str] = []
    if avg_pf < min_pf:
        alerts.append(f"PF medio abaixo do alvo: {avg_pf:.2f} < {min_pf:.2f}")
    if worst_dd > max_dd:
        alerts.append(f"Drawdown maximo acima do limite: {worst_dd:.2f}% > {max_dd:.2f}%")
    if worst_mc > max_mc_loss_prob:
        alerts.append(f"Monte Carlo (prob. perda) acima do limite: {worst_mc:.2f}% > {max_mc_loss_prob:.2f}%")
    if approval_rate < 60.0 and n > 0:
        alerts.append(f"Taxa de aprovacao baixa: {approval_rate:.2f}% < 60.00%")
    if avg_cost_2x_pf < min_pf and n > 0:
        alerts.append(f"PF medio no stress 2x abaixo do alvo: {avg_cost_2x_pf:.2f} < {min_pf:.2f}")
    if not alerts and n > 0:
        alerts.append("Sem alertas criticos no dia.")
    if not rows:
        alerts = ["Sem arquivos de walk-forward para a data informada."]
    paper_phase = {}
    phase_path = os.path.join("reports", "paper", "paper_phase_status.json")
    if os.path.exists(phase_path):
        try:
            with open(phase_path, "r", encoding="utf-8") as f:
                paper_phase = json.load(f) or {}
        except Exception:
            paper_phase = {}

    lines = [
        "# RELATORIO DIARIO - WALK FORWARD",
        "",
        f"- Data: {target_date}",
        f"- Status geral: **{status}**",
        f"- Runs: {n}",
        f"- Runs aprovados: {approved} ({approval_rate:.2f}%)",
        f"- Trades totais: {total_trades}",
        f"- PnL total: {total_pnl:.4f}",
        f"- PF medio: {avg_pf:.4f}",
        f"- Janelas positivas medias: {avg_pos:.2f}%",
        f"- Pior DD observado: {worst_dd:.2f}%",
        f"- Pior prob. de perda (MC): {worst_mc:.2f}%",
        f"- PF medio com custo 2x: {avg_cost_2x_pf:.4f}",
        f"- Aprovacao com custo 2x: {cost_2x_approved_rate:.2f}%",
        "",
        "## Alertas",
    ]
    for a in alerts:
        lines.append(f"- {a}")
    if isinstance(paper_phase, dict) and paper_phase:
        m = paper_phase.get("metrics", {}) if isinstance(paper_phase.get("metrics"), dict) else {}
        lines.extend(
            [
                "",
                "## Paper Phase",
                f"- Fase paper atual: {paper_phase.get('phase_current', 'PAPER')} -> {paper_phase.get('phase_next', 'PAPER')}",
                f"- Elegivel para micro: {paper_phase.get('eligible_for_micro', False)}",
                f"- Tracking error medio (paper vs backtest): {float(m.get('avg_tracking_error_pct', 0.0)):.4f}%",
                f"- Trades paper (janela): {int(m.get('trades', 0))}",
            ]
        )
    lines.extend(["", "## Destaques"])
    if best:
        lines.append(
            f"- Melhor run: `{best.file}` | strategy={best.strategy} | pnl={best.total_pnl:.4f} | pf={best.profit_factor:.2f}"
        )
    if worst:
        lines.append(
            f"- Pior run: `{worst.file}` | strategy={worst.strategy} | pnl={worst.total_pnl:.4f} | pf={worst.profit_factor:.2f}"
        )
    lines.extend(
        [
            "",
            "## Tabela de runs",
            "",
            "| file | strategy | approved | trades | pnl | pf | pos_win_% | dd_% | mc_loss_% | pf_2x | appr_2x |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for r in rows:
        lines.append(
            f"| {r.file} | {r.strategy} | {str(r.approved).lower()} | {r.total_trades} | {r.total_pnl:.4f} | {r.profit_factor:.2f} | {r.positive_windows_pct:.2f} | {r.max_drawdown_pct:.2f} | {r.mc_prob_loss_pct:.2f} | {r.cost_stress_2x_pf:.2f} | {str(r.cost_stress_2x_approved).lower()} |"
        )

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera relatorio diario a partir dos JSONs de walk-forward.")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="Data alvo YYYY-MM-DD.")
    parser.add_argument("--input-dir", default="reports/walk_forward")
    parser.add_argument("--output-dir", default="reports/daily")
    parser.add_argument("--min-pf", type=float, default=1.2)
    parser.add_argument("--max-dd-pct", type=float, default=10.0)
    parser.add_argument("--max-mc-loss-prob-pct", type=float, default=40.0)
    args = parser.parse_args()

    csv_out, md_out, count = generate_daily_report(
        target_date=args.date,
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        min_pf=args.min_pf,
        max_dd_pct=args.max_dd_pct,
        max_mc_loss_prob_pct=args.max_mc_loss_prob_pct,
    )

    print(f"Relatorio diario gerado para {args.date}")
    print(f"CSV: {csv_out}")
    print(f"MD:  {md_out}")
    print(f"Runs encontrados: {count}")


def generate_daily_report(
    target_date: str,
    input_dir: str = "reports/walk_forward",
    output_dir: str = "reports/daily",
    min_pf: float = 1.2,
    max_dd_pct: float = 10.0,
    max_mc_loss_prob_pct: float = 40.0,
) -> tuple[str, str, int]:
    rows = _list_runs(input_dir, target_date)
    csv_out = os.path.join(output_dir, f"daily_walk_forward_{target_date}.csv")
    md_out = os.path.join(output_dir, f"daily_walk_forward_{target_date}.md")
    _write_csv(rows, csv_out)
    _write_md(
        rows=rows,
        out_path=md_out,
        target_date=target_date,
        min_pf=min_pf,
        max_dd=max_dd_pct,
        max_mc_loss_prob=max_mc_loss_prob_pct,
    )
    return csv_out, md_out, len(rows)


if __name__ == "__main__":
    main()
