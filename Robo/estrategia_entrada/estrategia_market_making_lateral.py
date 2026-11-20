import logging
import math
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional

import pandas as pd

from utils.indicadores import calculate_indicators
from utils.utils_market import get_market_data
from utils.utils_telegram import send_telegram_message
from execution.execution_gate import executar_ordem_com_validacao
from regime.regime_detector import RegimeDetector
from estrategia_entrada.market_making_rules import canonical_config, quote_from_closes
from banco_trade.db_trades import salvar_trade_sqlite
from lineage.decision_lineage import finalize_decision, link_trade, record_event

logger = logging.getLogger(__name__)

MM_TIMEFRAME = os.getenv("MM_TIMEFRAME", "1")
MM_MAX_CONSECUTIVE_LOSSES = int(os.getenv("MM_MAX_CONSECUTIVE_LOSSES", 3))
MM_COOLDOWN_SECONDS = int(os.getenv("MM_COOLDOWN_SECONDS", 300))
MM_LOCK_MAX_AGE_SECONDS = int(os.getenv("MM_LOCK_MAX_AGE_SECONDS", 900))

# Os workers podem ser iniciados a partir de diretórios diferentes. Os flags
# precisam ficar sempre no mesmo diretório do projeto, e não no cwd do worker.
_MM_STATE_DIR = Path(__file__).resolve().parents[1]


def _get_env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except Exception:
        return default


def _get_env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _get_env_override(base: str, symbol: str, cast, default):
    sym_key = f"{base}_{symbol.upper()}"
    raw = os.getenv(sym_key)
    if raw is None or raw == "":
        raw = os.getenv(base)
    if raw is None or raw == "":
        return default
    try:
        return cast(raw)
    except Exception:
        return default


DEFAULT_CFG = {
    "fator_lateralidade": _get_env_float("MM_FATOR_LATERALIDADE", 0.25),
    "time_stop_segundos": _get_env_int("MM_TIME_STOP", 30),
    "atr_pct_max": _get_env_float("MM_ATR_PCT_MAX", 0.08),  # em %
    "spread_real_max_mult": _get_env_float("MM_SPREAD_REAL_MAX_MULT", 2.0),
    "spread_min_pct": _get_env_float("MM_SPREAD_MIN_PCT", 0.0001),
    "min_qty_default": _get_env_float("MM_MIN_QTY_DEFAULT", 0.001),
    "step_default": _get_env_float("MM_STEP_DEFAULT", 0.001),
    "cancel_open_before": bool(_get_env_int("MM_CANCEL_OPEN_BEFORE", 1)),
    # Distância máxima por lado em relação ao midpoint. Evita cotações
    # antigas/distantes quando o preço anda antes do preenchimento.
    "quote_max_distance_pct": _get_env_float("MM_QUOTE_MAX_DISTANCE_PCT", 0.0008),
    "regime_confirm_cycles": _get_env_int("MM_REGIME_CONFIRM_CYCLES", 3),
    "atr_hard_max": _get_env_float("MM_ATR_HARD_MAX", 0.10),  # em %
    "volume_ratio_min": _get_env_float("MM_VOLUME_RATIO_MIN", 0.90),
    "volume_ratio_max": _get_env_float("MM_VOLUME_RATIO_MAX", 1.20),
    "candle_body_max": _get_env_float("MM_CANDLE_BODY_MAX", 0.35),
    "spread_real_max_pct": _get_env_float("MM_SPREAD_REAL_MAX_PCT", 0.0015),
    "min_book_depth_quote": _get_env_float("MM_MIN_BOOK_DEPTH_QUOTE", 100.0),
}


def _ajustar_quantidade_minima(session, symbol: str, qty: float, cfg: Dict) -> float:
    """
    Ajusta qty para respeitar minOrderQty e qtyStep da corretora.
    """
    min_qty = cfg.get("min_qty_default", 0.001)
    step_size = cfg.get("step_default", 0.001)

    try:
        info = session.get_instruments_info(category="linear", symbol=symbol)
        if info.get("retCode") == 0:
            instrument = (info.get("result", {}).get("list", []) or [])[0]
            filtros = instrument.get("lotSizeFilter", {}) or {}
            min_qty = float(filtros.get("minOrderQty") or min_qty)
            step_size = float(filtros.get("qtyStep") or step_size)
            contract_size = float(instrument.get("contractSize") or 0)
            if contract_size > 0:
                # Na OKX SWAP lotSz/minSz são contratos, enquanto o robô
                # trabalha com quantidade da moeda-base. Ajusta para um
                # número inteiro de contratos antes do execution gate.
                # A OKX exige ``sz`` inteiro para SWAP, mesmo quando os
                # metadados retornam lotSz/minSz fracionários.
                contract_min = max(min_qty, 0.0)
                contract_step = max(step_size, 0.0)
                requested_contracts = float(qty) / contract_size
                if contract_step <= 0:
                    contract_step = contract_min or 1.0
                contracts = max(contract_min, math.ceil(requested_contracts / contract_step - 1e-12) * contract_step)
                normalized_qty = round(contracts * contract_size, 12)
                logger.info(
                    "[MM-LAT] Quantidade OKX normalizada | solicitado=%s base | "
                    "ctVal=%s | contratos=%s | enviado=%s base",
                    qty,
                    contract_size,
                    contracts,
                    normalized_qty,
                )
                return normalized_qty
    except Exception as e:
        logger.warning(f"[MM-LAT] Falha ao consultar min_qty, usando defaults. Erro: {e}")

    if qty < min_qty:
        qty = min_qty
    resto = qty % step_size
    if resto != 0:
        qty = round(qty - resto + step_size, 6)
    return round(qty, 6)


def _obter_sizing_por_capital(session, symbol: str, midpoint: float, qty_requested: float):
    """Limita o lote ao capital/margem que o execution gate aceitará."""
    try:
        resposta = session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        if not isinstance(resposta, dict) or resposta.get("retCode") not in (None, 0, "0"):
            raise RuntimeError(f"saldo invalido: {resposta}")

        equity = None
        available = None
        for account in (resposta.get("result", {}).get("list", []) or []):
            equity = equity or account.get("totalEquity")
            available = available or account.get("totalAvailableBalance")
            for coin in (account.get("coin", []) or []):
                if str(coin.get("coin", "")).upper() == "USDT":
                    equity = coin.get("equity") or equity
                    available = coin.get("totalAvailableBalance") or coin.get("availableToWithdraw") or available

        equity = float(equity or 0.0)
        available = float(available or 0.0)
        if equity <= 0 or midpoint <= 0:
            raise RuntimeError(f"equity invalida: equity={equity} midpoint={midpoint}")

        leverage = max(float(os.getenv("LEVERAGE", os.getenv("OKX_LEVERAGE", "2")) or 2.0), 1.0)
        exposure_pct = max(float(os.getenv("MAX_GLOBAL_EXPOSURE_PCT", "0.50") or 0.0), 0.0)
        margin_pct = max(float(os.getenv("MAX_GLOBAL_MARGIN_PCT", "0.50") or 0.0), 0.0)
        notional_caps = []
        if exposure_pct > 0:
            notional_caps.append(equity * exposure_pct)
        if margin_pct > 0:
            notional_caps.append(equity * margin_pct * leverage)
        if available > 0:
            notional_caps.append(available * leverage)
        max_order_notional = max(float(os.getenv("MAX_ORDER_NOTIONAL", "0") or 0.0), 0.0)
        if max_order_notional > 0:
            notional_caps.append(max_order_notional)

        qty_cap = min(notional_caps) / float(midpoint) if notional_caps else float(qty_requested)
        qty = min(float(qty_requested), max(qty_cap, 0.0))
        logger.info(
            "[MM-LAT] Sizing por capital | equity=%.4f available=%.4f leverage=%.2f "
            "qty_solicitada=%.8f qty_cap=%.8f qty_final=%.8f",
            equity, available, leverage, qty_requested, qty_cap, qty,
        )
        return qty, equity, available
    except Exception as exc:
        logger.warning("[MM-LAT] Nao foi possivel calcular sizing por capital: %s", exc)
        return 0.0, None, None


