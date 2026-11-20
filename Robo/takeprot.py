import logging

# Simulação dos valores recebidos
preco_atual = 107899.7
side = "buy"
atr_value = 71.89  # Use o valor real do último candle do seu df
modo_risco = "ia"  # Pode ser "fixo", "trailing", "ia"

stop_loss = None
take_profit = None
trailing_offset = None

print(f"\n[DEBUG] preco_atual={preco_atual}, side={side}, atr_value={atr_value}")

if modo_risco == "trailing":
    trailing_offset = round(atr_value * 1.5, 2)
    print(f"[DEBUG] Modo TRAILING → trailing_offset: {trailing_offset}")

elif modo_risco == "fixo":
    stop_loss = round(preco_atual - atr_value * 1.5, 2) if side == "buy" else round(preco_atual + atr_value * 1.5, 2)
    take_profit = round(preco_atual + atr_value * 3.0, 2) if side == "buy" else round(preco_atual - atr_value * 3.0, 2)
    print(f"[DEBUG] Modo FIXO → SL: {stop_loss} | TP: {take_profit}")

elif modo_risco == "ia":
    if atr_value > preco_atual * 0.015:
        trailing_offset = round(atr_value * 0.6, 2)
        stop_loss = round(preco_atual - atr_value * 1.2, 2)
        take_profit = round(preco_atual + atr_value * 2.5, 2)
        print(f"[DEBUG] Modo IA/Alta Vol → trailing: {trailing_offset} | SL: {stop_loss} | TP: {take_profit}")
    else:
        trailing_offset = None
        stop_loss = round(preco_atual - atr_value, 2)
        take_profit = round(preco_atual + atr_value * 2.0, 2)
        print(f"[DEBUG] Modo IA/Vol Normal → SL: {stop_loss} | TP: {take_profit}")

if stop_loss is None and take_profit is None and trailing_offset is None:
    print("❌ Nenhuma estratégia de risco definida!")

# Ajuste final
if take_profit is None:
    print("❌ [ERRO] Take-Profit permaneceu None!")

print(f"[RESULTADO FINAL] SL={stop_loss} | TP={take_profit} | Trailing={trailing_offset}")
