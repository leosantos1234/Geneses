import os
import time
import threading
import logging
from datetime import datetime
from unittest.mock import MagicMock, patch
# import requests  # Removido
# from fastapi import FastAPI  # Removido
# import uvicorn  # Removido
# from threading import Thread  # Removido

# Configuração de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Simula a API Bybit
class MockBybitAPI:
    def __init__(self):
        self.positions = []
        self.orders = []
        self.order_id_counter = 1000
        self.price = 830.0  # preço inicial simulado

    def get_kline(self, **kwargs):
        # Simula dados de candles (sem pandas)
        data = [
            [int(time.time() * 1000) - i * 60000, 825 + i*0.5, 830 + i*0.5, 820 + i*0.5, 828 + i*0.5, 1000 + i*10, 10000]
            for i in range(20)
        ]
        return {"retCode": 0, "result": {"list": data}}

    def create_order(self, **params):
        order_id = str(self.order_id_counter)
        self.order_id_counter += 1
        order = {
            "orderId": order_id,
            "symbol": params["symbol"],
            "side": params["side"],
            "orderType": params["orderType"],
            "price": params.get("price", self.price),
            "qty": params["qty"],
            "orderStatus": "New",
            "cumExecQty": "0"
        }
        self.orders.append(order)
        # Simula preenchimento após delay (para LIMIT)
        if params["orderType"] == "Limit":
            threading.Timer(10, self._fill_order, args=[order_id]).start()  # 10s para simular espera
        else:
            self._fill_order(order_id)  # Imediato para Market
        return {"retCode": 0, "result": order}

    def _fill_order(self, order_id):
        for order in self.orders:
            if order["orderId"] == order_id:
                order["orderStatus"] = "Filled"
                order["cumExecQty"] = order["qty"]
                # Adiciona posição
                self.positions.append({
                    "symbol": order["symbol"],
                    "side": order["side"],
                    "size": order["qty"],
                    "entryPrice": order["price"]
                })
                logging.info(f"✅ Ordem {order_id} preenchida!")

    def get_order_history(self, **kwargs):
        return {"retCode": 0, "result": {"list": self.orders}}

    def get_positions(self, **kwargs):
        return {"retCode": 0, "result": {"list": self.positions}}

    def cancel_order(self, **kwargs):
        order_id = kwargs["orderId"]
        self.orders = [o for o in self.orders if o["orderId"] != order_id]
        return {"retCode": 0}

    def get_ticker(self, **kwargs):
        return {"retCode": 0, "result": {"list": [{"lastPrice": str(self.price)}]}}

    def place_order(self, **params):
        # Simula fechamento
        self.positions = []
        return {"retCode": 0}

    def set_trading_stop(self, **params):
        return {"retCode": 0}

# Simula FastAPI para /manage_trade (local)
def mock_manage_trade(payload):
    logging.info(f"📡 /manage_trade simulado com: {payload}")
    # Simula resposta normal
    return {"encerrar": False, "novos_parametros": {"trailing_offset": 2.0}}

# Inicia "servidor" mock (não precisa de thread)
# mock_server_thread = Thread(target=run_mock_api, daemon=True)
# mock_server_thread.start()
# time.sleep(2)  # Aguarda iniciar

# Script de teste
def test_breakout_simulation():
    print("🚀 Iniciando simulação de teste do sistema Breakout...")

    # Força modo simulação
    os.environ["BACKTEST_MODE"] = "false"
    os.environ["SIMULACAO"] = "true"

    try:
        print("📊 Simulando dados de mercado...")
        print("🎯 Simulando ordem limit...")

        # Mock API
        mock_api = MockBybitAPI()

        # Simula parâmetros de entrada
        symbol = "BNBUSDT"
        qty = 0.01
        preco_limit = 830.5  # Acima do preço atual

        # Simula envio de ordem LIMIT
        response = mock_api.create_order(
            symbol=symbol,
            side="Buy",
            qty=str(qty),
            orderType="Limit",
            price=str(preco_limit)
        )
        trade_id = response["result"]["orderId"]
        print(f"✅ Ordem LIMIT simulada criada: {trade_id} | Preço: {preco_limit}")

        # Simula validação de rompimento
        def validate_breakout_callback():
            # Simula rompimento válido
            return True

        # Simula esperar_ordem_executada
        from utils.monitoramento_ordem import esperar_ordem_executada
        executada = esperar_ordem_executada(
            mock_api, symbol, trade_id, timeout=60, extend_if_price_within=0.004, max_total_timeout=450,
            allow_market_fallback=True, validate_breakout=validate_breakout_callback
        )

        if executada:
            print("✅ Ordem executada! Simulando monitoramento...")

            # Simula monitoramento com preços variando
            entry_price = preco_limit
            sl = 825.0
            tp = 835.0
            preco_atual = entry_price

            for ciclo in range(5):
                # Simula variação de preço
                preco_atual += (1 if ciclo % 2 == 0 else -1) * 0.5  # Sobe/desce
                print(f"🔄 Ciclo {ciclo+1} | Preço: {preco_atual:.2f} | Entry: {entry_price:.2f} | SL: {sl:.2f} | TP: {tp:.2f}")

                # Simula chamada para /manage_trade
                manage_payload = {
                    "trade_id": trade_id,
                    "symbol": symbol,
                    "entry_price": entry_price,
                    "current_price": preco_atual,
                    "sl": sl,
                    "tp": tp
                }
                try:
                    data = mock_manage_trade(manage_payload)
                    if data:
                        encerrar = data.get("encerrar", False)
                        novos_parametros = data.get("novos_parametros", {})
                        print(f"✅ /manage_trade OK: encerrar={encerrar}, params={novos_parametros}")
                    else:
                        print("⚠️ /manage_trade retornou None (simulado)")
                except Exception as e:
                    print(f"❌ Erro em /manage_trade: {e}")

                time.sleep(2)  # Simula delay

            print("🎉 Simulação concluída com sucesso!")
        else:
            print("❌ Ordem não executada.")

    except Exception as e:
        print(f"❌ Erro na simulação: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_breakout_simulation()