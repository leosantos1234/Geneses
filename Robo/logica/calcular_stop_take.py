import logging


def obter_maximas_minimas(df):
    """
    Função para extrair as máximas e mínimas mais recentes dos dados históricos.
    
    :param df: DataFrame com os dados históricos de mercado
    :return: duas listas (máximas e mínimas)
    """
    maximas = df['high'].tail(10)  # Pegando as últimas 10 máximas
    minimas = df['low'].tail(10)   # Pegando as últimas 10 mínimas
    return maximas, minimas


def calcular_stop_take_dinamico(preco_atual, atr, RRR_MINIMA, side, metodo, df=None):
    """
    Calcula Stop-Loss e Take-Profit com base no indicador escolhido dinamicamente (ATR, Fibonacci, RSI, MACD, etc.).
    A direção da posição (Buy ou Sell) também é levada em consideração.

    :param preco_atual: Preço atual de mercado.
    :param atr: Valor do ATR, usado para calcular o stop-take dinâmico.
    :param RRR_MINIMA: Relação de risco-recompensa mínima.
    :param side: "Buy" para compra ou "Sell" para venda.
    :param metodo: O método a ser usado para calcular o Stop-Loss/Take-Profit ("ATR", "Fibonacci", "RSI", "MACD", etc.)
    :param df: DataFrame com os dados históricos, necessário para o método Fibonacci e Bollinger.
    :return: Stop-Loss e Take-Profit ajustados.
    """

    max_stop_loss_percent = 0.05  # 5% do preço de entrada
    max_take_profit_percent = 0.10  # 10% do preço de entrada

    if metodo == "ATR":
        # ATR Método: Stop-Loss baseado na volatilidade medida pelo ATR.
          
        if side == "Buy":
            stop_loss = max(preco_atual - (atr), preco_atual * (1 - max_stop_loss_percent))  
            take_profit = min(preco_atual + (atr * RRR_MINIMA), preco_atual * (1 + max_take_profit_percent))
            logging.info(f"[ATR] Ajustado (Buy): Stop-Loss: {stop_loss}, Take-Profit: {take_profit}")
        elif side == "Sell":
            stop_loss = min(preco_atual + (atr), preco_atual * (1 + max_stop_loss_percent))
            take_profit = max(preco_atual - (atr * RRR_MINIMA), preco_atual * (1 - max_take_profit_percent))
            logging.info(f"[ATR] Ajustado (Sell): Stop-Loss: {stop_loss}, Take-Profit: {take_profit}")
        else:
            raise ValueError("Direção da posição inválida. Use 'Buy' ou 'Sell'.")

    elif metodo == "Fibonacci":
        # Fibonacci Método: Calcula com base nas máximas e mínimas históricas.
        if df is None:
            raise ValueError("Para o método Fibonacci, o DataFrame deve ser fornecido com os dados históricos.")

        ultimas_maximas, ultimas_minimas = obter_maximas_minimas(df)  # Obter máximas e mínimas recentes
        maximo_recente = max(ultimas_maximas)
        minimo_recente = min(ultimas_minimas)
        
        if side == "Buy":
            stop_loss = preco_atual - (maximo_recente - minimo_recente) * 0.236  # Stop-Loss baseado em Fibonacci
            take_profit = preco_atual + (maximo_recente - minimo_recente) * 0.618  # Take-Profit baseado em Fibonacci
        elif side == "Sell":
            stop_loss = preco_atual + (maximo_recente - minimo_recente) * 0.236  # Stop-Loss baseado em Fibonacci
            take_profit = preco_atual - (maximo_recente - minimo_recente) * 0.618  # Take-Profit baseado em Fibonacci
        else:
            raise ValueError("Direção da posição inválida. Use 'Buy' ou 'Sell'.")

    elif metodo == "RSI":
        # RSI Método: Utiliza o RSI para determinar condições de sobrecompra ou sobrevenda.
        rsi = df['RSI'].iloc[-1]  # Último valor do RSI
        if side == "Buy":
            if rsi < 30:  # Condição de sobrevenda, sinal para compra
                stop_loss = preco_atual - (atr * RRR_MINIMA)  # Stop-Loss abaixo do preço de entrada
                take_profit = preco_atual + (atr * RRR_MINIMA)  # Take-Profit acima do preço de entrada
            else:
                stop_loss = preco_atual - (atr * RRR_MINIMA)  # Condicional de compra baseada no RSI
                take_profit = preco_atual + (atr * RRR_MINIMA)
        elif side == "Sell":
            if rsi > 70:  # Condição de sobrecompra, sinal para venda
                stop_loss = preco_atual + (atr * RRR_MINIMA)  # Stop-Loss acima do preço de entrada
                take_profit = preco_atual - (atr * RRR_MINIMA)  # Take-Profit abaixo do preço de entrada
            else:
                stop_loss = preco_atual + (atr * RRR_MINIMA)
                take_profit = preco_atual - (atr * RRR_MINIMA)
        else:
            raise ValueError("Direção da posição inválida. Use 'Buy' ou 'Sell'.")

    elif metodo == "MACD":
        # MACD Método: Usa o MACD para determinar a tendência e os pontos de reversão.
        macd = df['MACD'].iloc[-1]  # Último valor do MACD
        signal_line = df['Signal_Line'].iloc[-1]  # Último valor da Signal Line

        if side == "Buy":
            if macd > signal_line:  # Tendência de alta, sinal de compra
                stop_loss = preco_atual - (atr * RRR_MINIMA)
                take_profit = preco_atual + (atr * RRR_MINIMA)
            else:
                stop_loss = preco_atual - (atr * RRR_MINIMA)
                take_profit = preco_atual + (atr * RRR_MINIMA)
        elif side == "Sell":
            if macd < signal_line:  # Tendência de baixa, sinal de venda
                stop_loss = preco_atual + (atr * RRR_MINIMA)
                take_profit = preco_atual - (atr * RRR_MINIMA)
            else:
                stop_loss = preco_atual + (atr * RRR_MINIMA)
                take_profit = preco_atual - (atr * RRR_MINIMA)
        else:
            raise ValueError("Direção da posição inválida. Use 'Buy' ou 'Sell'.")

    elif metodo == "Bollinger":
        # Bollinger Método: Utiliza as bandas de Bollinger.
        if df is None:
            raise ValueError("Para o método Bollinger, o DataFrame deve ser fornecido com os dados históricos.")

        bandas_bollinger_upper = df['Bollinger_Upper'].iloc[-1]  # Última banda superior
        bandas_bollinger_lower = df['Bollinger_Lower'].iloc[-1]  # Última banda inferior
        atr = df['ATR'].iloc[-1]  # Pega o último valor do ATR como referência de volatilidade
        fator_ajuste = 1.5  # Multiplicador para ajuste dinâmico de SL e TP
   
        if side == "Buy":
            stop_loss = max(preco_atual - (atr * fator_ajuste), bandas_bollinger_lower, preco_atual * (1 - max_stop_loss_percent))
            take_profit = min(preco_atual + (atr * fator_ajuste), preco_atual * (1 + max_take_profit_percent))
        elif side == "Sell":
            stop_loss = min(preco_atual + (atr * fator_ajuste), bandas_bollinger_upper, preco_atual * (1 + max_stop_loss_percent))
            take_profit = max(preco_atual - (atr * fator_ajuste), preco_atual * (1 - max_take_profit_percent))
        else:
            raise ValueError("Direção da posição inválida. Use 'Buy' ou 'Sell'.")

    else:
        raise ValueError("Método inválido. Use 'ATR', 'Fibonacci', 'RSI', 'MACD' ou 'Bollinger'.")

    return stop_loss, take_profit
