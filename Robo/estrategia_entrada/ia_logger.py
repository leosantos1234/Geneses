import os
import csv
from datetime import datetime

CAMINHO_LOG = "log_ia_marketmaking.csv"

def registrar_decisao_ia_marketmaking(preco, rsi, atr, decisao, spread, trailing, volume, volatilidade):
    """
    Salva a decisão da IA em um arquivo CSV com os dados do contexto de mercado.
    """
    header = [
        "timestamp", "preco", "RSI", "ATR", "decisao", 
        "spread", "trailing", "volume", "volatilidade"
    ]
    dados = [
        datetime.utcnow().isoformat(), round(preco, 2), rsi, atr, decisao,
        round(spread, 4), round(trailing, 4), round(volume, 2), round(volatilidade, 2)
    ]

    arquivo_existe = os.path.exists(CAMINHO_LOG)

    with open(CAMINHO_LOG, mode="a", newline="") as f:
        writer = csv.writer(f)
        if not arquivo_existe:
            writer.writerow(header)
        writer.writerow(dados)
