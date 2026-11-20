# ✅ Código completo para operar no mercado Perpétuo (linear) da Bybit

from utils.logger_config import configurar_logger_global
configurar_logger_global("compra1.log")

import logging
logger = logging.getLogger(__name__)

import hashlib
import hmac
import time
import requests
import inspect
import threading
import pandas as pd
from banco_trade.db_trades import salvar_trade_sqlite
from datetime import datetime
import json
from pybit.unified_trading import HTTP
from utils.risco import calcular_rrr
from utils.validar_sl_tp import validar_sl_tp
from leotelegram import send_telegram_message
from config import BYBIT_API_KEY, BYBIT_API_SECRET, TESTNET as BYBIT_TESTNET
from utils.timestamp import obter_diferenca_timestamp
from sessao_bybit import session
from gestor_de_trade import processar_ajustes_da_ia

# Importa o gestor de trade (faz trailing, parciais, SL/TP dinâmico)
from monitoramento import monitorar_ordem_ate_encerrar

# Banco de dados local para registrar o trade
from banco_db import salvar_trade_sqlite


URL_BASE = "https://api.bybit.com/v5"
API_KEY = BYBIT_API_KEY
API_SECRET = BYBIT_API_SECRET
CATEGORY = "linear"


#session = HTTP(api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET, testnet=BYBIT_TESTNET)
session = HTTP(
    api_key=BYBIT_API_KEY,
    api_secret=BYBIT_API_SECRET,
    testnet=BYBIT_TESTNET,
    recv_window=10000  # ⏱️ aumenta a janela de tolerância para 10 segundos
)

def testar_tipos_de_saldo():
    for tipo in ["UNIFIED", "CONTRACT"]:  # SPOT REMOVIDO
        try:
            logging.info(f"🔍 Testando accountType = {tipo}")
            response = session.get_wallet_balance(accountType=tipo)
            if response.get("retCode") == 0:
                logging.info(f"✅ Tipo {tipo} FUNCIONA para PERPÉTUO!")
                return tipo, response
        except Exception as e:
            logging.error(f"❌ Erro ao testar tipo {tipo}: {e}")
    logging.error("❌ Nenhum tipo de conta compatível com PERPÉTUO foi encontrada.")
    return None, None


def detectar_account_type():
    for tipo in ["UNIFIED", "CONTRACT"]:  # SPOT REMOVIDO
        try:
            res = session.get_wallet_balance(accountType=tipo)
            if res.get("retCode") == 0:
                logging.info(f"✅ Tipo de conta compatível com PERPÉTUO detectado: {tipo}")
                return tipo
        except Exception as e:
            logging.warning(f"Falha ao testar tipo {tipo}: {e}")
    logging.error("❌ Nenhum tipo de conta compatível encontrado.")
    return None


def get_balance():
    try:
        timestamp = int(time.time() * 1000) + obter_diferenca_timestamp()
        response = session.get_wallet_balance(accountType="UNIFIED", timestamp=timestamp)
        print("DEBUG SALDO BYBIT:", response)  # <-- Veja exatamente a resposta!
        if response["retCode"] == 0:
            for item in response["result"]["list"]:
                for coin in item["coin"]:
                    if coin["coin"] == "USDT":
                        print("==== DETALHE USDT ====")
                        for k, v in coin.items():
                            print(f"{k}: {v}")
                        # Escolha aqui o campo correto!
                        saldo = float(
                            coin.get("totalAvailableBalance")
                            or coin.get("availableToWithdraw")
                            or coin.get("walletBalance")
                            or coin.get("equity")
                            or 0
                        )
                        logging.info(f"💰 Saldo USDT (escolhido): {saldo}")
                        return saldo
    except Exception as e:
        logging.error(f"Exceção ao consultar saldo: {e}")
    return 0




def generate_signature(params, api_secret=BYBIT_API_SECRET):
    query_string = "&".join([f"{key}={params[key]}" for key in sorted(params)])
    return hmac.new(api_secret.encode(), query_string.encode(), hashlib.sha256).hexdigest()

