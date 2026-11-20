import logging
from compra1 import session

import logging

class OrdemUtils:
    @staticmethod
    def get_entry_price(session, symbol, order_id=None):
        """
        Busca o avgPrice no histórico; se não achar, tenta na posição aberta.
        Retorna float ou None.
        """
        try:
            res = session.get_order_history(category="linear", symbol=symbol, limit=20)
            if res.get("retCode") == 0 and "list" in res.get("result", {}):
                for order in res["result"]["list"]:
                    if order_id and str(order.get("orderId")) != str(order_id):
                        continue
                    price_str = order.get("avgPrice")
                    if price_str:
                        price = float(price_str)
                        if price > 0:
                            logging.info(f"✅ Entry price do histórico: {price}")
                            return price
        except Exception as e:
            logging.error(f"❌ Erro ao buscar entry_price (histórico): {e}")

        # Fallback: posição aberta
        try:
            pos = session.get_positions(category="linear", symbol=symbol)
            if pos.get("retCode") == 0:
                lst = pos.get("result", {}).get("list", [])
                if lst:
                    entry = lst[0].get("entryPrice")
                    if entry:
                        price = float(entry)
                        if price > 0:
                            logging.info(f"⚠️ Entry price via posição aberta: {price}")
                            return price
        except Exception as e:
            logging.error(f"❌ Erro no fallback via posição aberta: {e}")

        logging.warning(f"❌ Não foi possível recuperar entry_price para {symbol} (order_id={order_id})")
        return None

