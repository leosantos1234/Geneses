"""Swing trend following para uso em 4h/diário.

O módulo é deliberadamente independente do executor da exchange. Ele produz
um plano/sinal; a autorização final continua pertencendo ao funil, regime,
risco e idempotência do worker.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np
import pandas as pd


STRATEGY_NAME = "SWING_TREND_FOLLOWING"
ALLOWED_REGIMES = {1, 2}


def _regime_allowed(regime: Optional[int]) -> bool:
    """Allow Pullback regime 4 only when explicitly enabled by configuration."""
    if regime is None:
        return True
    value = int(regime)
    if value in ALLOWED_REGIMES:
        return True
    return value == 4 and os.getenv("PULLBACK_ALLOW_REGIME_4", "0").strip().lower() in ("1", "true", "yes", "on")


def _numeric_env(name: str, default: float) -> float:
    import os

    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return float(default)


def _integer_env(name: str, default: int) -> int:
    import os

    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return int(default)


@dataclass(frozen=True)
class SwingSignal:
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


def preparar_timeframes(df: pd.DataFrame, source_timeframe: str = "1h") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Converte candles intradiários em 4h e diário sem olhar o futuro."""
    required = {"time", "open", "high", "low", "close", "volume"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"Colunas ausentes: {sorted(missing)}")
    work = df.copy()
    work["time"] = pd.to_datetime(work["time"], utc=True)
    work = work.sort_values("time").drop_duplicates("time").set_index("time")
    work = work[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce").dropna()
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    four_hour = work.resample("4h", label="right", closed="right").agg(agg).dropna()
    daily = work.resample("1D", label="right", closed="right").agg(agg).dropna()
    return four_hour.reset_index(), daily.reset_index()


def carregar_timeframes_swing(session, symbol: str) -> dict[str, pd.DataFrame]:
    """Carrega 1h, 4h e diário do mesmo adaptador usado pelo worker.

    A função não envia ordens. Os limites são configuráveis porque a EMA200
    de 4h e o filtro diário precisam de uma janela maior que o breakout de 5m.
    """
    import os

    if (
        os.getenv("SWING_HISTORICAL_BACKTEST_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
        or os.getenv("PULLBACK_HISTORICAL_BACKTEST_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
    ):
        from .historical_cache import load_swing_history

        return load_swing_history(symbol, months=int(os.getenv("SWING_HISTORICAL_MONTHS", "36")))

    def _load(interval: str, limit: int) -> pd.DataFrame:
        response = session.get_kline(
            category="linear",
            symbol=symbol,
            interval=interval,
            limit=limit,
        )
        if not isinstance(response, dict) or response.get("retCode") != 0:
            message = response.get("retMsg") if isinstance(response, dict) else "resposta_invalida"
            raise RuntimeError(f"falha_kline_{interval}:{message}")
        rows = response.get("result", {}).get("list", [])
        frame = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume", "turnover"])
        if frame.empty:
            return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
        frame["time"] = pd.to_datetime(pd.to_numeric(frame["time"], errors="coerce"), unit="ms", utc=True)
        for column in ["open", "high", "low", "close", "volume"]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return (
            frame[["time", "open", "high", "low", "close", "volume"]]
            .dropna()
            .drop_duplicates("time")
            .sort_values("time")
            .reset_index(drop=True)
        )

    entry_1h = _load(os.getenv("SWING_ENTRY_INTERVAL", "60"), int(os.getenv("SWING_ENTRY_LIMIT", "1500")))
    four_hour = _load(os.getenv("SWING_4H_INTERVAL", "240"), int(os.getenv("SWING_4H_LIMIT", "300")))
    daily = _load(os.getenv("SWING_DAILY_INTERVAL", "D"), int(os.getenv("SWING_DAILY_LIMIT", "100")))
    return {"1h": entry_1h, "4h": four_hour, "1d": daily}


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    previous_close = df["close"].shift(1)
    true_range = pd.concat(
        [df["high"] - df["low"], (df["high"] - previous_close).abs(), (df["low"] - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(period, min_periods=period).mean()


def _trend_frame(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy().sort_values("time").reset_index(drop=True)
    work["ema20"] = work["close"].ewm(span=20, adjust=False).mean()
    work["ema50"] = work["close"].ewm(span=50, adjust=False).mean()
    work["ema200"] = work["close"].ewm(span=200, adjust=False).mean()
    work["atr"] = _atr(work)
    work["prior_high20"] = work["high"].shift(1).rolling(20, min_periods=20).max()
    work["prior_low20"] = work["low"].shift(1).rolling(20, min_periods=20).min()
    return work


def classificar_regime_swing(df_4h: pd.DataFrame, df_daily: pd.DataFrame) -> int:
    """Classifica o regime no horizonte da estratégia, não no candle de 5m.

    0 = dados insuficientes, 1 = tendência forte, 2 = tendência moderada,
    3 = lateralidade, 4 = volatilidade anormal. O regime 4h/diário é autoridade do swing;
    o regime operacional de 5m não entra nesta decisão.
    """
    if len(df_4h) < 220 or len(df_daily) < 55:
        # 0 é "indeterminado por falta de dados"; não confundir com o regime
        # 4, que significa volatilidade anormal com dados suficientes.
        return 0
    four = _trend_frame(df_4h)
    day = _trend_frame(df_daily)
    return _classificar_regime_frames(four, day)


def _classificar_regime_frames(four: pd.DataFrame, day: pd.DataFrame) -> int:
    """Classifica frames já enriquecidos, evitando recalcular indicadores."""
    if len(four) < 220 or len(day) < 55:
        return 0
    current = four.iloc[-1]
    daily_current = day.iloc[-1]
    if any(pd.isna(current.get(key)) for key in ("atr", "ema50", "ema200")):
        return 0
    atr_pct = float(current["atr"] / current["close"] * 100.0) if current["close"] else 0.0
    if atr_pct >= _numeric_env("SWING_REGIME4_ATR_PCT", 3.0):
        return 4
    long_aligned = current.close > current.ema50 > current.ema200 and daily_current.close > daily_current.ema20 > daily_current.ema50
    short_aligned = current.close < current.ema50 < current.ema200 and daily_current.close < daily_current.ema20 < daily_current.ema50
    if long_aligned or short_aligned:
        return 1
    four_trend = current.close > current.ema50 or current.close < current.ema50
    daily_trend = daily_current.close > daily_current.ema20 or daily_current.close < daily_current.ema20
    return 2 if four_trend and daily_trend else 3


def avaliar_setup(
    df_4h: pd.DataFrame,
    df_daily: pd.DataFrame,
    regime: Optional[int] = None,
    atr_stop_mult: float = 2.0,
    atr_target_mult: float = 4.0,
    atr_trailing_mult: float = 2.5,
    breakout_min_atr: Optional[float] = None,
    breakout_volume_ratio: Optional[float] = None,
    breakout_confirm_candles: Optional[int] = None,
) -> SwingSignal:
    """Avalia apenas candles já fechados e retorna um plano sem enviar ordem."""
    if not _regime_allowed(regime):
        return SwingSignal(False, None, "regime_nao_elegivel", int(regime), None, None, None, None, None)
    if len(df_4h) < 220 or len(df_daily) < 55:
        return SwingSignal(False, None, "dados_4h_diario_insuficientes", regime, None, None, None, None, None)

    four = _trend_frame(df_4h)
    day = _trend_frame(df_daily)
    breakout_min_atr = _numeric_env("SWING_BREAKOUT_MIN_ATR", 0.0) if breakout_min_atr is None else float(breakout_min_atr)
    breakout_volume_ratio = _numeric_env("SWING_BREAKOUT_VOLUME_RATIO", 0.0) if breakout_volume_ratio is None else float(breakout_volume_ratio)
    breakout_confirm_candles = _integer_env("SWING_BREAKOUT_CONFIRM_CANDLES", 1) if breakout_confirm_candles is None else int(breakout_confirm_candles)
    breakout_confirm_candles = max(1, breakout_confirm_candles)
    four["volume_sma20"] = four["volume"].rolling(20, min_periods=20).mean()
    current = four.iloc[-1]
    daily_current = day.iloc[-1]
    values = [current["close"], current["atr"], daily_current["ema20"], daily_current["ema50"]]
    if any(pd.isna(value) for value in values):
        return SwingSignal(False, None, "indicadores_insuficientes", regime, None, None, None, None, None)

    long_trend = current.close > current.ema50 > current.ema200 and daily_current.close > daily_current.ema20 > daily_current.ema50
    short_trend = current.close < current.ema50 < current.ema200 and daily_current.close < daily_current.ema20 < daily_current.ema50
    recent = four.tail(breakout_confirm_candles)
    long_break = (
        current.close >= current.prior_high20 + breakout_min_atr * current.atr
        and (breakout_volume_ratio <= 0 or current.volume >= current.volume_sma20 * breakout_volume_ratio)
        and all(row.close >= row.prior_high20 + breakout_min_atr * row.atr for _, row in recent.iterrows())
    )
    short_break = (
        current.close <= current.prior_low20 - breakout_min_atr * current.atr
        and (breakout_volume_ratio <= 0 or current.volume >= current.volume_sma20 * breakout_volume_ratio)
        and all(row.close <= row.prior_low20 - breakout_min_atr * row.atr for _, row in recent.iterrows())
    )
    entry = float(current.close)
    atr = float(current.atr)
    if long_trend and long_break:
        return SwingSignal(True, "buy", "tendencia_alta_breakout_4h_diario", regime, entry, entry - atr_stop_mult * atr, entry + atr_target_mult * atr, atr_trailing_mult * atr, atr, 3.0)
    if short_trend and short_break:
        return SwingSignal(True, "sell", "tendencia_baixa_breakdown_4h_diario", regime, entry, entry + atr_stop_mult * atr, entry - atr_target_mult * atr, atr_trailing_mult * atr, atr, 3.0)
    return SwingSignal(False, None, "sem_confluencia_tendencia_breakout", regime, entry, None, None, None, atr, 0.0)


def avaliar_setup_entrada_1h(
    df_1h: pd.DataFrame,
    df_4h: pd.DataFrame,
    df_daily: pd.DataFrame,
    regime: Optional[int] = None,
    breakout_min_atr: Optional[float] = None,
    breakout_volume_ratio: Optional[float] = None,
    breakout_confirm_candles: Optional[int] = None,
) -> SwingSignal:
    """Usa 4h/diário para direção e 1h somente para o gatilho."""
    if not _regime_allowed(regime):
        return SwingSignal(False, None, "regime_nao_elegivel", int(regime), None, None, None, None, None)
    if len(df_1h) < 40 or len(df_4h) < 220 or len(df_daily) < 55:
        return SwingSignal(False, None, "dados_1h_4h_diario_insuficientes", regime, None, None, None, None, None)

    entry = _trend_frame(df_1h)
    four = _trend_frame(df_4h)
    day = _trend_frame(df_daily)
    current = entry.iloc[-1]
    current_4h = four.iloc[-1]
    current_day = day.iloc[-1]
    values = [current.close, current.atr, current_4h.ema50, current_4h.ema200, current_day.ema20, current_day.ema50]
    if any(pd.isna(value) for value in values):
        return SwingSignal(False, None, "indicadores_insuficientes", regime, None, None, None, None, None)

    breakout_min_atr = _numeric_env("SWING_BREAKOUT_MIN_ATR", 0.0) if breakout_min_atr is None else float(breakout_min_atr)
    breakout_volume_ratio = _numeric_env("SWING_BREAKOUT_VOLUME_RATIO", 0.0) if breakout_volume_ratio is None else float(breakout_volume_ratio)
    breakout_confirm_candles = _integer_env("SWING_BREAKOUT_CONFIRM_CANDLES", 1) if breakout_confirm_candles is None else int(breakout_confirm_candles)
    breakout_confirm_candles = max(1, breakout_confirm_candles)
    entry["volume_sma20"] = entry["volume"].rolling(20, min_periods=20).mean()
    recent = entry.tail(breakout_confirm_candles)

    long_trend = current_4h.close > current_4h.ema50 > current_4h.ema200 and current_day.close > current_day.ema20 > current_day.ema50
    short_trend = current_4h.close < current_4h.ema50 < current_4h.ema200 and current_day.close < current_day.ema20 < current_day.ema50
    long_break = (
        current.close >= current.prior_high20 + breakout_min_atr * current.atr
        and (breakout_volume_ratio <= 0 or current.volume >= current.volume_sma20 * breakout_volume_ratio)
        and all(row.close >= row.prior_high20 + breakout_min_atr * row.atr for _, row in recent.iterrows())
    )
    short_break = (
        current.close <= current.prior_low20 - breakout_min_atr * current.atr
        and (breakout_volume_ratio <= 0 or current.volume >= current.volume_sma20 * breakout_volume_ratio)
        and all(row.close <= row.prior_low20 - breakout_min_atr * row.atr for _, row in recent.iterrows())
    )
    entry_price = float(current.close)
    atr = float(current.atr)
    if long_trend and long_break:
        return SwingSignal(True, "buy", "tendencia_4h_diario_breakout_1h", regime, entry_price, entry_price - 2.0 * atr, entry_price + 4.0 * atr, 2.5 * atr, atr, 3.0)
    if short_trend and short_break:
        return SwingSignal(True, "sell", "tendencia_4h_diario_breakdown_1h", regime, entry_price, entry_price + 2.0 * atr, entry_price - 4.0 * atr, 2.5 * atr, atr, 3.0)
    return SwingSignal(False, None, "sem_confluencia_4h_diario_breakout_1h", regime, entry_price, None, None, None, atr, 0.0)


def backtest_entrada_1h(
    df_1h: pd.DataFrame,
    df_4h: pd.DataFrame,
    df_daily: pd.DataFrame,
    regime: Optional[int] = None,
    breakout_min_atr: Optional[float] = None,
    breakout_volume_ratio: Optional[float] = None,
    breakout_confirm_candles: Optional[int] = None,
    paper_entry_mode: Optional[str] = None,
    paper_entry_ttl_candles: Optional[int] = None,
    paper_entry_price_offset_atr: Optional[float] = None,
    stop_atr: float = 2.0,
    target_atr: float = 4.0,
    trailing_atr: float = 2.5,
    entry_volume_max_ratio: Optional[float] = None,
) -> dict:
    """Backtest do gatilho 1h com tendência confirmada em 4h/diário."""
    historical_validation = None
    historical_enabled = os.getenv("SWING_HISTORICAL_BACKTEST_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
    if historical_enabled:
        from .historical_cache import validate_swing_history
        historical_validation = validate_swing_history(
            {"1h": df_1h, "4h": df_4h, "1d": df_daily},
            months=_integer_env("SWING_HISTORICAL_MONTHS", 36),
        )
        if not historical_validation["valid"]:
            return {
                "estrategia": STRATEGY_NAME,
                "sem_trade": True,
                "motivo": "historico_swing_insuficiente",
                "num_trades": 0,
                "backtest_valid": False,
                "historical_validation": historical_validation,
            }
    if len(df_1h) < 60 or len(df_4h) < 240 or len(df_daily) < 60:
        return {
            "estrategia": STRATEGY_NAME,
            "sem_trade": True,
            "motivo": "dados_1h_4h_diario_insuficientes",
            "num_trades": 0,
            "backtest_valid": False,
            "historical_validation": historical_validation,
        }
    breakout_min_atr = _numeric_env("SWING_BREAKOUT_MIN_ATR", 0.0) if breakout_min_atr is None else float(breakout_min_atr)
    breakout_volume_ratio = _numeric_env("SWING_BREAKOUT_VOLUME_RATIO", 0.0) if breakout_volume_ratio is None else float(breakout_volume_ratio)
    breakout_confirm_candles = _integer_env("SWING_BREAKOUT_CONFIRM_CANDLES", 1) if breakout_confirm_candles is None else int(breakout_confirm_candles)
    breakout_confirm_candles = max(1, breakout_confirm_candles)
    paper_entry_mode = (paper_entry_mode or os.getenv("SWING_PAPER_ENTRY_MODE", "IMMEDIATE")).strip().upper()
    paper_entry_ttl_candles = _integer_env("SWING_PAPER_ENTRY_TTL_CANDLES", 1) if paper_entry_ttl_candles is None else int(paper_entry_ttl_candles)
    paper_entry_ttl_candles = max(1, paper_entry_ttl_candles)
    paper_entry_price_offset_atr = _numeric_env("SWING_PAPER_ENTRY_PRICE_OFFSET_ATR", 0.0) if paper_entry_price_offset_atr is None else float(paper_entry_price_offset_atr)
    post_only = paper_entry_mode == "POST_ONLY"
    stop_atr = max(0.1, float(stop_atr))
    target_atr = max(0.1, float(target_atr))
    trailing_atr = max(0.1, float(trailing_atr))
    entry_volume_max_ratio = 0.0 if entry_volume_max_ratio is None else max(0.0, float(entry_volume_max_ratio))
    one = _trend_frame(df_1h)
    four = _trend_frame(df_4h)
    day = _trend_frame(df_daily)
    one["volume_sma20"] = one["volume"].rolling(20, min_periods=20).mean()
    position = None
    pending_order = None
    trades = []
    signal_count = 0
    filled_count = 0
    unfilled_count = 0

    def _build_position(order, fill_time):
        entry_price = float(order["entry"])
        atr = float(order["atr"])
        return {
            "side": order["side"],
            "entry": entry_price,
            "entry_time": fill_time,
            "signal_time": order["signal_time"],
            "regime": order["regime"],
            "stop": entry_price - stop_atr * atr if order["side"] == "buy" else entry_price + stop_atr * atr,
            "target": entry_price + target_atr * atr if order["side"] == "buy" else entry_price - target_atr * atr,
            "trail": trailing_atr * atr,
            "entry_atr_pct": order.get("entry_atr_pct"),
            "entry_volume_ratio": order.get("entry_volume_ratio"),
            "breakout_distance_atr": order.get("breakout_distance_atr"),
            "trend_strength_4h_pct": order.get("trend_strength_4h_pct"),
        }

    for index in range(40, len(one)):
        current = one.iloc[index]
        four_view = four[four["time"] <= current["time"]]
        day_view = day[day["time"] <= current["time"]]
        if len(four_view) < 220 or len(day_view) < 55:
            continue
        if pending_order is not None:
            pending_order["age"] += 1
            high, low = float(current.high), float(current.low)
            can_fill = ((pending_order["side"] == "buy" and low <= pending_order["entry"]) or
                        (pending_order["side"] == "sell" and high >= pending_order["entry"]))
            if can_fill:
                position = _build_position(pending_order, current["time"])
                pending_order = None
                filled_count += 1
                # A fill is evaluated starting on the next candle. This avoids
                # assuming that an order filled and hit its exit in the same
                # OHLC candle without knowing the intrabar path.
                continue
            if pending_order["age"] >= paper_entry_ttl_candles:
                pending_order = None
                unfilled_count += 1
            else:
                continue
        if position is not None:
            high, low = float(current.high), float(current.low)
            stop, target = float(position["stop"]), float(position["target"])
            exit_price = None
            exit_reason = None
            if position["side"] == "buy":
                if low <= stop:
                    exit_price, exit_reason = stop, "stop"
                elif high >= target:
                    exit_price, exit_reason = target, "target"
            else:
                if high >= stop:
                    exit_price, exit_reason = stop, "stop"
                elif low <= target:
                    exit_price, exit_reason = target, "target"
            if exit_price is not None:
                pnl = ((exit_price - position["entry"]) / position["entry"] * 100) if position["side"] == "buy" else ((position["entry"] - exit_price) / position["entry"] * 100)
                trades.append({"side": position["side"], "entry": position["entry"], "exit": exit_price, "pnl_pct": pnl, "entry_time": position["entry_time"], "exit_time": current["time"], "exit_reason": exit_reason, "regime": position["regime"], "entry_atr_pct": position.get("entry_atr_pct"), "entry_volume_ratio": position.get("entry_volume_ratio"), "breakout_distance_atr": position.get("breakout_distance_atr"), "trend_strength_4h_pct": position.get("trend_strength_4h_pct")})
                position = None
            elif position["side"] == "buy":
                position["stop"] = max(stop, float(current.close) - position["trail"])
            else:
                position["stop"] = min(stop, float(current.close) + position["trail"])
            continue
        regime_at_candle = _classificar_regime_frames(four_view, day_view)
        current_4h = four_view.iloc[-1]
        current_day = day_view.iloc[-1]
        recent = one.iloc[max(0, index - breakout_confirm_candles + 1): index + 1]
        long_trend = current_4h.close > current_4h.ema50 > current_4h.ema200 and current_day.close > current_day.ema20 > current_day.ema50
        short_trend = current_4h.close < current_4h.ema50 < current_4h.ema200 and current_day.close < current_day.ema20 < current_day.ema50
        volume_cap_ok = entry_volume_max_ratio <= 0 or (pd.notna(current.volume_sma20) and current.volume <= current.volume_sma20 * entry_volume_max_ratio)
        long_break = current.close >= current.prior_high20 + breakout_min_atr * current.atr and (breakout_volume_ratio <= 0 or current.volume >= current.volume_sma20 * breakout_volume_ratio) and volume_cap_ok and all(row.close >= row.prior_high20 + breakout_min_atr * row.atr for _, row in recent.iterrows())
        short_break = current.close <= current.prior_low20 - breakout_min_atr * current.atr and (breakout_volume_ratio <= 0 or current.volume >= current.volume_sma20 * breakout_volume_ratio) and volume_cap_ok and all(row.close <= row.prior_low20 - breakout_min_atr * row.atr for _, row in recent.iterrows())
        signal_side = "buy" if regime_at_candle in ALLOWED_REGIMES and long_trend and long_break else "sell" if regime_at_candle in ALLOWED_REGIMES and short_trend and short_break else None
        if signal_side:
            signal_count += 1
            entry_price = float(current.close)
            atr = float(current.atr)
            prior_level = float(current.prior_high20 if signal_side == "buy" else current.prior_low20)
            breakout_distance_atr = ((entry_price - prior_level) / atr if signal_side == "buy" else (prior_level - entry_price) / atr) if atr else None
            volume_ratio = (float(current.volume) / float(current.volume_sma20)) if pd.notna(current.volume_sma20) and float(current.volume_sma20) > 0 else None
            trend_strength_4h_pct = ((float(current_4h.close) / float(current_4h.ema200)) - 1.0) * 100.0 if signal_side == "buy" else ((float(current_4h.ema200) / float(current_4h.close)) - 1.0) * 100.0
            order_metadata = {"entry_atr_pct": (atr / entry_price * 100.0) if entry_price else None, "entry_volume_ratio": volume_ratio, "breakout_distance_atr": breakout_distance_atr, "trend_strength_4h_pct": trend_strength_4h_pct}
            if post_only:
                # The offset is applied away from the market to represent a
                # passive limit. The following candles decide whether it was
                # actually touched; queue position/partial fills are not
                # knowable from OHLC data and are intentionally not invented.
                entry_price = entry_price - paper_entry_price_offset_atr * atr if signal_side == "buy" else entry_price + paper_entry_price_offset_atr * atr
                pending_order = {"side": signal_side, "entry": entry_price, "atr": atr, "signal_time": current["time"], "regime": regime_at_candle, "age": 0, **order_metadata}
            else:
                position = _build_position({"side": signal_side, "entry": entry_price, "atr": atr, "signal_time": current["time"], "regime": regime_at_candle, **order_metadata}, current["time"])
    if position is not None:
        final_price = float(one.iloc[-1].close)
        pnl = ((final_price - position["entry"]) / position["entry"] * 100) if position["side"] == "buy" else ((position["entry"] - final_price) / position["entry"] * 100)
        trades.append({"side": position["side"], "entry": position["entry"], "exit": final_price, "pnl_pct": pnl, "entry_time": position["entry_time"], "exit_time": one.iloc[-1]["time"], "exit_reason": "end", "regime": position["regime"], "entry_atr_pct": position.get("entry_atr_pct"), "entry_volume_ratio": position.get("entry_volume_ratio"), "breakout_distance_atr": position.get("breakout_distance_atr"), "trend_strength_4h_pct": position.get("trend_strength_4h_pct")})
    if pending_order is not None:
        unfilled_count += 1
    pnls = [float(item["pnl_pct"]) for item in trades]
    maker_fee = _numeric_env("BACKTEST_MAKER_FEE_PCT_PER_SIDE", 0.0002)
    taker_fee = _numeric_env("BACKTEST_TAKER_FEE_PCT_PER_SIDE", 0.0005)
    slippage = _numeric_env("SWING_PAPER_EXIT_SLIPPAGE_PCT_PER_SIDE", 0.0002)
    estimated_cost_pct = filled_count * (maker_fee + taker_fee + slippage) * 100.0 if post_only else 0.0
    gross_pnl = sum(pnls)
    return {
        "estrategia": STRATEGY_NAME,
        "sem_trade": not trades,
        "num_trades": len(trades),
        "lucro_total": gross_pnl,
        "lucro_liquido_estimado": gross_pnl - estimated_cost_pct,
        "custos_estimados_pct": estimated_cost_pct,
        "winrate": sum(x > 0 for x in pnls) / len(pnls) * 100 if pnls else 0,
        "trade_log": trades,
        "regime": regime,
        "regimes_operados": sorted({int(item["regime"]) for item in trades if item.get("regime") in {1, 2, 3, 4}}),
        "paper_entry_mode": paper_entry_mode,
        "fill_model": "next_candle_touch" if post_only else "immediate",
        "num_sinais": signal_count,
        "num_preenchidas": filled_count,
        "num_nao_preenchidas": unfilled_count,
        "taxa_preenchimento_pct": filled_count / signal_count * 100 if signal_count else 0.0,
        "backtest_valid": (not historical_enabled or bool(historical_validation and historical_validation["valid"])),
        "historical_validation": historical_validation,
    }


def backtest(
    df_4h: pd.DataFrame,
    df_daily: pd.DataFrame,
    regime: Optional[int] = None,
    risk_reward: float = 2.0,
    dynamic_regime: bool = True,
    breakout_min_atr: Optional[float] = None,
    breakout_volume_ratio: Optional[float] = None,
    breakout_confirm_candles: Optional[int] = None,
) -> dict:
    """Backtest simples, sem look-ahead, para validar a estratégia antes do funil."""
    if len(df_4h) < 240 or len(df_daily) < 60:
        return {"estrategia": STRATEGY_NAME, "sem_trade": True, "motivo": "dados_insuficientes", "num_trades": 0}
    four = _trend_frame(df_4h)
    day = _trend_frame(df_daily)
    trades = []
    position = None
    for index in range(220, len(four)):
        current = four.iloc[index]
        daily = day[day["time"] <= current["time"]].iloc[-1] if not day[day["time"] <= current["time"]].empty else None
        if daily is None or position is not None:
            if position is not None:
                high, low = float(current.high), float(current.low)
                stop = float(position["stop"])
                target = float(position["target"])
                exit_price = None
                exit_reason = None
                if position["side"] == "buy":
                    # O candle é avaliado com o stop vigente. O trailing só
                    # será atualizado após o fechamento, para o próximo candle.
                    if low <= stop:
                        exit_price, exit_reason = stop, "stop"
                    elif high >= target:
                        exit_price, exit_reason = target, "target"
                else:
                    if high >= stop:
                        exit_price, exit_reason = stop, "stop"
                    elif low <= target:
                        exit_price, exit_reason = target, "target"
                if exit_price is not None:
                    pnl_pct = ((exit_price - position["entry"]) / position["entry"] * 100) if position["side"] == "buy" else ((position["entry"] - exit_price) / position["entry"] * 100)
                    trades.append({
                        "side": position["side"], "entry": position["entry"], "exit": exit_price,
                        "pnl_pct": pnl_pct, "entry_time": position["entry_time"],
                        "exit_time": current["time"], "exit_reason": exit_reason,
                        "regime": position["regime"],
                    })
                    position = None
                elif position["side"] == "buy":
                    position["stop"] = max(stop, float(current.close) - position["trail"])
                else:
                    position["stop"] = min(stop, float(current.close) + position["trail"])
            continue
        current_view = four.iloc[: index + 1]
        daily_view = day[day["time"] <= current["time"]]
        regime_at_candle = regime
        if dynamic_regime:
            regime_at_candle = classificar_regime_swing(current_view, daily_view)
        signal = avaliar_setup(
            current_view,
            daily_view,
            regime=regime_at_candle,
            breakout_min_atr=breakout_min_atr,
            breakout_volume_ratio=breakout_volume_ratio,
            breakout_confirm_candles=breakout_confirm_candles,
        )
        if signal.ok and position is None:
            position = {
                "side": signal.side,
                "entry": signal.entry,
                "entry_time": current["time"],
                "regime": regime_at_candle,
                "stop": signal.stop_loss,
                "target": signal.take_profit,
                "trail": signal.trailing_distance,
            }
    if position is not None:
        final_price = float(four.iloc[-1].close)
        pnl_pct = ((final_price - position["entry"]) / position["entry"] * 100) if position["side"] == "buy" else ((position["entry"] - final_price) / position["entry"] * 100)
        trades.append({
            "side": position["side"], "entry": position["entry"], "exit": final_price,
            "pnl_pct": pnl_pct, "entry_time": position["entry_time"],
            "exit_time": four.iloc[-1]["time"], "exit_reason": "end",
            "regime": position["regime"],
        })
    pnl = [float(item["pnl_pct"]) for item in trades]
    return {
        "estrategia": STRATEGY_NAME,
        "sem_trade": not trades,
        "num_trades": len(trades),
        "lucro_total": sum(pnl),
        "winrate": (sum(value > 0 for value in pnl) / len(pnl) * 100 if pnl else 0),
        "trade_log": trades,
        "regime": regime,
        "regimes_operados": sorted({int(item["regime"]) for item in trades if item.get("regime") in {1, 2, 3, 4}}),
    }
