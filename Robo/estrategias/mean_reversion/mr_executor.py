from __future__ import annotations

import logging
from datetime import datetime
from execution.execution_gate import executar_ordem_com_validacao
from dataclasses import dataclass
from typing import Any, Protocol

"""Interfaces de execução para a estratégia Mean Reversion (produção)."""

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StopLimitOrderRequest:
    symbol: str
    side: str  # "buy"/"sell"
    qty: float
    stop: float
    limit: float
    time_in_force: str = "GTC"
    client_order_id: str | None = None


@dataclass(frozen=True)
class OrderRef:
    order_id: str
    raw: Any | None = None


@dataclass(frozen=True)
class FillInfo:
    order_id: str
    status: str  # "filled" | "partially_filled" | "canceled" | "open" | "unknown"
    avg_price: float | None = None
    filled_qty: float | None = None
    raw: Any | None = None


class OrderExecutor(Protocol):
    """Interface mínima para execução em produção (pybit/ccxt/etc)."""

    def place_stop_limit(self, req: StopLimitOrderRequest) -> OrderRef: ...

    def cancel_order(self, *, symbol: str, order_id: str) -> None: ...

    def fetch_open_orders(self, *, symbol: str) -> list[dict[str, Any]]: ...

    def check_fill(self, *, symbol: str, order_id: str) -> FillInfo | None: ...


def _map_bybit_order_status(order_status: str) -> str:
    s = (order_status or "").strip().lower()
    if s in {"filled"}:
        return "filled"
    if s in {"partiallyfilled", "partially_filled", "partial"}:
        return "partially_filled"
    if s in {"cancelled", "canceled"}:
        return "canceled"
    if s in {"new", "created", "untriggered", "triggered", "active"}:
        return "open"
    return "unknown"


