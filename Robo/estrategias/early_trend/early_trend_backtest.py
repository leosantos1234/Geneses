import logging
import os
from dataclasses import dataclass

import pandas as pd

from regime.regime_detector import RegimeDetector
from regime.entry_profile import strategy_allowed
from utils.indicadores import calculate_indicators

from .early_trend_core import _get_bool_env, detectar_entrada_early_trend

logger = logging.getLogger(__name__)


@dataclass
class _Trade:
    side: str
    entry_i: int
    exit_i: int
    entry_price: float
    exit_price: float
    pnl_abs: float
    pnl_pct: float
    motivo_saida: str
    qty: float = 0.0
    entry_time: str | None = None
    exit_time: str | None = None
    regime: int | None = None


def backtest_early_trend(
    df_original: pd.DataFrame,
    direcao: str | None,
    capital_inicial: float = 10000.0,
    regime_override: int | None = None,
    symbol: str | None = None,
) -> dict:
    """
    Backtest mínimo e determinístico para Early Trend (Swing / Recuperação).
    - Não altera o RegimeDetector.
    - Só participa do ranking se gerar evidência mínima de trades (trade_log / num_trades).
    """
    if not _get_bool_env("EARLY_TREND_ENABLED", False):
        return {"estrategia": "EARLY_TREND", "sem_trade": True, "motivo": "desativada"}

    if df_original is None or df_original.empty or len(df_original) < 60:
        return {"estrategia": "EARLY_TREND", "sem_trade": True, "motivo": "df_curto"}

    df = df_original.copy().reset_index(drop=True)
    df = calculate_indicators(df)

    try:
        symbol_env = (symbol or os.getenv("SYMBOL", "") or os.getenv("ATIVOS", "") or os.getenv("PAR", "")).strip().upper()
        if regime_override in (1, 2, 3, 4):
            regime_atual = int(regime_override)
            logger.info(f"[EARLYTREND] Regime override aplicado: {regime_atual}")
        else:
            regime_atual = RegimeDetector().detectar(df, symbol=symbol_env or "DESCONHECIDO")
    except Exception as e:
        logger.warning(f"[EARLYTREND] Falha ao detectar regime, usando fallback 2: {e}")
        regime_atual = 2

    regime_dinamico = regime_override not in (1, 2, 3, 4) and _get_bool_env("BACKTEST_DYNAMIC_REGIME", True)
    if not strategy_allowed("EARLY_TREND", regime_atual) and not regime_dinamico:
        return {"estrategia": "EARLY_TREND", "sem_trade": True, "motivo": f"regime_{regime_atual}_bloqueado", "regime": regime_atual}

    # Respeita lado permitido do funil, quando existir.
    lado_permitido = direcao.lower() if isinstance(direcao, str) and direcao.lower() in ("buy", "sell") else None

    trades: list[_Trade] = []
    saldo = float(capital_inicial)
    risco_percentual = float(os.getenv("RISCO_PERCENTUAL", 0.004))
    in_pos = False
    entry_i = -1
    entry_price = 0.0
    side = None
    stop_loss = None
    take_profit = None
    pending_entry = None

    for i in range(30, len(df)):
        df_slice = df.iloc[: i + 1].copy()

        regime_candle = regime_atual
        if regime_dinamico:
            try:
                regime_candle = RegimeDetector().detectar(df_slice, symbol=symbol_env or "DESCONHECIDO")
            except Exception:
                regime_candle = regime_atual
            if not strategy_allowed("EARLY_TREND", regime_candle):
                continue

        if pending_entry is not None:
            if i < pending_entry["entry_i"]:
                continue
            if i == pending_entry["entry_i"]:
                pending_side = pending_entry["side"]
                pending_price = float(df["open"].iloc[i]) if "open" in df.columns else float(df["close"].iloc[i])
                pending_stop = float(pending_entry["stop_loss"])
                if (pending_side == "buy" and pending_stop >= pending_price) or (pending_side == "sell" and pending_stop <= pending_price):
                    pending_entry = None
                    continue
                entry_i = i
                entry_price = pending_price
                side = pending_side
                stop_loss = pending_stop
                take_profit = pending_entry.get("take_profit")
                in_pos = True
                pending_entry = None
                continue

        if not in_pos:
            entrada = detectar_entrada_early_trend(df_slice, regime_candle)
            if not entrada.ok or not entrada.side:
                continue

            if lado_permitido and entrada.side != lado_permitido:
                continue

            # Filtros opcionais: distancia do preco para EMA12 vs range mediano e pico de ATR
            try:
                ema_dist_max = float(os.getenv("EARLY_TREND_EMA12_DIST_MAX_MED20", "0") or 0.0)
                atr_spike_mult = float(os.getenv("EARLY_TREND_ATR_SPIKE_MULT", "0") or 0.0)
            except Exception:
                ema_dist_max = 0.0
                atr_spike_mult = 0.0

            if ema_dist_max > 0 or atr_spike_mult > 0:
                ema12_s = df_slice["EMA_12"] if "EMA_12" in df_slice.columns else df_slice.get("EMA12")
                close_s = df_slice["close"] if "close" in df_slice.columns else None
                high_s = df_slice["high"] if "high" in df_slice.columns else None
                low_s = df_slice["low"] if "low" in df_slice.columns else None
                atr_s = df_slice["ATR"] if "ATR" in df_slice.columns else None

                if ema_dist_max > 0 and ema12_s is not None and close_s is not None and high_s is not None and low_s is not None:
                    close = float(close_s.iloc[-1])
                    ranges = (high_s - low_s).tail(20)
                    if len(ranges) > 0:
                        med20 = float(ranges.median())
                        if med20 > 0:
                            ema12 = float(ema12_s.iloc[-1])
                            dist_ratio = abs(close - ema12) / med20
                            if dist_ratio > ema_dist_max:
                                continue

                if atr_spike_mult > 0 and atr_s is not None:
                    atr_cur = float(atr_s.iloc[-1])
                    atr_med = float(atr_s.tail(20).median()) if len(atr_s) >= 2 else atr_cur
                    if atr_med > 0 and (atr_cur / atr_med) > atr_spike_mult:
                        continue

            signal_price = float(df_slice["close"].iloc[-1])
            side = entrada.side
            signal_stop = float(entrada.stop_loss) if entrada.stop_loss is not None else None
            signal_take_profit = float(entrada.take_profit) if entrada.take_profit is not None else None

            # Stop precisa ser válido
            if signal_stop is None:
                continue
            if side == "buy" and signal_stop >= signal_price:
                continue
            if side == "sell" and signal_stop <= signal_price:
                continue

            entry_delay = max(1, int(os.getenv("BACKTEST_ENTRY_DELAY_CANDLES", "1")))
            pending_entry = {
                "entry_i": i + entry_delay,
                "side": side,
                "stop_loss": signal_stop,
                "take_profit": signal_take_profit,
                "regime": regime_candle,
            }
            continue

        # Gestão mínima: sai por stop, TP (se existir) ou perda de alinhamento EMA curto.
        close = float(df_slice["close"].iloc[-1])
        ema12_s = df_slice["EMA_12"] if "EMA_12" in df_slice.columns else df_slice.get("EMA12")
        ema26_s = df_slice["EMA_26"] if "EMA_26" in df_slice.columns else df_slice.get("EMA26")
        if ema12_s is None or ema26_s is None:
            motivo = "indicadores_ausentes"
            exit_i = i
            exit_price = close
            stop_distance = abs(entry_price - float(stop_loss)) if stop_loss is not None else 0.0
            qty = (saldo * risco_percentual / stop_distance) if stop_distance > 0 else 0.0
            pnl_abs = (exit_price - entry_price) * qty if side == "buy" else (entry_price - exit_price) * qty
            pnl_pct = ((exit_price - entry_price) / entry_price) * 100.0 if entry_price else 0.0
            saldo += pnl_abs
            trades.append(
                _Trade(
                    side=side,
                    entry_i=entry_i,
                    exit_i=exit_i,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    pnl_abs=pnl_abs,
                    pnl_pct=pnl_pct,
                    motivo_saida=motivo,
                    qty=qty,
                    entry_time=str(df["time"].iloc[entry_i]),
                    exit_time=str(df["time"].iloc[exit_i]),
                    regime=regime_candle,
                )
            )
            in_pos = False
            entry_i = -1
            entry_price = 0.0
            side = None
            stop_loss = None
            take_profit = None
            continue

        ema12 = float(ema12_s.iloc[-1])
        ema26 = float(ema26_s.iloc[-1])

        motivo = None
        candle_high = float(df_slice["high"].iloc[-1])
        candle_low = float(df_slice["low"].iloc[-1])
        if side == "buy":
            if stop_loss is not None and candle_low <= stop_loss:
                motivo = "stop"
            elif take_profit is not None and candle_high >= take_profit:
                motivo = "tp"
            elif ema12 < ema26:
                motivo = "perda_tendencia_curta"
        else:
            if stop_loss is not None and candle_high >= stop_loss:
                motivo = "stop"
            elif take_profit is not None and candle_low <= take_profit:
                motivo = "tp"
            elif ema12 > ema26:
                motivo = "perda_tendencia_curta"

        if motivo is None:
            continue

        exit_i = i
        exit_price = close
        stop_distance = abs(entry_price - float(stop_loss)) if stop_loss is not None else 0.0
        qty = (saldo * risco_percentual / stop_distance) if stop_distance > 0 else 0.0
        pnl_abs = (exit_price - entry_price) * qty if side == "buy" else (entry_price - exit_price) * qty
        pnl_pct = ((exit_price - entry_price) / entry_price) * 100.0 if entry_price else 0.0
        saldo += pnl_abs
        trades.append(
            _Trade(
                side=side,
                entry_i=entry_i,
                exit_i=exit_i,
                entry_price=entry_price,
                exit_price=exit_price,
                pnl_abs=pnl_abs,
                pnl_pct=pnl_pct,
                motivo_saida=motivo,
                qty=qty,
                entry_time=str(df["time"].iloc[entry_i]),
                exit_time=str(df["time"].iloc[exit_i]),
                regime=regime_candle,
            )
        )

        in_pos = False
        entry_i = -1
        entry_price = 0.0
        side = None
        stop_loss = None
        take_profit = None

    if not trades:
        return {"estrategia": "EARLY_TREND", "sem_trade": True, "motivo": "sem_trades"}

    saldo_inicial = float(capital_inicial)
    lucro_proxy = saldo - saldo_inicial
    saldo_final = saldo
    retorno_pct = (lucro_proxy / saldo_inicial * 100.0) if saldo_inicial else 0.0
    lucro_proxy = saldo - saldo_inicial
    saldo_final = saldo
    retorno_pct = (lucro_proxy / saldo_inicial * 100.0) if saldo_inicial else 0.0
    wins = sum(1 for t in trades if t.pnl_abs > 0)
    winrate = (wins / len(trades)) * 100.0 if trades else 0.0

    return {
        "estrategia": "EARLY_TREND",
        "direcao": (lado_permitido or trades[0].side),
        "lucro_total": float(lucro_proxy),
        "saldo_inicial": saldo_inicial,
        "saldo_final": float(saldo_final),
        "winrate": float(winrate),
        "retorno_pct": float(retorno_pct),
        "num_trades": int(len(trades)),
        "trade_log": [
            {
                "side": t.side,
                "entry_pos": t.entry_i,
                "exit_pos": t.exit_i,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "pnl_pct": t.pnl_pct,
                "motivo_saida": t.motivo_saida,
                "qty": t.qty,
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "regime": t.regime,
            }
            for t in trades
        ],
        "relatorio_completo": f"EARLY_TREND: trades={len(trades)} wins={wins} retorno_pct={retorno_pct:.2f}",
    }
