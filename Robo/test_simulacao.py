import os
import time
import threading
import logging
from datetime import datetime
from unittest.mock import MagicMock, patch
import pandas as pd
import requests
from fastapi import FastAPI
import uvicorn
from threading import Thread

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
        # Simula dados de candles
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

# Simula FastAPI para /manage_trade
app = FastAPI()

@app.post("/manage_trade")
def manage_trade(payload: dict):
    logging.info(f"📡 /manage_trade chamado com: {payload}")
    # Simula resposta normal
    return {"encerrar": False, "novos_parametros": {"trailing_offset": 2.0}}

def run_mock_api():
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")

# Inicia servidor mock em thread
mock_server_thread = Thread(target=run_mock_api, daemon=True)
mock_server_thread.start()
time.sleep(2)  # Aguarda iniciar

class MockBybitAPI:
    def __init__(self):
        self.positions = []
        self.orders = []
        self.order_id_counter = 1000
        self.price = 830.0  # preço inicial simulado

    def get_kline(self, **kwargs):
        # Simula dados de candles
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
        # Simula preenchimento após delay
        threading.Timer(5, self._fill_order, args=[order_id]).start()
        return {"retCode": 0, "result": order}

    def _fill_order(self, order_id):
        for order in self.orders:
            if order["orderId"] == order_id:
                order["orderStatus"] = "Filled"
                order["cumExecQty"] = order["qty"]
                # Adiciona posição
                self.positions.append({
                    "symbol": order["symbol"],
                    "size": order["qty"],
                    "entryPrice": order["price"],
                    "side": order["side"]
                })
                break

    def get_order_history(self, **kwargs):
        return {"retCode": 0, "result": {"list": self.orders}}

    def get_positions(self, **kwargs):
        return {"retCode": 0, "result": {"list": self.positions}}

    def get_wallet_balance(self, **kwargs):
        return {"retCode": 0, "result": {"list": [{"coin": "USDT", "walletBalance": "1000"}]}}

    def get_tickers(self, **kwargs):
        return {"retCode": 0, "result": {"list": [{"lastPrice": str(self.price)}]}}

    def place_order(self, **params):
        # Simula fechamento
        self.positions = []
        return {"retCode": 0}

    def set_trading_stop(self, **params):
        return {"retCode": 0}

# Script de teste
def test_breakout_simulation():
    print("🚀 Iniciando simulação de teste do sistema Breakout...")

    # Força modo simulação
    os.environ["BACKTEST_MODE"] = "false"
    os.environ["SIMULACAO"] = "true"

    # Simula breakout diretamente
    try:
        # Simula dados
        df = pd.DataFrame({
            'time': [int(time.time() * 1000) - i * 60000 for i in range(20)],
            'open': [825 + i*0.5 for i in range(20)],
            'high': [830 + i*0.5 for i in range(20)],
            'low': [820 + i*0.5 for i in range(20)],
            'close': [828 + i*0.5 for i in range(20)],
            'volume': [1000 + i*10 for i in range(20)],
            'turnover': [10000 for i in range(20)]
        })

        # Simula breakout bullish
        df['Resistance'] = df['high'].rolling(5).max()
        df['Support'] = df['low'].rolling(5).min()

        print("📊 Dados simulados criados.")
        print("🎯 Simulando ordem limit...")

        # Mock API
        mock_api = MockBybitAPI()

        # Simula entrada
        preco_limit = 830.5  # exemplo
        qty = 0.1

        # Cria ordem mock
        response = mock_api.create_order(
            category="linear",
            symbol="BNBUSDT",
            side="Buy",
            orderType="Limit",
            qty=str(qty),
            price=str(preco_limit)
        )

        trade_id = response["result"]["orderId"]
        print(f"✅ Ordem simulada criada: {trade_id} | Preço: {preco_limit} | Qty: {qty}")

        # Simula esperar_ordem_executada com validação de rompimento
        from utils.monitoramento_ordem import esperar_ordem_executada
        def mock_validate_breakout():
            # Simula validação: sempre True para teste
            return True
        executada = esperar_ordem_executada(
            mock_api, "BNBUSDT", trade_id, timeout=60, extend_if_price_within=0.004, max_total_timeout=450,
            allow_market_fallback=True, validate_breakout=mock_validate_breakout
        )

        if executada:
            print("✅ Ordem preenchida! Simulando monitoramento com Trailing Stop...")

            # Simula monitoramento com trailing
            entry_price = preco_limit
            sl = 825.0  # SL inicial
            tp = 835.0  # TP
            trailing_offset = 2.0  # Exemplo de offset
            preco_atual = entry_price

            print(f"🧠 Entrada: {entry_price:.2f} | SL inicial: {sl:.2f} | TP: {tp:.2f} | Trailing Offset: {trailing_offset:.2f}")

            for i in range(10):
                # Simula preço subindo
                preco_atual += 0.5
                time.sleep(0.5)

                # Simula cálculo de progresso
                progresso = (preco_atual - entry_price) / (tp - entry_price) * 100
                print(f"🔄 Ciclo {i+1}: Preço = {preco_atual:.2f} | Progresso = {progresso:.1f}%")

                # Simula ativação de trailing após progresso >30%
                if progresso > 30 and not hasattr(test_breakout_simulation, 'trailing_ativado'):
                    test_breakout_simulation.trailing_ativado = True
                    print("🔵 Trailing Stop ATIVADO!")

                # Simula movimento do SL com trailing
                if hasattr(test_breakout_simulation, 'trailing_ativado'):
                    novo_sl = preco_atual - trailing_offset
                    if novo_sl > sl:
                        sl = novo_sl
                        print(f"🟣 SL movido para: {sl:.2f} (trailing)")

                # Simula proteção de lucro
                lucro = preco_atual - entry_price
                if lucro > 1.0:  # >1 USDT
                    print(f"💰 Lucro atual: {lucro:.2f} — Proteção ativa, ignoraria saída imediata se acionada.")

                # Simula hit de TP
                if preco_atual >= tp:
                    print(f"🎯 TP atingido em {preco_atual:.2f}! Simulando fechamento.")
                    break

            print("🏁 Simulação de trailing concluída.")

        else:
            print("❌ Ordem não preenchida.")

    except Exception as e:
        print(f"❌ Erro na simulação: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_breakout_simulation()