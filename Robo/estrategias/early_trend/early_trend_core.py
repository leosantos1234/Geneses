import logging
import os
from dataclasses import dataclass
from typing import Optional

import pandas as pd
from regime.entry_profile import strategy_allowed

logger = logging.getLogger(__name__)


def _get_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "y", "on")


def _get_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        return float(raw)
    except Exception:
        return float(default)


def _get_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return int(default)
    try:
        return int(raw)
    except Exception:
        return int(default)


def _col(df: pd.DataFrame, *names: str) -> Optional[pd.Series]:
    for name in names:
        if name in df.columns:
            return df[name]
    return None


@dataclass(frozen=True)
class EarlyTrendEligibility:
    ok: bool
    motivo: str
    side: Optional[str] = None


@dataclass(frozen=True)
class EarlyTrendEntry:
    ok: bool
    motivo: str
    side: Optional[str] = None
    modo: Optional[str] = None
    preco_referencia: Optional[float] = None
    tr: Optional[float] = None
    atr: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    anchor: Optional[float] = None
    estrutura_nivel: Optional[float] = None


def _calc_regime_features(df: pd.DataFrame):
    close_s = _col(df, "close")
    atr_s = _col(df, "ATR")
    ema12_s = _col(df, "EMA_12", "EMA12")
    ema26_s = _col(df, "EMA_26", "EMA26")
    ema50_s = _col(df, "EMA_50", "EMA50")
    ema200_s = _col(df, "EMA_200", "EMA200")
    volume_s = _col(df, "volume")

    if close_s is None or atr_s is None or ema12_s is None or ema26_s is None or ema50_s is None or ema200_s is None or volume_s is None:
        raise KeyError("Colunas mínimas ausentes para features (close/ATR/EMA_12/EMA_26/EMA_50/EMA_200/volume).")

    close = float(close_s.iloc[-1])
    atr = float(atr_s.iloc[-1])
    atr_pct = (atr / close) * 100 if close else 0.0

    ema_fast = float(ema12_s.iloc[-1])
    ema_slow = float(ema26_s.iloc[-1])
    ema50 = float(ema50_s.iloc[-1])
    ema200 = float(ema200_s.iloc[-1])

    incl_fast = abs(ema_fast - ema_slow) / close if close else 0.0
    incl_slow = abs(ema50 - ema200) / close if close else 0.0

    vol_med = float(volume_s.rolling(20).mean().iloc[-1]) if len(volume_s) >= 20 else float(volume_s.mean())
    volume = float(volume_s.iloc[-1])
    volume_ratio = volume / vol_med if vol_med > 0 else 1.0

    high_s = _col(df, "high")
    low_s = _col(df, "low")
    open_s = _col(df, "open")
    if high_s is None or low_s is None or open_s is None:
        raise KeyError("Colunas mínimas ausentes para estrutura do candle (open/high/low).")

    high = float(high_s.iloc[-1])
    low = float(low_s.iloc[-1])
    open_ = float(open_s.iloc[-1])
    range_total = (high - low) + 1e-9
    corpo_pct = abs(close - open_) / range_total

    dist_ema200_pct = abs(close - ema200) / close * 100 if close else 0.0

    return {
        "close": close,
        "atr": atr,
        "atr_pct": atr_pct,
        "ema12": ema_fast,
        "ema26": ema_slow,
        "ema50": ema50,
        "ema200": ema200,
        "incl_fast": incl_fast,
        "incl_slow": incl_slow,
        "volume_ratio": volume_ratio,
        "corpo_pct": corpo_pct,
        "dist_ema200_pct": dist_ema200_pct,
    }


def _pivot_points(series: pd.Series, kind: str, left: int, right: int) -> list[tuple[int, float]]:
    points: list[tuple[int, float]] = []
    values = series.values
    n = len(values)
    for i in range(left, n - right):
        window = values[i - left : i + right + 1]
        center = values[i]
        if kind == "low":
            if center == window.min():
                points.append((i, float(center)))
        else:
            if center == window.max():
                points.append((i, float(center)))
    return points


def _has_consecutive_move(values: list[float], direction: str, min_len: int) -> bool:
    if min_len <= 1:
        return bool(values)
    if len(values) < min_len:
        return False
    run = 1
    for i in range(1, len(values)):
        prev = float(values[i - 1])
        cur = float(values[i])
        ok = (cur > prev) if direction == "up" else (cur < prev)
        if ok:
            run += 1
            if run >= min_len:
                return True
        else:
            run = 1
    return False


