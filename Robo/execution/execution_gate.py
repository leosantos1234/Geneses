import json
import logging
import os
import threading
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional, Tuple

from utils.risk_guard import evaluate_risk_limits
from genesis_runtime import connect_operational_database, database_backend, database_path

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_STATE_FILE = os.getenv(
    "EXECUTION_GATE_STATE_FILE",
    os.path.join("execution", "execution_gate_state.json"),
)
_DB_PATH = os.getenv("EXECUTION_GATE_DB_PATH", str(database_path()))
_BALANCE_CACHE: Dict[str, Any] = {"ts": 0.0, "equity": None, "available": None}
_BALANCE_CACHE_LOCK = threading.Lock()


def _float_or_none(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        number = float(value)
        return number if number >= 0 else None
    except (TypeError, ValueError):
        return None


def _get_account_balance_snapshot() -> Dict[str, Optional[float]]:
    """Lê equity e margem disponível da exchange ativa com cache curto.

    O gate é chamado por cinco workers. Um cache de poucos segundos evita cinco
    consultas simultâneas sem deixar a informação de margem envelhecer durante
    uma rodada de sinais.
    """
    import time

    with _BALANCE_CACHE_LOCK:
        if time.monotonic() - float(_BALANCE_CACHE.get("ts", 0.0)) < 2.0:
            return {
                "equity": _BALANCE_CACHE.get("equity"),
                "available": _BALANCE_CACHE.get("available"),
            }

    try:
        from exchange.factory import create_exchange_client

        client = create_exchange_client()
        response = client.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        if not isinstance(response, dict) or response.get("retCode") not in (None, 0, "0"):
            raise RuntimeError(f"resposta de saldo invalida: {response}")

        equity = None
        available = None
        for account in (response.get("result", {}).get("list", []) or []):
            account_equity = _float_or_none(account.get("totalEquity"))
            account_available = _float_or_none(account.get("totalAvailableBalance"))
            equity = equity if equity is not None else account_equity
            available = available if available is not None else account_available
            for coin in (account.get("coin", []) or []):
                if str(coin.get("coin", "")).upper() == "USDT":
                    equity = _float_or_none(coin.get("equity")) or equity
                    available = _float_or_none(coin.get("totalAvailableBalance"))
                    available = available if available is not None else _float_or_none(coin.get("availableToWithdraw"))

        snapshot = {"equity": equity, "available": available}
    except Exception as exc:
        logger.warning("[EXEC_GATE] Nao foi possivel consultar equity/margem OKX: %s", exc)
        snapshot = {"equity": None, "available": None}

    with _BALANCE_CACHE_LOCK:
        _BALANCE_CACHE.update({"ts": time.monotonic(), **snapshot})
    return snapshot


def _utc_now() -> datetime:
    return datetime.utcnow()


def _ensure_state_dir() -> None:
    state_dir = os.path.dirname(_STATE_FILE)
    if state_dir:
        os.makedirs(state_dir, exist_ok=True)


def _load_state() -> Dict[str, Any]:
    _ensure_state_dir()
    if not os.path.exists(_STATE_FILE):
        return {}
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("[EXEC_GATE] Falha ao ler estado de execucao: %s", e)
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    _ensure_state_dir()
    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if value in (None, "", 0, "0"):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.utcfromtimestamp(float(value))
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00").replace("+00:00", ""))
    except Exception:
        return None


def _build_expiration(contexto_trade: Dict[str, Any]) -> Tuple[Optional[datetime], Optional[int]]:
    expires_at = _parse_timestamp(contexto_trade.get("expires_at"))
    ttl_seconds_raw = contexto_trade.get("ttl_seconds")
    ttl_seconds: Optional[int] = None
    try:
        if ttl_seconds_raw not in (None, ""):
            ttl_seconds = int(float(ttl_seconds_raw))
    except Exception:
        ttl_seconds = None

    if expires_at is not None:
        return expires_at, ttl_seconds

    decision_ts = (
        _parse_timestamp(contexto_trade.get("decision_timestamp"))
        or _parse_timestamp(contexto_trade.get("timestamp_decisao"))
        or _parse_timestamp(contexto_trade.get("signal_timestamp"))
    )
    if decision_ts is None or ttl_seconds is None or ttl_seconds <= 0:
        return None, ttl_seconds
    return decision_ts + timedelta(seconds=ttl_seconds), ttl_seconds


def _get_open_trades_stats(db_path: str = _DB_PATH, symbol: Optional[str] = None) -> Dict[str, float]:
    try:
        conn = connect_operational_database()
        cur = conn.cursor()
        # A tabela usa ``quantity``; ``qty`` era um nome legado inexistente.
        # Registros antigos sem quantidade não representam uma operação
        # executável e não devem bloquear as novas entradas.
        exposure_expr = "COALESCE(SUM(COALESCE(entry_price, 0) * COALESCE(quantity, 0)), 0)"
        if database_backend() != "postgres":
            cur.execute("PRAGMA table_info(trades)")
            cols = {str(row[1]) for row in cur.fetchall()}
            if "quantity" not in cols:
                exposure_expr = "0"
        sql = f"""
        SELECT
          COUNT(*) AS total_open,
          {exposure_expr} AS open_exposure
        FROM trades
        WHERE LOWER(TRIM(COALESCE(status, ''))) IN
              ('aberto', 'open', 'pending', 'partially_filled')
          AND COALESCE(quantity, 0) > 0
        """
        args = []
        if symbol:
            sql += " AND symbol = ?"
            args.append(str(symbol).upper())
        if database_backend() == "postgres":
            sql = sql.replace("?", "%s")
        cur.execute(sql, args)
        row = cur.fetchone() or (0, 0.0)
        conn.close()
        return {
            "total_open": int(row[0] or 0),
            "open_exposure": float(row[1] or 0.0),
        }
    except Exception as e:
        logger.warning("[EXEC_GATE] Falha ao consultar trades abertos: %s", e)
        if database_backend() == "postgres":
            raise RuntimeError("Banco operacional PostgreSQL indisponível para o execution gate") from e
        return {"total_open": 0, "open_exposure": 0.0}


def _resolve_trade_id(contexto_trade: Dict[str, Any]) -> Optional[str]:
    trade_id = (
        contexto_trade.get("trade_id")
        or contexto_trade.get("signal_id")
        or contexto_trade.get("decision_id")
    )
    if trade_id:
        return str(trade_id)

    symbol = str(contexto_trade.get("symbol") or "").upper()
    side = str(contexto_trade.get("side") or "").upper()
    decision_timestamp = (
        contexto_trade.get("decision_timestamp")
        or contexto_trade.get("timestamp_decisao")
        or contexto_trade.get("signal_timestamp")
    )
    order_params = contexto_trade.get("order_params") or {}
    price = order_params.get("price") or contexto_trade.get("entry_price")
    qty = order_params.get("qty") or contexto_trade.get("qty")
    if symbol and side and decision_timestamp:
        return f"{symbol}|{side}|{decision_timestamp}|{price}|{qty}"
    return None


def validar_contexto(contexto_trade: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Valida a estrutura mínima exigida pelo gate antes de qualquer execução.
    """
    if not isinstance(contexto_trade, dict):
        return False, "contexto_invalido", {"tipo": type(contexto_trade).__name__}

    order_params = contexto_trade.get("order_params") or {}
    symbol = contexto_trade.get("symbol") or order_params.get("symbol")
    side = contexto_trade.get("side") or order_params.get("side")
    trade_id = _resolve_trade_id(contexto_trade)

    missing = []
    if not trade_id:
        missing.append("trade_id")
    if not symbol:
        missing.append("symbol")
    if not side:
        missing.append("side")
    if not isinstance(order_params, dict) or not order_params:
        missing.append("order_params")

    if missing:
        return False, "campos_obrigatorios_ausentes", {"campos": missing}

    return True, "ok", {"trade_id": trade_id, "symbol": str(symbol).upper(), "side": str(side)}


def ja_executado(contexto_trade: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    """
    Impede nova execução do mesmo trade_id.
    """
    trade_id = _resolve_trade_id(contexto_trade)
    if not trade_id:
        return False, {"trade_id": None}
    with _LOCK:
        state = _load_state()
        payload = state.get(trade_id)
        return trade_id in state, {"trade_id": trade_id, "registro": payload}


def expirado(contexto_trade: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    """
    Rejeita sinais vencidos com base em expires_at ou decision_timestamp + ttl_seconds.
    """
    expires_at, ttl_seconds = _build_expiration(contexto_trade)
    if expires_at is None:
        return False, {"expires_at": None, "ttl_seconds": ttl_seconds}

    now = _utc_now()
    return now > expires_at, {
        "agora": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "ttl_seconds": ttl_seconds,
    }


def risco_excedido(contexto_trade: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    """
    Consolida risco diário do sistema, limite de exposição aberta e máximo de trades simultâneos.
    """
    symbol = str(contexto_trade.get("symbol") or "").upper() or None
    try:
        risk_state = evaluate_risk_limits(_DB_PATH, symbol=symbol)
    except Exception as exc:
        logger.error("[EXEC_GATE] PostgreSQL indisponível; bloqueando execução: %s", exc)
        return True, {"reason": "DATABASE_UNAVAILABLE", "error": str(exc)}

    max_open_trades = int(float(os.getenv("MAX_CONCURRENT_POSITIONS", os.getenv("MAX_OPEN_TRADES", os.getenv("MAX_SIMULTANEOUS_TRADES", "5"))) or 5))
    max_symbol_open_trades = int(float(os.getenv("MAX_OPEN_TRADES_PER_SYMBOL", "1") or 1))
    max_open_exposure = float(os.getenv("MAX_OPEN_EXPOSURE_USDT", "0") or 0.0)
    max_order_notional = float(os.getenv("MAX_ORDER_NOTIONAL", "0") or 0.0)
    max_position_notional = float(os.getenv("MAX_POSITION_NOTIONAL", "0") or 0.0)
    max_global_exposure_pct = float(os.getenv("MAX_GLOBAL_EXPOSURE_PCT", "0.50") or 0.0)
    max_global_margin_pct = float(os.getenv("MAX_GLOBAL_MARGIN_PCT", "0.50") or 0.0)
    leverage = max(float(os.getenv("LEVERAGE", os.getenv("OKX_LEVERAGE", "2")) or 2.0), 1.0)
    max_risk_pct = float(os.getenv("MAX_RISK_PER_TRADE_PCT", os.getenv("RISCO_PERCENTUAL", "0.004")) or 0.004)

    try:
        global_open = _get_open_trades_stats(_DB_PATH, symbol=None)
        symbol_open = _get_open_trades_stats(_DB_PATH, symbol=symbol) if symbol else {"total_open": 0, "open_exposure": 0.0}
    except Exception as exc:
        return True, {"reason": "DATABASE_UNAVAILABLE", "error": str(exc)}

    entry_price = contexto_trade.get("entry_price")
    qty = contexto_trade.get("qty")
    order_params = contexto_trade.get("order_params") or {}
    try:
        if entry_price in (None, ""):
            entry_price = order_params.get("price")
        if qty in (None, ""):
            qty = order_params.get("qty")
        incremental_exposure = float(entry_price or 0.0) * float(qty or 0.0)
    except Exception:
        incremental_exposure = 0.0

    stop_loss = contexto_trade.get("stop_loss") or contexto_trade.get("stop") or order_params.get("stopLoss")
    risk_amount = _float_or_none(contexto_trade.get("risk_amount") or contexto_trade.get("risco_usdt"))
    stop_distance = None
    try:
        if stop_loss not in (None, "") and entry_price not in (None, ""):
            stop_distance = abs(float(entry_price) - float(stop_loss))
            if risk_amount is None and stop_distance > 0:
                risk_amount = stop_distance * float(qty or 0.0)
    except (TypeError, ValueError):
        stop_distance = None

    balance = _get_account_balance_snapshot()
    equity = _float_or_none(contexto_trade.get("equity") or contexto_trade.get("capital")) or balance.get("equity")
    available_margin = (
        _float_or_none(contexto_trade.get("available_margin"))
        or _float_or_none(contexto_trade.get("margem_disponivel"))
        or balance.get("available")
    )
    incremental_margin = incremental_exposure / leverage if leverage > 0 else incremental_exposure
    current_margin = float(global_open["open_exposure"]) / leverage if leverage > 0 else float(global_open["open_exposure"])

    reasons = []
    if risk_state.get("blocked"):
        reasons.append(str(risk_state.get("reason") or "risk_guard"))
    if max_open_trades > 0 and int(global_open["total_open"]) >= max_open_trades:
        reasons.append(f"max_open_trades {int(global_open['total_open'])}>={max_open_trades}")
    if symbol and max_symbol_open_trades > 0 and int(symbol_open["total_open"]) >= max_symbol_open_trades:
        reasons.append(f"max_open_trades_symbol {int(symbol_open['total_open'])}>={max_symbol_open_trades}")
    if max_open_exposure > 0 and (float(global_open["open_exposure"]) + incremental_exposure) > max_open_exposure:
        reasons.append(
            f"max_open_exposure {(float(global_open['open_exposure']) + incremental_exposure):.2f}>"
            f"{max_open_exposure:.2f}"
        )
    if max_order_notional > 0 and incremental_exposure > max_order_notional:
        reasons.append(f"max_order_notional {incremental_exposure:.2f}>{max_order_notional:.2f}")
    if max_position_notional > 0 and (float(symbol_open["open_exposure"]) + incremental_exposure) > max_position_notional:
        reasons.append(
            f"max_position_notional {(float(symbol_open['open_exposure']) + incremental_exposure):.2f}>"
            f"{max_position_notional:.2f}"
        )
    if equity is not None and max_global_exposure_pct > 0:
        max_global_exposure = equity * max_global_exposure_pct
        if float(global_open["open_exposure"]) + incremental_exposure > max_global_exposure:
            reasons.append(
                f"max_global_exposure {(float(global_open['open_exposure']) + incremental_exposure):.2f}>"
                f"{max_global_exposure:.2f} ({max_global_exposure_pct:.2%} equity)"
            )
    if equity is not None and max_global_margin_pct > 0:
        max_global_margin = equity * max_global_margin_pct
        if current_margin + incremental_margin > max_global_margin:
            reasons.append(
                f"max_global_margin {(current_margin + incremental_margin):.2f}>"
                f"{max_global_margin:.2f} ({max_global_margin_pct:.2%} equity)"
            )
    if available_margin is not None and incremental_margin > available_margin:
        reasons.append(f"available_margin {available_margin:.2f}<{incremental_margin:.2f}")
    if risk_amount is not None and equity is not None and risk_amount > equity * max_risk_pct:
        reasons.append(f"risk_per_trade {risk_amount:.4f}>{equity * max_risk_pct:.4f} ({max_risk_pct:.2%})")

    details = {
        "risk_guard": risk_state,
        "global_open_trades": int(global_open["total_open"]),
        "symbol_open_trades": int(symbol_open["total_open"]),
        "global_open_exposure": float(global_open["open_exposure"]),
        "incremental_exposure": float(incremental_exposure),
        "max_open_trades": max_open_trades,
        "max_open_trades_per_symbol": max_symbol_open_trades,
        "max_open_exposure_usdt": max_open_exposure,
        "max_order_notional_usdt": max_order_notional,
        "max_position_notional_usdt": max_position_notional,
        "equity_usdt": equity,
        "available_margin_usdt": available_margin,
        "leverage": leverage,
        "current_margin_usdt": current_margin,
        "incremental_margin_usdt": incremental_margin,
        "max_global_exposure_pct": max_global_exposure_pct,
        "max_global_margin_pct": max_global_margin_pct,
        "max_risk_per_trade_pct": max_risk_pct,
        "risk_amount_usdt": risk_amount,
        "risk_check": "stop_loss/risk_amount" if risk_amount is not None else "sizing_centralizado",
    }
    if reasons:
        details["reasons"] = reasons
    return len(reasons) > 0, details


def _dimensionar_ordem_por_capital(contexto_trade: Dict[str, Any]) -> None:
    """Reduz o lote de entradas ao limite de capital antes do gate final.

    Todas as estratégias passam por ``executar_ordem_com_validacao``. Centralizar
    o sizing aqui evita que uma estratégia calcule um lote incompatível com a
    equity, margem ou limite nocional enquanto outra usa uma regra diferente.
    Ordens reduce-only são saídas e permanecem com a quantidade da posição.
    """
    order_params = contexto_trade.get("order_params") or {}
    if bool(order_params.get("reduceOnly") or order_params.get("reduce_only")):
        return

    try:
        price = contexto_trade.get("entry_price") or order_params.get("price")
        qty = contexto_trade.get("qty") or order_params.get("qty")
        price = float(price)
        requested_qty = float(qty)
        if price <= 0 or requested_qty <= 0:
            return

        balance = _get_account_balance_snapshot()
        equity = _float_or_none(contexto_trade.get("equity")) or balance.get("equity")
        available = _float_or_none(contexto_trade.get("available_margin")) or balance.get("available")
        if equity is None or equity <= 0:
            return

        leverage = max(float(os.getenv("LEVERAGE", os.getenv("OKX_LEVERAGE", "2")) or 2.0), 1.0)
        exposure_pct = max(float(os.getenv("MAX_GLOBAL_EXPOSURE_PCT", "0.50") or 0.0), 0.0)
        margin_pct = max(float(os.getenv("MAX_GLOBAL_MARGIN_PCT", "0.50") or 0.0), 0.0)
        max_order_notional = max(float(os.getenv("MAX_ORDER_NOTIONAL", "0") or 0.0), 0.0)
        global_open = _get_open_trades_stats(_DB_PATH, symbol=None)

        notional_caps = []
        if exposure_pct > 0:
            notional_caps.append(max(equity * exposure_pct - float(global_open["open_exposure"]), 0.0))
        if margin_pct > 0:
            current_margin = float(global_open["open_exposure"]) / leverage
            notional_caps.append(max((equity * margin_pct - current_margin) * leverage, 0.0))
        if available is not None and available > 0:
            notional_caps.append(available * leverage)
        if max_order_notional > 0:
            notional_caps.append(max_order_notional)
        if notional_caps:
            qty_cap = min(notional_caps) / price
            if 0 < qty_cap < requested_qty:
                contexto_trade["qty"] = qty_cap
                order_params["qty"] = qty_cap
                contexto_trade["order_params"] = order_params
                contexto_trade["equity"] = equity
                if available is not None:
                    contexto_trade["available_margin"] = available
                logger.info(
                    "[EXEC_GATE] Sizing centralizado | symbol=%s | qty %.8f -> %.8f | "
                    "price=%.8f | equity=%.4f | cap_notional=%.4f",
                    contexto_trade.get("symbol"), requested_qty, qty_cap, price, equity, min(notional_caps),
                )
    except Exception as exc:
        logger.warning("[EXEC_GATE] Falha no sizing centralizado; mantendo lote original: %s", exc)


def _marcar_execucao(contexto_trade: Dict[str, Any], resposta: Any) -> None:
    trade_id = _resolve_trade_id(contexto_trade)
    if not trade_id:
        return
    payload = {
        "symbol": str(contexto_trade.get("symbol") or "").upper(),
        "side": str(contexto_trade.get("side") or "").upper(),
        "executed_at": _utc_now().isoformat(),
        "order_id": _extract_order_id(resposta),
    }
    with _LOCK:
        state = _load_state()
        state[trade_id] = payload
        _save_state(state)


def _extract_order_id(resposta: Any) -> Optional[str]:
    if not isinstance(resposta, dict):
        return None
    order_id = resposta.get("orderId")
    if order_id:
        return str(order_id)
    result = resposta.get("result") or {}
    if isinstance(result, dict) and result.get("orderId"):
        return str(result.get("orderId"))
    if isinstance(result, dict):
        rows = result.get("list") or []
        if isinstance(rows, list) and rows:
            row = rows[0] if isinstance(rows[0], dict) else {}
            order_id = row.get("orderId") or row.get("ordId")
            if order_id:
                return str(order_id)
    return None


def _resposta_valida(resposta: Any) -> bool:
    if not isinstance(resposta, dict):
        return False
    ret_code = resposta.get("retCode")
    if ret_code not in (None, 0, "0"):
        return False
    result = resposta.get("result") or {}
    if isinstance(result, dict):
        rows = result.get("list") or []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                s_code = row.get("sCode")
                if s_code not in (None, 0, "0"):
                    return False
    return _extract_order_id(resposta) is not None or resposta.get("status") == "ok"


def _executar_envio(contexto_trade: Dict[str, Any]) -> Any:
    from exchange.write_guard import assert_exchange_write_allowed

    assert_exchange_write_allowed("execution_gate")
    execution_fn = contexto_trade.get("execution_fn")
    order_params = contexto_trade.get("order_params") or {}

    if callable(execution_fn):
        return execution_fn(order_params)

    from compra1 import enviar_ordem_adaptativa

    return enviar_ordem_adaptativa(order_params)


def executar_ordem_com_validacao(contexto_trade: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ponto único autorizado para abertura de ordens.

    Fluxo:
    1. Valida contexto mínimo
    2. Bloqueia duplicidade por trade_id
    3. Rejeita sinal expirado
    4. Rejeita se risco/exposição excederem os limites
    5. Se tudo ok, executa a ordem via função de envio real
    """
    ok, motivo, detalhes_contexto = validar_contexto(contexto_trade)
    if not ok:
        return {"status": "bloqueado", "motivo": motivo, "detalhes": detalhes_contexto}

    _dimensionar_ordem_por_capital(contexto_trade)

    duplicado, detalhes_duplicidade = ja_executado(contexto_trade)
    if duplicado:
        return {"status": "bloqueado", "motivo": "trade_ja_executado", "detalhes": detalhes_duplicidade}

    stale, detalhes_expiracao = expirado(contexto_trade)
    if stale:
        return {"status": "bloqueado", "motivo": "sinal_expirado", "detalhes": detalhes_expiracao}

    blocked_risk, detalhes_risco = risco_excedido(contexto_trade)
    if blocked_risk:
        return {"status": "bloqueado", "motivo": "risco_excedido", "detalhes": detalhes_risco}

    try:
        resposta = _executar_envio(contexto_trade)
    except Exception as e:
        logger.error("[EXEC_GATE] Erro ao executar ordem: %s", e, exc_info=True)
        return {
            "status": "bloqueado",
            "motivo": "erro_execucao",
            "detalhes": {"erro": str(e), "trade_id": detalhes_contexto.get("trade_id")},
        }

    if not _resposta_valida(resposta):
        return {
            "status": "bloqueado",
            "motivo": "falha_exchange",
            "detalhes": {"trade_id": detalhes_contexto.get("trade_id"), "resposta": resposta},
        }

    _marcar_execucao(contexto_trade, resposta)
    return {
        "status": "executado",
        "motivo": "ordem_enviada",
        "detalhes": {
            "trade_id": detalhes_contexto.get("trade_id"),
            "order_id": _extract_order_id(resposta),
            "resposta": resposta,
        },
    }
