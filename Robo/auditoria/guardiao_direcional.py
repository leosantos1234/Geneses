import os
from typing import Any, Tuple
import math


def _last_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        if hasattr(value, "iloc"):
            return float(value.iloc[-1])
        return float(value)
    except Exception:
        return default


def _infer_from_close_series(close_series: Any) -> Tuple[str, str]:
    if close_series is None or not hasattr(close_series, "__len__") or len(close_series) < 210:
        return "UNKNOWN", "insufficient_close_history"
    try:
        s50 = close_series.rolling(50).mean()
        s200 = close_series.rolling(200).mean()
        sma50 = float(s50.iloc[-1])
        sma200 = float(s200.iloc[-1])
        sma50_prev = float(s50.iloc[-6]) if len(s50) >= 56 else float(s50.iloc[-2])
        slope = sma50 - sma50_prev
    except Exception:
        return "UNKNOWN", "sma_calc_failed"
    if sma50 < sma200 and slope < 0:
        return "BEAR", "sma50_lt_sma200_and_slope_neg"
    if sma50 > sma200 and slope > 0:
        return "BULL", "sma50_gt_sma200_and_slope_pos"
    return "LATERAL", "mixed_sma_state"


def _normalize_confidence(confidence_score: Any) -> float | None:
    """
    Normaliza confiança para escala 0..1.
    Aceita entrada em 0..1, 0..100 ou 0..10.
    """
    try:
        if confidence_score is None:
            return None
        val = float(confidence_score)
        if math.isnan(val) or math.isinf(val):
            return None
        if val < 0:
            return 0.0
        if val <= 1.0:
            return val
        if val <= 10.0:
            return val / 10.0
        if val <= 100.0:
            return val / 100.0
        return None
    except Exception:
        return None


def _infer_trend_from_ema_frame(indicators: Any) -> Tuple[str | None, str]:
    """
    Inferência robusta de tendência por EMA50 x EMA200 + inclinação da EMA50.
    Funciona com dict ou DataFrame.
    """
    try:
        ema50_series = indicators.get("EMA50")
        if ema50_series is None:
            ema50_series = indicators.get("EMA_50")
        ema200_series = indicators.get("EMA200")
        if ema200_series is None:
            ema200_series = indicators.get("EMA_200")
        close_series = indicators.get("close")

        ema50 = _last_float(ema50_series)
        ema200 = _last_float(ema200_series)
        close_now = _last_float(close_series)
        if ema50 is None or ema200 is None:
            return None, "ema_missing"

        slope_lookback = int(os.getenv("MACRO_SLOPE_LOOKBACK", "5"))
        slope_lookback = max(2, slope_lookback)
        slope = None
        try:
            if hasattr(ema50_series, "iloc") and len(ema50_series) > slope_lookback:
                slope = float(ema50_series.iloc[-1]) - float(ema50_series.iloc[-slope_lookback])
        except Exception:
            slope = None
        if slope is None:
            slope = _last_float(indicators.get("SMA_50_SLOPE"), 0.0)

        gap_pct = abs(ema50 - ema200) / max(abs(close_now or ema50), 1e-9)
        min_gap_pct = float(os.getenv("MACRO_MIN_GAP_PCT", "0.0007"))
        min_slope_pct = float(os.getenv("MACRO_MIN_SLOPE_PCT", "0.0001"))
        slope_pct = abs(slope) / max(abs(close_now or ema50), 1e-9)

        if gap_pct < min_gap_pct or slope_pct < min_slope_pct:
            return "LATERAL", "ema_gap_or_slope_low"
        if ema50 > ema200 and slope > 0:
            return "BULL", "ema50_gt_ema200_and_slope_pos"
        if ema50 < ema200 and slope < 0:
            return "BEAR", "ema50_lt_ema200_and_slope_neg"
        return "LATERAL", "ema_mixed"
    except Exception:
        return None, "ema_infer_failed"