def _obter_spread_real(session, symbol: str) -> Optional[float]:
    """
    Obtém spread real do book (best ask - best bid). Retorna None em falha.
    """
    try:
        orderbook = session.get_orderbook(category="linear", symbol=symbol, limit=1)
        if orderbook.get("retCode") != 0:
            return None
        bids = orderbook["result"]["b"]
        asks = orderbook["result"]["a"]
        if not bids or not asks:
            return None
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        return best_ask - best_bid
    except Exception as e:
        logger.warning(f"[MM-LAT] Falha ao obter spread real: {e}")
        return None


def _obter_topo_livro(session, symbol: str):
    """Retorna best bid, best ask e midpoint do livro real."""
    try:
        orderbook = session.get_orderbook(category="linear", symbol=symbol, limit=5)
        if orderbook.get("retCode") != 0:
            return None
        bids = orderbook.get("result", {}).get("b", []) or []
        asks = orderbook.get("result", {}).get("a", []) or []
        if not bids or not asks:
            return None
        bid = float(bids[0][0])
        ask = float(asks[0][0])
        if bid <= 0 or ask <= bid:
            return None
        return bid, ask, (bid + ask) / 2.0
    except Exception as exc:
        logger.warning("[MM-LAT] Falha ao obter topo do livro: %s", exc)
        return None


def _obter_profundidade_livro(session, symbol: str, levels: int = 5) -> Optional[float]:
    """Soma o valor nocional dos níveis superiores do book."""
    try:
        orderbook = session.get_orderbook(category="linear", symbol=symbol, limit=levels)
        if not isinstance(orderbook, dict) or orderbook.get("retCode") != 0:
            return None
        result = orderbook.get("result", {}) or {}
        bids = result.get("b", []) or []
        asks = result.get("a", []) or []
        nocional = 0.0
        for row in list(bids[:levels]) + list(asks[:levels]):
            if len(row) < 2:
                continue
            nocional += abs(float(row[0])) * abs(float(row[1]))
        return nocional if nocional > 0 else None
    except Exception as exc:
        logger.warning("[MM-LAT] Falha ao obter profundidade do book: %s", exc)
        return None


def _obter_tick(session, symbol: str, default: float = 0.1) -> float:
    try:
        info = session.get_instruments_info(category="linear", symbol=symbol)
        rows = info.get("result", {}).get("list", []) if isinstance(info, dict) else []
        tick = float((rows[0].get("priceFilter", {}) or {}).get("tickSize") or default)
        return tick if tick > 0 else default
    except Exception:
        return default


def _cotacoes_mm_desatualizadas(session, symbol: str, midpoint: float, max_distance_pct: float) -> bool:
    """Detecta cotações fora do centro ou do lado correto do livro."""
    try:
        positions = session.get_positions(category="linear", symbol=symbol)
        for position in positions.get("result", {}).get("list", []) or []:
            if abs(float(position.get("size") or position.get("pos") or 0)) > 0:
                return False

        resposta = session.get_open_orders(category="linear", symbol=symbol)
        ordens = resposta.get("result", {}).get("list", []) if isinstance(resposta, dict) else []
        quotes = []
        for ordem in ordens or []:
            reduce_only = ordem.get("reduceOnly", ordem.get("reduce_only", False))
            if reduce_only is True or str(reduce_only).lower() in ("1", "true", "yes", "sim"):
                continue
            price = float(ordem.get("price") or ordem.get("px") or 0)
            side = str(ordem.get("side") or "").lower()
            if price > 0 and side in ("buy", "sell"):
                quotes.append((side, price))

        if not quotes:
            return False
        limite = midpoint * max(float(max_distance_pct), 0.0001)
        for side, price in quotes:
            distancia = midpoint - price if side == "buy" else price - midpoint
            # Qualquer ordem do lado errado ou além do limite precisa ser
            # cancelada e recalculada no midpoint atual.
            if distancia <= 0 or distancia > limite:
                logger.warning(
                    "[MM-LAT] Cotação stale/inválida | side=%s price=%.8f mid=%.8f dist=%.8f limite=%.8f",
                    side, price, midpoint, distancia, limite,
                )
                return True
        return False
    except Exception as exc:
        logger.warning("[MM-LAT] Não foi possível validar cotações: %s", exc)
        return True


def _cancelar_ordens(session, symbol: str):
    try:
        # O adaptador OKX expõe cancelamento unitário; Bybit pode expor o
        # cancelamento em lote. Usamos ambos sem acoplar a estratégia à exchange.
        cancel_all = getattr(session, "cancel_all_orders", None)
        if callable(cancel_all):
            cancel_all(category="linear", symbol=symbol)
        else:
            get_open_orders = getattr(session, "get_open_orders", None)
            cancel_order = getattr(session, "cancel_order", None)
            if not callable(get_open_orders) or not callable(cancel_order):
                raise AttributeError("adaptador sem cancel_all_orders/get_open_orders/cancel_order")
            resposta = get_open_orders(category="linear", symbol=symbol)
            ordens = resposta.get("result", {}).get("list", []) if isinstance(resposta, dict) else []
            for ordem in ordens:
                order_id = ordem.get("orderId") or ordem.get("ordId")
                if order_id:
                    cancel_order(category="linear", symbol=symbol, orderId=order_id)
        logger.info(f"[MM-LAT] Ordens abertas canceladas para {symbol}.")
    except Exception as e:
        logger.warning(f"[MM-LAT] Falha ao cancelar ordens: {e}")


