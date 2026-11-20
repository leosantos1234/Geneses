import requests
from utils.logger_config import configurar_logger_global
import logging

logger = logging.getLogger(__name__)

def sugerir_spread_e_trailing(preco_mercado, volatilidade, volume):
    url = "http://localhost:8000/sugerir_spread_e_trailing"
    payload = {
        "preco_mercado": preco_mercado,
        "volatilidade": volatilidade,
        "volume": volume
    }

    try:
        response = requests.post(url, json=payload, timeout=5)
        response.raise_for_status()
        data = response.json()
        return data.get("spread", 5), data.get("trailing", 1.0)
    except Exception as e:
        logging.error(f"❌ Erro ao consultar IA para Spread/Trailing: {e}")
        return 5, 1.0
