from flask import Flask, jsonify, request
import pandas as pd
import time
import requests
import hmac
import hashlib
import base64
from dotenv import load_dotenv
import os

# Carregar variáveis do .env
load_dotenv()

API_KEY = os.getenv("COINBASE_API_KEY")
API_SECRET = os.getenv("COINBASE_API_SECRET")
API_PASSPHRASE = os.getenv("COINBASE_API_PASSPHRASE")
BASE_URL = "https://api.exchange.coinbase.com"

app = Flask(__name__)

# Gerar cabeçalhos de autenticação dinâmicos
def gerar_headers(endpoint, method="GET", body=""):
    timestamp = str(int(time.time()))
    message = f"{timestamp}{method}{endpoint}{body}"
    signature = hmac.new(API_SECRET.encode(), message.encode(), hashlib.sha256).digest()
    signature_b64 = base64.b64encode(signature).decode()
    return {
        "CB-ACCESS-KEY": API_KEY,
        "CB-ACCESS-SIGN": signature_b64,
        "CB-ACCESS-TIMESTAMP": timestamp,
        "CB-ACCESS-PASSPHRASE": API_PASSPHRASE,
        "Content-Type": "application/json",
    }

# Rota para obter dados de mercado
@app.route('/api/market_data', methods=['GET'])
def market_data():
    symbol = request.args.get('symbol', 'BTC-USD')
    endpoint = f"/products/{symbol}/candles"
    headers = gerar_headers(endpoint)
    params = {'granularity': 3600}  # Intervalo de 1h
    response = requests.get(BASE_URL + endpoint, headers=headers, params=params)

    if response.status_code == 200:
        data = response.json()
        df = pd.DataFrame(data, columns=['time', 'low', 'high', 'open', 'close', 'volume'])
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df.sort_values('time', inplace=True)
        return jsonify(df.to_dict(orient='records'))
    else:
        return jsonify({"error": "Failed to fetch data"}), response.status_code

# Rota para calcular indicadores técnicos
@app.route('/api/indicators', methods=['POST'])
def calculate_indicators():
    data = request.get_json()
    df = pd.DataFrame(data)

    sma_short = int(request.args.get('sma_short', 10))
    sma_long = int(request.args.get('sma_long', 50))
    rsi_period = int(request.args.get('rsi_period', 14))

    # SMA
    df["SMA_Short"] = df["close"].rolling(window=sma_short).mean()
    df["SMA_Long"] = df["close"].rolling(window=sma_long).mean()

    # RSI
    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=rsi_period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=rsi_period).mean()
    rs = gain / loss
    df["RSI"] = 100 - (100 / (1 + rs))

    return jsonify(df.to_dict(orient='records'))

if __name__ == '__main__':
    app.run(debug=True)
