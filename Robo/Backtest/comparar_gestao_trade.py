"""Comparação pareada de gestão de trade para backtest read-only.

Recebe candles OKX em JSON pelo stdin e compara uma saída fixa com uma
gestão adaptativa, usando exatamente os mesmos sinais de breakout + RSI.
Não envia ordens e não acessa banco ou credenciais.
"""

from __future__ import annotations

import json
import logging
import random
import sys
from pathlib import Path
from typing import Any


# Permite importar os mesmos módulos de produção quando o script é executado
# diretamente a partir de Robo/Backtest.
ROBO_ROOT = Path(__file__).resolve().parents[1]
if str(ROBO_ROOT) not in sys.path:
    sys.path.insert(0, str(ROBO_ROOT))


COST = 0.0006
SL_PCT = 0.0045
TP_PCT = 0.0060
TIMEOUT = 12
BREAKEVEN_TRIGGER = 0.0045
TRAIL_TRIGGER = 0.0055
TRAIL_DISTANCE = 0.0035
BOOTSTRAP_ITERATIONS = 5000


def ema(values: list[float], period: int) -> list[float]:
    alpha = 2.0 / (period + 1.0)
    current = values[0]
    result = []
    for value in values:
        current = alpha * value + (1.0 - alpha) * current
        result.append(current)
    return result


def rsi(values: list[float], period: int = 14) -> list[float]:
    result = [50.0] * len(values)
    gains = [0.0] * len(values)
    losses = [0.0] * len(values)
    for i in range(1, len(values)):
        delta = values[i] - values[i - 1]
        gains[i] = max(delta, 0.0)
        losses[i] = max(-delta, 0.0)
    for i in range(period, len(values)):
        gain = sum(gains[i - period + 1 : i + 1]) / period
        loss = sum(losses[i - period + 1 : i + 1]) / period
        result[i] = 100.0 if loss == 0 else 100.0 - (100.0 / (1.0 + gain / loss))
    return result


def classify_regime(rows: list[list[float]], index: int) -> int:
    """Use the production regime detector on candles available at the signal."""
    try:
        import pandas as pd
        from regime.regime_detector import RegimeDetector
        from utils.indicadores import calculate_indicators

        frame = pd.DataFrame(rows[: index + 1], columns=["time", "open", "high", "low", "close", "volume"])
        frame = calculate_indicators(frame)
        return int(RegimeDetector().detectar(frame, symbol="XRPUSDT"))
    except Exception:
        # Fail closed for a regime-aware study: unknown regime cannot qualify a
        # directional breakout.
        return 0