def _cancelar_ordens_abertas_nao_reduce_only(session, symbol: str):
    """Remove cotações normais depois que uma ponta do MM foi executada.

    A cotação oposta criada para formar o spread não pode permanecer ativa
    depois que uma posição foi aberta: sem ``reduceOnly`` ela pode inverter a
    posição ou abrir uma posição contrária. Ordens algorítmicas de TP/SL não
    são retornadas por ``get_open_orders`` e permanecem intactas.
    """
    get_open_orders = getattr(session, "get_open_orders", None)
    cancel_order = getattr(session, "cancel_order", None)
    if not callable(get_open_orders) or not callable(cancel_order):
        raise AttributeError("adaptador sem get_open_orders/cancel_order")

    resposta = None
    ultima_excecao = None
    # Sem confirmar o livro, nao cancelar nem criar novas cotacoes neste ciclo.
    for tentativa in range(1, 4):
        try:
            resposta = get_open_orders(category="linear", symbol=symbol)
            break
        except Exception as exc:
            ultima_excecao = exc
            logger.warning(
                "[MM-LAT] Falha ao consultar ordens pendentes de %s (tentativa %s/3): %s",
                symbol,
                tentativa,
                exc,
            )
            if tentativa < 3:
                time.sleep(0.5 * tentativa)

    if resposta is None:
        logger.error(
            "[MM-LAT] Estado de ordens indisponivel para %s; mantendo modo seguro: %s",
            symbol,
            ultima_excecao,
        )
        return False

    ordens = resposta.get("result", {}).get("list", []) if isinstance(resposta, dict) else []
    canceladas = 0
    for ordem in ordens or []:
        reduce_only = ordem.get("reduceOnly", ordem.get("reduce_only", False))
        if str(reduce_only).strip().lower() in ("1", "true", "yes", "sim") or reduce_only is True:
            continue
        order_id = ordem.get("orderId") or ordem.get("ordId")
        if not order_id:
            continue
        try:
            cancel_order(category="linear", symbol=symbol, orderId=order_id)
            canceladas += 1
        except Exception as exc:
            logger.warning(
                "[MM-LAT] Falha ao cancelar ordem %s de %s: %s",
                order_id,
                symbol,
                exc,
            )

    logger.info(
        "[MM-LAT] Cotações não reduce-only canceladas após preenchimento: %s | %s",
        symbol,
        canceladas,
    )
    return True


def _quantidade_base_da_posicao(session, symbol: str, size: float) -> float:
    """Converte o tamanho da posição (contratos) para quantidade-base da OKX."""
    contratos = abs(float(size or 0))
    if contratos <= 0:
        return 0.0
    try:
        info = session.get_instruments_info(category="linear", symbol=symbol)
        rows = info.get("result", {}).get("list", []) if isinstance(info, dict) else []
        instrument = rows[0] if rows else {}
        contract_size = float(instrument.get("contractSize") or 0)
        if contract_size > 0:
            return round(contratos * contract_size, 12)
    except Exception as exc:
        logger.warning("[MM-LAT] Falha ao converter posição para quantidade-base: %s", exc)
    return round(contratos, 12)


def _existe_saida_reduce_only(session, symbol: str, exit_side: str) -> bool:
    """Evita duplicar a ordem-limite de saída a cada ciclo do monitor."""
    get_open_orders = getattr(session, "get_open_orders", None)
    if not callable(get_open_orders):
        return False
    try:
        resposta = get_open_orders(category="linear", symbol=symbol)
    except Exception as exc:
        # Estado desconhecido: nao criar uma segunda saida reduce-only.
        logger.warning(
            "[MM-LAT] Nao foi possivel consultar saida reduce-only de %s; "
            "nao criando duplicata: %s",
            symbol,
            exc,
        )
        return True
    ordens = resposta.get("result", {}).get("list", []) if isinstance(resposta, dict) else []
    alvo = str(exit_side).lower()
    for ordem in ordens or []:
        reduce_only = ordem.get("reduceOnly", ordem.get("reduce_only", False))
        lado = str(ordem.get("side") or "").lower()
        if (
            (reduce_only is True or str(reduce_only).strip().lower() in ("1", "true", "yes", "sim"))
            and lado == alvo
        ):
            return True
    return False


def ativar_market_making(symbol: str):
    criar_mm_lock(symbol)
    logger.info(f"[MM-LAT] Market Making Lateral ATIVADO para {symbol}.")


def desativar_market_making(symbol: str, motivo: str = "desativação solicitada", session=None):
    logger.info(f"[MM-LAT] Market Making Lateral DESATIVADO para {symbol}. Motivo: {motivo}")
    if session:
        _cancelar_ordens(session, symbol)
    remover_mm_lock(symbol)


def get_mm_lock_path(symbol: str) -> str:
    return str(_MM_STATE_DIR / f"mm_ativo_{symbol.upper()}.flag")


def criar_mm_lock(symbol: str):
    try:
        with open(get_mm_lock_path(symbol), "w") as f:
            f.write("MM_ATIVO")
    except Exception as e:
        logger.warning(f"[MM-LAT] Falha ao criar lock: {e}")


def remover_mm_lock(symbol: str):
    try:
        path = get_mm_lock_path(symbol)
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        logger.warning(f"[MM-LAT] Falha ao remover lock: {e}")


def _exchange_has_active_state(session, symbol: str):
    """Retorna (ativo, motivo), mantendo o lock se a consulta falhar.

    O flag local é apenas uma proteção contra concorrência. A decisão de
    bloquear uma nova entrada deve ser baseada no estado real da exchange.
    Em caso de erro de comunicação, usamos fail-closed e mantemos o lock.
    """
    try:
        get_positions = getattr(session, "get_positions", None)
        get_open_orders = getattr(session, "get_open_orders", None)
        if not callable(get_positions) or not callable(get_open_orders):
            return True, "adaptador sem consulta de posições/ordens"

        positions = get_positions(category="linear", symbol=symbol)
        if not isinstance(positions, dict) or positions.get("retCode") != 0:
            return True, "falha ao consultar posições"
        for position in positions.get("result", {}).get("list", []) or []:
            try:
                size = abs(float(position.get("size") or position.get("pos") or 0))
            except (TypeError, ValueError):
                size = 0.0
            if size > 0:
                return True, "posição aberta na exchange"

        orders = get_open_orders(category="linear", symbol=symbol)
        if not isinstance(orders, dict) or orders.get("retCode") != 0:
            return True, "falha ao consultar ordens pendentes"
        pending = orders.get("result", {}).get("list", []) or []
        if pending:
            return True, f"{len(pending)} ordem(ns) pendente(s) na exchange"

        return False, "sem posição e sem ordem pendente"
    except Exception as exc:
        logger.warning("[MM-LAT] Não foi possível validar lock na exchange: %s", exc)
        return True, "erro na consulta da exchange"


