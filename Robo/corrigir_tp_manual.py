import requests

# === CONFIGURAÇÃO ===
API_IA_URL = "http://127.0.0.1:8001"  # Altere se sua API IA estiver em outra porta

# === DADOS DO TRADE ABERTO ===
payload = {
    "trade_id": "N/A",             # se souber o ID, pode colocar; senão, deixa assim
    "symbol": "BNBUSDT",           # ativo
    "entry_price": 937.4,          # preço médio de entrada (exato conforme a Bybit)
    "stop_loss": 942.1,            # SL atual (acima do preço)
    "take_profit": 0.02,           # TP vazio → IA vai recalcular
    "trailing_offset": 0,
    "rsi": 50,
    "atr": 1.5,
    "tendencia_macro": "neutra",
    "sentimento": "neutro",
    "side": "sell"                 # ⚠️ VENDA — ESSENCIAL!!!
}

# === EXECUÇÃO ===
print("🚀 Enviando solicitação de correção de TP para IA...")
resp = requests.post(f"{API_IA_URL}/manage_trade", json=payload, timeout=15)

print("\n📩 Resposta do servidor:")
print(resp.text)