def signals(rows: list[list[float]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    closes = [float(row[4]) for row in rows]
    rsi14 = rsi(closes)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    output = []
    regime_counts: dict[str, int] = {}
    next_allowed = 0
    for i in range(max(200, 20, 15), len(rows) - TIMEOUT):
        if i < next_allowed:
            continue
        prior_high = max(float(row[2]) for row in rows[i - 20 : i])
        prior_low = min(float(row[3]) for row in rows[i - 20 : i])
        volume_avg = sum(float(row[5]) for row in rows[i - 20 : i]) / 20.0
        close = closes[i]
        side = "buy" if close > prior_high else "sell" if close < prior_low else None
        if side is None or float(rows[i][5]) < volume_avg:
            continue
        crossed = ((side == "buy" and rsi14[i - 1] <= 50 < rsi14[i]) or
                   (side == "sell" and rsi14[i - 1] >= 50 > rsi14[i]))
        if not crossed:
            continue
        regime = classify_regime(rows, i)
        regime_counts[str(regime)] = regime_counts.get(str(regime), 0) + 1
        # BREAKOUT is authorized by the production gate only in regimes 1/2.
        if regime not in (1, 2):
            continue
        output.append({"index": i, "side": side, "entry": close,
                       "trend": "buy" if ema50[i] > ema200[i] else "sell",
                       "regime": regime})
        next_allowed = i + TIMEOUT
    return output, regime_counts


def run_trade(rows: list[list[float]], signal: dict[str, Any], intelligent: bool) -> dict[str, Any]:
    i = int(signal["index"])
    side = signal["side"]
    entry = float(signal["entry"])
    stop = entry * (1.0 - SL_PCT if side == "buy" else 1.0 + SL_PCT)
    target = entry * (1.0 + TP_PCT if side == "buy" else 1.0 - TP_PCT)
    best = entry
    breakeven = False
    reversal_bars = 0
    reason = "timeout"
    exit_price = float(rows[min(i + TIMEOUT, len(rows) - 1)][4])
    closes = [float(row[4]) for row in rows]
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    for j in range(i + 1, min(i + TIMEOUT + 1, len(rows))):
        high, low, close = map(float, (rows[j][2], rows[j][3], rows[j][4]))
        favorable = (high - entry) / entry if side == "buy" else (entry - low) / entry
        if intelligent:
            best = max(best, high) if side == "buy" else min(best, low)
            if favorable >= BREAKEVEN_TRIGGER:
                breakeven = True
                stop = max(stop, entry) if side == "buy" else min(stop, entry)
            if favorable >= TRAIL_TRIGGER:
                trail = best * (1.0 - TRAIL_DISTANCE) if side == "buy" else best * (1.0 + TRAIL_DISTANCE)
                stop = max(stop, trail) if side == "buy" else min(stop, trail)
            structural_reversal = (
                side == "buy" and close < ema50[j] and ema50[j] < ema200[j]
            ) or (
                side == "sell" and close > ema50[j] and ema50[j] > ema200[j]
            )
            reversal_bars = reversal_bars + 1 if structural_reversal else 0
            if reversal_bars >= 2:
                reason = "trend_reversal"
                exit_price = close
                break
        if side == "buy":
            if low <= stop:
                reason = "breakeven" if intelligent and breakeven and stop == entry else "stop"
                exit_price = stop
                break
            if high >= target:
                reason = "target"
                exit_price = target
                break
        else:
            if high >= stop:
                reason = "breakeven" if intelligent and breakeven and stop == entry else "stop"
                exit_price = stop
                break
            if low <= target:
                reason = "target"
                exit_price = target
                break
    gross = (exit_price - entry) / entry if side == "buy" else (entry - exit_price) / entry
    return {"index": i, "side": side, "return": gross - COST, "reason": reason}


def metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(item["return"]) for item in trades]
    equity = peak = drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    wins = sum(value > 0 for value in values)
    gross_win = sum(value for value in values if value > 0)
    gross_loss = abs(sum(value for value in values if value < 0))
    return {
        "trades": len(values),
        "wins": wins,
        "win_rate_pct": round(100 * wins / len(values), 2) if values else 0.0,
        "net_return_pct": round(100 * sum(values), 3),
        "expectancy_pct": round(100 * sum(values) / len(values), 3) if values else 0.0,
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss else None,
        "max_drawdown_pct": round(100 * drawdown, 3),
        "exit_reasons": {reason: sum(1 for item in trades if item["reason"] == reason) for reason in sorted({item["reason"] for item in trades})},
    }


def bootstrap_delta(baseline: list[dict[str, Any]], intelligent: list[dict[str, Any]]) -> dict[str, float]:
    deltas = [float(b["return"]) - float(a["return"]) for a, b in zip(baseline, intelligent)]
    if not deltas:
        return {"mean_delta_pct": 0.0, "ci95_low_pct": 0.0, "ci95_high_pct": 0.0}
    rng = random.Random(42)
    samples = []
    for _ in range(BOOTSTRAP_ITERATIONS):
        sample = [rng.choice(deltas) for _ in deltas]
        samples.append(sum(sample) / len(sample))
    samples.sort()
    return {
        "mean_delta_pct": round(100 * sum(deltas) / len(deltas), 3),
        "ci95_low_pct": round(100 * samples[int(0.025 * len(samples))], 3),
        "ci95_high_pct": round(100 * samples[int(0.975 * len(samples))], 3),
    }


def main() -> None:
    logging.disable(logging.CRITICAL)
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env-xrp", override=False)
    except Exception:
        pass
    raw = json.load(sys.stdin)
    raw = raw if isinstance(raw, list) else [raw]
    rows = [[item["ts"], item["o"], item["h"], item["l"], item["c"], item["v"]] if isinstance(item, dict) else item for item in raw]
    found, regime_counts = signals(rows)
    baseline = [run_trade(rows, item, False) for item in found]
    intelligent = [run_trade(rows, item, True) for item in found]
    result = {"candles": len(rows), "signals_after_regime_gate": len(found), "candidate_regimes_before_gate": regime_counts, "baseline_fixed_exit": metrics(baseline), "intelligent_management": metrics(intelligent), "paired_difference": bootstrap_delta(baseline, intelligent), "configuration": {"regime_gate": "production RegimeDetector; BREAKOUT only regimes 1/2", "sl_pct": SL_PCT, "tp_pct": TP_PCT, "timeout_bars": TIMEOUT, "breakeven_trigger_pct": BREAKEVEN_TRIGGER, "trail_trigger_pct": TRAIL_TRIGGER, "trail_distance_pct": TRAIL_DISTANCE, "cost_pct": COST}}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
