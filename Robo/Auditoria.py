import logging

# Função para auditoria da última ordem
def auditar_ordem(ordem_preco, ordem_quantidade_btc, saldo_btc, saldo_usdt, loss_limit_percent, capital_maximo_percent=0.02, rrr_minima=2, rsi_limite=30, atr_limite=1.5, sma10_limite=10000, sma50_limite=50000):
    """
    Auditoria da última ordem enviada, verificando se atendeu aos critérios de preço, tamanho de lote, risco, e critérios de entrada.
    :param ordem_preco: Preço da ordem (em USDT).
    :param ordem_quantidade_btc: Quantidade de BTC comprada na ordem.
    :param saldo_btc: Saldo total de BTC disponível.
    :param saldo_usdt: Saldo total em USDT disponível.
    :param loss_limit_percent: Percentual de risco máximo por operação (ex: 0.01 para 1%).
    :param capital_maximo_percent: Percentual máximo de capital arriscado (default 2%).
    :param rrr_minima: Relação Risco/Recompensa mínima (default 2:1).
    :param rsi_limite: Limite do RSI para compra (exemplo: abaixo de 30).
    :param atr_limite: Limite do ATR para decisão (exemplo: acima de 1.5).
    :param sma10_limite: Limite da SMA10 (exemplo: acima de 10.000).
    :param sma50_limite: Limite da SMA50 (exemplo: acima de 50.000).
    """
    
    # 1. Verificar o valor máximo da ordem (não deve ultrapassar 2% do capital)
    max_valor_ordem = saldo_usdt * capital_maximo_percent  # 2% do saldo disponível
    valor_da_ordem = ordem_preco * ordem_quantidade_btc  # Valor total da ordem em USDT

    logging.info(f"Valor máximo permitido para a ordem: {max_valor_ordem} USDT")
    logging.info(f"Valor da ordem em USDT: {valor_da_ordem} USDT")

    if valor_da_ordem > max_valor_ordem:
        logging.error(f"Erro: O valor da ordem ({valor_da_ordem} USDT) excede o limite máximo permitido ({max_valor_ordem} USDT).")
    else:
        logging.info(f"Valor da ordem ({valor_da_ordem} USDT) dentro do limite de 2% do capital disponível.")

    # 2. Verificar o tamanho do lote (com base no percentual de risco definido)
    tamanho_lote_calculado = saldo_btc * loss_limit_percent  # Tamanho do lote baseado no limite de risco (1% por padrão)
    logging.info(f"Tamanho do lote calculado com risco de {loss_limit_percent * 100}%: {tamanho_lote_calculado} BTC")
    
    if ordem_quantidade_btc > tamanho_lote_calculado:
        logging.error(f"Erro: O tamanho do lote ({ordem_quantidade_btc} BTC) excede o tamanho permitido ({tamanho_lote_calculado} BTC) com base no risco definido.")
    else:
        logging.info(f"Tamanho do lote ({ordem_quantidade_btc} BTC) dentro do limite de risco calculado.")

    # 3. Verificar os critérios de entrada (indicadores)
    logging.info("Verificando critérios de entrada...")

    # Exemplo de valores de indicadores técnicos para simulação (substituir pela lógica real)
    fechamento = 176000  # Fechamento do preço (exemplo)
    volume = 2000  # Volume (exemplo)
    rsi = 28  # RSI (exemplo)
    atr = 1.7  # ATR (exemplo)
    sma10 = 10500  # SMA10 (exemplo)
    sma50 = 52000  # SMA50 (exemplo)

    # Detalhamento de qual indicador falhou
    indicadores_fora_do_limite = []

    if fechamento < sma10_limite:
        indicadores_fora_do_limite.append("Fechamento abaixo da SMA10")

    if volume < 1000:
        indicadores_fora_do_limite.append("Volume abaixo do limite")

    if rsi < rsi_limite:
        indicadores_fora_do_limite.append(f"RSI abaixo do limite ({rsi_limite})")

    if atr < atr_limite:
        indicadores_fora_do_limite.append(f"ATR abaixo do limite ({atr_limite})")

    if sma10 < sma10_limite:
        indicadores_fora_do_limite.append(f"SMA10 abaixo do limite ({sma10_limite})")

    if sma50 < sma50_limite:
        indicadores_fora_do_limite.append(f"SMA50 abaixo do limite ({sma50_limite})")

    if indicadores_fora_do_limite:
        logging.error(f"Erro: Os seguintes indicadores estão fora do limite: {', '.join(indicadores_fora_do_limite)}")
    else:
        logging.info("Todos os indicadores de entrada estão dentro dos limites.")

    # 4. Validar a recomendação da OpenAI ou modelo preditivo
    logging.info("Verificando recomendação da OpenAI e modelo preditivo...")
    recomendacao_openai = "buy"  # Exemplo de recomendação (substituir pela lógica real da OpenAI)
    
    if recomendacao_openai == "buy":
        logging.info("Recomendação da OpenAI foi 'buy'. A ordem está correta.")
    elif recomendacao_openai == "sell":
        logging.info("Recomendação da OpenAI foi 'sell'. A ordem está correta.")
    else:
        logging.warning(f"Aviso: A recomendação da OpenAI foi {recomendacao_openai}. Verifique a decisão.")

    # 5. Verificar a relação risco/recompensa (RRR_MINIMA)
    stop_loss = ordem_preco - (ordem_preco * 0.01)  # Exemplo de cálculo de stop-loss (1% abaixo do preço)
    take_profit = ordem_preco + (ordem_preco * 0.02)  # Exemplo de cálculo de take-profit (2% acima do preço)

    rrr = (take_profit - ordem_preco) / (ordem_preco - stop_loss)  # Relação risco/recompensa

    if rrr >= rrr_minima:
        logging.info(f"A relação risco/recompensa é válida ({rrr_minima}:1).")
    else:
        logging.error(f"A relação risco/recompensa não atende ao mínimo ({rrr_minima}:1).")

    # 6. Resultados gerais da auditoria
    logging.info("Auditoria completa da ordem finalizada.")

# Exemplo de uso da função de auditoria
def auditoria():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    # Dados da última ordem
    ordem_preco = 176466.01  # Preço da ordem
    ordem_quantidade_btc = 0.008394  # Quantidade de BTC comprada
    saldo_btc = 0.77  # Saldo total de BTC disponível (exemplo)
    saldo_usdt = 10000  # Saldo total em USDT disponível (exemplo)
    loss_limit_percent = 0.01  # 1% de risco por operação (percentual de risco definido)

    # Chamada para auditoria da última ordem
    auditar_ordem(ordem_preco, ordem_quantidade_btc, saldo_btc, saldo_usdt, loss_limit_percent)

# Executar a auditoria
auditoria()
