"""
ordem_limite_entrada.py
=====================================================
Sistema profissional de entrada por ORDEM LIMITE

Separa corretamente:
1. INTENÇÃO DE ENTRADA (já validada)
2. EXECUÇÃO REAL (aguarda confirmação)

Evita:
- Entradas em breakouts falsos
- Consumo de taxa desnecessário
- Erros de Qty invalid
=====================================================
"""

import time
import logging
import os
from datetime import datetime
from typing import Dict, Optional, Tuple

from execution.execution_gate import executar_ordem_com_validacao

logger = logging.getLogger(__name__)

# =====================================================
# CONFIGURAÇÕES
# =====================================================

# Offset inteligente baseado em ATR
OFFSET_ENTRADA_ATR_FRAC = float(os.getenv("OFFSET_ENTRADA_ATR_FRAC", 0.10))

# Timeout máximo para aguardar execução
TIMEOUT_ENTRADA_SEGUNDOS = int(os.getenv("TIMEOUT_ENTRADA_SEGUNDOS", 180))

# Intervalo de verificação
INTERVALO_CHECK_ENTRADA = int(os.getenv("INTERVALO_CHECK_ENTRADA", 3))


# =====================================================
# FUNÇÕES AUXILIARES
# =====================================================

def calcular_preco_limite(
    entry_price: float,
    side: str,
    atr: float,
    offset_frac: float = OFFSET_ENTRADA_ATR_FRAC
) -> float:
    """
    Calcula preço da ordem limite com offset inteligente
    
    BUY: preco_limite = entry_price - (ATR * offset)
    SELL: preco_limite = entry_price + (ATR * offset)
    """
    offset = atr * offset_frac
    
    if side.lower() == "buy":
        preco_limite = entry_price - offset
    else:  # sell
        preco_limite = entry_price + offset
    
    logger.info(
        f"💰 Preço limite calculado: {preco_limite:.2f} "
        f"(Entry={entry_price:.2f}, Offset={offset:.2f})"
    )
    
    return preco_limite


def arredondar_preco_tick(preco: float, tick_size: float) -> float:
    """Arredonda preço conforme tickSize da Bybit"""
    if tick_size <= 0:
        return round(preco, 2)
    
    casas_decimais = len(str(tick_size).split('.')[-1].rstrip('0'))
    return round(round(preco / tick_size) * tick_size, casas_decimais)


# =====================================================
# ENVIO DE ORDEM LIMITE
# =====================================================

