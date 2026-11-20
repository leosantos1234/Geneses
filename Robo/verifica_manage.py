import requests
import json
import logging

logging.basicConfig(level=logging.DEBUG)

url = "http://localhost:8000/manage_trade"  # ajuste a URL conforme seu servidor e porta

payload = {
    "trade_id": "teste123",
    "symbol": "BTCUSDT",
    "entry_price": 115000.0,
    "position_size": 0.001,
    "risk_parameters": {
        "stop_loss": 114500.0,
        "take_profit": 116000.0,
        "trailing_stop": None
    },
    "indicators": {
        "RSI": 45.5,
        "ATR": 200.0
    }
}

logging.debug(f"Payload enviado para /manage_trade: {json.dumps(payload, indent=2)}")

response = requests.post(url, json=payload)

print("Status Code:", response.status_code)
print("Response JSON:", response.json())
