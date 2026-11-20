import logging
import traceback

def validar_sl_tp(entry_price, stop_loss, take_profit, side):
    erro = None

    if side.lower() == "buy":
        if stop_loss is not None and stop_loss >= entry_price:
            erro = f"❌ STOP-LOSS INVÁLIDO para BUY: stop_loss ({stop_loss}) >= entry_price ({entry_price})"
        if take_profit is not None and take_profit <= entry_price:
            erro = f"❌ TAKE-PROFIT INVÁLIDO para BUY: take_profit ({take_profit}) <= entry_price ({entry_price})"
    elif side.lower() == "sell":
        if stop_loss is not None and stop_loss <= entry_price:
            erro = f"❌ STOP-LOSS INVÁLIDO para SELL: stop_loss ({stop_loss}) <= entry_price ({entry_price})"
        if take_profit is not None and take_profit >= entry_price:
            erro = f"❌ TAKE-PROFIT INVÁLIDO para SELL: take_profit ({take_profit}) >= entry_price ({entry_price})"

    if erro:
        logging.error(erro)
        traceback.print_stack()  # <-- Mostra quem chamou a função!
        raise ValueError(erro)
    else:
        logging.info(f"SL/TP para {side.upper()} válidos: entry={entry_price}, SL={stop_loss}, TP={take_profit}")

    return True