def enviar_ordem_limite_entrada(
    api,
    symbol: str,
    side: str,
    qty: float,
    preco_limite: float,
    tick_size: float = 0.01,
    qty_step: float = 0.001,
    trade_id: Optional[str] = None,
    decision_timestamp: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
) -> Optional[Dict]:
    """
    Envia ordem LIMITE de entrada (SEM SL/TP)
    
    Returns:
        Dict com orderId e detalhes, ou None se falhar
    """
    
    # Arredondar preço conforme tickSize
    preco_limite = arredondar_preco_tick(preco_limite, tick_size)
    
    # Arredondar quantidade
    qty_decimals = len(str(qty_step).split('.')[-1].rstrip('0'))
    qty_ajustado = round(qty, qty_decimals)
    
    params = {
        "category": "linear",
        "symbol": symbol,
        "side": "Buy" if side.lower() == "buy" else "Sell",
        "orderType": "Limit",
        "qty": f"{qty_ajustado:.{qty_decimals}f}",
        "price": f"{preco_limite:.2f}",
        "timeInForce": "GTC"
    }
    
    try:
        logger.info(f"📤 Enviando ORDEM LIMITE: {params}")
        gate_result = executar_ordem_com_validacao(
            {
                "trade_id": trade_id,
                "symbol": symbol,
                "side": side,
                "entry_price": preco_limite,
                "qty": qty_ajustado,
                "decision_timestamp": decision_timestamp,
                "ttl_seconds": ttl_seconds or int(float(os.getenv("SIGNAL_TTL_SECONDS", "180"))),
                "order_params": params,
                "execution_fn": lambda payload: api.place_order(**payload),
            }
        )
        if gate_result.get("status") != "executado":
            logger.warning(f"Execution gate bloqueou ordem limite: {gate_result.get('motivo')}")
            return None

        resposta = (gate_result.get("detalhes") or {}).get("resposta")
        
        if resposta.get("retCode") != 0:
            logger.error(f"❌ Erro ao enviar ordem limite: {resposta.get('retMsg')}")
            return None
        
        result = resposta.get("result", {})
        order_id = result.get("orderId")
        
        logger.info(f"✅ Ordem limite enviada com sucesso! OrderID: {order_id}")
        
        return {
            "orderId": order_id,
            "symbol": symbol,
            "side": side,
            "qty": qty_ajustado,
            "price": preco_limite,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Exceção ao enviar ordem limite: {e}", exc_info=True)
        return None


# =====================================================
# VERIFICAÇÃO DE EXECUÇÃO
# =====================================================

def verificar_execucao_ordem_entrada(
    api,
    symbol: str,
    order_id: str
) -> Tuple[bool, Optional[float], Optional[float]]:
    """
    Verifica se a ordem limite foi executada
    
    Returns:
        (executada: bool, entry_price: float, qty_executado: float)
    """
    try:
        resposta = api.get_order_history(
            category="linear",
            symbol=symbol,
            orderId=order_id,
            limit=1
        )
        
        if resposta.get("retCode") != 0:
            logger.warning(f"⚠️ Falha ao consultar ordem: {resposta.get('retMsg')}")
            return False, None, None
        
        orders = resposta.get("result", {}).get("list", [])
        
        if not orders:
            logger.debug(f"🔍 Ordem {order_id} ainda não no histórico")
            return False, None, None
        
        order = orders[0]
        order_status = order.get("orderStatus", "")
        cum_exec_qty = float(order.get("cumExecQty", 0))
        avg_price = float(order.get("avgPrice", 0))
        
        if order_status == "Filled" and cum_exec_qty > 0 and avg_price > 0:
            logger.info(
                f"✅ ORDEM EXECUTADA! "
                f"Qty={cum_exec_qty:.6f} | AvgPrice={avg_price:.2f}"
            )
            return True, avg_price, cum_exec_qty
        
        logger.debug(
            f"🕐 Ordem ainda pendente: Status={order_status}, "
            f"CumQty={cum_exec_qty}"
        )
        return False, None, None
        
    except Exception as e:
        logger.error(f"❌ Erro ao verificar execução: {e}", exc_info=True)
        return False, None, None


def cancelar_ordem_entrada(
    api,
    symbol: str,
    order_id: str
) -> bool:
    """
    Cancela ordem limite não executada
    """
    try:
        logger.warning(f"🚫 Cancelando ordem {order_id}...")
        
        resposta = api.cancel_order(
            category="linear",
            symbol=symbol,
            orderId=order_id
        )
        
        if resposta.get("retCode") == 0:
            logger.info(f"✅ Ordem cancelada com sucesso")
            return True
        else:
            logger.error(f"❌ Falha ao cancelar: {resposta.get('retMsg')}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Exceção ao cancelar ordem: {e}", exc_info=True)
        return False


# =====================================================
# AGUARDAR EXECUÇÃO COM TIMEOUT
# =====================================================

def aguardar_execucao_ou_timeout(
    api,
    symbol: str,
    order_id: str,
    timeout: int = TIMEOUT_ENTRADA_SEGUNDOS,
    intervalo: int = INTERVALO_CHECK_ENTRADA
) -> Tuple[bool, Optional[float], Optional[float]]:
    """
    Aguarda execução da ordem limite com timeout
    
    Returns:
        (sucesso: bool, entry_price: float, qty: float)
    """
    inicio = time.time()
    tentativas = 0
    
    logger.info(
        f"⏳ Aguardando execução da ordem {order_id} "
        f"(timeout={timeout}s, intervalo={intervalo}s)"
    )
    
    while time.time() - inicio < timeout:
        tentativas += 1
        tempo_decorrido = time.time() - inicio
        
        # Verificar execução
        executada, entry_price, qty = verificar_execucao_ordem_entrada(
            api, symbol, order_id
        )
        
        if executada:
            logger.info(
                f"🎯 EXECUÇÃO CONFIRMADA após {tempo_decorrido:.1f}s "
                f"({tentativas} tentativas)"
            )
            return True, entry_price, qty
        
        # Log periódico
        if tentativas % 5 == 0:
            logger.info(
                f"⏱️ Aguardando execução... {tempo_decorrido:.1f}s / {timeout}s"
            )
        
        time.sleep(intervalo)
    
    # Timeout atingido
    logger.warning(
        f"⏰ TIMEOUT DE ENTRADA atingido após {timeout}s "
        f"({tentativas} tentativas)"
    )
    
    return False, None, None


# =====================================================
# FLUXO COMPLETO DE ENTRADA POR LIMITE
# =====================================================

def executar_entrada_por_limite(
    api,
    symbol: str,
    side: str,
    qty: float,
    entry_price: float,
    atr: float,
    tick_size: float = 0.01,
    qty_step: float = 0.001,
    trade_id: Optional[str] = None,
    decision_timestamp: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
) -> Optional[Dict]:
    """
    FLUXO COMPLETO de entrada por ordem limite
    
    Etapas:
    1. Calcula preço limite
    2. Envia ordem limite
    3. Aguarda execução com timeout
    4. Cancela se timeout
    5. Retorna dados confirmados ou None
    
    Returns:
        Dict com entry_price REAL, qty, orderId se sucesso
        None se falhar ou timeout
    """
    
    logger.info(
        f"🚀 INICIANDO ENTRADA POR LIMITE | "
        f"{symbol} {side.upper()} | Qty={qty:.6f}"
    )
    
    # 1. Calcular preço limite
    preco_limite = calcular_preco_limite(entry_price, side, atr)
    
    # 2. Enviar ordem limite
    ordem_enviada = enviar_ordem_limite_entrada(
        api,
        symbol,
        side,
        qty,
        preco_limite,
        tick_size,
        qty_step,
        trade_id=trade_id,
        decision_timestamp=decision_timestamp,
        ttl_seconds=ttl_seconds,
    )
    
    if not ordem_enviada:
        logger.error("❌ Falha ao enviar ordem limite")
        return None
    
    order_id = ordem_enviada["orderId"]
    
    # 3. Aguardar execução
    executada, entry_real, qty_real = aguardar_execucao_ou_timeout(
        api, symbol, order_id
    )
    
    if not executada:
        # 4. Timeout → cancelar ordem
        logger.warning("⏰ Entrada não confirmada → cancelando ordem")
        cancelar_ordem_entrada(api, symbol, order_id)
        return None
    
    # 5. Sucesso → retornar dados confirmados
    resultado = {
        "orderId": order_id,
        "symbol": symbol,
        "side": side,
        "entry_price": entry_real,  # PREÇO REAL DA EXECUÇÃO
        "qty": qty_real,
        "timestamp_entrada": datetime.now().isoformat(),
        "executada": True
    }
    
    logger.info(f"✅ ENTRADA POR LIMITE CONFIRMADA: {resultado}")
    
    return resultado
