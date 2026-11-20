import logging
import math
import os
import time
import json
from datetime import datetime
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from regime.regime_detector import RegimeDetector
from regime.entry_profile import strategy_allowed
from utils.indicadores import calculate_indicators
from execution.execution_gate import executar_ordem_com_validacao

from .early_trend_core import _get_bool_env, _get_float_env, _get_int_env, detectar_entrada_early_trend
from utils.utils_telegram import send_telegram_message

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EarlyTrendTickResult:
    status: str  # PENDING | FILLED | EXPIRED | INVALIDATED | DISABLED | NO_SIGNAL | ERROR
    order_id: Optional[str] = None
    fill_price: Optional[float] = None
    qty: Optional[float] = None
    message: str = ""


class EarlyTrendEngine:
    def __init__(self, *, session, symbol: str) -> None:
        self._session = session
        self._symbol = (symbol or "").upper()
        self._order_id: Optional[str] = None
        self._side: Optional[str] = None
        self._armed_ts: Optional[float] = None
        self._expires_ts: Optional[float] = None
        self._stop_loss: Optional[float] = None
        self._take_profit: Optional[float] = None
        self._trailing_stop: Optional[float] = None

    def _get_last_price(self) -> Optional[float]:
        try:
            ticker = self._session.get_tickers(category="linear", symbol=self._symbol)
            return float(ticker["result"]["list"][0]["lastPrice"])
        except Exception as e:
            logger.debug("[EARLYTREND] Falha ao obter lastPrice: %s", e)
            return None

    def _log_entry_checklist(self, *, decision: str, primary_reason: str, checks: list[dict]) -> None:
        if str(os.getenv("ENABLE_ENTRY_CHECKLIST_LOGS", "1")).strip().lower() not in {"1", "true", "yes", "y", "on"}:
            return
        passed = [c for c in (checks or []) if str(c.get("status", "")).upper() == "PASS"]
        failed = [c for c in (checks or []) if str(c.get("status", "")).upper() == "FAIL"]
        try:
            logger.info(
                "[ENTRY_CHECKLIST] strategy=EARLY_TREND symbol=%s decision=%s reason=%s pass=%d fail=%d",
                self._symbol,
                str(decision),
                str(primary_reason),
                len(passed),
                len(failed),
            )
            for item in passed:
                name = str(item.get("name", "unknown"))
                value = item.get("value", None)
                message = item.get("message", None)
                detail = message if message is not None else value
                if detail is None:
                    logger.info("  PASS  %s", name)
                else:
                    logger.info("  PASS  %s | %s", name, detail)
            for item in failed:
                name = str(item.get("name", "unknown"))
                value = item.get("value", None)
                message = item.get("message", None)
                detail = message if message is not None else value
                if detail is None:
                    logger.info("  FAIL  %s", name)
                else:
                    logger.info("  FAIL  %s | %s", name, detail)
        except Exception:
            return

    def _notify_telegram(self, message: str) -> None:
        try:
            send_telegram_message(message)
        except Exception as e:
            logger.warning("[EARLYTREND] Falha ao enviar Telegram: %s", e)

    @property
    def has_pending_order(self) -> bool:
        return bool(self._order_id)

    def _interval_seconds(self, interval: str) -> int:
        try:
            return int(float(str(interval).strip())) * 60
        except Exception:
            return 300

    def _obter_precisao(self) -> tuple[float, float, float]:
        tick_size, qty_step, min_qty = 0.01, 0.001, 0.0
        try:
            info = self._session.get_instruments_info(category="linear", symbol=self._symbol)
            item = info.get("result", {}).get("list", [{}])[0]
            tick_size = float(item.get("priceFilter", {}).get("tickSize", tick_size))
            qty_step = float(item.get("lotSizeFilter", {}).get("qtyStep", qty_step))
            min_qty = float(item.get("lotSizeFilter", {}).get("minOrderQty", min_qty))
        except Exception as e:
            logger.warning(f"[EARLYTREND] Não foi possível obter precisão de {self._symbol}: {e}")
        return tick_size, qty_step, min_qty

    def _round_step(self, valor: float, step: float) -> float:
        if step <= 0:
            return valor
        casas = len(str(step).split(".")[-1].rstrip("0"))
        return round(round(valor / step) * step, casas)

    def _ceil_step(self, valor: float, step: float) -> float:
        if step <= 0:
            return valor
        return math.ceil(valor / step) * step

    def _place_stop_limit(self, *, side: str, qty: float, trigger_price: float, stop_loss: float, take_profit: Optional[float]) -> Optional[str]:
        tick_size, qty_step, min_qty = self._obter_precisao()
        trigger = self._round_step(float(trigger_price), tick_size)
        qty_adj = self._round_step(float(qty), qty_step)

        if min_qty and qty_adj < min_qty:
            logger.warning("[EARLYTREND] Qty abaixo do minimo | qty=%.8f min=%.8f", qty_adj, min_qty)
            qty_adj = self._ceil_step(min_qty, qty_step)
            logger.info("[EARLYTREND] Qty ajustada para minimo | qty=%.8f", qty_adj)

        if trigger <= 0 or qty_adj <= 0:
            logger.warning(f"[EARLYTREND] STOP_LIMIT invalida | trigger={trigger} qty={qty_adj}")
            return None

        # A ordem condicional deve manter o trigger acima do ultimo preco em BUY
        # e abaixo do ultimo preco em SELL para evitar uma entrada imediata.
        side_norm = str(side or "").strip().lower()
        last_price = self._get_last_price()
        if last_price and last_price > 0:
            raw_gap = os.getenv(
                "EARLY_TREND_STOP_LIMIT_MIN_GAP_PCT",
                os.getenv("BREAKOUT_STOP_LIMIT_MIN_GAP_PCT", "0.0002"),
            )
            try:
                min_gap_pct = float(raw_gap or 0.0)
            except Exception:
                min_gap_pct = 0.0002
            if min_gap_pct < 0:
                min_gap_pct = 0.0

            tick_gap_pct = float(tick_size) / max(float(last_price), 1.0) if tick_size else 0.0
            gap_pct = max(float(min_gap_pct), float(tick_gap_pct))

            trigger_before = float(trigger)
            if side_norm == "buy" and float(trigger) <= float(last_price):
                trigger = self._round_step(float(last_price) * (1.0 + gap_pct), tick_size)
                if float(trigger) <= float(last_price) and tick_size:
                    trigger = self._round_step(float(last_price) + float(tick_size), tick_size)
            elif side_norm == "sell" and float(trigger) >= float(last_price):
                trigger = self._round_step(float(last_price) * (1.0 - gap_pct), tick_size)
                if float(trigger) >= float(last_price) and tick_size:
                    trigger = self._round_step(float(last_price) - float(tick_size), tick_size)

            if float(trigger) != trigger_before:
                logger.info(
                    "[EARLYTREND] Ajuste de trigger (anti-rejeicao) | side=%s | trigger=%.6f -> %.6f | last=%.6f",
                    side_norm.upper(),
                    trigger_before,
                    float(trigger),
                    float(last_price),
                )

        def _decimals(step: float) -> int:
            try:
                s = f"{float(step):.12f}".rstrip("0").rstrip(".")
                return len(s.split(".")[1]) if "." in s else 0
            except Exception:
                return 6

        price_dec = _decimals(tick_size)
        def _fmt_price(val: float) -> str:
            return f"{float(val):.{price_dec}f}"

        sl = self._round_step(float(stop_loss), tick_size)
        tp = self._round_step(float(take_profit), tick_size) if take_profit is not None else None

        if sl <= 0 or (tp is not None and tp <= 0):
            logger.warning(f"[EARLYTREND] SL/TP invalidos | SL={sl} TP={tp}")
            return None

        if side_norm == "buy":
            if sl >= trigger or (tp is not None and tp <= trigger):
                logger.warning(
                    "[EARLYTREND] SL/TP fora da hierarquia | side=BUY | SL=%.6f TP=%.6f trigger=%.6f",
                    sl,
                    float(tp or 0.0),
                    trigger,
                )
                return None
        else:
            if sl <= trigger or (tp is not None and tp >= trigger):
                logger.warning(
                    "[EARLYTREND] SL/TP fora da hierarquia | side=SELL | SL=%.6f TP=%.6f trigger=%.6f",
                    sl,
                    float(tp or 0.0),
                    trigger,
                )
                return None

        params = {
            "category": "linear",
            "symbol": self._symbol,
            "side": "Buy" if side.lower() == "buy" else "Sell",
            "orderType": "Limit",
            "qty": f"{qty_adj}",
            "price": _fmt_price(trigger),
            "triggerPrice": _fmt_price(trigger),
            "triggerDirection": 1 if side.lower() == "buy" else 2,
            "triggerBy": "LastPrice",
            "timeInForce": "GTC",
            "stopLoss": _fmt_price(sl),
            "slTriggerBy": "LastPrice",
        }
        if tp is not None:
            params["takeProfit"] = _fmt_price(tp)
            params["tpTriggerBy"] = "LastPrice"

        tp_msg = f"{float(tp):.4f}" if tp is not None else "None"
        logger.info(
            f"[EARLYTREND] ARMOU stop-limit | side={side.upper()} | stop={trigger:.4f} | limit={trigger:.4f} | qty={qty_adj} | SL={sl:.4f} | TP={tp_msg}"
        )
        try:
            gate_result = executar_ordem_com_validacao(
                {
                    "trade_id": f"early_trend_engine:{self._symbol}:{side}:{trigger}",
                    "symbol": self._symbol,
                    "side": side,
                    "entry_price": trigger,
                    "qty": qty_adj,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "decision_timestamp": datetime.utcnow().isoformat(),
                    "ttl_seconds": int(float(os.getenv("SIGNAL_TTL_SECONDS", "180"))),
                    "order_params": params,
                    "execution_fn": lambda payload: self._session.place_order(**payload),
                }
            )
            if gate_result.get("status") != "executado":
                logger.warning(f"[EARLYTREND] Execution gate bloqueou STOP_LIMIT: {gate_result.get('motivo')}")
                return None
            resp = (gate_result.get("detalhes") or {}).get("resposta")
        except Exception as e:
            logger.warning(f"[EARLYTREND] Falha ao enviar STOP_LIMIT: {e}", exc_info=True)
            return None

        if not resp or resp.get("retCode") != 0:
            logger.warning(f"[EARLYTREND] Resposta invalida ao enviar STOP_LIMIT: {resp}")
            return None

        return (resp.get("result") or {}).get("orderId") or resp.get("orderId")

    def _check_fill(self, order_id: str) -> tuple[bool, Optional[float], Optional[float], Optional[str]]:
        try:
            resp = self._session.get_order_history(category="linear", symbol=self._symbol, orderId=order_id, limit=1)
        except Exception as e:
            return False, None, None, f"erro_consulta:{e}"

        if not resp or resp.get("retCode") != 0:
            return False, None, None, f"erro_api:{resp.get('retMsg') if isinstance(resp, dict) else 'unknown'}"

        lista = resp.get("result", {}).get("list", []) or []
        if not lista:
            return False, None, None, "nao_encontrada"

        ordem = lista[0]
        status = (ordem.get("orderStatus") or "").lower()
        qty_exec = float(ordem.get("cumExecQty") or 0)
        avg_price = float(ordem.get("avgPrice") or 0)

        if status == "filled" and qty_exec > 0 and avg_price > 0:
            return True, avg_price, qty_exec, None
        return False, None, None, status

    def _cancel(self, order_id: str) -> None:
        try:
            self._session.cancel_order(category="linear", symbol=self._symbol, orderId=order_id)
        except Exception as e:
            logger.warning(f"[EARLYTREND] Falha ao cancelar ordem {order_id}: {e}")

    def _apply_sl_tp(self) -> bool:
        if self._stop_loss is None:
            logger.error("[EARLYTREND] SL/TP ausentes apos fill; bloqueando trade sem protecao.")
            return False
        try:
            payload = {
                "category": "linear",
                "symbol": self._symbol,
                "stopLoss": f"{float(self._stop_loss):.6f}",
                "slTriggerBy": "LastPrice",
            }
            if self._take_profit is not None:
                payload["takeProfit"] = f"{float(self._take_profit):.6f}"
                payload["tpTriggerBy"] = "LastPrice"
            if self._trailing_stop is not None and self._trailing_stop > 0:
                payload["trailingStop"] = f"{float(self._trailing_stop):.6f}"
            self._session.set_trading_stop(**payload)
            logger.info(
                f"[EARLYTREND] SL/TP aplicado | SL={payload.get('stopLoss')} | TP={payload.get('takeProfit')} | TS={payload.get('trailingStop')}"
            )
            return True
        except Exception as e:
            logger.warning(f"[EARLYTREND] Falha ao aplicar SL/TP (nao fatal): {e}", exc_info=True)
            return False

    def _reset(self) -> None:
        self._order_id = None
        self._side = None
        self._armed_ts = None
        self._expires_ts = None
        self._stop_loss = None
        self._take_profit = None
        self._trailing_stop = None

    def on_tick(
        self,
        *,
        df: pd.DataFrame,
        interval: str,
        qty: float,
        regime_override: Optional[int] = None,
    ) -> EarlyTrendTickResult:
        if not _get_bool_env("EARLY_TREND_ENABLED", False):
            self._reset()
            conditions = [{"name": "enabled", "status": "FAIL", "message": "EARLY_TREND_ENABLED=false"}]
            self._log_entry_checklist(decision="NO_ENTER", primary_reason="disabled", checks=conditions)
            return EarlyTrendTickResult(status="DISABLED", message="EARLY_TREND_ENABLED=false")

        if df is None or getattr(df, "empty", False):
            conditions = [{"name": "df_non_empty", "status": "FAIL", "message": "df_vazio"}]
            self._log_entry_checklist(decision="NO_ENTER", primary_reason="df_empty", checks=conditions)
            return EarlyTrendTickResult(status="ERROR", message="df_vazio")

        if str(os.getenv("BACKTEST_MODE", "false")).strip().lower() == "true":
            conditions = [{"name": "backtest_mode_off", "status": "FAIL", "message": "BACKTEST_MODE=true"}]
            self._log_entry_checklist(decision="NO_ENTER", primary_reason="backtest_mode", checks=conditions)
            return EarlyTrendTickResult(status="DISABLED", message="BACKTEST_MODE=true")

        try:
            df_ind = calculate_indicators(df.copy())
        except Exception:
            df_ind = df

        if regime_override is not None:
            regime = int(regime_override)
        else:
            try:
                regime = RegimeDetector().detectar(df_ind, symbol=self._symbol)
            except Exception as e:
                logger.warning(f"[EARLYTREND] Falha ao detectar regime, usando fallback 2: {e}")
                regime = 2

        now = time.time()

        # Se já existe ordem pendente, apenas gerencia timing (fill/expiração/invalidação por regime).
        if self._order_id:
            if not strategy_allowed("EARLY_TREND", regime):
                self._cancel(self._order_id)
                oid = self._order_id
                self._reset()
                logger.info(f"[EARLYTREND] INVALIDOU | motivo=regime_{regime} | order_id={oid}")
                conditions = [
                    {"name":"has_pending_order","status":"PASS"},
                    {"name":"regime_ok","status":"FAIL","value": regime,"expected": 2,"message": f"regime_{regime}"},
                ]
                self._log_entry_checklist(decision="CANCELLED", primary_reason=f"regime_{regime}", checks=conditions)
                self._notify_telegram(f"[EARLYTREND] Ordem cancelada | symbol={self._symbol} | motivo=regime_{regime} | order_id={oid}")
                return EarlyTrendTickResult(status="INVALIDATED", order_id=oid, message=f"regime_{regime}")

            if self._expires_ts and now >= self._expires_ts:
                self._cancel(self._order_id)
                oid = self._order_id
                self._reset()
                logger.info(f"[EARLYTREND] EXPIROU | order_id={oid}")
                conditions = [
                    {"name":"has_pending_order","status":"PASS"},
                    {"name":"ttl_not_expired","status":"FAIL","message": "expired"},
                ]
                self._log_entry_checklist(decision="CANCELLED", primary_reason="ttl_expired", checks=conditions)
                self._notify_telegram(f"[EARLYTREND] Ordem cancelada | symbol={self._symbol} | motivo=ttl_expired | order_id={oid}")
                return EarlyTrendTickResult(status="EXPIRED", order_id=oid)

            filled, price, qty_exec, status = self._check_fill(self._order_id)
            if filled:
                oid = self._order_id
                side_val = self._side or ""
                sl_val = self._stop_loss
                tp_val = self._take_profit
                applied = self._apply_sl_tp()
                self._reset()
                if not applied:
                    logger.error(f"[EARLYTREND] SL/TP falhou apos fill; order_id={oid}")
                    return EarlyTrendTickResult(status="ERROR", order_id=oid, fill_price=price, qty=qty_exec, message="sl_tp_failed")
                logger.info(f"[EARLYTREND] FILL | order_id={oid} | price={price:.4f} | qty={qty_exec}")
                conditions = [
                    {"name":"has_pending_order","status":"PASS"},
                    {"name":"filled","status":"PASS","message": "order_filled"},
                ]
                self._log_entry_checklist(decision="ENTER", primary_reason="filled", checks=conditions)
                try:
                    sl_msg = f"{float(sl_val):.4f}" if sl_val is not None else "0"
                    tp_msg = f"{float(tp_val):.4f}" if tp_val is not None else "0"
                except Exception:
                    sl_msg = "0"
                    tp_msg = "0"
                self._notify_telegram(
                    f"[EARLYTREND] Ordem preenchida | symbol={self._symbol} | side={side_val.upper()} | order_id={oid} | price={price:.4f} | qty={qty_exec:.6f} | SL={sl_msg} | TP={tp_msg}"
                )
                try:
                    from datetime import datetime
                    from banco_trade.db_trades import salvar_trade_sqlite

                    trade_entry = {
                        "trade_id": oid,
                        "timestamp": datetime.now().isoformat(),
                        "symbol": self._symbol,
                        "side": (side_val or "").lower(),
                        "entry_price": float(price or 0.0),
                        "exit_price": None,
                        "profit_loss": None,
                        "strategy": "EARLY_TREND",
                        "decision_source": os.getenv("ACTIVE_EXCHANGE", "unknown").strip().lower(),
                        "entry_score": None,
                        "sentiment_score": None,
                        "stop_loss": float(sl_val or 0.0),
                        "take_profit": float(tp_val or 0.0),
                        "trailing_stop": float(self._trailing_stop or 0.0),
                        "estrategia_utilizada": "EARLY_TREND",
                        "regime": regime,
                        "tendencia": (side_val or "").lower() or None,
                        "consenso": (side_val or "").lower() or None,
                        "consenso_score": None,
                        "consenso_razao": "sinal_early_trend",
                        "decisao_lstm": None,
                    }
                    salvar_trade_sqlite(trade_entry)
                except Exception as e:
                    logger.warning(f"[EARLYTREND] Falha ao salvar trade no DB: {e}")
                return EarlyTrendTickResult(status="FILLED", order_id=oid, fill_price=price, qty=qty_exec)

            conditions = [
                {"name":"has_pending_order","status":"PASS"},
                {"name":"filled","status":"FAIL","message": str(status or "pending")},
            ]
            self._log_entry_checklist(decision="WAITING_FILL", primary_reason=str(status or "pending"), checks=conditions)
            return EarlyTrendTickResult(status="PENDING", order_id=self._order_id, message=str(status or "pending"))

        # Sem ordem pendente: avalia sinal e arma pré-sinal (stop-limit) se elegível.
        entrada = detectar_entrada_early_trend(df_ind, regime)
        if not entrada.ok or not entrada.side or entrada.preco_referencia is None or entrada.tr is None:
            conditions = [
                {"name": "signal_detected", "status": "FAIL", "message": str(entrada.motivo)},
                {"name": "regime_ok", "status": ("PASS" if regime == 2 else "FAIL"), "value": regime, "expected": 2},
            ]
            self._log_entry_checklist(decision="NO_ENTER", primary_reason=str(entrada.motivo), checks=conditions)
            return EarlyTrendTickResult(status="NO_SIGNAL", message=entrada.motivo)

        if entrada.stop_loss is None:
            conditions = [
                {"name": "signal_detected", "status": "PASS"},
                {"name": "sl_tp_defined", "status": "FAIL", "message": "sl_indefinido"},
            ]
            self._log_entry_checklist(decision="NO_ENTER", primary_reason="sl_indefinido", checks=conditions)
            return EarlyTrendTickResult(status="NO_SIGNAL", message="sl_indefinido")

        try:
            stop_loss_val = float(entrada.stop_loss)
            take_profit_val = float(entrada.take_profit) if entrada.take_profit is not None else None
        except Exception:
            conditions = [
                {"name": "signal_detected", "status": "PASS"},
                {"name": "sl_tp_defined", "status": "FAIL", "message": "sl_tp_invalido"},
            ]
            self._log_entry_checklist(decision="NO_ENTER", primary_reason="sl_tp_invalido", checks=conditions)
            return EarlyTrendTickResult(status="NO_SIGNAL", message="sl_tp_invalido")

        if stop_loss_val <= 0 or (take_profit_val is not None and take_profit_val <= 0):
            conditions = [
                {"name": "signal_detected", "status": "PASS"},
                {"name": "sl_tp_defined", "status": "FAIL", "message": "sl_tp_invalido"},
            ]
            self._log_entry_checklist(decision="NO_ENTER", primary_reason="sl_tp_invalido", checks=conditions)
            return EarlyTrendTickResult(status="NO_SIGNAL", message="sl_tp_invalido")

        fator_tr = _get_float_env("EARLY_TREND_CONFIRM_TR_FACTOR", _get_float_env("FATOR_CONFIRMACAO_TR", 0.12))
        preco_confirmacao = (
            float(entrada.preco_referencia) + (float(entrada.tr) * fator_tr)
            if entrada.side == "buy"
            else float(entrada.preco_referencia) - (float(entrada.tr) * fator_tr)
        )
        preco_confirmacao_original = float(preco_confirmacao)

        last_price = self._get_last_price()
        if last_price and entrada.tr:
            gap_pct_max = _get_float_env("EARLY_TREND_MAX_TRIGGER_GAP_PCT", 0.003)
            gap_atr_max = _get_float_env("EARLY_TREND_MAX_TRIGGER_GAP_ATR", 0.5)

            if gap_pct_max < 0:
                gap_pct_max = 0.0
            if gap_atr_max < 0:
                gap_atr_max = 0.0

            if entrada.side == "buy":
                pct_cap = float(last_price) * (1.0 + gap_pct_max)
                atr_cap = float(last_price) + (float(entrada.tr) * gap_atr_max)
                cap = min(pct_cap, atr_cap)
                if float(preco_confirmacao) > cap:
                    logger.info(
                        "[EARLYTREND] Ajuste de trigger (cap distancia) | side=BUY | trigger=%.6f -> %.6f | last=%.6f",
                        float(preco_confirmacao),
                        float(cap),
                        float(last_price),
                    )
                    preco_confirmacao = float(cap)
            else:
                pct_cap = float(last_price) * (1.0 - gap_pct_max)
                atr_cap = float(last_price) - (float(entrada.tr) * gap_atr_max)
                cap = max(pct_cap, atr_cap)
                if float(preco_confirmacao) < cap:
                    logger.info(
                        "[EARLYTREND] Ajuste de trigger (cap distancia) | side=SELL | trigger=%.6f -> %.6f | last=%.6f",
                        float(preco_confirmacao),
                        float(cap),
                        float(last_price),
                    )
                    preco_confirmacao = float(cap)

        sl_pct_min = _get_float_env("EARLY_TREND_SL_PCT_MIN", 0.003)
        tp_pct_min = _get_float_env("EARLY_TREND_TP_PCT_MIN", 0.006)
        if sl_pct_min < 0:
            sl_pct_min = 0.0
        if tp_pct_min < 0:
            tp_pct_min = 0.0

        if entrada.side == "buy":
            min_sl = float(preco_confirmacao) * (1.0 - sl_pct_min)
            min_tp = float(preco_confirmacao) * (1.0 + tp_pct_min)
            if stop_loss_val > min_sl:
                stop_loss_val = min_sl
            if take_profit_val is not None and take_profit_val < min_tp:
                take_profit_val = min_tp
        else:
            min_sl = float(preco_confirmacao) * (1.0 + sl_pct_min)
            min_tp = float(preco_confirmacao) * (1.0 - tp_pct_min)
            if stop_loss_val < min_sl:
                stop_loss_val = min_sl
            if take_profit_val is not None and take_profit_val > min_tp:
                take_profit_val = min_tp

        use_fixed_tp = _get_bool_env("EARLY_TREND_USE_FIXED_TP", True)
        tp_r_mult = _get_float_env("EARLY_TREND_TP_R_MULT", 3.0)
        if tp_r_mult < 0:
            tp_r_mult = 0.0
        risk_dist = abs(float(preco_confirmacao) - float(stop_loss_val))
        if risk_dist <= 0 and entrada.tr:
            risk_dist = abs(float(entrada.tr))
        desired_tp = None
        if risk_dist > 0:
            if entrada.side == "buy":
                desired_tp = float(preco_confirmacao) + (risk_dist * tp_r_mult)
            else:
                desired_tp = float(preco_confirmacao) - (risk_dist * tp_r_mult)

        trailing_enabled = _get_bool_env("EARLY_TREND_TRAILING_ENABLED", False)
        if use_fixed_tp and desired_tp is not None:
            if take_profit_val is None:
                take_profit_val = float(desired_tp)
            elif entrada.side == "buy":
                take_profit_val = max(float(take_profit_val), float(desired_tp))
            else:
                take_profit_val = min(float(take_profit_val), float(desired_tp))
        elif not use_fixed_tp:
            if trailing_enabled:
                take_profit_val = None
            elif desired_tp is not None:
                take_profit_val = float(desired_tp)

        ttl_candles = _get_int_env("EARLY_TREND_INTENT_TTL_CANDLES", 3)
        ttl_seconds = float(ttl_candles) * float(self._interval_seconds(interval))

        if entrada.side == "buy":
            hierarquia_ok = stop_loss_val < float(preco_confirmacao) and (
                take_profit_val is None or float(preco_confirmacao) < take_profit_val
            )
        else:
            hierarquia_ok = stop_loss_val > float(preco_confirmacao) and (
                take_profit_val is None or float(preco_confirmacao) > take_profit_val
            )

        if not hierarquia_ok:
            sl_mult = _get_float_env("EARLY_TREND_SL_TR_MULT", 1.0)
            tp_mult = _get_float_env("EARLY_TREND_TP_TR_MULT", 1.0)
            base_range = float(entrada.atr or entrada.tr or 0.0)

            if entrada.side == "buy":
                sl_delta = max(preco_confirmacao_original - stop_loss_val, 0.0)
                tp_delta = max((take_profit_val or 0.0) - preco_confirmacao_original, 0.0)
                if sl_delta <= 0 or (take_profit_val is not None and tp_delta <= 0):
                    sl_delta = base_range * sl_mult
                    tp_delta = base_range * tp_mult
                stop_loss_val = float(preco_confirmacao) - sl_delta
                if take_profit_val is not None:
                    take_profit_val = float(preco_confirmacao) + tp_delta
            else:
                sl_delta = max(stop_loss_val - preco_confirmacao_original, 0.0)
                tp_delta = max(preco_confirmacao_original - (take_profit_val or 0.0), 0.0)
                if sl_delta <= 0 or (take_profit_val is not None and tp_delta <= 0):
                    sl_delta = base_range * sl_mult
                    tp_delta = base_range * tp_mult
                stop_loss_val = float(preco_confirmacao) + sl_delta
                if take_profit_val is not None:
                    take_profit_val = float(preco_confirmacao) - tp_delta

            if entrada.side == "buy":
                min_sl = float(preco_confirmacao) * (1.0 - sl_pct_min)
                min_tp = float(preco_confirmacao) * (1.0 + tp_pct_min)
                if stop_loss_val > min_sl:
                    stop_loss_val = min_sl
                if take_profit_val is not None and take_profit_val < min_tp:
                    take_profit_val = min_tp
            else:
                min_sl = float(preco_confirmacao) * (1.0 + sl_pct_min)
                min_tp = float(preco_confirmacao) * (1.0 - tp_pct_min)
                if stop_loss_val < min_sl:
                    stop_loss_val = min_sl
                if take_profit_val is not None and take_profit_val > min_tp:
                    take_profit_val = min_tp

            tp_msg = f"{float(take_profit_val):.6f}" if take_profit_val is not None else "None"
            logger.info(
                "[EARLYTREND] Ajuste SL/TP apos cap | side=%s | SL=%.6f TP=%s trigger=%.6f",
                entrada.side.upper(),
                stop_loss_val,
                tp_msg,
                float(preco_confirmacao),
            )

            if entrada.side == "buy":
                hierarquia_ok = stop_loss_val < float(preco_confirmacao) and (
                    take_profit_val is None or float(preco_confirmacao) < take_profit_val
                )
            else:
                hierarquia_ok = stop_loss_val > float(preco_confirmacao) and (
                    take_profit_val is None or float(preco_confirmacao) > take_profit_val
                )

            if not hierarquia_ok:
                conditions = [
                    {"name": "signal_detected", "status": "PASS"},
                    {"name": "sl_tp_defined", "status": "FAIL", "message": "sl_tp_invalido"},
                ]
                self._log_entry_checklist(decision="NO_ENTER", primary_reason="sl_tp_invalido", checks=conditions)
                return EarlyTrendTickResult(status="NO_SIGNAL", message="sl_tp_invalido")

        trailing_stop_val = None
        if trailing_enabled:
            trailing_atr_mult = _get_float_env("EARLY_TREND_TRAILING_ATR_MULT", 2.5)
            trailing_min_abs = _get_float_env("EARLY_TREND_TRAILING_MIN_ABS", 0.0)
            trailing_min_pct = _get_float_env("EARLY_TREND_TRAILING_MIN_PCT", 0.0)
            if trailing_atr_mult < 0:
                trailing_atr_mult = 0.0
            if trailing_min_abs < 0:
                trailing_min_abs = 0.0
            if trailing_min_pct < 0:
                trailing_min_pct = 0.0
            base_atr = float(entrada.atr or entrada.tr or 0.0)
            trailing_stop_val = max(
                base_atr * trailing_atr_mult,
                float(preco_confirmacao) * trailing_min_pct,
                trailing_min_abs,
            )
            if trailing_stop_val <= 0:
                trailing_stop_val = None
            else:
                max_offset = abs(float(preco_confirmacao) - float(stop_loss_val))
                if max_offset > 0:
                    trailing_stop_val = min(float(trailing_stop_val), max_offset)

        order_id = self._place_stop_limit(
            side=entrada.side,
            qty=float(qty),
            trigger_price=float(preco_confirmacao),
            stop_loss=stop_loss_val,
            take_profit=take_profit_val,
        )
        if not order_id:
            conditions = [
                {"name": "signal_detected", "status": "PASS"},
                {"name": "stop_limit_sent", "status": "FAIL", "message": "falha_place_order"},
            ]
            self._log_entry_checklist(decision="NO_ENTER", primary_reason="falha_place_order", checks=conditions)
            return EarlyTrendTickResult(status="ERROR", message="falha_place_order")

        self._order_id = str(order_id)
        self._side = entrada.side
        self._armed_ts = now
        self._expires_ts = now + max(1.0, ttl_seconds)
        self._stop_loss = stop_loss_val
        self._take_profit = take_profit_val
        self._trailing_stop = trailing_stop_val

        conditions = [
            {"name":"signal_detected","status":"PASS"},
            {"name":"regime_ok","status": ("PASS" if regime == 2 else "FAIL"),"value": regime,"expected": 2},
            {"name":"stop_limit_sent","status":"PASS","value": str(self._order_id)},
        ]
        self._log_entry_checklist(decision="WAITING_FILL", primary_reason="armed", checks=conditions)
        tp_msg = f"{float(take_profit_val):.4f}" if take_profit_val is not None else "None"
        self._notify_telegram(
            f"[EARLYTREND] Ordem armada | symbol={self._symbol} | side={entrada.side.upper()} | trigger={preco_confirmacao:.4f} | qty={qty:.6f} | SL={stop_loss_val:.4f} | TP={tp_msg} | order_id={self._order_id}"
        )
        return EarlyTrendTickResult(status="PENDING", order_id=self._order_id, message="armada")

