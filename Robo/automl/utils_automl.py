from estrategia_entrada.backtesting_geral import carregar_dados_historicos, executar_backtest_completo
from utils.parse_env import get_ativos_env
import os
import json

def ler_env(caminho_env):
    params = {}
    with open(caminho_env, encoding="utf-8") as f:
        for linha in f:
            if linha.strip() and not linha.strip().startswith("#"):
                chave, valor = linha.strip().split("=")
                params[chave.strip()] = valor.strip()
    return params

def salvar_env(caminho_env, params):
    with open(caminho_env, "w", encoding="utf-8") as f:
        for k, v in params.items():
            f.write(f"{k}={v}\n")


def rodar_backtest(caminho_env):
    # 1. Ler parâmetros do .env
    params = ler_env(caminho_env)
    symbol = params.get("ATIVOS", "BTCUSDT")
    interval = params.get("INTERVAL", "5")
    
    # 2. Carregar candles
    from sessao_bybit import session  # já está importado no seu projeto!
    df = carregar_dados_historicos(session, symbol, interval)
    if df is None or df.empty:
        return {
            "lucro": 0,
            "winrate": 0,
            "drawdown": 0,
            "num_trades": 0,
            "rrr": 0
        }
    
    # 3. Calcular indicadores, converter datas se necessário (igual ao seu main)
    from utils.indicadores import calculate_indicators
    from datetime import datetime
    def parse_timestamp_safe(valor):
        if isinstance(valor, datetime):
            return valor
        if isinstance(valor, str) and "T" in valor:
            return datetime.fromisoformat(valor)
        if isinstance(valor, (int, float)) or (isinstance(valor, str) and valor.isdigit()):
            return datetime.utcfromtimestamp(int(valor) / 1000)
        return valor
    df["time"] = df["time"].apply(parse_timestamp_safe)
    df = calculate_indicators(df.copy())
    
    # 4. Rodar backtest real
    melhor, relatorio, _ = executar_backtest_completo(df, capital_inicial=10000)
    # Você pode retornar só o melhor resultado ou tudo
    return melhor if melhor else {}


def salvar_resultado(caminho_json, params, resultado):
    try:
        with open(caminho_json, "r") as f:
            todos = json.load(f)
    except FileNotFoundError:
        todos = []
    todos.append({"params": params, "resultado": resultado})
    with open(caminho_json, "w") as f:
        json.dump(todos, f, indent=2)
