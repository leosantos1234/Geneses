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
    api, symbol, entry_price, stop_loss, take_profit, trailing_offset, side, preco_atual
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

    # Exemplo de trailing condicional: só ativa se lucro > X e offset razoável
    ativar_trailing = False
    lucro_minimo_pct = 0.005  # trailing só ativa se lucro > 0.5%
    side = side.lower()
    preco_entrada = float(entry_price)
    preco_agora = float(preco_atual)

    if side == "buy":
        progresso = preco_agora - preco_entrada
        if progresso > preco_entrada * lucro_minimo_pct and trailing_offset > 0:
            novo_stop = max(stop_loss, preco_agora - trailing_offset)
            if novo_stop > stop_loss:
                ativar_trailing = True
                stop_loss = novo_stop
    else:  # sell
        progresso = preco_entrada - preco_agora
        if progresso > preco_entrada * lucro_minimo_pct and trailing_offset > 0:
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




def processar_ajustes_da_ia(
    api=None,  # noqa: ARG002
    symbol=None,
    side=None,
    entry_price=None,
    stop_loss=None,
    take_profit=None,
    trailing_offset=None,
    indicators=None,
    trade_id=None,
    risk_parameters=None
):
    import requests
    import pandas as pd
    import logging
    import math
    import json

    ajustes = {}
    encerrar = False

    # ---------------- COMPATIBILIDADE RETROATIVA ----------------
    if risk_parameters is None:
        risk_parameters = {
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "trailing_stop": trailing_offset
        }

    # ---------------- FUNÇÃO AUXILIAR SEGURA ----------------
    def to_float_safe(value, default=0.0):
        try:
            v = float(value)
            if math.isnan(v):
                return default
            return v
        except Exception:
            return default

    try:
        # --- Extração robusta de indicadores (dict ou DataFrame) ---
        if isinstance(indicators, dict):
            rsi = indicators.get("RSI")
            atr = indicators.get("ATR")
        else:
            try:
                rsi = indicators["RSI"].iloc[-1] if "RSI" in indicators.columns else None
                atr = indicators["ATR"].iloc[-1] if "ATR" in indicators.columns else None
            except Exception:
                rsi, atr = None, None

        # --- Blindagem contra Series e NaN ---
        if isinstance(rsi, pd.Series):
            rsi = float(rsi.iloc[-1])
        if isinstance(atr, pd.Series):
            atr = float(atr.iloc[-1])

        rsi = to_float_safe(rsi, 50.0)
        atr = to_float_safe(atr, 100.0)
        entry_price_safe = to_float_safe(entry_price, 0.0)

        # --- Construção segura do payload ---
        payload = {
            "trade_id": trade_id or "N/A",
            "symbol": symbol or "UNKNOWN",
            "side": side or "buy",
            "entry_price": entry_price_safe,
            "stop_loss": to_float_safe(risk_parameters.get("stop_loss")),
            "take_profit": to_float_safe(risk_parameters.get("take_profit")),
            "trailing_offset": (
                to_float_safe(risk_parameters.get("trailing_stop").get("offset", 0.0))
                if isinstance(risk_parameters.get("trailing_stop"), dict)
                else to_float_safe(risk_parameters.get("trailing_stop"))
            ),

            "rsi": rsi,
            "atr": atr,
            "tendencia_macro": indicators.get("tendencia_macro", "neutra") if isinstance(indicators, dict) else "neutra",
            "sentimento": indicators.get("sentiment_score", "neutro") if isinstance(indicators, dict) else "neutro",
        }

        logging.debug(f"📤 Enviando payload para IA:\n{json.dumps(payload, indent=2)}")

        logging.debug(f"📤 Enviando payload para IA:\n{json.dumps(payload, indent=2)}")

        # === 🚨 CHAMADA SEGURA DA IA COM FALLBACK LOCAL ===
        try:
            resposta = requests.post(f"{API_IA_URL}/manage_trade", json=payload, timeout=8)
            resposta.raise_for_status()
        except Exception as e:
            logging.warning(f"⚠️ IA indisponível ({e}), usando fallback local.")
            ajustes = {
                "new_stop_loss": risk_parameters.get("stop_loss"),
                "new_take_profit": risk_parameters.get("take_profit"),
                "new_trailing_stop": risk_parameters.get("trailing_stop"),
            }
            return ajustes, risk_parameters, False

        # --- TRATAMENTO SEGURO DA RESPOSTA DA IA ---
        if resposta is None:
            logging.error("❌ Nenhuma resposta recebida da IA (resposta=None). Abortando ajustes.")
            return None

        if not hasattr(resposta, "json"):
            logging.error(f"❌ Objeto inesperado recebido da IA: {type(resposta)}")
            return None

        try:
            json_resp = resposta.json()
            if not json_resp:
                logging.error("⚠️ A resposta da IA veio vazia (JSON=None ou {}). Abortando ajustes.")
                return {}, risk_parameters, False
        except Exception as e:
            logging.error(f"❌ Falha ao converter resposta da IA em JSON: {e}")
            return {}, risk_parameters, False

        # 🔹 BLINDAGEM COMPLETA (Novo trecho!)
        if isinstance(json_resp, dict):
            ajustes = json_resp.get("ajustes") or json_resp.get("adjustments", {})
            # 🔹 Corrige se ajuste vier direto no root e for dict (OpenAI gpt-3.5 style)
            for k in ("new_stop_loss", "new_take_profit", "new_trailing_stop"):
                if k in json_resp and k not in ajustes:
                    ajustes[k] = json_resp[k]
            logging.info(f"✅ Ajustes recebidos da IA: {ajustes}")
        elif isinstance(json_resp, list):
            logging.error(f"⚠️ Resposta inesperada da IA (lista): conteúdo={json_resp}")
            # Tenta converter para dict se for [sl, tp]
            if len(json_resp) == 2 and all(isinstance(v, (int, float)) for v in json_resp):
                ajustes = {"new_stop_loss": json_resp[0], "new_take_profit": json_resp[1]}
                logging.warning(f"⚠️ Lista convertida para dict: {ajustes}")
            else:
                ajustes = {}
        else:
            logging.error(f"⚠️ Resposta inesperada da IA: tipo={type(json_resp)}, conteúdo={json_resp}")
            ajustes = {}

    except Exception as e:
        logging.error(f"❌ Erro ao enviar para IA: {e}", exc_info=True)
        return {"action": "manter"}, risk_parameters, False

    # 🔴 Caso a IA solicite encerramento
    if ajustes.get("action") == "close":
        encerrar = True
        return ajustes, risk_parameters, encerrar

    # 🔵 Aplicar ajustes válidos retornados
    for chave, nome_parametro in {
        "new_stop_loss": "stop_loss",
        "new_take_profit": "take_profit",
        "new_trailing_stop": "trailing_stop"
    }.items():
        novo_valor = ajustes.get(chave)
        valor_antigo = risk_parameters.get(nome_parametro)
        if novo_valor is not None and novo_valor != valor_antigo:
            risk_parameters[nome_parametro] = novo_valor
            logging.info(f"🤖 IA ajustou {nome_parametro} de {valor_antigo} → {novo_valor}")

    return ajustes, risk_parameters, encerrar








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
