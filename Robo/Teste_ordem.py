import os
import time
from dotenv import load_dotenv

# 1. Carrega o .env-btc
load_dotenv('.env-btc')

# 2. Importa do seu módulo compra1
from compra1 import create_order, session

# 3. Parâmetros do teste
symbol = "BTCUSDT"
side = "Buy"
qty = 0.001            # Altere para o mínimo aceito pela Testnet, ex: 0.001 BTC
order_type = "Market"  # Pode ser "Limit" se quiser testar preço fixo

print("Iniciando teste de ordem PERPÉTUO Bybit...")

# 4. (Opcional) Define a alavancagem
try:
    session.set_leverage(
        category="linear",
        symbol=symbol,
        buyLeverage=1,
        sellLeverage=1
    )
except Exception as e:
    print("Erro ao definir alavancagem (pode ignorar na testnet):", e)

# 5. Cria a ordem PERPÉTUO
try:
    response = create_order(
        symbol=symbol,
        side=side,
        qty=qty,
        order_type=order_type
    )
    print("Resposta da API:", response)
except Exception as e:
    print("Erro ao criar ordem:", e)
