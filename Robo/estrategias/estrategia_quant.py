import pandas as pd
import numpy as np
from pybit.unified_trading import HTTP
from utils.parse_env import get_ativos_env
import os
from dotenv import load_dotenv
load_dotenv()

symbol = get_ativos_env()[0]  # Primeiro ativo do seu .env
interval = os.getenv("INTERVAL", "1")

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")
TESTNET = os.getenv("TESTNET", "false").lower() == "true"

session = HTTP(
    api_key=BYBIT_API_KEY,
    api_secret=BYBIT_API_SECRET,
    testnet=TESTNET
)

# Função para Arbitragem Triangular
def encontrar_arbitragem_triangular(session, pares):
    try:
        precos = {}
        response = session.get_tickers(category="spot")
        if response.get("retCode") == 0:
            for par in pares:
                encontrado = False
                for ticker in response["result"]["list"]:
                    if ticker["symbol"] == par:
                        preco = ticker.get("lastPrice")
                        if preco:
                            precos[par] = float(preco)
                            encontrado = True
                        else:
                            print(f"Preço para o par {par} não encontrado no campo 'lastPrice'.")
                        break
                if not encontrado:
                    print(f"Par {par} não encontrado na lista de tickers.")
        else:
            print(f"Erro ao buscar informações dos pares: {response.get('retMsg')}")

        if len(precos) == len(pares):
            if precos[pares[1]] == 0:  # Prevenir divisão por zero
                print(f"Divisão por zero detectada para o par {pares[1]}.")
                return
            lucro_teorico = (precos[pares[0]] * precos[pares[2]]) / precos[pares[1]]
            if lucro_teorico > 1:
                print(f"Oportunidade de arbitragem encontrada: {lucro_teorico:.4f}")
            else:
                print("Sem oportunidades de arbitragem no momento.")
        else:
            print("Não foi possível obter preços para todos os pares.")
    except Exception as e:
        print(f"Erro ao verificar arbitragem: {e}")

# Função para Market Making
def market_making(symbol, session, spread_percent=0.01):
    try:
        response = session.get_tickers(category="spot")
        if response.get("retCode") == 0:
            encontrado = False
            for ticker in response["result"]["list"]:
                if ticker["symbol"] == symbol:
                    preco = ticker.get("lastPrice")
                    if preco:
                        preco_atual = float(preco)
                        spread = preco_atual * spread_percent
                        preco_compra = preco_atual - spread
                        preco_venda = preco_atual + spread
                        print(f"Ordens simuladas para {symbol}:")
                        print(f"Comprar a: {preco_compra:.2f}, Vender a: {preco_venda:.2f}")
                        encontrado = True
                    else:
                        print(f"Preço para o símbolo {symbol} não encontrado no campo 'lastPrice'.")
                    break
            if not encontrado:
                print(f"Símbolo {symbol} não encontrado na lista de tickers.")
        else:
            print(f"Erro ao buscar preço do ativo: {response.get('retMsg')}")
    except Exception as e:
        print(f"Erro no market making: {e}")




# Função para Volatility Breakout
def detectar_rompimento_volatilidade(df):
    try:
        df["ATR"] = (df["high"] - df["low"]).rolling(window=14).mean()
        df["breakout"] = df["ATR"] > (df["ATR"].rolling(window=20).mean() * 1.5)
        rompimentos = df[df["breakout"]]
        if not rompimentos.empty:
            print("Rompimentos detectados:")
            print(rompimentos[["time", "close", "ATR"]].tail())
        else:
            print("Nenhum rompimento detectado.")
        return rompimentos
    except Exception as e:
        print(f"Erro ao detectar rompimentos: {e}")
        return None

# Função para Backtesting
def backtesting(df, saldo_inicial=10000):
    saldo = saldo_inicial
    posicao = 0
    for i in range(len(df)):
        if df["Prediction"].iloc[i] == 1 and posicao == 0:
            posicao = saldo / df["close"].iloc[i]
            saldo = 0
        elif df["Prediction"].iloc[i] == 0 and posicao > 0:
            saldo = posicao * df["close"].iloc[i]
            posicao = 0
    saldo_final = saldo + (posicao * df["close"].iloc[-1] if posicao > 0 else 0)
    print(f"Saldo Final após o backtesting: R${saldo_final:.2f}")
    return saldo_final

# Testes diretos
# Testes diretos
if __name__ == "__main__":
    print("Testando estratégias...\n")

    # Testar Arbitragem Triangular
    print("== Testando Arbitragem Triangular ==")
    pares = [symbol, "ETHUSDT", "BTCETH"]  # Pode montar dinamicamente os pares
    encontrar_arbitragem_triangular(session, pares)

    # Testar Market Making
    print("\n== Testando Market Making ==")
    market_making(symbol, session, spread_percent=0.01)  # Usa o symbol dinâmico


    # Testar Volatility Breakout
    print("\n== Testando Volatility Breakout ==")
    data = {
        "time": pd.date_range(start="2023-01-01", periods=100, freq="min"),
        "close": np.random.uniform(low=100, high=200, size=100),
        "high": np.random.uniform(low=200, high=300, size=100),
        "low": np.random.uniform(low=50, high=100, size=100),
        "volume": np.random.uniform(low=1000, high=10000, size=100),
    }
    df = pd.DataFrame(data)
    detectar_rompimento_volatilidade(df)

    # Testar Backtesting
    print("\n== Testando Backtesting ==")
    data = {
        "time": pd.date_range(start="2023-01-01", periods=100, freq="min"),
        "close": np.random.uniform(low=100, high=200, size=100),
        "Prediction": np.random.choice([0, 1], size=100),
    }
    df = pd.DataFrame(data)
    backtesting(df)
