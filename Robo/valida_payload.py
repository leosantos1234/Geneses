import sys
import os

# Ajuste para o caminho do seu manage_trade.py
sys.path.insert(0, r"C:\Users\emgepron\Documents\Agente_IA")

from manage_trade import TradeRequest

# Simule o payload que você está tentando enviar:
payload = {
    "trade_id": "manual",
    "symbol": "BTCUSDT",
    "entry_price": "107400.0",    # Se botar como string, deve dar erro (tem que ser float)
    "position_size": 0.001,
    "risk_parameters": {
        "stop_loss": 106000.0,
        "take_profit": 109000.0,
        "trailing_stop": 50
    },
    "indicators": {
        "RSI": 51.2,
        "ATR": 120.4
    }
}

from pydantic import ValidationError

try:
    trade = TradeRequest(**payload)
    print("✅ Payload válido para TradeRequest!")
    print(trade)
except ValidationError as e:
    print("❌ Erros encontrados no payload:")
    for err in e.errors():
        print(f"Campo: {' -> '.join(str(loc) for loc in err['loc'])}")
        print(f"  Mensagem: {err['msg']}")
        print(f"  Tipo: {err['type']}\n")
