import uuid
from pybit.unified_trading import HTTP

# Configurações de acesso à API
session = HTTP(
    testnet=True,  # Mudar para False em produção
    api_key="zJprgOUpJUQeZZaRqO",
    api_secret="lGAHa337TPEsApB5tBPe7elhSZ1nnTjCORf8",
)

# Gerar um novo ID único para cada pedido
order_link_id = str(uuid.uuid4())

# Definindo os preços de Stop-Loss e Take-Profit
take_profit_price = "16000"  # Preço para Take-Profit
stop_loss_price = "15400"  # Preço para Stop-Loss
trigger_price = "15600"  # Preço de disparo da ordem (quando o mercado atingir este preço, a ordem será executada)

# Criando a ordem Limit (compra inicial)
response = session.place_order(
    category="spot",
    symbol="BTCUSDT",
    side="Buy",
    orderType="Limit",
    qty="0.1",  # Quantidade de BTC
    price="15600",  # Preço de execução da ordem
    timeInForce="PostOnly",
)

# Exibindo a resposta da ordem Limit
print("Ordem Limit Criada:", response)

# Criando a ordem de Take-Profit como condicional
take_profit_response = session.place_order(
    category="spot",
    symbol="BTCUSDT",
    side="Sell",  # Ordem de venda para Take-Profit
    orderType="Limit",
    qty="0.1",  # Quantidade de BTC
    price=take_profit_price,  # Preço de Take-Profit
    timeInForce="PostOnly",
    orderFilter="StopOrder",  # Indicando que é uma ordem condicional (stop)
    triggerPrice=take_profit_price,  # Preço de disparo da ordem (Take-Profit)
)

# Exibindo a resposta da ordem Take-Profit
print("Ordem Take-Profit Criada:", take_profit_response)

# Criando a ordem de Stop-Loss como condicional
stop_loss_response = session.place_order(
    category="spot",
    symbol="BTCUSDT",
    side="Sell",  # Ordem de venda para Stop-Loss
    orderType="Limit",
    qty="0.1",  # Quantidade de BTC
    price=stop_loss_price,  # Preço de Stop-Loss
    timeInForce="PostOnly",
    orderFilter="StopOrder",  # Indicando que é uma ordem condicional (stop)
    triggerPrice=stop_loss_price,  # Preço de disparo da ordem (Stop-Loss)
)

# Exibindo a resposta da ordem Stop-Loss
print("Ordem Stop-Loss Criada:", stop_loss_response)