def initialize_session():
    try:
        return HTTP(api_key=API_KEY, api_secret=API_SECRET, testnet=BYBIT_TESTNET)
    except Exception as e:
        logging.error(f"❌ Erro ao inicializar sessão da Bybit: {e}")
        return None


def obter_preco_mercado(session=None, symbol=None, candle=None, modo_teste=False):
    if modo_teste and candle:
        logging.debug(f"🔁 MODO TESTE ATIVO — Retornando candle['close']: {candle['close']}")
        return candle["close"]

    try:
        response = session.get_tickers(category="linear", symbol=symbol)
        logging.debug(f"📦 DEBUG: resposta bruta da Bybit: {response}")

        if response.get("retCode", -1) != 0:
            logging.warning(f"⚠️ API Bybit retornou erro: {response.get('retMsg')}")
            return None

        lista_precos = response.get("result", {}).get("list", [])

        if not lista_precos or not isinstance(lista_precos, list):
            logging.error("❌ Lista de preços vazia ou inválida.")
            return None

        preco_str = lista_precos[0].get("lastPrice")
        if preco_str is None:
            logging.error("❌ Campo 'lastPrice' ausente.")
            return None

        try:
            preco_float = float(preco_str)
            logging.debug(f"✅ Preço de mercado extraído: {preco_float}")
            return preco_float

        except ValueError:
            logging.error(f"❌ Erro ao converter 'lastPrice' para float: {preco_str}")
            return None

    except Exception as e:
        logging.error(f"❌ Exceção ao obter preço de mercado: {e}", exc_info=True)
        return None








def cancelar_todas_ordens(symbol="BTCUSDT"):
    try:
        symbol = symbol.strip().upper() if symbol else "BTCUSDT"
        logging.info(f"🔍 Cancelando ordens abertas para o símbolo: {symbol}")

        # Consulta as ordens abertas com símbolo específico
        response = session.get_open_orders(category="linear", symbol=symbol or "BTCUSDT")


        if response.get("retCode") == 0 and response.get("result", {}).get("list"):
            for order in response["result"]["list"]:
                order_id = order.get("orderId")
                if order_id:
                    session.cancel_order(category="linear", symbol=symbol, orderId=order_id)
                    logging.info(f"✅ Ordem {order_id} cancelada.")
        else:
            logging.warning(f"⚠️ Nenhuma ordem aberta encontrada ou erro na resposta: {response}")
    except Exception as e:
        logging.error(f"❌ Erro ao cancelar ordens abertas: {e}")



def get_saldo_disponivel(symbol: str) -> float:
    try:
        from sessao_bybit import session
        res = session.get_wallet_balance(accountType="UNIFIED")
        if res.get("retCode") != 0:
            return 0.0
        for coin in res["result"]["list"]:
            if coin["coin"] == "USDT":  # assumindo par USDT
                return float(coin.get("totalAvailableBalance", "0") or coin.get("walletBalance", "0"))
        return 0.0
    except Exception:
        return 0.0




def ajustar_qty_por_saldo(saldo_disponivel: float, preco_entrada: float, margem_porcentagem: float = 0.1) -> float:
    # margem_porcentagem: % do saldo que você quer usar para essa ordem (ex: 10%)
    max_valor_ordem = saldo_disponivel * margem_porcentagem
    qty_max = max_valor_ordem / preco_entrada
    return round(qty_max, 6)  # ajuste conforme o mínimo da corretora





