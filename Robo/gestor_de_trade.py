import time
import requests
import logging
import os
import uuid
import pandas as pd
import json
from utils.utils_market import get_market_data_bybit
from utils.indicadores import calculate_indicators
from sessao_bybit import session
from db_manager import salvar_trade
from datetime import datetime
from pybit.unified_trading import HTTP
from utils.logica_stop_take_adaptativo import calcular_stop_take_adaptativo

import os
from dotenv import load_dotenv
load_dotenv()
API_IA_URL = os.getenv("API_IA_URL", "http://localhost:8000")

#Ativos
from utils.parse_env import get_ativos_env
symbol = get_ativos_env()[0]


logger = logging.getLogger(__name__)
ajustes_aplicados_cache = {}
TEMPO_MAXIMO = 60 * 60  # 1 hora, por exemplo

# -----------------------------
# Funções auxiliares
# -----------------------------

def get_sl_tp():
    from router_ajustes import SL_GLOBAL, TP_GLOBAL
    return SL_GLOBAL, TP_GLOBAL


def parse_timestamp_safe(valor):
    if isinstance(valor, datetime):
        return valor
    if isinstance(valor, str) and "T" in valor:
        return datetime.fromisoformat(valor)
    if isinstance(valor, (int, float)) or (isinstance(valor, str) and valor.isdigit()):
        return datetime.utcfromtimestamp(int(valor) / 1000)
    raise ValueError(f"Formato inválido para timestamp: {valor}")


def verificar_ou_aguardar_operacao(session, symbol):
    while os.path.exists("operacao_ativa.flag"):
        logging.info("⚠️ Flag de operação ativa detectada. Verificando posição real na Bybit...")
        try:
            response = session.get_positions(category="linear", symbol=symbol)
            positions = response.get("result", {}).get("list", [])
            pos_aberta = any(float(p.get("size", 0)) > 0 for p in positions)
            if pos_aberta:
                logging.info("⏳ Posição ainda aberta na Bybit. Aguardando encerramento...")
                time.sleep(30)
            else:
                os.remove("operacao_ativa.flag")
                logging.info("✅ Flag removida. Sistema pronto para nova análise.")
                break
        except Exception as e:
            logging.error(f"❌ Erro ao verificar posições na Bybit: {e}")
            time.sleep(60)


def ajustar_trailing_stop_condicional(
    api,
    symbol,
    entry_price,
    stop_loss,
    take_profit,
    trailing_offset,
    side,
    preco_atual,
    estrutura_confirmada=None,
):
    """
    Ajusta o trailing stop de forma condicional.
    Centraliza a lógica para evitar múltiplas ativações simultâneas.

    Parâmetros:
        api: objeto da API da corretora
        symbol: string (ex: 'BNBUSDT')
        entry_price: preço de entrada da posição
        stop_loss: valor atual do stop-loss
        take_profit: valor atual do take-profit
        trailing_offset: offset do trailing stop sugerido/calculado
        side: 'buy' ou 'sell'
        preco_atual: preço mais recente (candle atual)

    Retorna:
        Novo valor de stop_loss (pode ser igual ao anterior se não houver ajuste)
    """
    import logging
    import os

    # 🧱 Governança: "Stop só anda quando a estrutura anda"
    # Se a estrutura ainda não confirmou, bloqueia qualquer aperto/movimento de SL.
    if estrutura_confirmada is False:
        logging.info("🧱 Estrutura não confirmada — ajuste de trailing bloqueado.")
        return stop_loss

    # Exemplo de trailing condicional: só ativa se lucro > X e offset razoável
    ativar_trailing = False
    lucro_minimo_pct = 0.005  # trailing só ativa se lucro > 0.5%
    side = side.lower()
    preco_entrada = float(entry_price)
    preco_agora = float(preco_atual)
    trailing_r = float(os.getenv("TRAILING_ACTIVATION_R", 1.0))
    risk_dist = abs(preco_entrada - float(stop_loss)) if stop_loss else 0.0

    if side == "buy":
        progresso = preco_agora - preco_entrada
        if risk_dist > 0 and (progresso / risk_dist) >= trailing_r and trailing_offset > 0:
            novo_stop = max(stop_loss, preco_agora - trailing_offset)
            if novo_stop > stop_loss:
                ativar_trailing = True
                stop_loss = novo_stop
    else:  # sell
        progresso = preco_entrada - preco_agora
        if risk_dist > 0 and (progresso / risk_dist) >= trailing_r and trailing_offset > 0:
            novo_stop = min(stop_loss, preco_agora + trailing_offset)
            if novo_stop < stop_loss:
                ativar_trailing = True
                stop_loss = novo_stop

    if ativar_trailing:
        logging.info(
            f"🔵 [Trailing] Ativado! Novo SL: {stop_loss:.2f} (offset: {trailing_offset}, lucro: {progresso:.2f})"
        )
    else:
        logging.info("⏸️ Trailing não ativado neste ciclo.")

    return stop_loss