def inferir_direcao_macro(indicators=None, market_data=None) -> Tuple[str, str]:
    indicators = indicators or {}

    regime_txt = str(indicators.get("regime") or "").strip().lower()
    tendencia_txt = str(indicators.get("tendencia") or indicators.get("tendencia_macro") or "").strip().lower()
    raw_txt = f"{regime_txt} {tendencia_txt}"
    if any(k in raw_txt for k in ("bear", "baixa", "down", "downtrend")):
        return "BEAR", "fields"
    if any(k in raw_txt for k in ("bull", "alta", "up", "uptrend")):
        return "BULL", "fields"
    if any(k in raw_txt for k in ("lateral", "range", "consolid", "neutra", "neutro")):
        return "LATERAL", "fields"

    macro_ema, macro_ema_src = _infer_trend_from_ema_frame(indicators)
    if macro_ema is not None:
        return macro_ema, macro_ema_src

    close_series = None
    if market_data is not None and hasattr(market_data, "__getitem__"):
        try:
            close_series = market_data["close"]
        except Exception:
            close_series = None
    if close_series is None and indicators is not None:
        close_series = indicators.get("close")
    if close_series is not None:
        return _infer_from_close_series(close_series)

    return "UNKNOWN", "no_directional_inputs"


def permitir_operacao(
    side: str | None,
    macro_regime: str,
    estrategia: str | None,
    confidence_score: Any = None,
) -> Tuple[bool, str, float]:
    side_norm = (side or "").strip().lower()
    if side_norm not in ("buy", "sell"):
        return False, "side_ausente_ou_invalido", 0.0

    regime_norm = (macro_regime or "UNKNOWN").strip().upper()
    estr_norm = (estrategia or "").strip().lower()
    is_mean_reversion = "mean_reversion" in estr_norm or estr_norm in ("mr", "reversao", "meanreversion")
    is_trend_breakout = any(k in estr_norm for k in ("breakout", "trend", "continuation", "bybit_existing"))

    unknown_policy = os.getenv("GUARDIAO_UNKNOWN_POLICY", "block").strip().lower()
    mr_factor = float(os.getenv("MR_COUNTERTREND_RISK_FACTOR", "0.5") or 0.5)
    allow_mr_countertrend = os.getenv("ALLOW_MR_COUNTERTREND", "1").strip().lower() in ("1", "true", "yes")
    trend_filter_mode = os.getenv("TREND_FILTER_MODE", "mandatory").strip().lower()

    allow_confidence_override = os.getenv("ALLOW_COUNTERTREND_CONFIDENCE_OVERRIDE", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    countertrend_min_conf = float(os.getenv("COUNTERTREND_MIN_CONFIDENCE", "0.90") or 0.90)
    countertrend_override_risk = float(os.getenv("COUNTERTREND_OVERRIDE_RISK_FACTOR", "0.5") or 0.5)

    conf_norm = _normalize_confidence(confidence_score)
    conf_override_ok = bool(
        allow_confidence_override
        and conf_norm is not None
        and conf_norm >= countertrend_min_conf
    )

    if regime_norm == "UNKNOWN":
        if unknown_policy == "allow":
            return True, "unknown_policy_allow", 1.0
        if unknown_policy == "reduce":
            return True, "unknown_policy_reduce_risk", 0.5
        return False, "unknown_policy_block", 0.0

    if regime_norm == "BEAR" and side_norm == "buy":
        if trend_filter_mode == "mandatory":
            if conf_override_ok:
                return True, "countertrend_override_confidence_em_bear", countertrend_override_risk
            return False, "bloqueado_buy_em_bear_filtro_obrigatorio", 0.0
        if is_mean_reversion and allow_mr_countertrend:
            return True, "mr_contratendencia_permitida_em_bear", mr_factor
        if is_trend_breakout:
            return False, "bloqueado_buy_em_bear_para_trend_breakout", 0.0
        return False, "bloqueado_buy_em_bear", 0.0

    if regime_norm == "BULL" and side_norm == "sell":
        if trend_filter_mode == "mandatory":
            if conf_override_ok:
                return True, "countertrend_override_confidence_em_bull", countertrend_override_risk
            return False, "bloqueado_sell_em_bull_filtro_obrigatorio", 0.0
        if is_mean_reversion and allow_mr_countertrend:
            return True, "mr_contratendencia_permitida_em_bull", mr_factor
        if is_trend_breakout:
            return False, "bloqueado_sell_em_bull_para_trend_breakout", 0.0
        return False, "bloqueado_sell_em_bull", 0.0

    return True, "alinhado_com_regime_macro", 1.0