def create_order(
    symbol, side, qty, order_type,
    stop_loss=None, take_profit=None, trailing_offset=None,
    price=None, alavancagem=None
):
    try:
        # 1) Preço de entrada
        order_type = str(order_type).lower()  # 🛡️ Correção obrigatória

        preco_entrada = (
            obter_preco_mercado(session, symbol)
            if order_type == "market"
            else price
        )
        if preco_entrada is None:
            raise ValueError("Preço de entrada inválido")

        # 2) Saldo e qty
        saldo = get_saldo_disponivel(symbol)
        if alavancagem:
            session.set_leverage("linear", symbol, alavancagem)
        else:
            alavancagem = 2
        qty_ajustada = calcular_qty_maxima(saldo, preco_entrada, alavancagem, 0.1)
        min_q = get_min_qty(symbol) or 0.001
        qty_ajustada = max(qty_ajustada, min_q)

        # Parâmetros básicos
        params = {
            "category": "linear",
            "symbol": symbol,
            "side": side,
            "orderType": order_type.capitalize(),  # "Market" ou "Limit"
            "qty": f"{qty_ajustada:.6f}",
            "timeInForce": "GTC",
        }

        # ── LIMIT ──
        if order_type.lower() == "limit":
            # sanitiza SL/TP/TS
            stop_loss, take_profit, trailing_offset = sanitize_risk_params(
                preco_entrada, stop_loss, take_profit, trailing_offset, side.lower()
            )
            params["price"] = f"{preco_entrada:.2f}"
            if stop_loss:
                params["stopLoss"], params["slTriggerBy"] = f"{stop_loss:.2f}", "LastPrice"
            if take_profit:
                params["takeProfit"], params["tpTriggerBy"] = f"{take_profit:.2f}", "LastPrice"
            if trailing_offset:
                params["trailingStop"]    = str(int(trailing_offset))
                params["triggerDirection"]= 1 if side.lower() == "buy" else 2

            logging.info(f"ENVIANDO LIMIT: {params}")
            resp = session.place_order(**params)
            return resp["result"] if resp.get("retCode") == 0 else None

        # ── MARKET ──
        if order_type.lower() == "market":
            # 1) sanitiza SL/TP/TS
            stop_loss, take_profit, trailing_offset = sanitize_risk_params(
                preco_entrada,
                stop_loss,
                take_profit,
                trailing_offset,
                side.lower()
            )

            # 2) monta todos os parâmetros no mesmo dict
            params.update({
                "stopLoss":      f"{stop_loss:.2f}",
                "slTriggerBy":   "LastPrice",
                "takeProfit":    f"{take_profit:.2f}",
                "tpTriggerBy":   "LastPrice",
                "trailingStop":  str(int(trailing_offset)) if trailing_offset else "0",
                "triggerDirection": 1 if side.lower() == "buy" else 2
            })

            logging.info(f"ENVIANDO MARKET COM SL/TP/TS: {params}")
            resp = session.place_order(**params)
            if resp.get("retCode") != 0:
                logging.error(f"❌ RetCode inesperado: {resp}")
                return None

            return resp["result"]

        logging.error(f"Tipo inválido: {order_type}")
        return None

    except Exception as e:
        logging.error(f"Erro create_order: {e}", exc_info=True)
        return None

















