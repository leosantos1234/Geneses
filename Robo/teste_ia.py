import requests

API_IA_URL = "http://127.0.0.1:8001"  # altere se sua IA estiver em outra porta

payload = {
    "trade_id": "N/A",
    "symbol": "BNBUSDT",
    "entry_price": 937.4,
    "stop_loss": 942.1,
    "take_profit": 0.02,
    "trailing_offset": 0,
    "rsi": 50,
    "atr": 1.5,
    "tendencia_macro": "neutra",
    "sentimento": "neutro",
    "side": "sell"
}

print("🚀 Enviando solicitação de correção de TP para IA...")

try:
    resp = requests.post(f"{API_IA_URL}/manage_trade", json=payload, timeout=15)
    print("\n📩 Resposta bruta do servidor:")
    print(resp.text)

    if resp.status_code == 200:
        try:
            dados = resp.json()
            sl = dados.get("new_stop_loss", None)
            tp = dados.get("new_take_profit", None)
            if sl and tp:
                print(f"\n✅ IA retornou novos parâmetros:")
                print(f"   → Stop-Loss: {sl}")
                print(f"   → Take-Profit: {tp}")
                print("\nVerifique no gráfico da Bybit se o TP apareceu abaixo da entrada. 🔽")
            else:
                print("\n⚠️ A IA respondeu, mas sem valores válidos. Verifique os logs.")
        except Exception as e:
            print(f"\n⚠️ Erro ao interpretar resposta JSON: {e}")
    else:
        print(f"\n❌ Falha na requisição: HTTP {resp.status_code}")
except Exception as e:
    print(f"\n❌ Erro ao enviar solicitação para IA: {e}")
