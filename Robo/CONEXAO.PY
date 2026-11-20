import time
import hmac
import hashlib
import requests

# Credenciais de Testnet
api_key = "zJprgOUpJUQeZZaRqO"
api_secret = "lGAHa337TPEsApB5tBPe7elhSZ1nnTjCORf8"
url = "https://api-testnet.bybit.com/v5/account/wallet-balance"

# Parâmetros obrigatórios
timestamp = int(time.time() * 1000)
params = {
    "api_key": api_key,
    "timestamp": timestamp,
    "recvWindow": 10000,
    "accountType": "UNIFIED"  # Adicionado accountType
}

# Gerar assinatura HMAC SHA256
param_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
signature = hmac.new(api_secret.encode(), param_string.encode(), hashlib.sha256).hexdigest()
params["sign"] = signature

# Enviar requisição como GET
response = requests.get(url, params=params)

# Exibir resposta
print(f"Parâmetros enviados: {params}")
print(f"Status Code: {response.status_code}")
print(f"Headers: {response.headers}")
print(f"Content: {response.content}")
print(f"Resposta da API: {response.json()}")