def create_buy_order(symbol, qty, entry_price, entry_score=0, sentiment_score=0, strategy="breakout"):
    try:
        atr = 100
        rrr = 2.0

        stop_loss = round(entry_price - atr, 2)
        if stop_loss >= entry_price:
            stop_loss = round(entry_price * 0.995, 2)

        take_profit = round(entry_price + atr * rrr, 2)
        trailing_offset = round(atr * 0.5)

        logging.info(f"📤 Enviando ordem de COMPRA para {symbol}")
        logging.info(f"📊 Entrada: {entry_price} | QTY: {qty}")
        logging.info(f"🎯 TP: {take_profit} | 📉 SL: {stop_loss} | 🔃 TS: {trailing_offset}")

        response = create_order(
            symbol=symbol,
            side="Buy",
            qty=qty,
            order_type="Market",
            stop_loss=stop_loss,
            take_profit=take_profit,
            trailing_offset=trailing_offset
        )

        if not response or response.get("retCode", 0) != 0:
            logging.error(f"❌ Erro da Bybit ao criar ordem: {response}")
            return None

        result = response.get("result", {})
        order_id = result.get("orderId")
        preco_execucao = float(result.get("avgPrice", entry_price))
        lucro_estimado = round(preco_execucao - entry_price, 4)

        salvar_trade_sqlite({
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "side": "buy",
            "qty": qty,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "entry_price": entry_price,
            "exit_price": preco_execucao,
            "profit_loss": lucro_estimado,
            "strategy": strategy,
            "decision_source": "IA",
            "entry_score": entry_score,
            "sentiment_score": sentiment_score,
            "regime": None,
            "tendencia": "buy",
            "consenso": "buy",
            "consenso_score": entry_score,
            "consenso_razao": "legacy_fluxo_ordem",
            "decisao_lstm": None,
        })

        logging.info(f"✅ Ordem de COMPRA confirmada: ID={order_id} | Lucro estimado: {lucro_estimado}")
        return {
            "order_id": order_id,
            "symbol": symbol,
            "qty": qty,
            "entry_price": entry_price,
            "side": "buy",
            "strategy": strategy,
            "entry_score": entry_score,
            "sentiment_score": sentiment_score,
            "response_raw": response
        }

    except Exception as e:
        logging.error(f"❌ Erro ao executar ordem de compra: {e}", exc_info=True)
        return None






from datetime import datetime
import logging

def create_sell_order(symbol, qty, entry_price, entry_score=0, sentiment_score=0, strategy="breakout"):
    try:
        atr = 100
        rrr = 2.0

        stop_loss = round(entry_price + atr, 2)
        if stop_loss <= entry_price:
            stop_loss = round(entry_price * 1.005, 2)

        take_profit = round(entry_price - atr * rrr, 2)
        trailing_offset = round(atr * 0.5)

        logging.info(f"📤 Enviando ordem de VENDA para {symbol}")
        logging.info(f"📊 Entrada: {entry_price} | QTY: {qty}")
        logging.info(f"🎯 TP: {take_profit} | 📉 SL: {stop_loss} | 🔃 TS: {trailing_offset}")

        response = create_order(
            symbol=symbol,
            side="Sell",
            qty=qty,
            order_type="Market",
            stop_loss=stop_loss,
            take_profit=take_profit,
            trailing_offset=trailing_offset
        )

        if not response or response.get("retCode", 0) != 0:
            logging.error(f"❌ Erro da Bybit ao criar ordem: {response}")
            return None

        result = response.get("result", {})
        order_id = result.get("orderId")
        preco_execucao = float(result.get("avgPrice", entry_price))
        lucro_estimado = round(entry_price - preco_execucao, 4)

        salvar_trade_sqlite({
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "side": "sell",
            "qty": qty,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "entry_price": entry_price,
            "exit_price": preco_execucao,
            "profit_loss": lucro_estimado,
            "strategy": strategy,
            "decision_source": "IA",
            "entry_score": entry_score,
            "sentiment_score": sentiment_score,
            "regime": None,
            "tendencia": "sell",
            "consenso": "sell",
            "consenso_score": entry_score,
            "consenso_razao": "legacy_fluxo_ordem",
            "decisao_lstm": None,
        })

        logging.info(f"✅ Ordem de VENDA registrada: ID={order_id} | Lucro estimado: {lucro_estimado}")
        return {
            "order_id": order_id,
            "symbol": symbol,
            "qty": qty,
            "entry_price": entry_price,
            "side": "sell",
            "strategy": strategy,
            "entry_score": entry_score,
            "sentiment_score": sentiment_score,
            "response_raw": response
        }

    except Exception as e:
        logging.error(f"❌ Erro ao executar ordem de venda: {e}", exc_info=True)
        return None



