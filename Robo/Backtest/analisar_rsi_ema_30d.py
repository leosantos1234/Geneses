"""Estudo read-only de RSI/EMA em breakouts da OKX.

Baixa candles públicos do swap BTC-USDT e compara as mesmas oportunidades
com quatro políticas: breakout puro, breakout + RSI, breakout + EMA e ambos.
Não usa credenciais nem envia ordens.
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ENDPOINT = "https://www.okx.com/api/v5/market/history-candles"
BAR = "5m"
INST_ID = "BTC-USDT-SWAP"
LOOKBACK_DAYS = 30
BREAKOUT_LOOKBACK = 20
VOLUME_LOOKBACK = 20
HORIZON_BARS = 12
SL_PCT = 0.0045
TP_PCT = 0.0060
COST_PCT = 0.0006  # 0,03% slippage + 0,03% taxas, ida e volta aproximada
PULLBACK_HORIZON_BARS = 24
PULLBACK_TP_ATR = 1.7
PULLBACK_SL_ATR = 1.0


def fetch_candles(start_ms: int, end_ms: int) -> list[list[float]]:
    rows: dict[int, list[float]] = {}
    after = end_ms
    while True:
        query = urlencode({"instId": INST_ID, "bar": BAR, "limit": 100, "after": str(after)})
        request = Request(f"{ENDPOINT}?{query}", headers={"User-Agent": "genesis-readonly-backtest/1.0"})
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
        if payload.get("code") != "0":
            raise RuntimeError(f"OKX respondeu erro: {payload}")
        batch = payload.get("data") or []
        if not batch:
            break
        oldest = None
        for raw in batch:
            ts = int(raw[0])
            oldest = ts if oldest is None else min(oldest, ts)
            if start_ms <= ts <= end_ms:
                rows[ts] = [ts, float(raw[1]), float(raw[2]), float(raw[3]), float(raw[4]), float(raw[5])]
        if oldest is None or oldest <= start_ms or len(batch) < 100:
            break
        after = oldest - 1
        time.sleep(0.12)
    return [rows[ts] for ts in sorted(rows)]


def ema(values: list[float], period: int) -> list[float]:
    alpha = 2.0 / (period + 1.0)
    result: list[float] = []
    current = values[0]
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
    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period
    for i in range(period + 1, len(values)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period
        result[i] = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    return result


def atr(rows: list[list[float]], period: int = 14) -> list[float]:
    true_ranges = []
    previous_close = rows[0][4]
    for row in rows:
        high, low, close = row[2], row[3], row[4]
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
        previous_close = close
    values = []
    current = sum(true_ranges[:period]) / period
    for i, value in enumerate(true_ranges):
        if i < period:
            values.append(current)
        else:
            current = ((current * (period - 1)) + value) / period
            values.append(current)
    return values


def evaluate_pullback(rows: list[list[float]]) -> list[tuple[int, float]]:
    """Pullback no 5m alinhado à tendência EMA50/EMA200 agregada em 1h."""
    closes = [row[4] for row in rows]
    ema20 = ema(closes, 20)
    rsi14 = rsi(closes)
    atr14 = atr(rows)
    # Agrega candles de 5m em candles de 1h e replica a tendência para os 5m.
    hourly_closes = [closes[i] for i in range(11, len(closes), 12)]
    hourly_ema50 = ema(hourly_closes, 50)
    hourly_ema200 = ema(hourly_closes, 200)
    trend = [None] * len(rows)
    for hour_index, start in enumerate(range(0, len(rows) - 11, 12)):
        if hour_index >= 200:
            trend_value = "buy" if hourly_ema50[hour_index] > hourly_ema200[hour_index] else "sell"
            for j in range(start, start + 12):
                trend[j] = trend_value

    trades: list[tuple[int, float]] = []
    next_allowed = 0
    for i in range(201 * 12, len(rows) - PULLBACK_HORIZON_BARS):
        if i < next_allowed or trend[i] is None:
            continue
        close = closes[i]
        distance_to_ema = abs(close - ema20[i]) / close
        volume_avg = sum(row[5] for row in rows[i - VOLUME_LOOKBACK : i]) / VOLUME_LOOKBACK
        volume_ok = rows[i][5] >= volume_avg
        side = trend[i]
        if side == "buy":
            signal = close > ema20[i] and 45 <= rsi14[i] <= 65
        else:
            signal = close < ema20[i] and 35 <= rsi14[i] <= 55
        if not signal or distance_to_ema > 0.0025 or not volume_ok:
            continue
        entry = close
        stop_distance = max(atr14[i] * PULLBACK_SL_ATR, entry * 0.0015)
        target_distance = atr14[i] * PULLBACK_TP_ATR
        stop = entry - stop_distance if side == "buy" else entry + stop_distance
        target = entry + target_distance if side == "buy" else entry - target_distance
        pnl = None
        for j in range(i + 1, i + PULLBACK_HORIZON_BARS + 1):
            high, low = rows[j][2], rows[j][3]
            if side == "buy":
                if low <= stop:
                    pnl = (-stop_distance / entry) - COST_PCT
                    break
                if high >= target:
                    pnl = (target_distance / entry) - COST_PCT
                    break
            else:
                if high >= stop:
                    pnl = (-stop_distance / entry) - COST_PCT
                    break
                if low <= target:
                    pnl = (target_distance / entry) - COST_PCT
                    break
        if pnl is None:
            exit_price = closes[i + PULLBACK_HORIZON_BARS]
            pnl = ((exit_price - entry) / entry if side == "buy" else (entry - exit_price) / entry) - COST_PCT
        trades.append((i, pnl))
        next_allowed = i + PULLBACK_HORIZON_BARS
    return trades


def evaluate(rows: list[list[float]], use_rsi: bool, use_ema: bool) -> list[tuple[int, float]]:
    closes = [row[4] for row in rows]
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    rsi14 = rsi(closes)
    trades: list[tuple[int, float]] = []
    next_allowed = 0
    for i in range(max(BREAKOUT_LOOKBACK, 200, 15), len(rows) - HORIZON_BARS):
        if i < next_allowed:
            continue
        prior_high = max(row[2] for row in rows[i - BREAKOUT_LOOKBACK : i])
        prior_low = min(row[3] for row in rows[i - BREAKOUT_LOOKBACK : i])
        volume_avg = sum(row[5] for row in rows[i - VOLUME_LOOKBACK : i]) / VOLUME_LOOKBACK
        close = closes[i]
        direction = "buy" if close > prior_high else "sell" if close < prior_low else None
        if direction is None or rows[i][5] < volume_avg:
            continue
        # Regra de produção: o RSI precisa cruzar a linha de 50 no candle do sinal.
        if use_rsi and not ((direction == "buy" and rsi14[i - 1] <= 50 < rsi14[i]) or (direction == "sell" and rsi14[i - 1] >= 50 > rsi14[i])):
            continue
        if use_ema and not ((direction == "buy" and ema50[i] > ema200[i]) or (direction == "sell" and ema50[i] < ema200[i])):
            continue

        entry = close
        tp = entry * (1.0 + TP_PCT if direction == "buy" else 1.0 - TP_PCT)
        sl = entry * (1.0 - SL_PCT if direction == "buy" else 1.0 + SL_PCT)
        pnl = None
        for j in range(i + 1, i + HORIZON_BARS + 1):
            high, low = rows[j][2], rows[j][3]
            if direction == "buy":
                if low <= sl:
                    pnl = -SL_PCT - COST_PCT
                    break
                if high >= tp:
                    pnl = TP_PCT - COST_PCT
                    break
            else:
                if high >= sl:
                    pnl = -SL_PCT - COST_PCT
                    break
                if low <= tp:
                    pnl = TP_PCT - COST_PCT
                    break
        if pnl is None:
            exit_price = closes[i + HORIZON_BARS]
            pnl = ((exit_price - entry) / entry if direction == "buy" else (entry - exit_price) / entry) - COST_PCT
        trades.append((i, pnl))
        next_allowed = i + HORIZON_BARS
    return trades


def summarize(trades: list[tuple[int, float]]) -> dict:
    values = [value for _, value in sorted(trades)]
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
    wins = sum(value > 0 for value in values)
    return {
        "trades": len(values),
        "wins": wins,
        "losses": sum(value <= 0 for value in values),
        "win_rate_pct": round(100.0 * wins / len(values), 2) if values else 0.0,
        "net_return_pct": round(100.0 * sum(values), 3),
        "average_return_pct": round(100.0 * sum(values) / len(values), 3) if values else 0.0,
        "max_drawdown_pct": round(100.0 * max_drawdown, 3),
    }


def main() -> None:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)
    rows = fetch_candles(int(start.timestamp() * 1000), int(end.timestamp() * 1000))
    if len(rows) < 250:
        raise RuntimeError(f"Candles insuficientes: {len(rows)}")
    policies = {
        "breakout_puro": (False, False),
        "breakout_rsi": (True, False),
        "breakout_ema": (False, True),
        "breakout_rsi_ema": (True, True),
    }
    result = {
        "period_start_utc": datetime.fromtimestamp(rows[0][0] / 1000, timezone.utc).isoformat(),
        "period_end_utc": datetime.fromtimestamp(rows[-1][0] / 1000, timezone.utc).isoformat(),
        "instrument": INST_ID,
        "bar": BAR,
        "candles": len(rows),
        "method": {"breakout_lookback_bars": BREAKOUT_LOOKBACK, "horizon_bars": HORIZON_BARS, "sl_pct": SL_PCT, "tp_pct": TP_PCT, "cost_pct": COST_PCT},
        "policies": {name: summarize(evaluate(rows, *flags)) for name, flags in policies.items()},
    }
    result["method"].update({"pullback_horizon_bars": PULLBACK_HORIZON_BARS, "pullback_sl_atr": PULLBACK_SL_ATR, "pullback_tp_atr": PULLBACK_TP_ATR})
    result["policies"]["pullback_1h_ema_5m_rsi"] = summarize(evaluate_pullback(rows))
    output = Path(__file__).resolve().parents[1] / "resultados_backtest" / "rsi_ema_30d.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"Resultado salvo em: {output}")


if __name__ == "__main__":
    main()