def mm_lock_ativo(symbol: str, session=None) -> bool:
    path = get_mm_lock_path(symbol)
    if not os.path.exists(path):
        return False

    # Quando há uma sessão, o flag só é mantido se a OKX confirmar risco real.
    if session is not None:
        ativo, motivo = _exchange_has_active_state(session, symbol)
        if not ativo:
            remover_mm_lock(symbol)
            logger.info(
                "[MM-LAT] Lock órfão removido para %s: %s. Pronto para nova entrada.",
                symbol,
                motivo,
            )
            return False
        logger.info("[MM-LAT] Lock mantido para %s: %s.", symbol, motivo)
        return True

    # Fallback para chamadas internas sem sessão: lock muito antigo é
    # considerado suspeito, mas não é apagado sem validação da exchange.
    try:
        idade = time.time() - os.path.getmtime(path)
        if idade > MM_LOCK_MAX_AGE_SECONDS:
            logger.warning(
                "[MM-LAT] Lock antigo detectado para %s (idade=%.0fs); "
                "aguardando validação da exchange.",
                symbol,
                idade,
            )
    except OSError:
        pass
    return True


def get_mm_cooldown_path(symbol: str) -> str:
    return str(_MM_STATE_DIR / f"mm_cooldown_{symbol.upper()}.flag")


def em_cooldown(symbol: str) -> float:
    try:
        path = get_mm_cooldown_path(symbol)
        if not os.path.exists(path):
            return 0
        with open(path, "r") as f:
            ts = float(f.read().strip() or 0)
        if ts > time.time():
            return ts
        os.remove(path)
        return 0
    except Exception:
        return 0


def set_cooldown(symbol: str, seconds: int):
    try:
        until = time.time() + seconds
        with open(get_mm_cooldown_path(symbol), "w") as f:
            f.write(str(until))
    except Exception as e:
        logger.warning(f"[MM-LAT] Falha ao registrar cooldown: {e}")


def _detectar_regime(regime_getter: Optional[Callable[[], int]], regime_atual: int) -> int:
    if regime_getter is None:
        return regime_atual
    try:
        return int(regime_getter())
    except Exception:
        return regime_atual


