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


DEFAULT_ASSETS = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "AVAXUSDT")
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


def _run_one_breakout(asset: str, params: dict[str, float]) -> dict:
    """Executa uma simulação local de Breakout Donchian + tendência.

    O cálculo é autocontido para que o laboratório permaneça offline e não
    dependa do worker de produção ou de credenciais da exchange.
    """
    one = _read_frame(asset, "1h")
    frame = one.set_index("time")[["open", "high", "low", "close", "volume"]].copy()
    previous_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    frame["atr"] = true_range.rolling(14, min_periods=14).mean()
    frame["atr_median"] = frame["atr"].rolling(100, min_periods=100).median()
    frame["ema50"] = frame["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    frame["ema200"] = frame["close"].ewm(span=200, adjust=False, min_periods=200).mean()
    lookback = int(params["lookback"])
    frame["prior_high"] = frame["high"].shift(1).rolling(lookback, min_periods=lookback).max()
    frame["prior_low"] = frame["low"].shift(1).rolling(lookback, min_periods=lookback).min()
    frame["volume_avg"] = frame["volume"].rolling(20, min_periods=20).mean()
    frame = frame.dropna().reset_index()

    balance = INITIAL_BALANCE
    peak = balance
    max_drawdown = 0.0
    position = None
    trades: list[dict] = []
    for i in range(len(frame) - 1):
        row = frame.iloc[i]
        next_row = frame.iloc[i + 1]
        if position is not None:
            stop = position["stop"]
            if position["side"] == "long":
                hit = float(row["low"]) <= stop
                if not hit:
                    position["stop"] = max(stop, float(row["close"]) - position["atr"] * params["trailing_atr"])
            else:
                hit = float(row["high"]) >= stop
                if not hit:
                    position["stop"] = min(stop, float(row["close"]) + position["atr"] * params["trailing_atr"])
            if hit:
                exit_price = stop
                gross = (
                    (exit_price - position["entry"]) / position["entry"]
                    if position["side"] == "long"
                    else (position["entry"] - exit_price) / position["entry"]
                )
                net = gross - params["cost_per_trade"]
                balance *= 1.0 + net * position["notional"] / balance
                trades.append({"entry_time": position["entry_time"], "exit_time": row["time"], "pnl_pct": net * 100.0})
                position = None
                peak = max(peak, balance)
                max_drawdown = min(max_drawdown, balance / peak - 1.0)
            continue

        expansion = float(row["atr"]) >= float(row["atr_median"]) * params["atr_expansion"]
        volume_ok = float(row["volume"]) >= float(row["volume_avg"]) * params["volume_min_ratio"]
        long_signal = float(row["close"]) > float(row["prior_high"]) and float(row["ema50"]) > float(row["ema200"])
        short_signal = float(row["close"]) < float(row["prior_low"]) and float(row["ema50"]) < float(row["ema200"])
        if not expansion or not volume_ok or not (long_signal or short_signal):
            continue
        side = "long" if long_signal else "short"
        entry = float(next_row["open"])
        atr = float(row["atr"])
        stop = entry - params["stop_atr"] * atr if side == "long" else entry + params["stop_atr"] * atr
        risk_amount = balance * params["risk_per_trade"]
        notional = min(balance, risk_amount * entry / max(abs(entry - stop), 1e-12))
        position = {"side": side, "entry": entry, "entry_time": next_row["time"], "stop": stop, "atr": atr, "notional": notional}

    if position is not None:
        row = frame.iloc[-1]
        exit_price = float(row["close"])
        gross = (
            (exit_price - position["entry"]) / position["entry"]
            if position["side"] == "long"
            else (position["entry"] - exit_price) / position["entry"]
        )
        net = gross - params["cost_per_trade"]
        balance *= 1.0 + net * position["notional"] / balance
        trades.append({"entry_time": position["entry_time"], "exit_time": row["time"], "pnl_pct": net * 100.0})
        peak = max(peak, balance)
        max_drawdown = min(max_drawdown, balance / peak - 1.0)

    period_end = pd.to_datetime(frame["time"].max(), utc=True)
    oos_start = period_end - pd.Timedelta(days=365)
    oos_balance, oos_return = _compound(trades, 0.0, oos_start)
    return {
        "asset": asset,
        "strategy": "BREAKOUT",
        "timeframe": "1h",
        "period_start": str(pd.to_datetime(frame["time"].min(), utc=True)),
        "period_end": str(period_end),
        "parameters": params,
        "trades": len(trades),
        "wins": int(sum(float(t.get("pnl_pct", 0.0) or 0.0) > 0 for t in trades)),
        "winrate_pct": float(sum(float(t.get("pnl_pct", 0.0) or 0.0) > 0 for t in trades) / len(trades) * 100.0) if trades else 0.0,
        "return_pct": float((balance / INITIAL_BALANCE - 1.0) * 100.0),
        "final_balance": float(balance),
        "max_drawdown_pct": float(max_drawdown * 100.0),
        "oos_start": str(oos_start),
        "oos_return_pct": float(oos_return),
        "oos_final_balance": float(oos_balance),
        "criteria_reference": "local_breakout_donchian_ema_atr",
    }


def _run_one_mean_reversion(asset: str, params: dict[str, float]) -> dict:
    """Executa Mean Reversion com Bollinger, RSI e filtro lateral local."""
    one = _read_frame(asset, "1h")
    frame = one.set_index("time")[["open", "high", "low", "close", "volume"]].copy()
    window = int(params["window"])
    frame["mid"] = frame["close"].rolling(window, min_periods=window).mean()
    deviation = frame["close"].rolling(window, min_periods=window).std()
    frame["upper"] = frame["mid"] + params["band_std"] * deviation
    frame["lower"] = frame["mid"] - params["band_std"] * deviation
    previous_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    frame["atr"] = true_range.rolling(14, min_periods=14).mean()
    frame["ema50"] = frame["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    frame["ema200"] = frame["close"].ewm(span=200, adjust=False, min_periods=200).mean()
    delta = frame["close"].diff()
    gain = delta.clip(lower=0).rolling(14, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).rolling(14, min_periods=14).mean()
    frame["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, pd.NA))
    frame = frame.dropna().reset_index()

    balance = INITIAL_BALANCE
    peak = balance
    max_drawdown = 0.0
    position = None
    trades: list[dict] = []
    for i in range(len(frame) - 1):
        row = frame.iloc[i]
        next_row = frame.iloc[i + 1]
        if position is not None:
            position["bars"] += 1
            exit_price = None
            if position["side"] == "long":
                if float(row["low"]) <= position["stop"]:
                    exit_price = position["stop"]
                elif float(row["close"]) >= float(row["mid"]):
                    exit_price = float(row["mid"])
            else:
                if float(row["high"]) >= position["stop"]:
                    exit_price = position["stop"]
                elif float(row["close"]) <= float(row["mid"]):
                    exit_price = float(row["mid"])
            if position["bars"] >= int(params["max_bars"]) and exit_price is None:
                exit_price = float(row["close"])
            if exit_price is not None:
                gross = (
                    (exit_price - position["entry"]) / position["entry"]
                    if position["side"] == "long"
                    else (position["entry"] - exit_price) / position["entry"]
                )
                net = gross - params["cost_per_trade"]
                account_return_pct = net * position["notional"] / balance * 100.0
                balance *= 1.0 + net * position["notional"] / balance
                trades.append({"entry_time": position["entry_time"], "exit_time": row["time"], "pnl_pct": account_return_pct})
                position = None
                peak = max(peak, balance)
                max_drawdown = min(max_drawdown, balance / peak - 1.0)
            continue

        lateral = abs(float(row["ema50"]) / float(row["ema200"]) - 1.0) <= params["lateral_threshold"]
        long_signal = lateral and float(row["close"]) <= float(row["lower"]) and float(row["rsi"]) <= params["rsi_low"]
        short_signal = lateral and float(row["close"]) >= float(row["upper"]) and float(row["rsi"]) >= params["rsi_high"]
        if not (long_signal or short_signal):
            continue
        side = "long" if long_signal else "short"
        entry = float(next_row["open"])
        atr = float(row["atr"])
        stop = entry - params["stop_atr"] * atr if side == "long" else entry + params["stop_atr"] * atr
        risk_amount = balance * params["risk_per_trade"]
        notional = min(balance, risk_amount * entry / max(abs(entry - stop), 1e-12))
        position = {"side": side, "entry": entry, "entry_time": next_row["time"], "stop": stop, "notional": notional, "bars": 0}

    if position is not None:
        row = frame.iloc[-1]
        exit_price = float(row["close"])
        gross = (
            (exit_price - position["entry"]) / position["entry"]
            if position["side"] == "long"
            else (position["entry"] - exit_price) / position["entry"]
        )
        net = gross - params["cost_per_trade"]
        account_return_pct = net * position["notional"] / balance * 100.0
        balance *= 1.0 + net * position["notional"] / balance
        trades.append({"entry_time": position["entry_time"], "exit_time": row["time"], "pnl_pct": account_return_pct})
        peak = max(peak, balance)
        max_drawdown = min(max_drawdown, balance / peak - 1.0)

    period_end = pd.to_datetime(frame["time"].max(), utc=True)
    oos_start = period_end - pd.Timedelta(days=365)
    oos_balance, oos_return = _compound(trades, 0.0, oos_start)
    return {
        "asset": asset,
        "strategy": "MEAN_REVERSION_BOLLINGER_RSI",
        "timeframe": "1h",
        "period_start": str(pd.to_datetime(frame["time"].min(), utc=True)),
        "period_end": str(period_end),
        "parameters": params,
        "trades": len(trades),
        "wins": int(sum(float(t.get("pnl_pct", 0.0) or 0.0) > 0 for t in trades)),
        "winrate_pct": float(sum(float(t.get("pnl_pct", 0.0) or 0.0) > 0 for t in trades) / len(trades) * 100.0) if trades else 0.0,
        "return_pct": float((balance / INITIAL_BALANCE - 1.0) * 100.0),
        "final_balance": float(balance),
        "max_drawdown_pct": float(max_drawdown * 100.0),
        "oos_start": str(oos_start),
        "oos_return_pct": float(oos_return),
        "oos_final_balance": float(oos_balance),
        "criteria_reference": "local_mean_reversion_bollinger_rsi",
    }


def _run_one_market_making(asset: str, params: dict[str, float]) -> dict:
    """Executa o simulador canônico de Market Making em modo local."""
    one = _read_frame(asset, "1h")
    from laboratorio_market_making import run_local_market_making

    result = run_local_market_making(one.copy(), params, INITIAL_BALANCE)
    trades = result.get("trade_log", []) or []
    period_start = pd.to_datetime(one["time"].min(), utc=True)
    period_end = pd.to_datetime(one["time"].max(), utc=True)
    oos_start = period_end - pd.Timedelta(days=365)
    oos_return = sum(
        float(trade.get("pnl_pct", 0.0) or 0.0)
        for trade in trades
        if pd.to_datetime(trade.get("entry_time"), utc=True, errors="coerce") >= oos_start
    )
    return {
        "asset": asset,
        "strategy": "MARKET_MAKING_LATERAL",
        "timeframe": "1h",
        "period_start": str(period_start),
        "period_end": str(period_end),
        "parameters": params,
        "trades": int(result.get("num_trades", len(trades))),
        "wins": int(sum(float(t.get("lucro", 0.0) or 0.0) > 0 for t in trades)),
        "winrate_pct": float(result.get("winrate", 0.0) or 0.0),
        "return_pct": float(result.get("retorno_pct", 0.0) or 0.0),
        "final_balance": float(result.get("saldo_final", INITIAL_BALANCE) or INITIAL_BALANCE),
        "max_drawdown_pct": 0.0,
        "oos_start": str(oos_start),
        "oos_return_pct": float(oos_return),
        "oos_final_balance": float(INITIAL_BALANCE * (1.0 + oos_return / 100.0)),
        "fills": int(result.get("fills", 0) or 0),
        "inventory_stops": int(result.get("inventory_stops", 0) or 0),
        "criteria_reference": "canonical_market_making_lateral_backtest",
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


def _breakout_parameter_grid() -> list[dict[str, float]]:
    values = {
        "lookback": (20, 40, 55),
        "stop_atr": (1.8, 2.5, 3.0),
        "trailing_atr": (2.0, 3.0),
        "atr_expansion": (1.0, 1.05),
        "volume_min_ratio": (0.8, 1.0),
        "risk_per_trade": (0.005,),
        "cost_per_trade": (0.0019,),
    }
    names = tuple(values)
    return [dict(zip(names, combo)) for combo in itertools.product(*(values[name] for name in names))]


def _mean_reversion_parameter_grid() -> list[dict[str, float]]:
    values = {
        "window": (20, 30),
        "band_std": (2.0, 2.5),
        "rsi_low": (30, 35),
        "rsi_high": (65, 70),
        "stop_atr": (1.5, 2.0),
        "max_bars": (20, 30),
        "lateral_threshold": (0.03,),
        "risk_per_trade": (0.005,),
        "cost_per_trade": (0.0019,),
    }
    names = tuple(values)
    return [dict(zip(names, combo)) for combo in itertools.product(*(values[name] for name in names))]


def _market_making_parameter_grid() -> list[dict[str, float]]:
    values = {
        "order_qty": (0.001, 0.002),
        "max_inventory_qty": (0.02, 0.04),
        "stop_inventory_qty": (0.018, 0.035),
        "min_spread_pct": (0.0004, 0.0008),
        "max_spread_pct": (0.01,),
        "spread_vol_mult": (1.5, 2.0),
        "skew_strength": (0.5, 1.0),
        "cost_per_trade": (0.0019,),
    }
    names = tuple(values)
    return [dict(zip(names, combo)) for combo in itertools.product(*(values[name] for name in names))]


def main() -> int:
    parser = argparse.ArgumentParser(description="Busca local de backtests de 36 meses")
    parser.add_argument("--strategy", choices=("PULLBACK", "BREAKOUT", "MEAN_REVERSION", "MARKET_MAKING_LATERAL"), default="PULLBACK")
    parser.add_argument("--assets", nargs="+", default=list(DEFAULT_ASSETS), choices=list(DEFAULT_ASSETS))
    parser.add_argument("--min-return", type=float, default=DEFAULT_MIN_RETURN)
    args = parser.parse_args()

    for folder in ("candidatos", "relatorios", "logs"):
        (OUT / folder).mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    log_path = OUT / "logs" / f"laboratorio_{started.strftime('%Y%m%d_%H%M%S')}.log"
    ranking_path = OUT / "relatorios" / f"ranking_{started.strftime('%Y%m%d_%H%M%S')}.csv"
    grid = {
        "PULLBACK": _parameter_grid,
        "BREAKOUT": _breakout_parameter_grid,
        "MEAN_REVERSION": _mean_reversion_parameter_grid,
        "MARKET_MAKING_LATERAL": _market_making_parameter_grid,
    }[args.strategy]()
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
                    runners = {
                        "PULLBACK": _run_one,
                        "BREAKOUT": _run_one_breakout,
                        "MEAN_REVERSION": _run_one_mean_reversion,
                        "MARKET_MAKING_LATERAL": _run_one_market_making,
                    }
                    result = runners[args.strategy](asset, params)
                    rows.append(result)
                    status = "CANDIDATO" if result["return_pct"] >= args.min_return else "descartado"
                    write(f"[{completed}/{total}] {asset} retorno={result['return_pct']:.2f}% OOS={result['oos_return_pct']:.2f}% trades={result['trades']} => {status}")
                    if status == "CANDIDATO":
                        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                        candidate = OUT / "candidatos" / f"{asset}_{args.strategy.lower()}_{result['return_pct']:.2f}_{stamp}.json"
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
