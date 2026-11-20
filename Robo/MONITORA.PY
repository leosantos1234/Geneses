import logging

# Simulação de uma sessão (Mock)
class MockSession:
    def get_open_orders(self, category):
        if category == "linear":
            return {
                "retCode": 0,
                "result": {
                    "list": [
                        {"orderId": "1", "orderStatus": "open", "symbol": "BTCUSDT"},
                        {"orderId": "2", "orderStatus": "open", "symbol": "ETHUSDT"}
                    ]
                }
            }
        return {"retCode": 1, "retMsg": "Erro na API"}

def obter_preco_mercado(session, symbol):
    # Simulação para retornar preços aleatórios
    if symbol == "BTCUSDT":
        return 98000
    elif symbol == "ETHUSDT":
        return 1200
    return None

def cancelar_order(session, order_id, symbol):
    logging.info(f"Ordem cancelada: ID={order_id}, Symbol={symbol}")

# Função revisada
def monitorar_ordens_abertas(session):
    if not session:
        logging.error("Nenhuma sessão fornecida para monitorar_ordens_abertas.")
        return

    try:
        # Obter ordens abertas
        response = session.get_open_orders(category="linear")
        if response and response.get("retCode") == 0:
            ordens_abertas = response["result"]["list"]
            if not ordens_abertas:
                logging.info("Nenhuma ordem aberta encontrada.")
                return

            for ordem in ordens_abertas:
                logging.info(f"ID: {ordem['orderId']}, Status: {ordem['orderStatus']}")

                # Obter preço atual
                preco_atual = obter_preco_mercado(session, ordem["symbol"])
                if not preco_atual:
                    logging.error("Erro ao obter o preço atual.")
                    continue

                # Configurar Stop Loss e Take Profit
                stop_loss = 95000  # Exemplo
                take_profit = 100000  # Exemplo

                if preco_atual <= stop_loss:
                    logging.info(f"Ativando Stop Loss para ordem {ordem['orderId']}. Cancelando ordem.")
                    cancelar_order(session, ordem['orderId'], ordem['symbol'])
                elif preco_atual >= take_profit:
                    logging.info(f"Ativando Take Profit para ordem {ordem['orderId']}. Cancelando ordem.")
                    cancelar_order(session, ordem['orderId'], ordem['symbol'])

        else:
            logging.error(f"Erro ao listar ordens abertas: {response.get('retMsg')}")

    except Exception as e:
        logging.error(f"Erro no monitoramento de ordens: {e}")

# Configuração de logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# Teste isolado
if __name__ == "__main__":
    session = MockSession()
    monitorar_ordens_abertas(session)
