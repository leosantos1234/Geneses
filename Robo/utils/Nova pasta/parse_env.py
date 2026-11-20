import os

def get_ativos_env():
    ativos = os.getenv("ATIVOS", "BTCUSDT")
    # Aceita separação por vírgula, espaço ou ponto-e-vírgula
    return [a.strip().upper() for a in ativos.replace(";",",").replace(" ",",").split(",") if a.strip()]
