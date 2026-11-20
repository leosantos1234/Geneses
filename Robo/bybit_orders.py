import logging

def cancelar_order(session, order_id, symbol):
    """
    Cancela a ordem com base no ID fornecido.
    """
    response = session.cancel_order(orderId=order_id, symbol=symbol)
    if response.get('retCode') == 0:
        logging.info(f"Ordem {order_id} cancelada com sucesso.")
    else:
        logging.error(f"Erro ao cancelar ordem {order_id}: {response.get('retMsg')}")