def sanitize_risk_params(entry_price, stop_loss, take_profit, trailing_stop, side="buy", max_dist_pct=0.1):
    """
    Ajusta SL/TP/Trailing e valida automaticamente com fallback seguro.
    """
    from utils.validar_sl_tp import validar_sl_tp

    side = side.lower()
    max_dist = entry_price * max_dist_pct

    # Defaults
    if stop_loss is None or stop_loss <= 0:
        stop_loss = entry_price * (0.995 if side == "buy" else 1.005)
    if take_profit is None or take_profit <= 0:
        take_profit = entry_price * (1.01 if side == "buy" else 0.99)
    if trailing_stop is None or trailing_stop <= 0:
        trailing_stop = max(int(entry_price * 0.00015), 1)

    # Correções manuais com base no side
    if side == "buy":
        if take_profit <= entry_price:
            take_profit = entry_price * 1.01
        if stop_loss >= entry_price:
            stop_loss = entry_price * 0.995
        if (entry_price - stop_loss) > max_dist:
            stop_loss = entry_price - max_dist
    else:
        if take_profit >= entry_price:
            take_profit = entry_price * 0.99
        if stop_loss <= entry_price:
            stop_loss = entry_price * 1.005
        if (stop_loss - entry_price) > max_dist:
            stop_loss = entry_price + max_dist

    # Arredondamento final
    stop_loss = round(stop_loss, 2)
    take_profit = round(take_profit, 2)

    # Validação com autocorreção
    stop_loss, take_profit = validar_sl_tp(
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        side=side,
        auto_fix=True,
        raise_on_error=False
    )

    return stop_loss, take_profit, trailing_stop





def executar_ordem_dinamica(
    acao, symbol, qty, entry_price, entry_score=0, sentiment_score=0,
    strategy="breakout", stop_loss=None, take_profit=None,
    trailing_stop=None, order_type="Market"
):
    """
    Executa uma ordem com SL, TP e trailing definidos, processa ajustes da IA
    e faz monitoramento até o encerramento.
    """
    acao = acao.lower()
    side = acao.capitalize()

    # 1) Sanitização INICIAL dos parâmetros de risco (inclui side)
    stop_loss, take_profit, trailing_stop = sanitize_risk_params(
        entry_price,
        stop_loss,
        take_profit,
        trailing_stop,
        side=acao
    )
    logging.info(f"🔐 SL: {stop_loss} | TP: {take_profit} | "
                 f"Trailing: {trailing_stop} | Estratégia: {strategy} | "
                 f"Entry Score: {entry_score} | Sentimento: {sentiment_score}")

    # 2) Envia a ordem inicial com SL/TP/TS
    response = create_order(
        symbol=symbol,
        side=side,
        qty=qty,
        order_type=order_type,
        price=entry_price if order_type.lower() == "limit" else None,
        stop_loss=stop_loss,
        take_profit=take_profit,
        trailing_offset=trailing_stop
    )

    if not response:
        logging.error("❌ Falha ao enviar ordem.")
        return None

    result = response.get("result", {})
    order_id = result.get("orderId")
    preco_entrada = float(result.get("avgPrice", entry_price))

    # 3) IA pós-ordem: solicita ajustes dinâmicos de SL/TP/TS
    trade_id = order_id or "manual"
    logging.info("🧠 Solicitando ajustes da IA após envio da ordem...")
    ajustes, novos_parametros, encerrar = processar_ajustes_da_ia(
        api=session,
        symbol=symbol,
        side=acao,
        trade_id=trade_id,
        entry_price=preco_entrada,
        position_size=qty,
        risk_parameters={"stop_loss": stop_loss,
                         "take_profit": take_profit,
                         "trailing_stop": trailing_stop},
        indicators={"RSI": 50, "ATR": 300}
    )

    # 4) Sanitização FINAL após ajustes da IA
    stop = novos_parametros.get("stop_loss", stop_loss)
    take = novos_parametros.get("take_profit", take_profit)
    trailing = novos_parametros.get("trailing_stop", trailing_stop)
    stop, take, trailing = sanitize_risk_params(
        preco_entrada,
        stop,
        take,
        trailing,
        side=acao
    )

    # 5) Aplica novos SL/TP/TS se IA não ordenou encerramento
    if not encerrar:
        try:
            logging.info("🛠️ Reajustando SL/TP/TS pela IA...")
            session.set_trading_stop(
                category="linear",
                symbol=symbol,
                stopLoss=str(stop),
                takeProfit=str(take),
                trailingStop=str(int(trailing)),
                tpTriggerBy="LastPrice",
                slTriggerBy="LastPrice",
                triggerDirection=1 if acao == "buy" else 2
            )
        except Exception as e:
            logging.warning(f"⚠️ Falha ao aplicar SL/TP/TS após ajustes da IA: {e}")

    # 6) Inicia monitoramento até fechamento
    monitorar_ordem_ate_encerrar(
        trade_id=order_id,
        symbol=symbol,
        entry_price=preco_entrada,
        position_size=qty,
        risk_parameters={"stop_loss": stop,
                         "take_profit": take,
                         "trailing_stop": trailing},
        indicators={"RSI": 50, "ATR": 300,
                    "score": entry_score, "estrategia": strategy},
        api=session,
        side=acao
    )

    return response







