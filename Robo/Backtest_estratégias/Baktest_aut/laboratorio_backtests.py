"""Laboratório local de busca de backtests.

Executa somente simulações em arquivos históricos locais. Não abre sessão de
exchange, não lê chaves e não envia ordens.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


HERE = Path(__file__).resolve().parent
ROBO = HERE.parents[1]
DATA = ROBO / "dados_historicos" / "swing_36m"
OUT = HERE
sys.path.insert(0, str(ROBO))


DEFAULT_ASSETS = ("SOLUSDT", "AVAXUSDT")
DEFAULT_MIN_RETURN = 300.0
INITIAL_BALANCE = 1000.0


def _read_frame(asset: str, timeframe: str) -> pd.DataFrame:
    path = DATA / f"{asset}_{timeframe}.csv"
    if not path.exists():
        raise FileNotFoundError(f"histórico ausente: {path}")
    frame = pd.read_csv(path)
    frame["time"] = pd.to_datetime(frame["time"], utc=True, errors="coerce")
    return frame.dropna(subset=["time"]).sort_values("time").drop_duplicates("time").reset_index(drop=True)


def _compound(trades: list[dict], cost: float, start: pd.Timestamp | None = None) -> tuple[float, float]:
    balance = INITIAL_BALANCE
    selected = []
    for trade in trades:
        entry_time = pd.to_datetime(trade.get("entry_time"), utc=True, errors="coerce")
        if start is None or entry_time >= start:
            selected.append(trade)
    for trade in sorted(selected, key=lambda item: str(item.get("exit_time", ""))):
        pnl = float(trade.get("pnl_pct", 0.0) or 0.0)
        balance *= 1.0 + pnl / 100.0 - cost
    return balance, (balance / INITIAL_BALANCE - 1.0) * 100.0


def _run_one(asset: str, params: dict[str, float]) -> dict:
    # O scanner usa o mesmo módulo canônico do worker, mas sem exigir
    # validação externa e sem permitir qualquer operação de exchange.
    os.environ.update(
        {
            "PULLBACK_STOP_ATR": str(params["stop_atr"]),
            "PULLBACK_TARGET_ATR": str(params["target_atr"]),
            "PULLBACK_TRAILING_ATR": str(params["trailing_atr"]),
            "PULLBACK_VOLUME_MIN_RATIO": str(params["volume_min_ratio"]),
            "PULLBACK_COOLDOWN_CANDLES": str(params["cooldown_candles"]),
            "PULLBACK_COST_PER_TRADE": str(params["cost_per_trade"]),
            "PULLBACK_BACKTEST_PARITY": "1",
            "PULLBACK_HISTORICAL_BACKTEST_ENABLED": "0",
        }
    )
    from estrategia_entrada.pullback_trend_following.strategy import backtest_entrada_1h

    one = _read_frame(asset, "1h")
    four = _read_frame(asset, "4h")
    daily = _read_frame(asset, "1d")
    result = backtest_entrada_1h(one, four, daily, regime=2)
    trades = result.get("trade_log", []) or []
    period_end = pd.to_datetime(one["time"].max(), utc=True)
    oos_start = period_end - pd.Timedelta(days=365)
    oos_balance, oos_return = _compound(trades, params["cost_per_trade"], oos_start)
    return {
        "asset": asset,
        "strategy": "PULLBACK_TREND_FOLLOWING",
        "timeframe": "1h",
        "period_start": str(one["time"].min()),
        "period_end": str(period_end),
        "parameters": params,
        "trades": int(result.get("num_trades", len(trades))),
        "wins": int(sum(float(t.get("pnl_pct", 0.0) or 0.0) > 0 for t in trades)),
        "winrate_pct": float(result.get("winrate", 0.0) or 0.0),
        "return_pct": float(result.get("retorno_pct", result.get("retorno_composto_pct", 0.0)) or 0.0),
        "final_balance": float(result.get("saldo_final", INITIAL_BALANCE) or INITIAL_BALANCE),
        "max_drawdown_pct": float(result.get("max_drawdown_pct", 0.0) or 0.0),
        "oos_start": str(oos_start),
        "oos_return_pct": oos_return,
        "oos_final_balance": oos_balance,
        "criteria_reference": "pullback_trend_following_canonical",
    }


def _parameter_grid() -> list[dict[str, float]]:
    values = {
        "stop_atr": (1.6, 1.8, 2.0),
        "target_atr": (2.5, 3.0, 3.5),
        "trailing_atr": (1.5, 2.0),
        "volume_min_ratio": (0.8, 1.0),
        "cooldown_candles": (3,),
        "cost_per_trade": (0.00095,),
    }
    names = tuple(values)
    return [dict(zip(names, combo)) for combo in itertools.product(*(values[name] for name in names))]


def main() -> int:
    parser = argparse.ArgumentParser(description="Busca local de backtests Pullback de 36 meses")
    parser.add_argument("--assets", nargs="+", default=list(DEFAULT_ASSETS), choices=list(DEFAULT_ASSETS))
    parser.add_argument("--min-return", type=float, default=DEFAULT_MIN_RETURN)
    args = parser.parse_args()

    for folder in ("candidatos", "relatorios", "logs"):
        (OUT / folder).mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    log_path = OUT / "logs" / f"laboratorio_{started.strftime('%Y%m%d_%H%M%S')}.log"
    ranking_path = OUT / "relatorios" / f"ranking_{started.strftime('%Y%m%d_%H%M%S')}.csv"
    grid = _parameter_grid()
    total = len(args.assets) * len(grid)
    rows = []
    with log_path.open("w", encoding="utf-8") as log:
        def write(message: str) -> None:
            print(message, flush=True)
            log.write(message + "\n")
            log.flush()

        write("LABORATÓRIO LOCAL — somente backtest, sem ordens")
        write(f"Ativos={','.join(args.assets)} | combinações={total} | filtro={args.min_return:.2f}%")
        completed = 0
        for asset in args.assets:
            for params in grid:
                completed += 1
                try:
                    result = _run_one(asset, params)
                    rows.append(result)
                    status = "CANDIDATO" if result["return_pct"] >= args.min_return else "descartado"
                    write(f"[{completed}/{total}] {asset} retorno={result['return_pct']:.2f}% OOS={result['oos_return_pct']:.2f}% trades={result['trades']} => {status}")
                    if status == "CANDIDATO":
                        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                        candidate = OUT / "candidatos" / f"{asset}_pullback_{result['return_pct']:.2f}_{stamp}.json"
                        candidate.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                except Exception as exc:
                    write(f"[{completed}/{total}] {asset} ERRO: {type(exc).__name__}: {exc}")

    rows.sort(key=lambda item: (item.get("oos_return_pct", 0.0), item.get("return_pct", 0.0)), reverse=True)
    fields = ["asset", "strategy", "timeframe", "period_start", "period_end", "return_pct", "oos_return_pct", "final_balance", "oos_final_balance", "trades", "wins", "winrate_pct", "max_drawdown_pct", "parameters"]
    with ranking_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: json.dumps(row[field], ensure_ascii=False) if field == "parameters" else row.get(field) for field in fields})
    print(f"\nRanking salvo em: {ranking_path}")
    print(f"Candidatos salvos em: {OUT / 'candidatos'}")
    print(f"Total acima do filtro: {sum(row.get('return_pct', 0.0) >= args.min_return for row in rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
