# regime_detector.py

import logging
import os
import numpy as np

class RegimeDetector:

    def __init__(self):
        self.last_diagnostics = {}

    def detectar(self, df, symbol="DESCONHECIDO"):
        symbol = (symbol or "DESCONHECIDO").strip().upper()

        logger = logging.getLogger(__name__)

        def _env_float(name: str, default: float) -> float:
            # Permite override por ativo: EX. REGIME4_ATR_MIN_BTCUSDT
            if symbol and symbol != "DESCONHECIDO":
                val = os.getenv(f"{name}_{symbol}")
                if val is None:
                    val = os.getenv(name)
            else:
                val = os.getenv(name)
            try:
                return float(val) if val is not None else float(default)
            except Exception:
                return float(default)

        # =========================
        # 1) VOLATILIDADE
        # =========================
        close = df["close"].iloc[-1]
        atr = df["ATR"].iloc[-1]
        atr_med = df["ATR"].rolling(20).mean().iloc[-1]
        if np.isnan(atr_med) or atr_med <= 0:
            atr_med = atr
        atr_pct = (atr / close) * 100
        atr_pct_med = (atr_med / close) * 100

        # =========================
        # 2) EMAs – FORÇA DA TENDÊNCIA
        # =========================
        ema_fast = df["EMA_12"].iloc[-1]
        ema_slow = df["EMA_26"].iloc[-1]
        ema50    = df["EMA_50"].iloc[-1]
        ema200   = df["EMA_200"].iloc[-1]

        incl_fast = abs(ema_fast - ema_slow) / close
        incl_slow = abs(ema50 - ema200) / close
        # A distancia entre EMAs nao basta para detectar uma tendencia recente.
        # Mede tambem a variacao das EMAs ao longo de alguns candles.
        slope_lookback = max(int(_env_float("REGIME_SLOPE_LOOKBACK", 5)), 1)
        if len(df) > slope_lookback:
            ema_fast_prev = float(df["EMA_12"].iloc[-1 - slope_lookback])
            ema50_prev = float(df["EMA_50"].iloc[-1 - slope_lookback])
        else:
            ema_fast_prev = float(df["EMA_12"].iloc[0])
            ema50_prev = float(df["EMA_50"].iloc[0])
        slope_fast = abs(float(ema_fast) - ema_fast_prev) / close
        slope_slow = abs(float(ema50) - ema50_prev) / close
        ema200_prev = float(df["EMA_200"].iloc[-1 - slope_lookback]) if len(df) > slope_lookback else float(df["EMA_200"].iloc[0])
        slope_ema200 = abs(float(ema200) - ema200_prev) / close

        trend_fast_min = _env_float("REGIME_TREND_FAST_MIN", 0.0025)
        trend_slow_min = _env_float("REGIME_TREND_SLOW_MIN", 0.004)
        trend_mod_fast_min = _env_float("REGIME_TREND_MOD_FAST_MIN", 0.0012)
        trend_mod_slow_min = _env_float("REGIME_TREND_MOD_SLOW_MIN", 0.002)
        trend_fast_slope_min = _env_float("REGIME_TREND_FAST_SLOPE_MIN", 0.0015)
        trend_slow_slope_min = _env_float("REGIME_TREND_SLOW_SLOPE_MIN", 0.0010)
        trend_mod_fast_slope_min = _env_float("REGIME_TREND_MOD_FAST_SLOPE_MIN", 0.0008)
        trend_mod_slow_slope_min = _env_float("REGIME_TREND_MOD_SLOW_SLOPE_MIN", 0.0005)
        lateral_slope_max = _env_float("REGIME_LATERAL_SLOPE_MAX", 0.0008)
        lateral_atr_max = _env_float("REGIME_LATERAL_ATR_MAX", 0.08)

        tendencia_forte = (
            (incl_fast > trend_fast_min and incl_slow > trend_slow_min)
            or (slope_fast > trend_fast_slope_min and slope_slow > trend_slow_slope_min)
        )
        tendencia_moderada = (
            incl_fast > trend_mod_fast_min
            or incl_slow > trend_mod_slow_min
            or slope_fast > trend_mod_fast_slope_min
            or slope_slow > trend_mod_slow_slope_min
        )
        lateral_suave = (
            incl_fast < 0.0010
            and slope_fast < lateral_slope_max
            and slope_slow < lateral_slope_max
            and atr_pct < lateral_atr_max
        )

        # =========================
        # 3) VOLUME RELATIVO
        # =========================
        volume = df["volume"].iloc[-1]
        vol_med = df["volume"].rolling(20).mean().iloc[-1]
        volume_ratio = volume / vol_med if vol_med > 0 else 1

        # =========================
        # 4) ESTRUTURA DO CANDLE
        # =========================
        high = df["high"].iloc[-1]
        low = df["low"].iloc[-1]
        open_ = df["open"].iloc[-1]

        range_total = high - low + 1e-9
        corpo_pct = abs(close - open_) / range_total

        # =========================
        # 5) DISTÂNCIA AO EMA200
        # =========================
        dist_ema200_pct = abs(close - ema200) / close * 100

        # =========================
        # LOGS
        # =========================
        logger.info(f"Regime slopes: EMA12/{slope_lookback}={slope_fast:.4f} | EMA50/{slope_lookback}={slope_slow:.4f}")
        logger.info("🔍 [REGIME] Diagnóstico do Mercado")
        logger.info(f"[ATR][REGIME] ATR%: {atr_pct:.2f}%")
        logger.info(f"• Inclinação Fast: {incl_fast:.4f}")
        logger.info(f"• Inclinação Slow: {incl_slow:.4f}")
        logger.info(f"• Volume Ratio: {volume_ratio:.2f}")
        logger.info(f"• Corpo%: {corpo_pct:.2f}")
        logger.info(f"• Distância EMA200%: {dist_ema200_pct:.2f}%")

        # =========================
        # CLASSIFICAÇÃO DO REGIME
        # =========================

        reg1_atr_min = _env_float("REGIME1_ATR_MIN", 0.12)
        reg1_vol_min = _env_float("REGIME1_VOL_MIN", 1.1)
        reg1_body_min = _env_float("REGIME1_BODY_MIN", 0.40)
        reg1_ema200_dist_min = _env_float("REGIME1_EMA200_DIST_MIN", 0.80)

        reg2_atr_min = _env_float("REGIME2_ATR_MIN", 0.10)
        reg2_vol_min = _env_float("REGIME2_VOL_MIN", 1.0)
        reg2_ema200_dist_min = _env_float("REGIME2_EMA200_DIST_MIN", 0.60)

        reg3_atr_min = _env_float("REGIME3_ATR_MIN", 0.04)
        reg3_atr_max = _env_float("REGIME3_ATR_MAX", 0.18)
        reg3_vol_min = _env_float("REGIME3_VOL_MIN", 0.80)
        reg3_vol_max = _env_float("REGIME3_VOL_MAX", 1.30)
        reg3_ema200_dist_max = _env_float("REGIME3_EMA200_DIST_MAX", 1.00)
        reg3_body_max = _env_float("REGIME3_BODY_MAX", 0.45)

        reg4_atr_min = _env_float("REGIME4_ATR_MIN", 0.60)
        reg4_atr_mult = _env_float("REGIME4_ATR_MULT", 2.0)
        reg4_atr_lim = max(reg4_atr_min, atr_pct_med * reg4_atr_mult)

        # Chaos has precedence over trend classification.  Otherwise a candle
        # with extreme volatility could be labelled as regime 1/2 first and
        # bypass the no-trade guard.
        regime4_rule = atr_pct > reg4_atr_lim
        regime1_rule = (
            tendencia_forte
            and atr_pct > reg1_atr_min
            and volume_ratio > reg1_vol_min
            and corpo_pct > reg1_body_min
            and dist_ema200_pct > reg1_ema200_dist_min
        )
        regime3_rule_path_1 = lateral_suave and not tendencia_moderada
        regime3_rule_path_2 = (
            atr_pct >= reg3_atr_min and atr_pct <= reg3_atr_max
            and volume_ratio >= reg3_vol_min and volume_ratio <= reg3_vol_max
            and dist_ema200_pct <= reg3_ema200_dist_max
            and corpo_pct <= reg3_body_max
            and not tendencia_moderada
        )
        regime2_explicit_rule = (
            tendencia_moderada
            and atr_pct > reg2_atr_min
            and dist_ema200_pct > reg2_ema200_dist_min
            and volume_ratio > reg2_vol_min
        )

        if regime4_rule:
            regime = 4
            winning_rule = "REGIME4_RULE"
        elif regime1_rule:
            regime = 1
            winning_rule = "REGIME1_RULE"
        elif regime3_rule_path_1:
            regime = 3
            winning_rule = "REGIME3_RULE_PATH_1"
        elif regime2_explicit_rule:
            regime = 2
            winning_rule = "REGIME2_EXPLICIT_RULE"
        elif regime3_rule_path_2:
            regime = 3 #3 
            winning_rule = "REGIME3_RULE_PATH_2"
        else:
            regime = 2
            winning_rule = "FALLBACK_TO_REGIME2"

        self.last_diagnostics = {
            "close": float(close), "atr": float(atr), "atr_percent": float(atr_pct),
            "ema12": float(ema_fast), "ema26": float(ema_slow), "ema50": float(ema50),
            "ema200": float(ema200), "ema12_ema26_distance": float(incl_fast),
            "ema50_ema200_distance": float(incl_slow), "ema12_slope": float(slope_fast),
            "ema50_slope": float(slope_slow), "ema200_slope": float(slope_ema200),
            "ema200_distance_percent": float(dist_ema200_pct), "volume": float(volume),
            "volume_average": float(vol_med), "volume_ratio": float(volume_ratio),
            "body_fraction": float(corpo_pct), "tendencia_forte": bool(tendencia_forte),
            "tendencia_moderada": bool(tendencia_moderada), "regime4_rule": bool(regime4_rule),
            "regime1_rule": bool(regime1_rule), "regime3_rule_path_1": bool(regime3_rule_path_1),
            "regime3_rule_path_2": bool(regime3_rule_path_2),
            "regime2_explicit_rule": bool(regime2_explicit_rule),
            "fallback_to_regime2": winning_rule == "FALLBACK_TO_REGIME2",
            "winning_rule": winning_rule,
        }

        logger.info(f"🏁 REGIME DETECTADO PARA {symbol}: {regime}")

        return regime
