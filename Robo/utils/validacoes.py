import logging
import math


logger = logging.getLogger(__name__)


def validar_preco(preco: float, symbol: str) -> bool:
    """
    Valida preço de entrada vindo do mercado (Bybit).
    Retorna False se o preço for None, zero, negativo, NaN ou não finito.
    """
    try:
        if preco is None:
            logger.error(f"🚫 PREÇO INVÁLIDO | {symbol} | valor=None")
            return False

        preco_float = float(preco)
        if preco_float <= 0:
            logger.error(f"🚫 PREÇO INVÁLIDO | {symbol} | valor<=0: {preco_float}")
            return False

        if math.isnan(preco_float) or math.isinf(preco_float):
            logger.error(f"🚫 PREÇO INVÁLIDO | {symbol} | valor não finito: {preco_float}")
            return False

        return True
    except Exception as e:
        logger.error(f"🚫 PREÇO INVÁLIDO | {symbol} | erro ao validar: {e}")
        return False
