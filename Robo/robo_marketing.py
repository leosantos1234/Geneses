import time
import random
import csv
import os
from pybit.unified_trading import HTTP

# 🛠️ MODO SIMULAÇÃO (True = ativa simulação, False = usa API real da Bybit)
modo_simulacao = True

# Configuração da API da Bybit
session = HTTP(
    testnet=True,  # Defina como False para operar na mainnet (produção)
    api_key="SUA_CHAVE_API",
    api_secret="SUA_CHAVE_SECRETA"
)

# Configuração do Market Maker
symbol = "BTCUSDT"
spread = 5  # Spread ajustado
quantidade = 0.01  # Quantidade de BTC por ordem
tempo_limite_ordem = 60  # Tempo máximo para execução da ordem (segundos)
ajuste_venda = 0.2  # Ajuste percentual no preço de venda caso a ordem não seja executada

# Variáveis de desempenho
saldo_acumulado = 0.0  # Lucro acumulado ao longo do tempo
log_file = "trades_log.csv"  # Nome do arquivo para salvar os logs

# Criar arquivo de log se não existir
if not os.path.exists(log_file):
    with open(log_file, mode="w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["Ordem", "Preço Compra", "Preço Venda", "Lucro", "Saldo Acumulado"])

print(f"🔍 Iniciando Market Making para {symbol}...")

while True:
    try:
        # Obter o preço atual, Bid e Ask do mercado
        ticker_data = session.get_tickers(category="spot", symbol=symbol)['result']['list'][0]
        market_price = round(float(ticker_data['lastPrice']), 2)
        bid_price = round(float(ticker_data['bid1Price']), 2)  # Melhor preço de compra
        ask_price = round(float(ticker_data['ask1Price']), 2)  # Melhor preço de venda

        print(f"📊 Preço atual de {symbol}: {market_price} | Bid: {bid_price} | Ask: {ask_price}")

        # ✅ Ajustar o preço de compra para respeitar o limite da Bybit
        limite_preco_compra = round(market_price * 0.995, 2)  # Ajuste de -0.5%
        if bid_price > limite_preco_compra:
            bid_price = limite_preco_compra  # Ajusta para não ultrapassar o limite da Bybit
            print(f"⚠️ Ajustando ordem de compra para respeitar o limite permitido: {bid_price}")

        # Definir o preço de compra e venda ajustado
        bid_price = round(bid_price - spread / 2, 2)  # Ordem de compra abaixo do Bid
        ask_price = round(ask_price + spread / 2, 2)  # Ordem de venda acima do Ask

        # ✅ Simulação de execução da compra
        if modo_simulacao:
            print(f"💰 [SIMULAÇÃO] Criando ordem de compra em: {bid_price}")
            time.sleep(2)  # Aguarda 2 segundos antes de "executar" a compra

            # Simular se a ordem foi preenchida
            if random.random() > 0.2:  # 80% de chance de execução
                print(f"✅ [SIMULAÇÃO] Ordem de compra executada a {bid_price}!")
                preco_compra = bid_price
                ordem_executada = True
            else:
                print(f"⚠️ [SIMULAÇÃO] Ordem de compra expirou!")
                ordem_executada = False

        else:
            # Criar ordem de compra real
            print(f"💰 Criando ordem de compra em: {bid_price}")
            buy_order = session.place_order(
                category="spot",
                symbol=symbol,
                side="Buy",
                orderType="Limit",
                qty=str(quantidade),
                price=str(bid_price),
                timeInForce="GTC"
            )
            preco_compra = bid_price
            ordem_executada = True

        # ✅ Simulação de criação da venda após a compra ser executada
        if ordem_executada:
            while True:  # Loop para tentar vender até a ordem ser preenchida
                if modo_simulacao:
                    print(f"📈 [SIMULAÇÃO] Criando ordem de venda em: {ask_price}")
                    time.sleep(2)  # Aguarda 2 segundos antes de "executar" a venda

                    # Simular execução da venda
                    if random.random() > 0.3:  # 70% de chance de execução
                        preco_venda = ask_price
                        print(f"✅ [SIMULAÇÃO] Ordem de venda executada a {preco_venda}!")
                        break
                    else:
                        print(f"⚠️ [SIMULAÇÃO] Ordem de venda não foi preenchida. Ajustando preço...")
                        ask_price = round(ask_price - (ask_price * ajuste_venda / 100), 2)
                        time.sleep(2)  # Espera antes de tentar de novo

                else:
                    print(f"📈 Criando ordem de venda em: {ask_price}")
                    sell_order = session.place_order(
                        category="spot",
                        symbol=symbol,
                        side="Sell",
                        orderType="Limit",
                        qty=str(quantidade),
                        price=str(ask_price),
                        timeInForce="GTC"
                    )
                    preco_venda = ask_price
                    break

            # ✅ Cálculo do lucro por trade
            lucro_trade = (preco_venda - preco_compra) * quantidade
            saldo_acumulado += lucro_trade

            # ✅ Registrar transação no arquivo CSV
            with open(log_file, mode="a", newline="") as file:
                writer = csv.writer(file)
                writer.writerow(["Trade", preco_compra, preco_venda, round(lucro_trade, 2), round(saldo_acumulado, 2)])

            print(f"💰 Lucro da operação: {round(lucro_trade, 2)} USDT | Saldo acumulado: {round(saldo_acumulado, 2)} USDT")

        else:
            print("⚠️ A ordem de compra não foi completamente executada. Não podemos vender ainda.")

        # Esperar antes da próxima iteração
        time.sleep(10)

    except Exception as e:
        print(f"❌ Erro no Market Maker: {e}")
        time.sleep(5)
