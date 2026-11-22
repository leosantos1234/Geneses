"""Pullback Trend Following para entrada em 1h com contexto 4h/diário.

Este módulo somente produz sinais e métricas. A autorização final permanece no
funil, Commander, risco e write gate do worker.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Optional

import pandas as pd


STRATEGY_NAME = "PULLBACK_TREND_FOLLOWING"
ALLOWED_REGIMES = {1, 2}


def _backtest_parity_enabled() -> bool:
    return os.getenv("PULLBACK_BACKTEST_PARITY", "0").strip().lower() in {"1", "true", "yes", "on"}


def _regime_allowed(regime: Optional[int]) -> bool:
    allow_regime_4 = os.getenv("PULLBACK_ALLOW_REGIME_4", "0").strip().lower() in {"1", "true", "yes", "on"}
    if allow_regime_4:
        return regime is None or int(regime) in {1, 2, 3, 4}
    return regime is None or _backtest_parity_enabled() or int(regime) in ALLOWED_REGIMES


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return float(default)


@dataclass(frozen=True)
class PullbackSignal:
    ok: bool
    side: Optional[str]
    reason: str
    regime: Optional[int]
    entry: Optional[float]
    stop_loss: Optional[float]
    take_profit: Optional[float]
    trailing_distance: Optional[float]
    atr: Optional[float]
    score: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def _atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    previous = frame["close"].shift(1)
    true_range = pd.concat(
        [frame["high"] - frame["low"],
         (frame["high"] - previous).abs(),
         (frame["low"] - previous).abs()],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(period, min_periods=period).mean()


def _prepare(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"time", "open", "high", "low", "close", "volume"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"colunas_ausentes:{sorted(missing)}")
    out = frame.copy()
    out["time"] = pd.to_datetime(out["time"], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        out[column] = pd.to_numeric(out[column], errors="coerce")
    out = out.dropna(subset=["time", "open", "high", "low", "close", "volume"])
    return out.sort_values("time").drop_duplicates("time").reset_index(drop=True)


def _enrich(frame: pd.DataFrame) -> pd.DataFrame:
    out = _prepare(frame)
    out["ema20"] = out["close"].ewm(span=20, adjust=False, min_periods=20).mean()
    out["ema50"] = out["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    out["ema200"] = out["close"].ewm(span=200, adjust=False, min_periods=200).mean()
    out["atr"] = _atr(out)
    out["volume_sma20"] = out["volume"].rolling(20, min_periods=20).mean()
    delta = out["close"].diff()
    gain = delta.clip(lower=0).rolling(14, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).rolling(14, min_periods=14).mean()
    out["rsi14"] = 100.0 - (100.0 / (1.0 + gain / loss.replace(0, pd.NA)))
    return out


def _latest_asof(frame: pd.DataFrame, timestamp, closed_context: bool = True) -> Optional[pd.Series]:
    # Busca binária evita filtrar milhares de candles a cada candle de 1h.
    # Isso é importante porque o histórico de 36 meses é usado no ranking.
    times = frame["time"].array
    index = times.searchsorted(timestamp, side="right") - 1
    if closed_context:
        # Os arquivos/cache identificam candles pelo horário de abertura. O
        # candle imediatamente anterior é o último contexto inequivocamente
        # fechado no início do candle de entrada.
        index -= 1
    if index < 0:
        return None
    return frame.iloc[int(index)]


def _context_signal(one: pd.Series, four: pd.Series, day: pd.Series, regime: Optional[int]) -> PullbackSignal:
    values = [one.get("close"), one.get("atr"), one.get("ema20"), one.get("ema50"),
              one.get("volume_sma20"), one.get("rsi14"), four.get("ema50"),
              four.get("ema200"), day.get("ema20"), day.get("ema50")]
    if any(pd.isna(value) for value in values):
        return PullbackSignal(False, None, "indicadores_insuficientes", regime, None, None, None, None, None)

    entry = float(one["close"])
    atr = float(one["atr"])
    volume_ratio = _float_env("PULLBACK_VOLUME_MIN_RATIO", 0.8)
    stop_mult = _float_env("PULLBACK_STOP_ATR", 1.8)
    target_mult = _float_env("PULLBACK_TARGET_ATR", 3.0)
    trail_mult = _float_env("PULLBACK_TRAILING_ATR", 2.0)
    long_context = four.close > four.ema50 > four.ema200 and day.close > day.ema20 > day.ema50
    short_context = four.close < four.ema50 < four.ema200 and day.close < day.ema20 < day.ema50
    volume_ok = float(one.volume) >= float(one.volume_sma20) * volume_ratio
    touched_long = float(one.low) <= max(float(one.ema20), float(one.ema50))
    touched_short = float(one.high) >= min(float(one.ema20), float(one.ema50))
    bullish = touched_long and entry > float(one.ema20) and entry > float(one.open) and float(one.rsi14) >= 45 and float(one.rsi14) <= 68
    bearish = touched_short and entry < float(one.ema20) and entry < float(one.open) and float(one.rsi14) >= 32 and float(one.rsi14) <= 55
    if long_context and bullish and volume_ok:
        return PullbackSignal(True, "buy", "pullback_ema_tendencia_alta_4h_diario", regime, entry, entry - stop_mult * atr, entry + target_mult * atr, trail_mult * atr, atr, 3.0)
    if short_context and bearish and volume_ok:
        return PullbackSignal(True, "sell", "pullback_ema_tendencia_baixa_4h_diario", regime, entry, entry + stop_mult * atr, entry - target_mult * atr, trail_mult * atr, atr, 3.0)
    return PullbackSignal(False, None, "sem_pullback_com_confluencia", regime, entry, None, None, None, atr, 0.0)


def _avaliar_setup_entrada(df_entry: pd.DataFrame, df_4h: pd.DataFrame, df_daily: pd.DataFrame, regime: Optional[int] = None, entry_label: str = "1h") -> PullbackSignal:
    """Avalia somente candles fechados já fornecidos pelo worker."""
    if not _regime_allowed(regime):
        return PullbackSignal(False, None, "regime_nao_elegivel", int(regime), None, None, None, None, None)
    if len(df_entry) < 60 or len(df_4h) < 220 or len(df_daily) < 55:
        return PullbackSignal(False, None, f"dados_{entry_label}_4h_diario_insuficientes", regime, None, None, None, None, None)
    one = _enrich(df_entry)
    four = _enrich(df_4h)
    day = _enrich(df_daily)
    current = one.iloc[-1]
    current_four = _latest_asof(four, current["time"], closed_context=True)
    current_day = _latest_asof(day, current["time"], closed_context=True)
    if current_four is None or current_day is None:
        return PullbackSignal(False, None, "contexto_4h_diario_ausente", regime, None, None, None, None, None)
    previous = one.iloc[-2]
    signal = _context_signal(current, current_four, current_day, regime)
    if signal.ok and ((signal.side == "buy" and float(current.close) <= float(previous.close)) or (signal.side == "sell" and float(current.close) >= float(previous.close))):
        return PullbackSignal(False, signal.side, "fechamento_nao_confirma_movimento", regime, signal.entry, None, None, None, signal.atr)
    return signal


def avaliar_setup_entrada_1h(df_1h: pd.DataFrame, df_4h: pd.DataFrame, df_daily: pd.DataFrame, regime: Optional[int] = None) -> PullbackSignal:
    return _avaliar_setup_entrada(df_1h, df_4h, df_daily, regime=regime, entry_label="1h")


def avaliar_setup_entrada_4h(df_4h: pd.DataFrame, df_daily: pd.DataFrame, regime: Optional[int] = None) -> PullbackSignal:
    return _avaliar_setup_entrada(df_4h, df_4h, df_daily, regime=regime, entry_label="4h")


def _backtest_reference(df_1h: pd.DataFrame, df_4h: pd.DataFrame, df_daily: pd.DataFrame, regime: Optional[int], historical_validation):
    """Implementa o critério canônico usado no backtest AVAX de referência."""
    one = _enrich(df_1h)
    one["rsi14"] = one["rsi14"].fillna(50.0)
    four = _enrich(df_4h)
    day = _enrich(df_daily)

    # Mantém exatamente a convenção do backtest de referência: o contexto
    # 4h/diário é deslocado uma vela antes de ser anexado ao candle 1h.
    four_ctx = four[["time", "close", "ema20", "ema50", "ema200"]].rename(
        columns={c: f"four_{c}" for c in ("close", "ema20", "ema50", "ema200")}
    )
    day_ctx = day[["time", "close", "ema20", "ema50"]].rename(
        columns={c: f"day_{c}" for c in ("close", "ema20", "ema50")}
    )
    four_ctx["time"] = four_ctx["time"].shift(1)
    day_ctx["time"] = day_ctx["time"].shift(1)
    data = pd.merge_asof(one.sort_values("time"), four_ctx.dropna().sort_values("time"), on="time", direction="backward")
    data = pd.merge_asof(data.sort_values("time"), day_ctx.dropna().sort_values("time"), on="time", direction="backward")
    data = data.dropna(subset=["ema20", "ema50", "atr", "volume_sma20", "four_ema50", "four_ema200", "day_ema20", "day_ema50"]).reset_index(drop=True)

    stop_mult = _float_env("PULLBACK_STOP_ATR", 1.8)
    target_mult = _float_env("PULLBACK_TARGET_ATR", 3.0)
    trail_mult = _float_env("PULLBACK_TRAILING_ATR", 2.0)
    cooldown_after_stop = max(0, int(_float_env("PULLBACK_COOLDOWN_CANDLES", 3)))
    cost_per_trade = max(0.0, _float_env("PULLBACK_COST_PER_TRADE", 0.00095))
    position = None
    cooldown = 0
    trades = []
    for index, row in enumerate(data.itertuples(index=False)):
        high, low, close, atr = float(row.high), float(row.low), float(row.close), float(row.atr)
        if position is not None:
            exit_price = None
            reason = None
            if position["side"] == "buy":
                if low <= position["stop"]:
                    exit_price, reason = position["stop"], "stop"
                elif high >= position["target"]:
                    exit_price, reason = position["target"], "target"
            else:
                if high >= position["stop"]:
                    exit_price, reason = position["stop"], "stop"
                elif low <= position["target"]:
                    exit_price, reason = position["target"], "target"
            if exit_price is not None:
                pnl = ((exit_price - position["entry"]) / position["entry"] * 100.0) if position["side"] == "buy" else ((position["entry"] - exit_price) / position["entry"] * 100.0)
                trades.append({**position, "exit": exit_price, "exit_time": row.time, "exit_reason": reason, "pnl_pct": pnl, "lucro": pnl})
                cooldown = cooldown_after_stop if reason == "stop" else 0
                position = None
            elif position["side"] == "buy":
                position["stop"] = max(position["stop"], close - position["trail"])
            else:
                position["stop"] = min(position["stop"], close + position["trail"])
            continue
        if cooldown:
            cooldown -= 1
            continue
        previous = data.iloc[index - 1] if index else None
        if previous is None:
            continue
        long_context = row.four_close > row.four_ema50 > row.four_ema200 and row.day_close > row.day_ema20 > row.day_ema50
        short_context = row.four_close < row.four_ema50 < row.four_ema200 and row.day_close < row.day_ema20 < row.day_ema50
        touched_long = low <= float(row.ema20) or low <= float(row.ema50)
        touched_short = high >= float(row.ema20) or high >= float(row.ema50)
        long_rejection = touched_long and close > float(row.ema20) and close > float(row.open) and close > float(previous.close) and 45.0 <= float(row.rsi14) <= 68.0
        short_rejection = touched_short and close < float(row.ema20) and close < float(row.open) and close < float(previous.close) and 32.0 <= float(row.rsi14) <= 55.0
        volume_ok = float(row.volume) >= float(row.volume_sma20) * _float_env("PULLBACK_VOLUME_MIN_RATIO", 0.8)
        side = "buy" if long_context and long_rejection and volume_ok else "sell" if short_context and short_rejection and volume_ok else None
        if side == "buy":
            position = {"side": side, "entry": close, "entry_time": row.time, "stop": close - stop_mult * atr, "target": close + target_mult * atr, "trail": trail_mult * atr, "regime": regime}
        elif side == "sell":
            position = {"side": side, "entry": close, "entry_time": row.time, "stop": close + stop_mult * atr, "target": close - target_mult * atr, "trail": trail_mult * atr, "regime": regime}
    if position is not None:
        final = float(data.iloc[-1].close)
        pnl = ((final - position["entry"]) / position["entry"] * 100.0) if position["side"] == "buy" else ((position["entry"] - final) / position["entry"] * 100.0)
        trades.append({**position, "exit": final, "exit_time": data.iloc[-1].time, "exit_reason": "end", "pnl_pct": pnl, "lucro": pnl})
    pnls = [float(item["pnl_pct"]) for item in trades]
    balance = 1000.0
    peak = balance
    max_dd = 0.0
    for pnl in pnls:
        balance *= 1.0 + pnl / 100.0 - cost_per_trade
        peak = max(peak, balance)
        max_dd = min(max_dd, balance / peak - 1.0)
    # Este backtest é a referência histórica de 36 meses usada nos estudos
    # locais. O PnL de cada trade é uma variação percentual; portanto,
    # ``lucro_total`` não pode ser a soma desses percentuais em um contrato
    # que espera lucro monetário. O saldo composto é a métrica canônica.
    lucro_composto = balance - 1000.0
    custo_pullback_total = len(trades) * cost_per_trade
    return {
        "estrategia": STRATEGY_NAME,
        "sem_trade": not trades,
        "num_trades": len(trades),
        "lucro_total": lucro_composto,
        "lucro_total_pct_linear": sum(pnls),
        "lucro_bruto_pct": sum(pnls),
        "lucro_bruto": lucro_composto,
        "lucro_liquido_estimado": lucro_composto,
        "saldo_inicial": 1000.0,
        "saldo_final": balance,
        "retorno_composto_pct": (balance / 1000.0 - 1.0) * 100.0,
        "retorno_pct": (balance / 1000.0 - 1.0) * 100.0,
        "max_drawdown_pct": max_dd * 100.0,
        "custos_estimados_pct": custo_pullback_total * 100.0,
        "custos_simulados": custo_pullback_total,
        "custos_simulados_aplicados": True,
        "custos_modelo": "pullback_cost_per_trade_composto",
        "winrate": (sum(p > 0 for p in pnls) / len(pnls) * 100.0 if pnls else 0.0),
        "trade_log": trades,
        "regime": regime,
        "regimes_operados": [regime] if regime is not None else [],
        "backtest_valid": (not historical_validation or bool(historical_validation.get("valid"))),
        "historical_validation": historical_validation,
        "closed_candles_only": True,
        "criteria_reference": "pullback_trend_following_avax_36m",
    }


def backtest_entrada_1h(df_1h: pd.DataFrame, df_4h: pd.DataFrame, df_daily: pd.DataFrame, regime: Optional[int] = None, **_) -> dict:
    """Backtest sem look-ahead, usado apenas para ranking/evidência do funil."""
    historical_validation = None
    historical_enabled = (
        os.getenv("PULLBACK_HISTORICAL_BACKTEST_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
        or os.getenv("SWING_HISTORICAL_BACKTEST_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
    )
    if historical_enabled:
        try:
            from estrategia_entrada.swing_trend_following.historical_cache import validate_swing_history
            historical_validation = validate_swing_history(
                {"1h": df_1h, "4h": df_4h, "1d": df_daily},
                months=int(_float_env("SWING_HISTORICAL_MONTHS", 36)),
            )
        except Exception as exc:
            historical_validation = {"valid": False, "error": str(exc)}
        if not historical_validation.get("valid"):
            return {"estrategia": STRATEGY_NAME, "sem_trade": True, "motivo": "historico_pullback_insuficiente", "num_trades": 0, "backtest_valid": False, "historical_validation": historical_validation}
    if len(df_1h) < 60 or len(df_4h) < 220 or len(df_daily) < 55:
        return {"estrategia": STRATEGY_NAME, "sem_trade": True, "motivo": "dados_1h_4h_diario_insuficientes", "num_trades": 0, "backtest_valid": False, "historical_validation": historical_validation}
    return _backtest_reference(df_1h, df_4h, df_daily, regime, historical_validation)
    one = _enrich(df_1h)
    four = _enrich(df_4h)
    day = _enrich(df_daily)
    stop_mult = _float_env("PULLBACK_STOP_ATR", 1.8)
    target_mult = _float_env("PULLBACK_TARGET_ATR", 3.0)
    trail_mult = _float_env("PULLBACK_TRAILING_ATR", 2.0)
    cooldown_after_stop = max(0, int(_float_env("PULLBACK_COOLDOWN_CANDLES", 3)))
    position = None
    cooldown = 0
    trades: list[dict] = []
    for index in range(1, len(one)):
        current = one.iloc[index]
        if position is not None:
            high, low, close = float(current.high), float(current.low), float(current.close)
            exit_price = None
            reason = None
            if position["side"] == "buy":
                if low <= position["stop"]:
                    exit_price, reason = position["stop"], "stop"
                elif high >= position["target"]:
                    exit_price, reason = position["target"], "target"
            else:
                if high >= position["stop"]:
                    exit_price, reason = position["stop"], "stop"
                elif low <= position["target"]:
                    exit_price, reason = position["target"], "target"
            if exit_price is not None:
                pnl = ((exit_price - position["entry"]) / position["entry"] * 100.0) if position["side"] == "buy" else ((position["entry"] - exit_price) / position["entry"] * 100.0)
                trades.append({**position, "exit": exit_price, "exit_time": current["time"], "exit_reason": reason, "pnl_pct": pnl, "lucro": pnl})
                cooldown = cooldown_after_stop if reason == "stop" else 0
                position = None
            elif position["side"] == "buy":
                position["stop"] = max(position["stop"], close - position["trail"])
            else:
                position["stop"] = min(position["stop"], close + position["trail"])
            continue
        if cooldown:
            cooldown -= 1
            continue
        current_four = _latest_asof(four, current["time"], closed_context=True)
        current_day = _latest_asof(day, current["time"], closed_context=True)
        if current_four is None or current_day is None:
            continue
        previous = one.iloc[index - 1]
        signal = _context_signal(current, current_four, current_day, regime)
        if signal.ok and ((signal.side == "buy" and float(current.close) <= float(previous.close)) or (signal.side == "sell" and float(current.close) >= float(previous.close))):
            continue
        if signal.ok:
            position = {"side": signal.side, "entry": signal.entry, "entry_time": current["time"], "stop": signal.stop_loss, "target": signal.take_profit, "trail": signal.trailing_distance, "regime": regime}
    if position is not None:
        final = float(one.iloc[-1].close)
        pnl = ((final - position["entry"]) / position["entry"] * 100.0) if position["side"] == "buy" else ((position["entry"] - final) / position["entry"] * 100.0)
        trades.append({**position, "exit": final, "exit_time": one.iloc[-1]["time"], "exit_reason": "end", "pnl_pct": pnl, "lucro": pnl})
    pnls = [float(t["pnl_pct"]) for t in trades]
    return {"estrategia": STRATEGY_NAME, "sem_trade": not trades, "num_trades": len(trades), "lucro_total": sum(pnls), "winrate": (sum(p > 0 for p in pnls) / len(pnls) * 100.0 if pnls else 0.0), "trade_log": trades, "regime": regime, "regimes_operados": [regime] if regime is not None else [], "backtest_valid": (not historical_enabled or bool(historical_validation and historical_validation.get("valid"))), "historical_validation": historical_validation}