class BybitMRExecutor:
    """Executor real usando pybit v5 (unified_trading.HTTP)."""

    def __init__(self, session: Any, *, category: str = "linear"):
        self._session = session
        self._category = str(category)
    @staticmethod
    def _round_step(valor: float, step: float) -> float:
        if step <= 0:
            return valor
        casas = len(str(step).split(".")[-1].rstrip("0"))
        return round(round(valor / step) * step, casas)

    def _get_qty_filters(self, symbol: str) -> tuple[float, float]:
        min_qty, qty_step = 0.001, 0.001
        try:
            info = self._session.get_instruments_info(category=self._category, symbol=symbol)
            item = info.get("result", {}).get("list", [{}])[0]
            filtros = item.get("lotSizeFilter", {})
            min_qty = float(filtros.get("minOrderQty", min_qty))
            qty_step = float(filtros.get("qtyStep", qty_step))
        except Exception as e:
            logger.warning("[MR-EXEC] Falha ao obter min_qty/step: %s", e)
        return min_qty, qty_step


    @staticmethod
    def _side_to_bybit(side: str) -> str:
        s = (side or "").strip().lower()
        if s == "buy":
            return "Buy"
        if s == "sell":
            return "Sell"
        raise ValueError(f"side inválido: {side!r} (esperado 'buy'/'sell')")

    @staticmethod
    def _trigger_direction(side: str) -> int:
        # Bybit v5: 1 = gatilho quando preço sobe, 2 = gatilho quando preço cai.
        s = (side or "").strip().lower()
        if s == "buy":
            return 1
        if s == "sell":
            return 2
        raise ValueError(f"side inválido: {side!r} (esperado 'buy'/'sell')")

    def place_stop_limit(self, req: StopLimitOrderRequest) -> OrderRef:
        min_qty, qty_step = self._get_qty_filters(req.symbol)
        qty_adj = self._round_step(float(req.qty), qty_step)
        if qty_adj < min_qty:
            logger.warning("[MR-EXEC] Qty ajustada para minimo | qty=%.8f min=%.8f", qty_adj, min_qty)
            qty_adj = min_qty
        payload: dict[str, Any] = {
            "category": self._category,
            "symbol": req.symbol,
            "side": self._side_to_bybit(req.side),
            "orderType": "Limit",
            "qty": str(qty_adj),
            "price": str(req.limit),
            "timeInForce": req.time_in_force,
            "triggerPrice": str(req.stop),
            "triggerDirection": int(self._trigger_direction(req.side)),
            "reduceOnly": False,
        }
        if req.client_order_id:
            payload["orderLinkId"] = str(req.client_order_id)

        gate_result = executar_ordem_com_validacao(
            {
                "trade_id": req.client_order_id or f"mr:{req.symbol}:{req.side}:{req.stop}:{req.limit}",
                "symbol": req.symbol,
                "side": req.side,
                "entry_price": req.limit,
                "qty": qty_adj,
                "stop_loss": req.stop,
                "decision_timestamp": datetime.utcnow().isoformat() if "datetime" in globals() else None,
                "ttl_seconds": 180,
                "order_params": payload,
                "execution_fn": lambda params: self._session.place_order(**params),
            }
        )
        if gate_result.get("status") != "executado":
            raise RuntimeError(f"Execution gate bloqueou MR: {gate_result}")
        resp = (gate_result.get("detalhes") or {}).get("resposta")
        if resp.get("retCode") != 0:
            raise RuntimeError(f"Bybit place_order falhou: {resp}")

        order_id = str(resp.get("result", {}).get("orderId") or "")
        if not order_id:
            raise RuntimeError(f"Bybit não retornou orderId: {resp}")

        logger.info("[MR-EXEC] STOP_LIMIT enviada | order_id=%s | stop=%s | limit=%s", order_id, req.stop, req.limit)
        return OrderRef(order_id=order_id, raw=resp)

    def cancel_order(self, *, symbol: str, order_id: str) -> None:
        resp = self._session.cancel_order(category=self._category, symbol=symbol, orderId=order_id)
        if resp.get("retCode") != 0:
            raise RuntimeError(f"Bybit cancel_order falhou: {resp}")
        logger.info("[MR-EXEC] Ordem cancelada | order_id=%s", order_id)

    def fetch_open_orders(self, *, symbol: str) -> list[dict[str, Any]]:
        resp = self._session.get_open_orders(category=self._category, symbol=symbol)
        if resp.get("retCode") != 0:
            raise RuntimeError(f"Bybit get_open_orders falhou: {resp}")
        return list(resp.get("result", {}).get("list", []) or [])

    def check_fill(self, *, symbol: str, order_id: str) -> FillInfo | None:
        # Preferimos order history para enxergar status final (Filled/Cancelled), mesmo se não estiver aberto.
        try:
            resp = self._session.get_order_history(category=self._category, symbol=symbol, orderId=order_id)
            if resp.get("retCode") == 0:
                rows = list(resp.get("result", {}).get("list", []) or [])
                if rows:
                    row = rows[0]
                    status_raw = str(row.get("orderStatus") or "").strip()
                    avg_price_raw = row.get("avgPrice")
                    cum_exec_qty_raw = row.get("cumExecQty")
                    info = FillInfo(
                        order_id=order_id,
                        status=_map_bybit_order_status(status_raw),
                        avg_price=float(avg_price_raw) if avg_price_raw not in (None, "", "0") else None,
                        filled_qty=float(cum_exec_qty_raw) if cum_exec_qty_raw not in (None, "", "0") else None,
                        raw=row,
                    )
                    if info.status == "filled":
                        logger.info("[MR-EXEC] FILL confirmado | order_id=%s | price=%s", order_id, info.avg_price)
                    return info
        except Exception:
            pass

        # Fallback: tenta achar em open orders.
        try:
            open_orders = self.fetch_open_orders(symbol=symbol)
            for row in open_orders:
                if str(row.get("orderId")) == str(order_id):
                    status_raw = str(row.get("orderStatus") or "").strip()
                    return FillInfo(order_id=order_id, status=_map_bybit_order_status(status_raw), raw=row)
        except Exception:
            pass

        return None


class NotImplementedExecutor:
    """Placeholder para você plugar ccxt/pybit sem mexer na FSM."""

    def place_stop_limit(self, req: StopLimitOrderRequest) -> OrderRef:
        raise NotImplementedError("Implemente um executor (ccxt/pybit) para place_stop_limit()")

    def cancel_order(self, *, symbol: str, order_id: str) -> None:
        raise NotImplementedError("Implemente um executor (ccxt/pybit) para cancel_order()")

    def fetch_open_orders(self, *, symbol: str) -> list[dict[str, Any]]:
        raise NotImplementedError("Implemente um executor (ccxt/pybit) para fetch_open_orders()")

    def check_fill(self, *, symbol: str, order_id: str) -> FillInfo | None:
        raise NotImplementedError("Implemente um executor (ccxt/pybit) para check_fill()")