def _check_recovery_phase(df: pd.DataFrame, side: str) -> tuple[bool, str, dict]:
    lookback = _get_int_env("EARLY_TREND_LOOKBACK", 80)
    pivot_left = _get_int_env("EARLY_TREND_PIVOT_LEFT", 2)
    pivot_right = _get_int_env("EARLY_TREND_PIVOT_RIGHT", 2)
    pivots_min = _get_int_env("EARLY_TREND_PIVOTS_MIN", 2)

    highs = _col(df, "high")
    lows = _col(df, "low")
    closes = _col(df, "close")
    ema12 = _col(df, "EMA_12", "EMA12")
    ema26 = _col(df, "EMA_26", "EMA26")
    atr_s = _col(df, "ATR")
    if highs is None or lows is None or closes is None or ema12 is None or ema26 is None or atr_s is None:
        return False, "colunas_ausentes", {}

    if len(df) < max(lookback + 5, 30):
        return False, "df_curto", {}

    janela = df.tail(lookback).reset_index(drop=True)
    highs_j = janela["high"]
    lows_j = janela["low"]
    closes_j = janela["close"]
    ema12_j = _col(janela, "EMA_12", "EMA12")
    ema26_j = _col(janela, "EMA_26", "EMA26")
    atr_j = _col(janela, "ATR")

    if ema12_j is None or ema26_j is None or atr_j is None:
        return False, "colunas_ausentes", {}

    close_atual = float(closes_j.iloc[-1])
    atr_atual = float(atr_j.iloc[-1])

    if side == "buy":
        anchor = float(lows_j.min())
        recovery_distance = (close_atual - anchor)
        recovery_pct = (recovery_distance / anchor) * 100 if anchor else 0.0

        min_recovery_pct = _get_float_env("EARLY_TREND_MIN_RECOVERY_PCT", 0.0)
        if recovery_pct <= min_recovery_pct:
            return False, "recovery_insuficiente", {"anchor": anchor, "recovery_pct": recovery_pct}

        pivots = _pivot_points(lows_j, "low", pivot_left, pivot_right)
        pivots = pivots[-max(pivots_min * 2, 6) :]
        pivot_vals = [p[1] for p in pivots]
        seq = _has_consecutive_move(pivot_vals, direction="up", min_len=pivots_min)
        if not seq:
            return False, "higher_lows_insuficiente", {"anchor": anchor, "pivots": pivot_vals}

        ema_ok = float(ema12_j.iloc[-1]) > float(ema26_j.iloc[-1])
        ema_slope_ok = float(ema12_j.iloc[-1]) > float(ema12_j.iloc[-3]) if len(ema12_j) >= 3 else True
        if not (ema_ok and ema_slope_ok):
            return False, "ema_nao_alinhada", {"ema_ok": ema_ok, "ema_slope_ok": ema_slope_ok}

        estrutura_nivel = float(highs_j.max())
        return True, "ok", {
            "anchor": anchor,
            "recovery_pct": recovery_pct,
            "atr": atr_atual,
            "estrutura_nivel": estrutura_nivel,
            "ultimo_pivot": pivot_vals[-1] if pivot_vals else None,
        }

    # sell
    anchor = float(highs_j.max())
    recovery_distance = (anchor - close_atual)
    recovery_pct = (recovery_distance / anchor) * 100 if anchor else 0.0

    min_recovery_pct = _get_float_env("EARLY_TREND_MIN_RECOVERY_PCT", 0.0)
    if recovery_pct <= min_recovery_pct:
        return False, "recovery_insuficiente", {"anchor": anchor, "recovery_pct": recovery_pct}

    pivots = _pivot_points(highs_j, "high", pivot_left, pivot_right)
    pivots = pivots[-max(pivots_min * 2, 6) :]
    pivot_vals = [p[1] for p in pivots]
    seq = _has_consecutive_move(pivot_vals, direction="down", min_len=pivots_min)
    if not seq:
        return False, "lower_highs_insuficiente", {"anchor": anchor, "pivots": pivot_vals}

    ema_ok = float(ema12_j.iloc[-1]) < float(ema26_j.iloc[-1])
    ema_slope_ok = float(ema12_j.iloc[-1]) < float(ema12_j.iloc[-3]) if len(ema12_j) >= 3 else True
    if not (ema_ok and ema_slope_ok):
        return False, "ema_nao_alinhada", {"ema_ok": ema_ok, "ema_slope_ok": ema_slope_ok}

    estrutura_nivel = float(lows_j.min())
    return True, "ok", {
        "anchor": anchor,
        "recovery_pct": recovery_pct,
        "atr": atr_atual,
        "estrutura_nivel": estrutura_nivel,
        "ultimo_pivot": pivot_vals[-1] if pivot_vals else None,
    }


