import os
from dotenv import load_dotenv
import logging

# 🔹 Carrega variáveis do .env (já faz isso só uma vez)
load_dotenv()

def get_symbol():
    """Retorna o símbolo do ativo configurado no .env e garante formato válido."""
    symbol = os.getenv("SYMBOL", "BTCUSDT").upper()

    # Valida — evita vazio, None ou texto inválido
    if symbol not in {"BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"}:
        logging.warning(f"⚠️ SYMBOL inválido ou ausente ({symbol}). Usando padrão BTCUSDT.")
        symbol = "BTCUSDT"

    return symbol

# Define uma variável global segura
SYMBOL = get_symbol()
