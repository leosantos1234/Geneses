import hashlib
import hmac
import time
import requests

# Credenciais e URL
api_key = "zJprgOUpJUQeZZaRqO"
api_secret = "lGAHa337TPEsApB5tBPe7elhSZ1nnTjCORf8"
url = "https://api-testnet.bybit.com/v5/order/create"

# Parâmetros de requisição
timestamp = int(time.time() * 1000)  # Timestamp atual
params = {
    "api_key": api_key,
    "category": "spot",
    "symbol": "BTCUSDT",
    "side": "Buy",
    "orderType": "Limit",
    "qty": "0.000048",
    "price": "30000",
    "timeInForce": "GTC",
    "timestamp": timestamp,
    "recvWindow": 10000
}

# Gerar assinatura HMAC SHA256
param_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
signature = hmac.new(api_secret.encode(), param_string.encode(), hashlib.sha256).hexdigest()
params["sign"] = signature

# Enviar requisição
response = requests.post(url, json=params)
print("Resposta da API:", response.json())
