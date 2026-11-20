from pydantic import ValidationError
import json

# Exemplo: importe o seu modelo
from manage_trade import TradeRequest

def validar_payload(payload: dict):
    """
    Valida qualquer payload usando o modelo Pydantic TradeRequest.
    Mostra todos os campos ausentes ou inválidos em formato amigável.
    """
    try:
        # Tenta validar o payload
        TradeRequest(**payload)
        print("✅ Payload válido! Nenhum erro encontrado.")
    except ValidationError as ve:
        print("❌ Erros encontrados no payload:")
        for erro in ve.errors():
            caminho = " -> ".join(str(p) for p in erro['loc'])
            print(f"Campo: {caminho}\n  Mensagem: {erro['msg']}\n  Tipo: {erro['type']}\n")

# --- USO ---

if __name__ == "__main__":
    # Exemplo de payload inválido (você pode mudar aqui)
    payload = {
        "trade_id": "manual",
        "symbol": "BTCUSDT",
        "entry_price": "ERRO",         # Intencionalmente errado
        "position_size": 0.001,
        "risk_parameters": {
            "stop_loss": 107200,
            "take_profit": 108000
            # "trailing_stop" está OK se for opcional
        },
        "indicators": {
            # "RSI": 51.0,             # Está faltando de propósito
            "ATR": 110.8
        }
    }

    validar_payload(payload)
