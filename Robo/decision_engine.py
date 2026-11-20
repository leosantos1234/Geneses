import logging
import pandas as pd
from utils.openai_module import analisar_mercado_openai

# Função para avaliar a decisão com base nos indicadores, modelo de ML e OpenAI
def avaliar_decisao_entrada(symbol, df, modelo, rsi, atr, sma_10, sma_50):
    """
    Avalia a entrada no mercado considerando indicadores técnicos, modelo preditivo e OpenAI.
    :param symbol: Símbolo do par de negociação (ex: BTCUSDT).
    :param df: DataFrame com dados de mercado.
    :param modelo: Modelo de Machine Learning para previsão (RandomForest, XGBoost, etc.).
    :param rsi: Valor do RSI calculado.
    :param atr: Valor do ATR calculado.
    :param sma_10: Valor da SMA_10 calculada.
    :param sma_50: Valor da SMA_50 calculada.
    :return: 'buy', 'sell', ou 'wait' (para aguardar).
    """
    
    # 1. Análise com Indicadores Técnicos:
    if rsi < 30 and sma_10 > sma_50:
        recomendacao_indicadores = "buy"
        logging.info(f"Indicadores indicam oportunidade de compra para {symbol}.")
    elif rsi > 70 and sma_10 < sma_50:
        recomendacao_indicadores = "sell"
        logging.info(f"Indicadores indicam oportunidade de venda para {symbol}.")
    else:
        recomendacao_indicadores = "wait"
        logging.info(f"Indicadores não sugerem uma ação clara para {symbol}.")
    
    # 2. Previsão com Modelo de Machine Learning:
    ultimo_dado = pd.DataFrame([df.iloc[-1][['close', 'volume', 'SMA_10', 'SMA_50', 'RSI', 'ATR']]])
    predicao_ml = modelo.predict(ultimo_dado)[0]  # 1 para 'buy', 0 para 'sell'
    
    if predicao_ml == 1:
        recomendacao_ml = "buy"
    else:
        recomendacao_ml = "sell"
    
    logging.info(f"Recomendação do modelo de Machine Learning: {recomendacao_ml}")

    # 3. Consulta à OpenAI se necessário:
    if recomendacao_indicadores == "wait":
        prompt = f"""
        O mercado para {symbol} apresenta as seguintes condições:
        RSI: {rsi}
        ATR: {atr}
        SMA_10: {sma_10}
        SMA_50: {sma_50}

        Com base nessas informações, o que você recomendaria? 'buy', 'sell' ou 'wait'? 
        """
        recomendacao_openai = analisar_mercado_openai(prompt)
        
        if recomendacao_openai:
            logging.info(f"Recomendação da OpenAI: {recomendacao_openai}")
            if "buy" in recomendacao_openai.lower():
                recomendacao_openai = "buy"
            elif "sell" in recomendacao_openai.lower():
                recomendacao_openai = "sell"
            else:
                recomendacao_openai = "wait"
        else:
            recomendacao_openai = recomendacao_ml  # Se OpenAI não responder, usamos o ML

    # 4. Decisão Final com Priorização do Modelo de ML:
    if recomendacao_ml == "buy" and recomendacao_indicadores != "sell" and recomendacao_openai != "sell":
        return "buy"
    elif recomendacao_ml == "sell" and recomendacao_indicadores != "buy" and recomendacao_openai != "buy":
        return "sell"
    else:
        logging.info(f"Decisão final: Esperar. Recomendação: {recomendacao_indicadores}/{recomendacao_ml}/{recomendacao_openai}")
        return "wait"

