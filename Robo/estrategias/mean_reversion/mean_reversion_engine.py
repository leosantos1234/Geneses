from __future__ import annotations

import logging
import os
from dataclasses import asdict
from typing import Any

from .mean_reversion_fsm import (
    MRState,
    MRTrigger,
    MeanReversionStateMachine,
    MarketSnapshot,
    PositionSnapshot,
)
from .mr_executor import FillInfo, OrderExecutor, StopLimitOrderRequest
from .mr_state_store import MRStateStore
from .mr_telemetry import log_mr_event
from banco_trade.db_trades import salvar_trade_sqlite

logger = logging.getLogger(__name__)


def _fsm_to_persistable(fsm: MeanReversionStateMachine, *, order_id: str | None) -> dict[str, Any]:
    return {
        "state": str(fsm.state.value),
        "state_since": fsm.state_since.isoformat(),
        "reversal_setup": (asdict(fsm.reversal_setup) if fsm.reversal_setup else None),
        "stop_limit_intent": (fsm.stop_limit_intent.to_dict() if fsm.stop_limit_intent else None),
        "position_plan": (fsm.position_plan.to_dict() if fsm.position_plan else None),
        "order_id": order_id,
    }


class MeanReversionEngine:
    """Camada de orquestração (produção) sobre a FSM.

    Responsabilidades:
    - chamar `fsm.step()` com snapshots
    - ao entrar em ReversaoEmFormacao: criar/atualizar ordem stop-limit (prova de mercado)
    - ao invalidar/expirar: cancelar ordem
    - persistir estado para sobreviver a restart

    Importante:
    - a decisão/validação da reversão vem do PREÇO (stop/limit), não de indicadores
    - a FSM continua pura (sem side-effects de exchange)
    """

    def __init__(
        self,
        *,
        fsm: MeanReversionStateMachine,
        executor: OrderExecutor,
        store: MRStateStore | None = None,
        qty: float = 0.0,
    ):
        self.fsm = fsm
        self.executor = executor
        self.store = store
        self.qty = float(qty)
        self._active_order_id: str | None = None

    def _log_entry_checklist(self, *, symbol: str, decision: str, primary_reason: str, checks: list[dict[str, Any]]) -> None:
        if str(os.getenv("ENABLE_ENTRY_CHECKLIST_LOGS", "1")).strip().lower() not in {"1", "true", "yes", "y", "on"}:
            return
        passed = [c for c in (checks or []) if str(c.get("status", "")).upper() == "PASS"]
        failed = [c for c in (checks or []) if str(c.get("status", "")).upper() == "FAIL"]
        try:
            sym = str(symbol or "").upper() or "UNKNOWN"
            logger.info(
                "[ENTRY_CHECKLIST] strategy=MEAN_REVERSION symbol=%s decision=%s reason=%s pass=%d fail=%d",
                sym,
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

    def on_tick(self, market: MarketSnapshot, position: PositionSnapshot) -> list[dict[str, Any]]:
        transitions = self.fsm.step(market, position)
        events: list[dict[str, Any]] = []
        armed_this_tick = False
        filled_this_tick = False
        cancelled_this_tick_reason: str | None = None
        fill_status: str | None = None

        for t in transitions:
            events.append({"from": t.from_state.value, "to": t.to_state.value, "trigger": t.trigger.value, "reason": t.reason, "data": t.data})

            if t.to_state == MRState.REVERSAO_EM_FORMACAO and self.fsm.stop_limit_intent and not self._active_order_id:
                if self.qty <= 0:
                    continue
                intent = self.fsm.stop_limit_intent
                logger.info(
                    "[MR-FSM] ARMOU stop-limit | stop=%s | limit=%s | microRes=%s | atr=%s",
                    float(intent.stop),
                    float(intent.limit),
                    float(intent.micro_res),
                    float(intent.atr),
                )
                req = StopLimitOrderRequest(
                    symbol=market.symbol,
                    side="buy",
                    qty=self.qty,
                    stop=float(intent.stop),
                    limit=float(intent.limit),
                )
                ref = self.executor.place_stop_limit(req)
                self._active_order_id = ref.order_id
                armed_this_tick = True

                try:
                    pivot_low = float(self.fsm.reversal_setup.pivot_low) if self.fsm.reversal_setup else None
                    log_mr_event(
                        timestamp=market.now,
                        symbol=market.symbol,
                        strategy="MEAN_REVERSION",
                        event="ARMOU_STOP_LIMIT",
                        pivot_low=pivot_low,
                        micro_res=float(intent.micro_res),
                        stop=float(intent.stop),
                        limit=float(intent.limit),
                        atr=float(intent.atr),
                        timeframe=market.timeframe,
                    )
                except Exception:
                    logger.warning("[MR-TELEM] Falha ao registrar ARMOU_STOP_LIMIT", exc_info=True)

            if t.trigger in {MRTrigger.INVALIDACAO, MRTrigger.TIMEOUT}:
                if self._active_order_id:
                    try:
                        self.executor.cancel_order(symbol=market.symbol, order_id=self._active_order_id)
                    finally:
                        self._active_order_id = None
                        cancelled_this_tick_reason = str(t.reason or t.trigger.value)

            # Se você executar em produção, o fill real vem da exchange (não do OHLC).
            # Aqui, se a FSM entrar em EmPosicao por simulação, cancelamos a ordem remanescente.
            if t.to_state == MRState.EM_POSICAO:
                if self._active_order_id:
                    try:
                        self.executor.cancel_order(symbol=market.symbol, order_id=self._active_order_id)
                    finally:
                        self._active_order_id = None

        # Produção: confirma fill via exchange (não via OHLC) quando houver ordem ativa.
        if self._active_order_id and self.fsm.state == MRState.REVERSAO_EM_FORMACAO:
            fill = self.executor.check_fill(symbol=market.symbol, order_id=self._active_order_id)
            if isinstance(fill, FillInfo):
                fill_status = str(fill.status or "") or None
            if isinstance(fill, FillInfo) and fill.status == "filled":
                entry_price = float(fill.avg_price) if fill.avg_price is not None else None
                if entry_price is None and self.fsm.stop_limit_intent is not None:
                    entry_price = float(self.fsm.stop_limit_intent.limit)
                if entry_price is not None:
                    extra_transitions = self.fsm.apply_exchange_fill(
                        entry_price=float(entry_price),
                        market_now=market.now,
                        fill_meta={"order_id": self._active_order_id, "avg_price": fill.avg_price, "filled_qty": fill.filled_qty},
                    )
                    if self.fsm.position_plan:
                        try:
                            log_mr_event(
                                timestamp=market.now,
                                symbol=market.symbol,
                                strategy="MEAN_REVERSION",
                                event="FILL",
                                order_id=self._active_order_id,
                                fill_price=float(entry_price),
                                qty=float(self.qty),
                                sl=float(self.fsm.position_plan.stop_loss),
                                timeframe=market.timeframe,
                            )
                        except Exception:
                            logger.warning("[MR-TELEM] Falha ao registrar FILL", exc_info=True)
                    try:
                        stop_loss = None
                        take_profit = None
                        if self.fsm.position_plan:
                            stop_loss = float(self.fsm.position_plan.stop_loss)
                            take_profit = float(self.fsm.position_plan.take_profit)
                        regime_atual = None
                        if isinstance(market.indicators, dict):
                            regime_atual = market.indicators.get("regime")

                        salvar_trade_sqlite(
                            {
                                "trade_id": self._active_order_id,
                                "timestamp": market.now.isoformat(),
                                "symbol": market.symbol,
                                "side": "buy",
                                "entry_price": float(entry_price),
                                "strategy": "MEAN_REVERSION",
                                "decision_source": "bybit",
                                "stop_loss": stop_loss,
                                "take_profit": take_profit,
                                "regime": regime_atual,
                                "tendencia": None,
                                "consenso": "buy",
                                "consenso_score": None,
                                "consenso_razao": "fill_confirmado_exchange",
                                "decisao_lstm": None,
                            }
                        )
                    except Exception:
                        logger.warning("[MR-DB] Falha ao salvar trade", exc_info=True)
                    for t in extra_transitions:
                        events.append({"from": t.from_state.value, "to": t.to_state.value, "trigger": t.trigger.value, "reason": t.reason, "data": t.data})
                filled_this_tick = True
                self._active_order_id = None

        if self.store:
            self.store.save(market.symbol, _fsm_to_persistable(self.fsm, order_id=self._active_order_id))

        try:
            checks: list[dict[str, Any]] = [
                {"name": "qty_positive", "status": ("PASS" if self.qty > 0 else "FAIL"), "value": self.qty},
                {"name": "in_position", "status": ("PASS" if position.in_position else "FAIL"), "value": bool(position.in_position)},
                {"name": "fsm_state", "status": "PASS", "value": str(self.fsm.state.value)},
                {"name": "has_stop_limit_intent", "status": ("PASS" if self.fsm.stop_limit_intent else "FAIL")},
                {"name": "has_active_order", "status": ("PASS" if self._active_order_id else "FAIL"), "value": self._active_order_id},
            ]

            if armed_this_tick:
                checks.append({"name": "armed_stop_limit", "status": "PASS"})
            if filled_this_tick:
                checks.append({"name": "filled", "status": "PASS"})
            if fill_status and self._active_order_id:
                checks.append({"name": "fill_status", "status": "PASS", "value": fill_status})

            if position.in_position or self.fsm.state == MRState.EM_POSICAO:
                self._log_entry_checklist(symbol=market.symbol, decision="IN_POSITION", primary_reason="in_position", checks=checks)
            elif filled_this_tick:
                self._log_entry_checklist(symbol=market.symbol, decision="ENTER", primary_reason="filled", checks=checks)
            elif cancelled_this_tick_reason:
                self._log_entry_checklist(symbol=market.symbol, decision="CANCELLED", primary_reason=cancelled_this_tick_reason, checks=checks)
            elif self._active_order_id:
                self._log_entry_checklist(symbol=market.symbol, decision="WAITING_FILL", primary_reason=str(fill_status or "pending"), checks=checks)
            else:
                primary_reason = None
                if self.qty <= 0:
                    primary_reason = "qty_zero"
                elif self.fsm.state == MRState.REVERSAO_EM_FORMACAO and self.fsm.stop_limit_intent:
                    primary_reason = "no_active_order_id"
                elif transitions:
                    primary_reason = str(transitions[-1].reason or transitions[-1].trigger.value)
                else:
                    primary_reason = "no_signal"
                self._log_entry_checklist(symbol=market.symbol, decision="NO_ENTER", primary_reason=str(primary_reason), checks=checks)
        except Exception:
            pass

        return events