def calcular_qty_maxima(saldo_usdt, preco_entrada, alavancagem=2, margem_pct=0.05):
    capital_usado = saldo_usdt * margem_pct
    qty_max = (capital_usado * alavancagem) / preco_entrada
    return round(qty_max, 6)



DELTA_TIMESTAMP = obter_diferenca_timestamp()

def get_step_size(symbol="BTCUSDT"):
    print("USANDO FUNÇÃO DE TIMESTAMP DE:", __file__)  # <-- Adicione ESTA linha
    try:
        params = {
            "api_key": API_KEY,
            "timestamp": int(time.time() * 1000) + DELTA_TIMESTAMP,
            "category": CATEGORY
        }
        params["sign"] = generate_signature(params, API_SECRET)
        response = requests.get(f"{URL_BASE}/market/instruments-info", params=params).json()
        if response["retCode"] == 0:
            for item in response["result"]["list"]:
                if item["symbol"] == symbol:
                    return float(item["lotSizeFilter"]["qtyStep"])
    except Exception as e:
        logging.error(f"Erro ao consultar step size: {e}")
    return 0.001  # fallback seguro



def get_min_qty(symbol):
    try:
        info = session.get_instruments_info(category="linear", symbol=symbol)
        min_qty = float(info["result"]["list"][0]["lotSizeFilter"]["minOrderQty"])
        return min_qty
    except Exception as e:
        logging.warning(f"⚠️ Falha ao obter min_qty para {symbol}: {e}")
        return 0.001  # fallback conservador




def get_order_status(order_id):
    try:
        return session.get_order_list(category=CATEGORY, orderId=order_id)
    except Exception as e:
        logging.error(f"Erro ao consultar status da ordem: {e}")
        return None

def get_minimum_qty(symbol="BTCUSDT"):
    try:
        params = {
            "api_key": API_KEY,
            "timestamp": int(time.time() * 1000) + DELTA_TIMESTAMP,
            "category": CATEGORY
        }
        params["sign"] = generate_signature(params)
        response = requests.get(f"{URL_BASE}/market/instruments-info", params=params).json()
        if response["retCode"] == 0:
            for item in response["result"]["list"]:
                if item["symbol"] == symbol:
                    return float(item["lotSizeFilter"]["minOrderQty"])
    except Exception as e:
        logging.error(f"Erro ao consultar quantidade mínima: {e}")
    return None

def cancel_order(order_id, symbol): 
    try:
        return session.cancel_order(category=CATEGORY, symbol=symbol, orderId=order_id)
    except Exception as e:
        logging.error(f"Erro ao cancelar ordem {order_id} para {symbol}: {e}")
        return None


def list_open_orders():
    try:
        return session.get_order_list(category=CATEGORY)
    except Exception as e:
        logging.error(f"Erro ao listar ordens abertas: {e}")
        return None
