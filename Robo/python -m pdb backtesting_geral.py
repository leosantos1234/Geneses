import logging
import pandas as pd
import numpy as np
import tensorflow as tf
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout
from sklearn.preprocessing import MinMaxScaler
import os
import sys

# 📌 Adiciona o diretório raiz ao sys.path para encontrar módulos corretamente
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

# 📌 Importa módulo de entrada no mercado
from entrada_mercado import should_enter_market

# 📌 Importação das Estratégias dentro da pasta estrategia_entrada
from estrategia_entrada.estrategia_arbitragem_triangular import estrategia_arbitragem_triangular
from estrategia_entrada.estrategia_momentum_ia import estrategia_momentum_ia
from estrategia_entrada.estrategia_mean_reversion import estrategia_mean_reversion
from estrategia_entrada.estrategia_sor import estrategia_sor
from estrategia_entrada.estrategia_volume_profile import estrategia_volume_profile
from estrategia_entrada.estrategia_breakout_filtros import estrategia_breakout_filtros
from estrategia_entrada.estrategia_sentiment_trading import estrategia_sentiment_trading
from estrategia_entrada.estrategia_market_making import market_making

# 📌 Configuração do logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# 📌 Função para Treinar o Modelo LSTM
def train_lstm(df):
    logging.info("🚀 Iniciando o treinamento do modelo LSTM...")

    scaler = MinMaxScaler(feature_range=(0, 1))
    df_scaled = scaler.fit_transform(df[['close', 'high', 'low', 'volume']])

    sequence_length = 10
    X, y = [], []
    for i in range(len(df_scaled) - sequence_length):
        X.append(df_scaled[i:i+sequence_length])
        y.append(df_scaled[i+sequence_length, 0])

    X, y = np.array(X), np.array(y)

    if len(X) == 0:
        logging.error("🚨 Erro: Dados insuficientes para o LSTM! Abortando execução.")
        return None, None

    model = Sequential([
        tf.keras.Input(shape=(sequence_length, 4)),
        LSTM(50, return_sequences=True),
        Dropout(0.2),
        LSTM(50, return_sequences=False),
        Dropout(0.2),
        Dense(25),
        Dense(1)
    ])

    model.compile(optimizer='adam', loss='mean_squared_error')

    logging.info("🧠 Treinando modelo LSTM...")
    model.fit(X, y, batch_size=16, epochs=5, verbose=1)
    logging.info("✅ Treinamento do modelo LSTM concluído!")

    return model, scaler

# 📌 Função para Executar Backtesting
def executar_backtesting(dados, estrategia_func, saldo_inicial=10000):
    """
    Executa o backtesting em dados históricos com base em uma estratégia.
    """
    logging.info(f"📊 Iniciando Backtest para: {estrategia_func.__name__} com saldo inicial de {saldo_inicial}...")

    saldo = saldo_inicial
    posicao = 0
    for _, linha in dados.iterrows():
        try:
            sinal = estrategia_func(linha)
        except Exception as e:
            logging.error(f"⚠️ Erro ao rodar estratégia {estrategia_func.__name__}: {e}")
            continue

        if sinal == "buy" and saldo > 0:
            posicao += saldo / linha["close"]
            saldo = 0
            logging.info(f"✅ COMPRA em {linha['time']} | Preço: {linha['close']:.2f}")

        elif sinal == "sell" and posicao > 0:
            saldo += posicao * linha["close"]
            posicao = 0
            logging.info(f"✅ VENDA em {linha['time']} | Preço: {linha['close']:.2f}")

    saldo_final = saldo + (posicao * dados.iloc[-1]["close"])
    lucro = saldo_final - saldo_inicial

    return lucro

# 📌 Escolhe a Melhor Estratégia Baseada no Backtest
def escolher_melhor_estrategia(df_real):
    estrategias = {
        "Arbitragem Triangular": estrategia_arbitragem_triangular,
        "Momentum Trading com IA": estrategia_momentum_ia,
        "Mean Reversion Adaptativa": estrategia_mean_reversion,
        "Smart Order Routing (SOR)": estrategia_sor,
        "Volume Profile + Order Flow": estrategia_volume_profile,
        "Breakout Trading com Filtros Avançados": estrategia_breakout_filtros,
        "AI Sentiment Trading": estrategia_sentiment_trading,
        "Market Making": market_making
    }

    melhor_estrategia = None
    melhor_lucro = float('-inf')
    resultados = []

    logging.info("\n📊 **INICIANDO BACKTEST DE TODAS AS ESTRATÉGIAS**")

    for nome, func in estrategias.items():
        logging.info(f"🔄 Testando estratégia: {nome}...")

        try:
            lucro = executar_backtesting(df_real, func)
            resultados.append((nome, lucro))
            logging.info(f"📈 Estratégia: {nome} | Lucro: {lucro:.2f} USDT")
        except Exception as e:
            logging.error(f"⚠️ Erro no backtest de {nome}: {e}")
            continue

        if lucro > melhor_lucro:
            melhor_lucro = lucro
            melhor_estrategia = nome

    resultados.sort(key=lambda x: x[1], reverse=True)

    logging.info("\n🏆 **RANKING DAS ESTRATÉGIAS TESTADAS:**")
    for rank, (nome, lucro) in enumerate(resultados, start=1):
        logging.info(f"{rank}. {nome} | Lucro: {lucro:.2f} USDT")

    if melhor_estrategia:
        logging.info(f"\n✅ **Melhor Estratégia Selecionada: {melhor_estrategia}**")
    else:
        logging.warning("⚠️ Nenhuma estratégia se destacou!")

    return melhor_estrategia

# 📌 Fluxo Principal do Robô
def iniciar_robo_back(df, model, scaler):
    logging.info("🚀 Iniciando robô de trading...")

    if model is None or scaler is None:
        logging.error("❌ Modelo LSTM não foi treinado corretamente. Encerrando execução.")
        return

    logging.info("🔍 Verificando se o mercado está favorável...")
    
    if should_enter_market(df, model, scaler):
        logging.info("✅ Mercado favorável! Rodando backtest para definir estratégia...")
        melhor_estrategia = escolher_melhor_estrategia(df)

        if melhor_estrategia:
            logging.info(f"🏆 Estratégia Selecionada: {melhor_estrategia} ✅")
        else:
            logging.warning("⚠️ Nenhuma estratégia foi considerada ideal no backtest.")
    else:
        logging.info("⏳ Mercado não está favorável para entrada.")

# 📌 Executa o Robô Quando o Script é Rodado Diretamente
if __name__ == "__main__":
    logging.info("🔹 O código principal está sendo executado!")

    dados = pd.DataFrame({
        "time": ["2025-02-20 12:00", "2025-02-20 13:00"],
        "open": [100, 105],
        "close": [105, 103],
        "high": [107, 108],
        "low": [99, 101],
        "volume": [1500, 1600],
        "ATR": [0.02, 0.03]
    })

    modelo_teste, scaler_teste = train_lstm(dados)
    iniciar_robo_back(dados, modelo_teste, scaler_teste)