def avaliar_elegibilidade_early_trend(df: pd.DataFrame, regime: int) -> EarlyTrendEligibility:
    if not _get_bool_env("EARLY_TREND_ENABLED", False):
        return EarlyTrendEligibility(False, "desativada")

    if not strategy_allowed("EARLY_TREND", regime):
        return EarlyTrendEligibility(False, f"regime_{regime}_bloqueado")

    try:
        f = _calc_regime_features(df)
    except Exception as e:
        return EarlyTrendEligibility(False, f"features_erro:{e}")

    incl_fast_min = _get_float_env("EARLY_TREND_INCL_FAST_MIN", 0.0012)
    incl_slow_min = _get_float_env("EARLY_TREND_INCL_SLOW_MIN", 0.0020)
    tendencia_moderada = (f["incl_fast"] > incl_fast_min) or (f["incl_slow"] > incl_slow_min)
    if not tendencia_moderada:
        return EarlyTrendEligibility(False, "tendencia_moderada_false")

    corpo_min = _get_float_env("EARLY_TREND_CORPO_PCT_MIN", 0.0)
    if f["corpo_pct"] <= corpo_min:
        return EarlyTrendEligibility(False, "corpo_pct_baixo")

    atr_pct_min = _get_float_env("EARLY_TREND_ATR_PCT_MIN", 0.0)
    atr_pct_max = _get_float_env("EARLY_TREND_ATR_PCT_MAX", 1e9)
    if not (atr_pct_min <= f["atr_pct"] <= atr_pct_max):
        return EarlyTrendEligibility(False, "atr_pct_fora_faixa")

    vol_ratio_min = _get_float_env("EARLY_TREND_VOLUME_RATIO_MIN", 0.0)
    if f["volume_ratio"] < vol_ratio_min:
        return EarlyTrendEligibility(False, "volume_ratio_baixo")

    # Filtros opcionais: distancia do preco para EMA12 vs range mediano e pico de ATR
    ema_dist_max = _get_float_env("EARLY_TREND_EMA12_DIST_MAX_MED20", 0.0)
    atr_spike_mult = _get_float_env("EARLY_TREND_ATR_SPIKE_MULT", 0.0)
    if ema_dist_max > 0 or atr_spike_mult > 0:
        close_s = _col(df, "close")
        ema12_s = _col(df, "EMA_12", "EMA12")
        high_s = _col(df, "high")
        low_s = _col(df, "low")
        atr_s = _col(df, "ATR")

        if ema_dist_max > 0:
            if close_s is None or ema12_s is None or high_s is None or low_s is None:
                return EarlyTrendEligibility(False, "filtro_dist_ema_indisp")
            ranges = (high_s - low_s).tail(20)
            if len(ranges) > 0:
                med20 = float(ranges.median())
                if med20 > 0:
                    dist_ratio = abs(float(close_s.iloc[-1]) - float(ema12_s.iloc[-1])) / med20
                    if dist_ratio > ema_dist_max:
                        return EarlyTrendEligibility(False, "dist_ema12_alta")

        if atr_spike_mult > 0:
            if atr_s is None:
                return EarlyTrendEligibility(False, "filtro_atr_indisp")
            atr_cur = float(atr_s.iloc[-1])
            atr_med = float(atr_s.tail(20).median()) if len(atr_s) >= 2 else atr_cur
            if atr_med > 0 and (atr_cur / atr_med) > atr_spike_mult:
                return EarlyTrendEligibility(False, "atr_spike")

    # Determina lado a partir do alinhamento EMA curto.
    side: Optional[str] = None
    if f["ema12"] > f["ema26"]:
        side = "buy"
    elif f["ema12"] < f["ema26"]:
        side = "sell"

    if side is None:
        return EarlyTrendEligibility(False, "lado_indefinido")

    ok_fase, motivo_fase, _ = _check_recovery_phase(df, side)
    if not ok_fase:
        return EarlyTrendEligibility(False, f"fase_{motivo_fase}", side=side)

    return EarlyTrendEligibility(True, "ok", side=side)


