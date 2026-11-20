# utils/timestamp.py

import time
import requests
import logging

def obter_diferenca_timestamp():
    try:
        response = requests.get("https://api.bybit.com/v5/market/time", timeout=5)
        print("RESPOSTA RAW DO BYBIT:", response.json())   # <-- Adicione esta linha!
        response.raise_for_status()
        server_time = int(response.json()["time"])
        local_time = int(time.time() * 1000)
        diferenca = server_time - local_time
        logging.info(f"🕰️ Timestamp Bybit: {server_time} | Local: {local_time} | Δ: {diferenca} ms")
        return diferenca
    except Exception as e:
        logging.warning(f"⚠️ Falha ao obter timestamp Bybit. Usando local. Erro: {e}")
        return 0
