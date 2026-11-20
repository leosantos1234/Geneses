import requests

# URL da API Testnet
url = "https://api-testnet.bybit.com/v5/order/create"

# Parâmetros da requisição
data = {
    "api_key": "X1lsLrEkTORS4fT7Wb",
    "category": "spot",
    "symbol": "BTCUSDT",
    "side": "Buy",
    "orderType": "Limit",
    "qty": "0.000048",
    "price": "30000",
    "timeInForce": "GTC",
    "timestamp": "1734477989713",
    "sign": "39a07ca896e2a23812cd65d6d45f5f928cabedf9ffc2ce0d53d25fbae3c971a7"
}

# Enviar POST
response = requests.post(url, json=data)

# Exibir resposta
print("Resposta da API:", response.json())
