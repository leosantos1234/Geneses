from compra1 import create_order

symbol = "BTCUSDT"
side = "Buy"
qty = 0.001
order_type = "Market"
stop_loss = 109500.0
take_profit = 111000.0
trailing_offset = 150.0  # trailing stop de 150 USD

ordem = create_order(
    symbol=symbol,
    side=side,
    qty=qty,
    order_type=order_type,
    stop_loss=stop_loss,
    take_profit=take_profit,
    trailing_offset=trailing_offset
)

print("\n✅ Resultado:")
print(ordem)
