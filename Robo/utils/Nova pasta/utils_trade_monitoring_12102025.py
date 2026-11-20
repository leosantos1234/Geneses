import logging
from compra1 import session


class OrdemUtils:
    @staticmethod
    def get_last_filled_order_id(symbol: str) -> str:
        try:
            response = session.get_order_history(
                category="linear",
                symbol=symbol,
                limit=10
            )
            order_history = response["result"]["list"]
            filled_orders = [o for o in order_history if o["orderStatus"] == "Filled"]

            if filled_orders:
                return filled_orders[0]["orderId"]
            else:
                logging.warning(f"⚠️ Nenhuma ordem preenchida encontrada para {symbol}")
                return None

        except Exception as e:
            logging.error(f"❌ Erro ao buscar última ordem filled: {e}", exc_info=True)
            return None

    @staticmethod
    def get_entry_price(symbol: str) -> float:
        try:
            response = session.get_order_history(
                category="linear",
                symbol=symbol,
                limit=10
            )
            order_history = response["result"]["list"]
            filled_orders = [o for o in order_history if o["orderStatus"] == "Filled"]

            if filled_orders:
                avg_price = filled_orders[0].get("avgPrice") or 0.0
                return float(avg_price)
            else:
                logging.warning(f"⚠️ Nenhuma ordem preenchida para obter preço de entrada: {symbol}")
                return 0.0

        except Exception as e:
            logging.error(f"❌ Erro ao buscar entry_price: {e}", exc_info=True)
            return 0.0