def executar_market_making(
    session,
    symbol: str,
    df: pd.DataFrame,
    regime_atual: int,
    regime_getter: Optional[Callable[[], int]] = None,
    cfg: Optional[Dict] = None,
    decision_id: Optional[str] = None,
):
    """
    Market Making passivo para regime de lateralidade (nível 3).
    - Duas ordens LIMIT (buy abaixo, sell acima).
    - Spread baseado em ATR% * preço * fator_lateralidade.
    - Time-stop obrigatório: cancela e recota após X segundos.
    - Kill switch: regime 4, ATR% acima do limite, spread real > 2x spread teórico.
    """
    cfg_local = {**DEFAULT_CFG, **(cfg or {})}
    cfg_local["fator_lateralidade"] = _get_env_override(
        "MM_FATOR_LATERALIDADE", symbol, float, cfg_local["fator_lateralidade"]
    )
    cfg_local["time_stop_segundos"] = _get_env_override(
        "MM_TIME_STOP", symbol, int, cfg_local["time_stop_segundos"]
    )
    cfg_local["atr_pct_max"] = _get_env_override(
        "MM_ATR_PCT_MAX", symbol, float, cfg_local["atr_pct_max"]
    )
    cfg_local["spread_real_max_mult"] = _get_env_override(
        "MM_SPREAD_REAL_MAX_MULT", symbol, float, cfg_local["spread_real_max_mult"]
    )
    cfg_local["spread_min_pct"] = _get_env_override(
        "MM_SPREAD_MIN_PCT", symbol, float, cfg_local.get("spread_min_pct", 0.0001)
    )
    cfg_local["min_qty_default"] = _get_env_override(
        "MM_MIN_QTY_DEFAULT", symbol, float, cfg_local["min_qty_default"]
    )
    cfg_local["step_default"] = _get_env_override(
        "MM_STEP_DEFAULT", symbol, float, cfg_local["step_default"]
    )
    cfg_local["cancel_open_before"] = bool(
        _get_env_override(
            "MM_CANCEL_OPEN_BEFORE",
            symbol,
            int,
            1 if cfg_local.get("cancel_open_before", True) else 0,
        )
    )
    cfg_local["regime_confirm_cycles"] = max(
        _get_env_override("MM_REGIME_CONFIRM_CYCLES", symbol, int, cfg_local["regime_confirm_cycles"]),
        1,
    )
    cfg_local["atr_hard_max"] = min(
        _get_env_override("MM_ATR_HARD_MAX", symbol, float, cfg_local["atr_hard_max"]),
        0.10,
    )
    cfg_local["volume_ratio_min"] = _get_env_override(
        "MM_VOLUME_RATIO_MIN", symbol, float, cfg_local["volume_ratio_min"]
    )
    cfg_local["volume_ratio_max"] = _get_env_override(
        "MM_VOLUME_RATIO_MAX", symbol, float, cfg_local["volume_ratio_max"]
    )
    cfg_local["candle_body_max"] = _get_env_override(
        "MM_CANDLE_BODY_MAX", symbol, float, cfg_local["candle_body_max"]
    )
    cfg_local["spread_real_max_pct"] = _get_env_override(
        "MM_SPREAD_REAL_MAX_PCT", symbol, float, cfg_local["spread_real_max_pct"]
    )
    cfg_local["min_book_depth_quote"] = _get_env_override(
        "MM_MIN_BOOK_DEPTH_QUOTE", symbol, float, cfg_local["min_book_depth_quote"]
    )
    mm_cfg = canonical_config()

    cooldown_until = em_cooldown(symbol)
    loss_buy_count = 0
    loss_sell_count = 0
    tracked_entry_price = None
    tracked_side = None
    position_tracked = False

    if time.time() < cooldown_until:
        logger.info("⏸️ MM em cooldown — aguardando.")
        _finish_decision("BLOCKED", "MM_COOLDOWN")
        return {"status": "cooldown"}

    lock_ativo = mm_lock_ativo(symbol, session=session)
    if lock_ativo:
        posicao_existente = False
        try:
            pos_snapshot = session.get_positions(category="linear", symbol=symbol)
            for pos_item in (pos_snapshot.get("result", {}).get("list", []) or []):
                try:
                    pos_size = abs(float(pos_item.get("size") or pos_item.get("pos") or 0))
                except (TypeError, ValueError):
                    pos_size = 0.0
                if pos_size > 0:
                    posicao_existente = True
                    break
        except Exception as exc:
            logger.warning("[MM-LAT] Falha ao ler posicao para gestao do lock: %s", exc)
        if posicao_existente:
            logger.info(
                f"[MM-LAT] Posicao existente detectada para {symbol}; "
                "lock mantido para impedir novas entradas. Iniciando gestao."
            )
        logger.warning(f"[MM-LAT] Lock ativo encontrado para {symbol}. MM não será iniciado.")
        if not posicao_existente:
            _finish_decision("BLOCKED", "MM_LOCK_ACTIVE")
            return {"status": "bloqueado", "motivo": "lock_ativo"}

    tempo_inicio = time.time()
    regime_corrente = regime_atual
    mm_cycle_pnl = 0.0
    mm_trades = 0
    mm_start_time = None
    mm_notified_start = False
    regime3_confirmed_cycles = 0
    accepted_order_ids = {}
    persisted_mm_trade_id = None
    decision_finalized = False

    def _finish_decision(status: str, reason: str):
        nonlocal decision_finalized
        if decision_id and not decision_finalized:
            finalize_decision(decision_id, status, reason)
            decision_finalized = True

    def _record_mm_fill(position: dict, entry_price: float, position_side: str, quantity: float):
        """Persist the exchange-confirmed MM entry and link it once."""
        nonlocal persisted_mm_trade_id
        if persisted_mm_trade_id or quantity <= 0 or not entry_price:
            return
        side_lower = str(position_side or "").strip().lower()
        order_id = accepted_order_ids.get(side_lower)
        stable_suffix = order_id or f"{side_lower}:{entry_price:.10f}:{quantity:.10f}"
        trade_id = f"mm_entry:{symbol}:{stable_suffix}"
        try:
            salvar_trade_sqlite({
                "trade_id": trade_id,
                "decision_id": decision_id,
                "timestamp": datetime.utcnow().isoformat(),
                "symbol": symbol,
                "side": side_lower,
                "quantity": abs(float(quantity)),
                "entry_price": float(entry_price),
                "strategy": "MARKET_MAKING_LATERAL",
                "regime": 3,
                "status": "aberto",
                "order_id": order_id,
            })
            persisted_mm_trade_id = trade_id
            if decision_id:
                link_trade(decision_id, trade_id=trade_id, order_id=order_id, status="OPENED")
                record_event(
                    decision_id,
                    "FILL_RECORDED",
                    "EXECUTION",
                    status="SUCCESS",
                    component="market_making",
                    payload={
                        "strategy": "MARKET_MAKING_LATERAL",
                        "symbol": symbol,
                        "side": side_lower,
                        "quantity": abs(float(quantity)),
                        "entry_price": float(entry_price),
                        "order_id": order_id,
                    },
                )
            logger.info(
                "[MM-LAT] Entrada persistida | trade_id=%s | order_id=%s | side=%s | qty=%s | entry=%s",
                trade_id, order_id, side_lower, quantity, entry_price,
            )
        except Exception as exc:
            logger.error("[MM-LAT] Falha ao persistir entrada confirmada: %s", type(exc).__name__)

    def _telegram_safe(msg: str):
        try:
            send_telegram_message(msg)
        except Exception as e:
            logger.warning(f"[MM-LAT] Falha ao enviar Telegram: {e}")

    def notificar_fim(motivo: str):
        if not mm_notified_start:
            return
        duracao_min = 0.0
        if mm_start_time:
            duracao_min = round((time.time() - mm_start_time) / 60, 2)
        emoji = "🟢" if mm_cycle_pnl >= 0 else "🔴"
        mensagem = (
            f"{emoji} Market Making encerrado\n"
            f"Ativo: {symbol}\n"
            f"Resultado: {'POSITIVO' if mm_cycle_pnl >= 0 else 'NEGATIVO'}\n"
            f"PnL do ciclo: {mm_cycle_pnl:.2f}\n"
            f"Trades no ciclo: {mm_trades}\n"
            f"Duração: {duracao_min} min\n"
            f"Motivo da saída: {motivo}"
        )
        _telegram_safe(mensagem)

    def _proteger_posicao_mm(
        posicao: dict,
        entry_price: float,
        position_side: str,
        spread_ref: Optional[float] = None,
        atr_ref: Optional[float] = None,
    ) -> bool:
        """Arma a protecao especifica do MM sem reutilizar TP/SL de tendencia.

        O MM realiza a captura do spread. Portanto, o TP deve ficar proximo
        da distancia do spread e o SL deve ser apenas uma protecao de
        emergencia baseada em poucos spreads/ATR, em vez de usar SL_PCT_MIN
        e RRR globais das estrategias direcionais.
        """
        if not hasattr(session, "set_trading_stop") or not entry_price:
            return False

        close_algos = posicao.get("closeOrderAlgo") or []
        algo_ativo = next(
            (algo for algo in close_algos
             if isinstance(algo, dict)
             and algo.get("slTriggerPx") not in (None, "", "0")
             and algo.get("tpTriggerPx") not in (None, "", "0")
             and str(algo.get("state") or "").lower() not in ("canceled", "cancelled", "order_failed")),
            None,
        ) if isinstance(close_algos, list) else None
        if algo_ativo:
            logger.info("[MM-LAT][PROTECAO] %s ja possui TP/SL confirmado na OKX.", symbol)
            return True

        try:
            side_lower = str(position_side or "").lower()
            if side_lower not in ("buy", "sell"):
                logger.error("[MM-LAT][PROTECAO] Lado invalido para %s: %s", symbol, position_side)
                return False

            tp_spread_mult = max(
                _get_env_override("MM_TP_SPREAD_MULT", symbol, float, 1.0),
                0.5,
            )
            sl_spread_mult = max(
                _get_env_override("MM_SL_SPREAD_MULT", symbol, float, 2.0),
                1.0,
            )
            tp_atr_mult = max(
                _get_env_override("MM_TP_ATR_MULT", symbol, float, 1.5),
                0.0,
            )
            sl_atr_mult = max(
                _get_env_override("MM_SL_ATR_MULT", symbol, float, 3.0),
                0.0,
            )
            tick_size = _obter_tick(session, symbol)
            spread_value = abs(float(spread_ref or 0.0))
            atr_value = abs(float(atr_ref or 0.0))

            # O spread e a referencia principal do MM. O ATR funciona como
            # piso de seguranca para evitar protecao estreita demais.
            tp_distance = max(
                spread_value * tp_spread_mult,
                atr_value * tp_atr_mult,
                tick_size * 2.0,
            )
            sl_distance = max(
                spread_value * sl_spread_mult,
                atr_value * sl_atr_mult,
                tick_size * 3.0,
            )
            if tp_distance <= 0 or sl_distance <= 0:
                logger.error(
                    "[MM-LAT][PROTECAO] Distancias invalidas para %s | spread=%s atr=%s",
                    symbol,
                    spread_ref,
                    atr_ref,
                )
                return False

            if side_lower == "buy":
                close_side = "sell"
                stop_loss = entry_price - sl_distance
                take_profit = entry_price + tp_distance
            else:
                close_side = "buy"
                stop_loss = entry_price + sl_distance
                take_profit = entry_price - tp_distance

            pos_side = str(posicao.get("posSide") or "").lower()
            if pos_side not in ("long", "short"):
                pos_side = None

            resposta = session.set_trading_stop(
                category="linear",
                symbol=symbol,
                stopLoss=round(stop_loss, 8),
                takeProfit=round(take_profit, 8),
                side=close_side,
                posSide=pos_side,
            )
            if isinstance(resposta, dict) and resposta.get("retCode") not in (None, 0):
                raise RuntimeError(resposta.get("retMsg") or resposta)

            # O POST 200 apenas confirma o recebimento da solicitacao. Valida
            # novamente a posicao para garantir que a OKX criou closeOrderAlgo.
            confirmado = None
            for _ in range(3):
                time.sleep(0.35)
                verificacao = session.get_positions(category="linear", symbol=symbol)
                linhas = (verificacao.get("result", {}).get("list", []) or []) if isinstance(verificacao, dict) else []
                linha = next((item for item in linhas if abs(float(item.get("size") or 0)) > 0), None)
                algos = (linha or {}).get("closeOrderAlgo") or []
                confirmado = next(
                    (algo for algo in algos
                     if isinstance(algo, dict)
                     and algo.get("slTriggerPx") not in (None, "", "0")
                     and algo.get("tpTriggerPx") not in (None, "", "0")
                     and str(algo.get("state") or "").lower() not in ("canceled", "cancelled", "order_failed")),
                    None,
                ) if isinstance(algos, list) else None
                if confirmado:
                    break
            if not confirmado:
                raise RuntimeError("OKX nao confirmou closeOrderAlgo na leitura da posicao")
            logger.info(
                "[MM-LAT][PROTECAO] TP/SL MM confirmado para %s | lado=%s | "
                "spread=%.8f atr=%.8f | distSL=%.8f distTP=%.8f | SL=%.8f | TP=%.8f",
                symbol,
                side_lower,
                spread_value,
                atr_value,
                sl_distance,
                tp_distance,
                stop_loss,
                take_profit,
            )
            return True
        except Exception as exc:
            logger.error("[MM-LAT][PROTECAO] Falha ao armar TP/SL para %s: %s", symbol, exc)
            return False

    while True:
        time.sleep(2)

        # Recarrega dados e recalcula ATR dinamicamente
        df_raw = get_market_data(symbol, session, interval=MM_TIMEFRAME, limit=200)
        if df_raw is None or df_raw.empty:
            logger.warning("[MM-LAT] Falha ao carregar dados para recalcular ATR. Tentando novamente.")
            continue
        df = calculate_indicators(df_raw).bfill().ffill()
        preco_atual = float(df["close"].iloc[-1])
        atr = float(df["ATR"].iloc[-1])
        atr_pct = (atr / preco_atual) * 100  # %

        # O regime precisa ser confirmado antes de qualquer nova cotação.
        # Um único candle em regime 3 não é suficiente para habilitar MM.
        if regime_getter is None:
            try:
                regime_corrente = int(RegimeDetector().detectar(df, symbol=symbol))
            except Exception as exc:
                logger.warning("[MM-LAT] Falha ao recalcular regime: %s", exc)
                regime_corrente = 4
        else:
            regime_corrente = _detectar_regime(regime_getter, regime_corrente)

        if regime_corrente == 3:
            regime3_confirmed_cycles += 1
        else:
            regime3_confirmed_cycles = 0

        volume_atual = float(df["volume"].iloc[-1]) if "volume" in df.columns else 0.0
        volume_medio = float(df["volume"].rolling(20).mean().iloc[-1]) if "volume" in df.columns else 0.0
        volume_ratio = volume_atual / volume_medio if volume_medio > 0 else 0.0
        candle_high = float(df["high"].iloc[-1])
        candle_low = float(df["low"].iloc[-1])
        candle_open = float(df["open"].iloc[-1])
        candle_range = max(candle_high - candle_low, 1e-12)
        candle_body_ratio = abs(preco_atual - candle_open) / candle_range

        posicao_aberta = False
        inventory_qty = 0.0
        try:
            pos_snapshot = session.get_positions(category="linear", symbol=symbol)
            for pos_item in (pos_snapshot.get("result", {}).get("list", []) or []):
                raw_size = float(pos_item.get("size") or pos_item.get("pos") or 0)
                if abs(raw_size) > 0:
                    posicao_aberta = True
                    side = str(pos_item.get("side") or "").lower()
                    inventory_qty += abs(raw_size) if side in ("buy", "long") else -abs(raw_size)
        except Exception as exc:
            logger.warning("[MM-LAT] Falha ao verificar posição antes dos filtros: %s", exc)

        # Paridade com o backtest: o MM sÃ³ usa o regime 3 como regra de entrada.
        filtros_entrada_ok = posicao_aberta or regime_corrente == 3
        if not filtros_entrada_ok:
            logger.info(
                "[MM-LAT] Entrada aguardando regime do backtest | regime=%s",
                regime_corrente,
            )
            if regime_corrente != 3 and not posicao_aberta:
                _cancelar_ordens_abertas_nao_reduce_only(session, symbol)
                remover_mm_lock(symbol)
            continue

        # O último fechamento pode estar defasado. Para cotar MM, o centro
        # obrigatório é o midpoint atual do livro, não o close do candle.
        topo = _obter_topo_livro(session, symbol)
        if topo is None:
            logger.warning("[MM-LAT] Livro sem bid/ask válido; não vou cotar neste ciclo.")
            continue
        best_bid, best_ask, midpoint = topo
        quotes = quote_from_closes(
            df["close"].iloc[-20:],
            midpoint,
            inventory_qty,
            mm_cfg["max_inventory_qty"],
            mm_cfg,
        )
        spread = midpoint * quotes["spread_pct"]
        quote_bid = quotes["bid"]
        quote_ask = quotes["ask"]
        tick_size = _obter_tick(session, symbol)
        max_quote_distance = max(
            midpoint * float(cfg_local.get("quote_max_distance_pct", 0.0008)),
            tick_size * 2,
        )
        # Garante que o spread alvo não produza cotações além do limite por
        # lado. O spread real do livro ainda precisa ser respeitado.
        # Backtest parity: keep the theoretical spread from quote_from_closes.

        # Se o preço se moveu, remove as cotações antigas e libera uma nova
        # cotação no midpoint atual. A posição aberta não é afetada: o helper
        # só considera ordens normais quando não há posição na exchange.
        if mm_lock_ativo(symbol, session) and _cotacoes_mm_desatualizadas(
            session, symbol, midpoint, float(cfg_local.get("quote_max_distance_pct", 0.0008))
        ):
            logger.info("[MM-LAT] Recotando: ordens fora do midpoint/faixa permitida.")
            _cancelar_ordens(session, symbol)
            remover_mm_lock(symbol)

        if regime_corrente == 3 and not mm_lock_ativo(symbol):
            # Só cria lock e ordens na primeira passagem do loop
            ativar_market_making(symbol)
            if cfg_local.get("cancel_open_before", True):
                _cancelar_ordens(session, symbol)

            qty, sizing_equity, sizing_available = _obter_sizing_por_capital(
                session,
                symbol,
                midpoint,
                mm_cfg["order_qty"],
            )
            if qty <= 0:
                logger.warning(
                    "[MM-LAT] Entrada nao enviada: quantidade solicitada excede o limite de capital/margem."
                )
                remover_mm_lock(symbol)
                continue

            qty_capital = qty
            qty = _ajustar_quantidade_minima(session, symbol, qty, cfg_local)
            if qty > qty_capital * (1.0 + 1e-9):
                logger.warning(
                    "[MM-LAT] Entrada nao enviada: lote minimo normalizado %.8f excede o limite de risco %.8f.",
                    qty,
                    qty_capital,
                )
                remover_mm_lock(symbol)
                continue
            buy_price = quote_bid
            sell_price = quote_ask
            # Quantização conservadora: BUY sempre abaixo do ask e SELL acima
            # do bid, preservando PostOnly e evitando ordem invertida.
            buy_price = math.floor(buy_price / tick_size) * tick_size
            sell_price = math.ceil(sell_price / tick_size) * tick_size
            buy_price = round(buy_price, 10)
            sell_price = round(sell_price, 10)

            if not (buy_price < best_ask and sell_price > best_bid):
                logger.warning(
                    "[MM-LAT] Cotações inválidas para PostOnly | bid=%.8f ask=%.8f buy=%.8f sell=%.8f",
                    best_bid, best_ask, buy_price, sell_price,
                )
                remover_mm_lock(symbol)
                continue

            if False:  # backtest parity: no extra quote-distance filter
                logger.warning("[MM-LAT] Cotações excederam a distância máxima; não enviando.")
                remover_mm_lock(symbol)
                continue

            logger.info(
                f"[MM-LAT] Regime={regime_corrente} | ATR%={atr_pct:.4f}% | "
                f"Bid={best_bid:.6f} | Ask={best_ask:.6f} | Mid={midpoint:.6f} | "
                f"SpreadTeor={spread:.6f} | Buy@{buy_price} | Sell@{sell_price} | Qty={qty}"
            )

            gate_buy = None
            gate_sell = None
            try:
                gate_buy = executar_ordem_com_validacao(
                    {
                        "trade_id": f"mm_lat:{symbol}:buy:{int(time.time())}",
                        "symbol": symbol,
                        "side": "buy",
                        "entry_price": buy_price,
                        "qty": qty,
                        "equity": sizing_equity,
                        "available_margin": sizing_available,
                        "decision_timestamp": datetime.utcnow().isoformat() if "datetime" in globals() else None,
                        "ttl_seconds": int(float(os.getenv("SIGNAL_TTL_SECONDS", "60"))),
                        "order_params": {
                            "category": "linear",
                            "symbol": symbol,
                            "side": "Buy",
                            "orderType": "Limit",
                            "qty": qty,
                            "price": buy_price,
                            "timeInForce": "PostOnly",
                            "reduceOnly": False,
                        },
                        "execution_fn": lambda payload: session.place_order(**payload),
                    }
                )
                if gate_buy.get("status") == "executado":
                    order_id = (gate_buy.get("detalhes") or {}).get("order_id")
                    if order_id:
                        accepted_order_ids["buy"] = str(order_id)
                    if decision_id:
                        record_event(decision_id, "ORDER_ACCEPTED", "ORDER", status="ACCEPTED", component="market_making", payload={"order_id": order_id, "side": "buy"})
                    logger.info(f"[MM-LAT] Ordem BUY colocada @ {buy_price}")
                else:
                    logger.warning(f"[MM-LAT] BUY bloqueada pelo execution gate: {gate_buy.get('motivo')}")
            except Exception as e:
                logger.error(f"[MM-LAT] Falha ao enviar BUY: {e}")

            try:
                gate_sell = executar_ordem_com_validacao(
                    {
                        "trade_id": f"mm_lat:{symbol}:sell:{int(time.time())}",
                        "symbol": symbol,
                        "side": "sell",
                        "entry_price": sell_price,
                        "qty": qty,
                        "equity": sizing_equity,
                        "available_margin": sizing_available,
                        "decision_timestamp": datetime.utcnow().isoformat() if "datetime" in globals() else None,
                        "ttl_seconds": int(float(os.getenv("SIGNAL_TTL_SECONDS", "60"))),
                        "order_params": {
                            "category": "linear",
                            "symbol": symbol,
                            "side": "Sell",
                            "orderType": "Limit",
                            "qty": qty,
                            "price": sell_price,
                            "timeInForce": "PostOnly",
                            "reduceOnly": False,
                        },
                        "execution_fn": lambda payload: session.place_order(**payload),
                    }
                )
                if gate_sell.get("status") == "executado":
                    order_id = (gate_sell.get("detalhes") or {}).get("order_id")
                    if order_id:
                        accepted_order_ids["sell"] = str(order_id)
                    if decision_id:
                        record_event(decision_id, "ORDER_ACCEPTED", "ORDER", status="ACCEPTED", component="market_making", payload={"order_id": order_id, "side": "sell"})
                    logger.info(f"[MM-LAT] Ordem SELL colocada @ {sell_price}")
                else:
                    logger.warning(f"[MM-LAT] SELL bloqueada pelo execution gate: {gate_sell.get('motivo')}")
            except Exception as e:
                logger.error(f"[MM-LAT] Falha ao enviar SELL: {e}")

            # Telegram somente depois que a OKX aceitar pelo menos uma ordem.
            # Bloqueios de risco, sinais e tentativas não geram alerta de entrada.
            ordem_buy_aceita = (
                isinstance(gate_buy, dict) and gate_buy.get("status") == "executado"
            )
            ordem_sell_aceita = (
                isinstance(gate_sell, dict) and gate_sell.get("status") == "executado"
            )
            if not mm_notified_start and (ordem_buy_aceita or ordem_sell_aceita):
                _finish_decision("AUTHORIZED", "ORDER_ACCEPTED")
                mm_start_time = time.time()
                lados = []
                if ordem_buy_aceita:
                    lados.append("BUY")
                if ordem_sell_aceita:
                    lados.append("SELL")
                _telegram_safe(
                    "🔵 Ordem Market Making aceita na OKX\n"
                    f"Ativo: {symbol}\n"
                    "Regime: Lateralidade (3)\n"
                    f"Lados: {'/'.join(lados)}\n"
                    f"Spread alvo: {spread:.4f}"
                )
                mm_notified_start = True

        regime_corrente = _detectar_regime(regime_getter, regime_corrente)
        if regime_corrente == 4:
            desativar_market_making(symbol, "Kill switch: regime 4", session)
            notificar_fim("Regime 4 (defesa total)")
            _finish_decision("BLOCKED", "REGIME_4")
            return {"status": "abortado", "motivo": "regime_4"}
        if regime_corrente != 3:
            desativar_market_making(symbol, "Regime mudou", session)
            notificar_fim("Regime mudou")
            _finish_decision("BLOCKED", "REGIME_CHANGED")
            return {"status": "abortado", "motivo": "regime_mudou"}

        if False:  # backtest parity: no live ATR kill switch
            desativar_market_making(symbol, "Kill switch: ATR% acima do limite", session)
            notificar_fim("Kill switch de volatilidade (ATR)")
            _finish_decision("BLOCKED", "ATR_LIMIT")
            return {"status": "abortado", "motivo": "atr_limite"}

        spread_real = None
        if False:  # backtest parity: no live spread availability gate
            desativar_market_making(symbol, "Kill switch: spread real indisponível", session)
            notificar_fim("Spread real indisponível")
            _finish_decision("BLOCKED", "SPREAD_UNAVAILABLE")
            return {"status": "abortado", "motivo": "spread_real_indisponivel"}
        if False:  # backtest parity: no live spread kill switch
            desativar_market_making(symbol, "Kill switch: spread real > limite", session)
            notificar_fim("Kill switch de liquidez (spread real)")
            _finish_decision("BLOCKED", "SPREAD_LIMIT")
            return {"status": "abortado", "motivo": "spread_real_excedido"}

        # Checa execução
        pos = session.get_positions(category="linear", symbol=symbol)
        size = 0.0
        entry_price = None
        side = None

        positions = (pos.get("result") or {}).get("list", []) if isinstance(pos, dict) else []
        if pos.get("retCode") == 0 and positions:
            p = positions[0]
            size = float(p.get("size") or 0)
            entry_price = float(p.get("entryPrice") or 0)
            side = p.get("side")

        # A OKX representa posições vendidas com ``size`` negativo.
        # O gerenciamento/TP deve considerar long e short igualmente.
        if abs(size) > 0 and entry_price and side:
            side_lower = side.lower()
            if not _proteger_posicao_mm(
                p,
                entry_price,
                side,
                spread_ref=spread,
                atr_ref=atr,
            ):
                # Nao coloca uma saida parcial/recotacao enquanto a protecao
                # da corretora nao estiver confirmada; o proximo ciclo tenta
                # novamente sem declarar a posicao como gerenciada.
                continue

            # Uma das cotacoes do spread foi executada. Remova a cotacao
            # oposta normal antes de criar a saida; caso contrario ela pode
            # inverter a posicao quando for preenchida depois.
            try:
                _cancelar_ordens_abertas_nao_reduce_only(session, symbol)
            except Exception as exc:
                logger.error(
                    f"[MM-LAT] Falha ao cancelar cotacao oposta de {symbol}: {exc}"
                )

            # Estrat?gia de sa?da: pr?ximo toque do spread te?rico.
            target = entry_price + spread if side_lower == "buy" else entry_price - spread
            try:
                exit_side = "Sell" if side_lower == "buy" else "Buy"
                if _existe_saida_reduce_only(session, symbol, exit_side):
                    logger.info(
                        f"[MM-LAT] Saida reduce-only ja existente para {symbol} "
                        f"lado={exit_side}; nao duplicando."
                    )
                else:
                    qty_saida = _quantidade_base_da_posicao(session, symbol, size)
                    session.place_order(
                        category="linear",
                        symbol=symbol,
                        side=exit_side,
                        orderType="Limit",
                        qty=qty_saida,
                        price=round(target, 4),
                        timeInForce="PostOnly",
                        # O cliente OKX recebe snake_case. O antigo reduceOnly
                        # era absorvido por **kwargs e nao chegava ao payload.
                        reduce_only=True,
                    )
                    logger.info(
                        f"[MM-LAT] Posicao detectada ({size}) lado={side} | "
                        f"saida reduce-only @ {target} | qty_base={qty_saida}"
                    )
            except Exception as e:
                logger.error(f"[MM-LAT] Falha ao armar saida reduce-only: {e}")

            # Registra posi??o para avaliar PnL no fechamento.
            _record_mm_fill(p, entry_price, side, abs(size))
            tracked_entry_price = entry_price
            tracked_side = side
            position_tracked = True
            continue

        if position_tracked and abs(size) <= 0:
            exit_price = 0.0
            side_lower = tracked_side.lower() if tracked_side else None

            # Fonte primária: histórico de PnL fechado
            try:
                resp = session.get_closed_pnl(category="linear", symbol=symbol, limit=1)
                if (
                    resp.get("retCode") == 0
                    and resp.get("result", {}).get("list")
                    and resp["result"]["list"][0].get("exitPrice")
                ):
                    exit_price = float(resp["result"]["list"][0].get("exitPrice") or 0)
            except Exception as e:
                logger.warning(f"[MM-LAT] Falha ao obter exitPrice do PnL fechado: {e}")

            # Fallback: avgExitPrice da posição (caso a posição ainda exista)
            if exit_price <= 0:
                try:
                    posicao = session.get_positions(category="linear", symbol=symbol)
                    if (
                        posicao.get("retCode") == 0
                        and posicao.get("result", {}).get("list")
                        and posicao["result"]["list"][0].get("avgExitPrice")
                    ):
                        exit_price = float(posicao["result"]["list"][0].get("avgExitPrice") or 0)
                except Exception as e:
                    logger.warning(f"[MM-LAT] Falha ao ler avgExitPrice: {e}")

            if not exit_price or exit_price <= 0 or not tracked_entry_price or not side_lower:
                logger.warning("[MM-LAT] exit_price inválido ou indisponível. Contadores não atualizados.")
            else:
                pnl = exit_price - tracked_entry_price if side_lower == "buy" else tracked_entry_price - exit_price
                if pnl < 0:
                    if side_lower == "buy":
                        loss_buy_count += 1
                    elif side_lower == "sell":
                        loss_sell_count += 1
                    logger.warning(
                        f"[MM-LAT] Perda registrada no lado {tracked_side}. "
                        f"LossBuy={loss_buy_count} | LossSell={loss_sell_count}"
                    )
                else:
                    loss_buy_count = 0
                    loss_sell_count = 0
                mm_cycle_pnl += pnl
                mm_trades += 1

            tracked_entry_price = None
            tracked_side = None
            position_tracked = False

            if (
                loss_buy_count >= MM_MAX_CONSECUTIVE_LOSSES
                or loss_sell_count >= MM_MAX_CONSECUTIVE_LOSSES
            ):
                logger.warning(
                    "[MM-LAT] Defesa: perdas consecutivas atingiram limite. "
                    f"Cooldown de {MM_COOLDOWN_SECONDS}s."
                )
                _cancelar_ordens(session, symbol)
                desativar_market_making(symbol, "perdas_consecutivas", session)
                set_cooldown(symbol, MM_COOLDOWN_SECONDS)
                notificar_fim("Cooldown por perdas consecutivas")
                _finish_decision("AUTHORIZED" if persisted_mm_trade_id else "BLOCKED", "POSITION_CLOSED")
                return {"status": "cooldown", "motivo": "perdas_consecutivas"}
        if time.time() - tempo_inicio >= cfg_local["time_stop_segundos"]:
            logger.info(f"[MM-LAT] Time-stop atingido ({cfg_local['time_stop_segundos']}s). Cancelando e recotando.")
            _cancelar_ordens(session, symbol)
            desativar_market_making(symbol, "time_stop", session)
            notificar_fim("Time-stop do Market Making")
            _finish_decision("AUTHORIZED" if persisted_mm_trade_id else "BLOCKED", "TIME_STOP")
            return {"status": "timeout", "motivo": "time_stop"}