def detectar_entrada_early_trend(df: pd.DataFrame, regime: int) -> EarlyTrendEntry:
    elig = avaliar_elegibilidade_early_trend(df, regime)
    if not elig.ok or not elig.side:
        return EarlyTrendEntry(False, elig.motivo, side=elig.side)

    side = elig.side
    ok_fase, _, detalhes = _check_recovery_phase(df, side)
    if not ok_fase:
        return EarlyTrendEntry(False, "fase_nao_ok", side=side)

    close_s = _col(df, "close")
    open_s = _col(df, "open")
    high_s = _col(df, "high")
    low_s = _col(df, "low")
    ema_fast_s = _col(df, "EMA_12", "EMA12")
    tr_s = _col(df, "TrueRange")
    atr_s = _col(df, "ATR")
    if close_s is None or open_s is None or high_s is None or low_s is None or ema_fast_s is None:
        return EarlyTrendEntry(False, "colunas_ausentes", side=side)

    close = float(close_s.iloc[-1])
    open_ = float(open_s.iloc[-1])
    high = float(high_s.iloc[-1])
    low = float(low_s.iloc[-1])
    ema_fast = float(ema_fast_s.iloc[-1])

    tr = None
    atr = None
    if tr_s is not None:
        tr = float(tr_s.iloc[-1])
    if atr_s is not None:
        atr = float(atr_s.iloc[-1])
    if tr is None and atr is not None:
        tr = float(atr)
    if tr is None or tr <= 0:
        tr = abs(high - low)

    # MODO A: pullback na EMA rápida + fechamento de retomada
    if side == "buy":
        if low <= ema_fast and close > ema_fast and close >= open_:
            stop = detalhes.get("ultimo_pivot") or detalhes.get("anchor")
            if stop is None:
                return EarlyTrendEntry(False, "stop_indefinido", side=side)
            tp = detalhes.get("estrutura_nivel")
            return EarlyTrendEntry(
                True,
                "ok",
                side=side,
                modo="PULLBACK_EMA",
                preco_referencia=close,
                tr=tr,
                atr=atr,
                stop_loss=float(stop),
                take_profit=float(tp) if tp is not None else None,
                anchor=float(detalhes.get("anchor")) if detalhes.get("anchor") is not None else None,
                estrutura_nivel=float(detalhes.get("estrutura_nivel")) if detalhes.get("estrutura_nivel") is not None else None,
            )
    else:
        if high >= ema_fast and close < ema_fast and close <= open_:
            stop = detalhes.get("ultimo_pivot") or detalhes.get("anchor")
            if stop is None:
                return EarlyTrendEntry(False, "stop_indefinido", side=side)
            tp = detalhes.get("estrutura_nivel")
            return EarlyTrendEntry(
                True,
                "ok",
                side=side,
                modo="PULLBACK_EMA",
                preco_referencia=close,
                tr=tr,
                atr=atr,
                stop_loss=float(stop),
                take_profit=float(tp) if tp is not None else None,
                anchor=float(detalhes.get("anchor")) if detalhes.get("anchor") is not None else None,
                estrutura_nivel=float(detalhes.get("estrutura_nivel")) if detalhes.get("estrutura_nivel") is not None else None,
            )

    # MODO B: retomada de microestrutura (rompimento curto)
    micro_window = _get_int_env("EARLY_TREND_MICRO_WINDOW", 6)
    if len(df) >= micro_window + 2:
        prev_close = float(close_s.iloc[-2])
        janela = df.tail(micro_window + 1).iloc[:-1]
        if side == "buy":
            micro_res = float(janela["high"].max())
            if prev_close <= micro_res and close > micro_res:
                stop = detalhes.get("ultimo_pivot") or detalhes.get("anchor")
                tp = detalhes.get("estrutura_nivel")
                return EarlyTrendEntry(
                    True,
                    "ok",
                    side=side,
                    modo="RETOMADA_MICRO",
                    preco_referencia=micro_res,
                    tr=tr,
                    atr=atr,
                    stop_loss=float(stop) if stop is not None else None,
                    take_profit=float(tp) if tp is not None else None,
                    anchor=float(detalhes.get("anchor")) if detalhes.get("anchor") is not None else None,
                    estrutura_nivel=float(detalhes.get("estrutura_nivel")) if detalhes.get("estrutura_nivel") is not None else None,
                )
        else:
            micro_sup = float(janela["low"].min())
            if prev_close >= micro_sup and close < micro_sup:
                stop = detalhes.get("ultimo_pivot") or detalhes.get("anchor")
                tp = detalhes.get("estrutura_nivel")
                return EarlyTrendEntry(
                    True,
                    "ok",
                    side=side,
                    modo="RETOMADA_MICRO",
                    preco_referencia=micro_sup,
                    tr=tr,
                    atr=atr,
                    stop_loss=float(stop) if stop is not None else None,
                    take_profit=float(tp) if tp is not None else None,
                    anchor=float(detalhes.get("anchor")) if detalhes.get("anchor") is not None else None,
                    estrutura_nivel=float(detalhes.get("estrutura_nivel")) if detalhes.get("estrutura_nivel") is not None else None,
                )

    return EarlyTrendEntry(False, "sem_sinal", side=side)
