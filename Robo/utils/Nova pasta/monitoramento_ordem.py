# utils/monitoramento_ordem.py

import time
import logging

def esperar_ordem_executada(api, symbol, order_id, timeout=30):
    """
    Aguarda até a ordem ser executada (Filled) ou até timeout (segundos).
    Retorna True se executada, False caso contrário.
    """
    tempo_inicio = time.time()
    while time.time() - tempo_inicio < timeout:
        status = api.get_order_history(category="linear", symbol=symbol, orderId=order_id)
        if status.get("retCode", 1) == 0:
            orders = status["result"].get("list", [])
            if orders:
                order = orders[0]
                if float(order.get("cumExecQty", 0)) > 0 and order.get("orderStatus") == "Filled":
                    logging.info(f"✅ Ordem {order_id} EXECUTADA!")
                    return True
        time.sleep(1)
    logging.warning(f"⏳ Timeout: Ordem {order_id} não executada.")
    return False