def detectar_reversao(df):
    try:
        if 'Bollinger_Lower' not in df.columns or 'Bollinger_Upper' not in df.columns:
            logging.error("🚨 Colunas Bollinger_Lower ou Bollinger_Upper ausentes!")
            return None

        macd, signal = df["MACD"].iloc[-1], df["Signal_Line"].iloc[-1]
        macd_ant, signal_ant = df["MACD"].iloc[-2], df["Signal_Line"].iloc[-2]
        rsi, rsi_ant = df["RSI"].iloc[-1], df["RSI"].iloc[-2]
        close = df["close"].iloc[-1]
        lower_band, upper_band = df["Bollinger_Lower"].iloc[-1], df["Bollinger_Upper"].iloc[-1]

        if macd > signal and macd_ant < signal_ant and rsi > 30 and close > lower_band:
            logging.info("🔍 Reversão de ALTA detectada")
            return "reversao_alta"

        if macd < signal and macd_ant > signal_ant and rsi < 70 and rsi_ant > 50 and close < upper_band:
            logging.info("🔍 Reversão de BAIXA detectada")
            return "reversao_baixa"
    except Exception as e:
        logging.warning(f"⚠️ Falha ao detectar reversão: {e}")
    return None


def avaliar_score_tecnico(df, direcao):
    score = 0
    macd, signal = df["MACD"].iloc[-1], df["Signal_Line"].iloc[-1]
    rsi = df["RSI"].iloc[-1]
    close, sma = df["close"].iloc[-1], df["SMA_10"].iloc[-1]

    if direcao == "buy":
        if macd > signal or abs(macd - signal) < 2: score += 1
        if rsi > 50: score += 1
        if close > sma: score += 1
    elif direcao == "sell":
        if macd < signal or abs(macd - signal) < 2: score += 1
        if rsi < 50: score += 1
        if close < sma: score += 1

    logging.info(f"📊 Score técnico {direcao.upper()}: {score}/3")
    return score


def calcular_lucro_e_registrar(trade_id, symbol, entry_price, position_size, last_price, side, payload):
    lucro = (last_price - entry_price) * position_size if side == "buy" else (entry_price - last_price) * position_size
    payload["resultado"] = "win" if lucro >= 0 else "loss"
    payload["lucro"] = round(lucro, 2)
    salvar_trade(payload)
    logger.info(f"📝 Trade registrado: {payload['resultado']}, Lucro: {payload['lucro']}")





def encerrar_ordem(trade_id=None, symbol=None, api=None):
    try:
        if not symbol:
            symbol = get_ativos_env()[0]
        posicoes = api.get_positions(category="linear", symbol=symbol)
        posicao = next((p for p in posicoes.get("result", {}).get("list", []) if float(p.get("size", 0)) > 0), None)

        if posicao:
            side_oposto = "Sell" if posicao["side"] == "Buy" else "Buy"
            qty = float(posicao["size"])
            session.place_order(category="linear", symbol=symbol, side=side_oposto,
                                orderType="Market", qty=str(qty), reduceOnly=True, timeInForce="IOC")
        elif trade_id:
            session.cancel_order(category="linear", symbol=symbol, orderId=trade_id)
    except Exception as e:
        logging.error(f"❌ Erro ao encerrar trade {trade_id}: {e}")


def aplicar_ajustes(symbol, ajustes):
    global ajustes_aplicados_cache
    trade_id = f"{symbol}_ajuste"

    if trade_id in ajustes_aplicados_cache and ajustes_aplicados_cache[trade_id] == ajustes:
        return

    try:
        posicoes = session.get_positions(category="linear", symbol=symbol)
        tem_posicao = any(float(p.get("size", 0)) > 0 for p in posicoes.get("result", {}).get("list", []))
        if not tem_posicao: return

        payload = {
            "category": "linear",
            "symbol": symbol,
            "takeProfit": str(round(ajustes.get("new_take_profit", 0), 2)) if ajustes.get("new_take_profit") else None,
            "stopLoss": str(round(ajustes.get("new_stop_loss", 0), 2)) if ajustes.get("new_stop_loss") else None,
            "trailingStop": str(round(ajustes.get("new_trailing_stop", 0), 1)) if ajustes.get("new_trailing_stop") else None,
            "tpTriggerBy": "LastPrice",
            "slTriggerBy": "LastPrice"
        }

        payload = {k: v for k, v in payload.items() if v not in [None, 0.0, "", "0.0"]}
        session.set_trading_stop(**payload)
        ajustes_aplicados_cache[trade_id] = ajustes
    except Exception as e:
        logging.error(f"❌ Erro ao aplicar ajustes na posição de {symbol}: {e}")
