import logging
from utils.indicadores import calculate_indicators

def escolher_metodo_stop_take(df, preco_atual):
    """
    Seleciona o método para calcular Stop-Loss e Take-Profit com base no comportamento do mercado.
    """
    # Calcular indicadores
    df_indicators = calculate_indicators(df)  # Calcula todos os indicadores
    if df_indicators is None:
        logging.error("Erro ao calcular indicadores técnicos.")
        return None

    # Extrair os indicadores necessários
    atr = df_indicators['ATR'].iloc[-1]  # Último valor do ATR
    rsi = df_indicators['RSI'].iloc[-1]  # Último valor do RSI
    macd = df_indicators['MACD'].iloc[-1]  # Último valor do MACD
    bandas_bollinger_upper = df_indicators['Bollinger_Upper'].iloc[-1]
    bandas_bollinger_lower = df_indicators['Bollinger_Lower'].iloc[-1]

    # Log para verificar os valores dos indicadores
    logging.info(f"📊 Indicadores: ATR={atr}, RSI={rsi}, MACD={macd}, BB Upper={bandas_bollinger_upper}, BB Lower={bandas_bollinger_lower}, Preço Atual={preco_atual}")

    # Lógica de decisão para escolher o método baseado nos indicadores
    if macd > 0 and rsi > 70:  # Tendência forte e sobrecompra
        metodo = 'Fibonacci'
        logging.info("🎯 Condição de tendência forte e sobrecompra detectada. Método: Fibonacci.")
    elif atr > 10000:  # Alta volatilidade
        metodo = 'ATR'
        logging.info("🎯 Alta volatilidade detectada. Método: ATR.")
    elif bandas_bollinger_lower < preco_atual < bandas_bollinger_upper:  # Mercado lateral (range-bound)
        metodo = 'Bollinger'
        logging.info("🎯 Mercado lateral (dentro das Bandas de Bollinger). Método: Bollinger.")
    else:
        metodo = 'Fibonacci'  # Default: Fibonacci, para tendências menos claras
        logging.info("🎯 Condição não detectada para os outros métodos. Método default: Fibonacci.")

    logging.info(f"🧠 Método escolhido para Stop-Loss/Take-Profit: {metodo}")

    # Verificando se o método escolhido é Bollinger e se os dados históricos estão presentes
    if metodo == 'Bollinger':
        if df.empty or len(df) < 20 or 'Bollinger_Upper' not in df.columns or 'Bollinger_Lower' not in df.columns:
            logging.error("❌ Para o método Bollinger, o DataFrame deve ter pelo menos 20 candles históricos com as Bandas de Bollinger calculadas.")
            return None

    return metodo