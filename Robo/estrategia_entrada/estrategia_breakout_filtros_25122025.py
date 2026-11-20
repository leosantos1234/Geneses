import os
from utils.logger_config import configurar_logger_global
configurar_logger_global("breakout_filtros.log") 
import logging
logger = logging.getLogger(__name__)

import pandas as pd
import time
import os
import hmac
import hashlib
import numpy as np
import json
import sys
from datetime import datetime, timedelta
from functools import lru_cache
import requests
from utils.risco import calcular_rrr
from utils.logica_stop_take_adaptativo import calcular_stop_take_adaptativo
from compra1 import cancel_order, session,  create_order, initialize_session, obter_preco_mercado
from utils.indicadores import calculate_indicators
from utils.monitoramento_ordem import esperar_ordem_executada
from pybit.unified_trading import HTTP
from monitoramento import monitorar_ordem_ate_encerrar
from config import BYBIT_API_KEY, BYBIT_API_SECRET, TESTNET as BYBIT_TESTNET
from utils.ml_core import validar_com_modelo, carregar_modelo_com_fallback
from compra1 import executar_ordem_dinamica, get_saldo_disponivel, ajustar_para_min_qty
from utils.utils_market import get_market_data_bybit
from estrategia_entrada.pos_execucao_monitoramento import preparar_trade_para_monitoramento
from utils.utils_telegram import send_telegram_message
from utils.parse_env import get_ativos_env
from regime.regime_detector import RegimeDetector
from regime.regime_configurator import RegimeConfigurator
from utils.entrada_breakout_confirmada import (
    calcular_preco_confirmacao,
    executar_entrada_breakout_confirmada,
)
from utils.validacoes import validar_preco
symbol = get_ativos_env()[0]
interval = os.getenv("INTERVAL", "1")

import threading
from fastapi import FastAPI

app = FastAPI()

#os.environ["BACKTEST_MODE"] = "false"

#os.environ["MODO_RELAXADO"] = "true"  # ✅  ATIVADO o modo relaxado
#MODO_RELAXADO = os.getenv("MODO_RELAXADO", "false").lower() == "true"
#logging.info(f"⚙️ Forçando MODO_RELAXADO = {MODO_RELAXADO}")


from utils.config_flags import BACKTEST_MODE

ATR_BREAKOUT_MIN_PCT = float(os.getenv("THRESHOLD_ATR_MIN", 0.002))  # Default: 0.2%
DELTA_MINIMO_BREAKOUT = float(os.getenv("DELTA_MINIMO_BREAKOUT", 0.7))
USAR_ENTRADA_CONFIRMADA = os.getenv("USAR_ENTRADA_CONFIRMADA", "true").strip().lower() in ["true", "1", "yes"]
FATOR_CONFIRMACAO_TR = float(os.getenv("FATOR_CONFIRMACAO_TR", os.getenv("OFFSET_CONFIRMACAO_ATR", 0.12)))
TIMEOUT_ENTRADA_SEGUNDOS = int(os.getenv("TIMEOUT_ENTRADA_SEGUNDOS", 180))
INTERVALO_VERIFICACAO_ENTRADA = int(os.getenv("INTERVALO_VERIFICACAO_ENTRADA", 3))

# 🔐 Variáveis globais de filtros (inicializadas para evitar NameError)
filtro_rsi = False
filtro_volume = False
filtro_media = False
resultado_final = 0



# 🔹 Inicializar sessão autenticada da Bybit antes de qualquer uso
session = HTTP(
    testnet=BYBIT_TESTNET,  # <- isso será testnet=False
    api_key=BYBIT_API_KEY,
    api_secret=BYBIT_API_SECRET
)


# 🔹 Definir API corretamente
api = session

# 🔹 Teste: Obter saldo da conta (agora `session` já está definido)
try:
    response = session.get_wallet_balance(accountType="UNIFIED")
    print("✅ Conexão bem-sucedida! Saldo:", response)
except Exception as e:
    print(f"❌ Erro ao consultar saldo: {e}")


# 🔹 Variáveis globais de médias (inicializadas para evitar UnboundLocalError)
ema50 = None
ema200 = None


# Generate_signature()
def generate_signature(params, api_secret=BYBIT_API_SECRET):
    """
    Gera a assinatura HMAC-SHA256 para autenticação na API Bybit.

    Args:
        params (dict): Dicionário com os parâmetros da requisição.
        api_secret (str): Chave secreta da API.

    Returns:
        str: Assinatura HMAC-SHA256 em hexadecimal.
    """
    query_string = "&".join([f"{key}={params[key]}" for key in sorted(params)])
    
    # Criar hash HMAC-SHA256
    return hmac.new(
        bytes(api_secret, "utf-8"), 
        bytes(query_string, "utf-8"), 
        hashlib.sha256
    ).hexdigest()



def get_lock_path(symbol):
    return f"ordem_ativa_{symbol}.flag"

def existe_lock_fisico(symbol):
    return os.path.exists(get_lock_path(symbol))

def cria_lock_fisico(symbol):
    with open(get_lock_path(symbol), "w") as f:
        f.write("lock")


def obter_config_validacao():
    modo = os.getenv("MODO_VALIDACAO", "agressivo").strip().lower()
    mapa_score_minimo = {
        "agressivo": int(os.getenv("SCORE_MINIMO_AGRESSIVO", 1)),
        "moderado": int(os.getenv("SCORE_MINIMO_MODERADO", 3)),
        "conservador": int(os.getenv("SCORE_MINIMO_CONSERVADOR", 4)),
    }
    score_pct = {
        "agressivo": int(os.getenv("SCORE_PCT_AGRESSIVO", 0)),
        "moderado": int(os.getenv("SCORE_PCT_MODERADO", 50)),
        "conservador": int(os.getenv("SCORE_PCT_CONSERVADOR", 65)),
    }
    return mapa_score_minimo.get(modo, 4), score_pct.get(modo, 65), modo





# ✅ Evitar múltiplas chamadas da API armazenando em cache
import pandas as pd
import logging

def get_market_data_bybit(symbol, session, interval="60", limit=20):
    """
    Obtém os dados de mercado (candles) da Bybit e retorna um DataFrame formatado corretamente.

    Args:
        symbol (str): Par de negociação (ex: BTCUSDT).
        session (objeto HTTP): Sessão autenticada da Bybit API.
        interval (str): Intervalo de tempo dos candles (ex: "1" para 1min, "60" para 1h).
        limit (int): Número de candles a serem buscados.

    Returns:
        pd.DataFrame: DataFrame com os dados de mercado ordenados por tempo.
    """
    try:
        # 🔹 Fazendo a requisição à API da Bybit
        response = session.get_kline(
            category="linear",
            symbol=symbol,
            interval=interval,
            limit=limit
        )

        logging.debug(f"🔍 DEBUG - Resposta bruta da API: {response}")  # 🚀 Log para depuração

        # 🚨 Verifica se a resposta é válida
        if not isinstance(response, dict):
            logging.error("❌ Erro: Resposta da API não é um dicionário válido!")
            return pd.DataFrame()

        if response.get("retCode") != 0:
            logging.error(f"⚠️ Erro ao obter dados: {response.get('retMsg', 'Mensagem não disponível')}")
            return pd.DataFrame()

        # 🔹 Extração segura dos dados
        data = response.get("result", {}).get("list", [])
        if not data:
            logging.warning("⚠️ Aviso: Nenhum dado foi retornado pela API.")
            return pd.DataFrame()

        # 🔹 Convertendo para DataFrame e nomeando as colunas
        df = pd.DataFrame(data, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])

        # 🔹 Convertendo colunas para tipos numéricos
        numeric_columns = ['open', 'high', 'low', 'close', 'volume', 'turnover']
        df[numeric_columns] = df[numeric_columns].apply(pd.to_numeric, errors='coerce')

        # 🔹 Ordenando os dados do mais antigo para o mais recente
        df = df.sort_values('time')

        # 🔍 Logando os últimos 5 candles para depuração
        logging.info(f"📊 Últimos 5 candles recebidos ({symbol}):\n{df.tail()}")

        return df

    except Exception as e:
        logging.error(f"❌ Erro ao obter dados de mercado: {e}")
        return pd.DataFrame()


 

def calcular_tamanho_lote(risco_em_usdt, stop_distance, preco_atual, saldo_disponivel, fallback=0.01):
    try:
        if stop_distance <= 0 or preco_atual <= 0:
            raise ValueError("stop_distance ou preco_atual inválido")

        lote = risco_em_usdt / stop_distance
        maximo = saldo_disponivel / preco_atual
        return round(min(lote, maximo), 3)
    except Exception as e:
        logging.warning(f"⚠️ Erro ao calcular tamanho do lote: {e}")
        return fallback



def confirmar_entrada_com_criterios(df, side, is_backtest=False):
    """
    Confirma a entrada com base nos critérios definidos e no modo de validação configurado (.env).
    """
    sentimento_simulado = "positivo" if side == "buy" else "negativo"
    criterios = calcular_score_completo(df, side=side, sentimento_geral=sentimento_simulado)
    score_final = criterios.pop("score", 0)

    # 🔄 Usa o modo de validação do .env
    score_requerido, _, modo = obter_config_validacao()

    logging.info(f"📌 Score final: {score_final} | Score mínimo exigido para modo '{modo}': {score_requerido}")

    return score_final >= score_requerido




def calculate_breakout_levels(df, period=5):  # 🔥 Reduzindo de 20 para 5 candles
    """Calcula suporte e resistência mais próximos para melhor detecção de breakouts."""
    df["Resistance"] = df["high"].rolling(window=period, min_periods=1).max()
    df["Support"] = df["low"].rolling(window=period, min_periods=1).min()

    # 🔹 Suaviza para evitar níveis irreais
    df["Resistance"] = df["Resistance"].rolling(window=3, min_periods=1).mean()
    df["Support"] = df["Support"].rolling(window=3, min_periods=1).mean()

    return df



def parse_timestamp_safe(valor):
    try:
        if isinstance(valor, datetime):
            return valor
        if isinstance(valor, str) and "T" in valor:
            return datetime.fromisoformat(valor)
        if isinstance(valor, (int, float)) or (isinstance(valor, str) and valor.isdigit()):
            return datetime.utcfromtimestamp(int(valor) / 1000)
    except Exception as e:
        logging.error(f"❌ Erro ao interpretar timestamp: {valor} | Erro: {e}")
    return None

def mock_breakout_candle(df):
    if "Resistance" not in df.columns or "Support" not in df.columns:
        df = calculate_breakout_levels(df)

    # Converter último timestamp para datetime com segurança
    ultimo_tempo_raw = df["time"].iloc[-1]
    ultimo_tempo = parse_timestamp_safe(ultimo_tempo_raw)
    if ultimo_tempo is None:
        logging.error("❌ Timestamp inválido no último candle. Abortando mock.")
        return df

    proximo_tempo = ultimo_tempo + timedelta(minutes=1)
    proximo_tempo_ts = int(proximo_tempo.timestamp() * 1000)

    resistencia = df["Resistance"].iloc[-1]
    suporte = df["Support"].iloc[-1]

    candle_falso = {
        "time": proximo_tempo.isoformat(),
        "open": resistencia - 200,
        "high": resistencia + 150,
        "low": suporte - 100,
        "close": resistencia + 100,
        "volume": 800,
        "turnover": (resistencia + 100) * 800,
    }

    df_mockado = pd.concat([df.copy(), pd.DataFrame([candle_falso])], ignore_index=True)
    return df_mockado




import os  # no topo do seu arquivo, se ainda não estiver

def detect_breakout(
    df,
    breakout_margin=0.0005,
    rigor=None
):
    """
    Detecta breakout com lógica de net score (score_buy - score_sell).
    Parametrizável via .env (min_score).
    Confirmações obrigatórias: Volume, RSI, EMAcurta/media (local) e EMAmedia/EMA_TREND (global, flexível!).
    """
    import os

    # === Parâmetros das EMAs (agora totalmente flexíveis) ===
    EMA_CURTA = int(os.getenv("EMA_CURTA", 20))
    EMA_MEDIA = int(os.getenv("EMA_MEDIA", 50))
    EMA_LONGA = int(os.getenv("EMA_LONGA", 200))
    EMA_TREND = int(os.getenv("EMA_TREND", EMA_LONGA))  # padrão = EMA_LONGA

    col_ema_curta = f"EMA{EMA_CURTA}"
    col_ema_media = f"EMA{EMA_MEDIA}"
    col_ema_longa = f"EMA{EMA_LONGA}"
    col_ema_trend = f"EMA{EMA_TREND}"

    # 🔁 Se rigor não for passado, usa o do .env
    if rigor is None:
        rigor = os.getenv("MODO_VALIDACAO", "normal").strip().lower()

    # 🔁 Parametrização do min_score via .env
    mapa_min_score = {
        "agressivo": int(os.getenv("MIN_SCORE_BREAKOUT_AGRESSIVO", 1)),
        "moderado": int(os.getenv("MIN_SCORE_BREAKOUT_MODERADO", 2)),
        "conservador": int(os.getenv("MIN_SCORE_BREAKOUT_CONSERVADOR", 3)),
    }
    min_score = mapa_min_score.get(rigor, 2)

    # 🔒 Sanidade dos dados
    if "Resistance" not in df.columns or "Support" not in df.columns:
        logging.error("❌ Colunas 'Resistance' ou 'Support' não encontradas.")
        return None
    if len(df) < 2:
        logging.warning("⚠️ Dados insuficientes para detectar breakout.")
        return None

    # 🔢 Dados atuais
    close = df["close"].iloc[-1]
    high = df["high"].iloc[-1]
    low = df["low"].iloc[-1]
    rsi = df.get("RSI", pd.Series([50])).iloc[-1]
    volume = df["volume"].iloc[-1]
    volume_medio = df["volume"].rolling(window=20, min_periods=1).mean().iloc[-1]

    resistencia = df["Resistance"].iloc[-2]
    suporte = df["Support"].iloc[-2]
    upper_limit = resistencia * (1 + breakout_margin)
    lower_limit = suporte * (1 - breakout_margin)

    logging.info(
        f"📐 Fechamento: {close:.2f} | Resistência={resistencia:.2f} (limite={upper_limit:.2f}) | "
        f"Suporte={suporte:.2f} (limite={lower_limit:.2f})"
    )

# === Scoring BUY / SELL ===
    score_buy = 0
    score_sell = 0

    # → Breakout de alta
    if high > upper_limit:
        score_buy += 1; logging.info("✅ Wick rompeu resistência")
    if close > resistencia:
        score_buy += 1; logging.info("✅ Fechamento acima da resistência")
    if abs(close - resistencia) / resistencia < 0.001 and (rsi > 50 or volume > volume_medio):
        score_buy += 1; logging.info("✅ Proximidade da resistência com RSI>50 ou volume alto")

    # → Breakout de baixa
    if low < lower_limit:
        score_sell += 1; logging.info("✅ Wick rompeu suporte")
    if close < suporte:
        score_sell += 1; logging.info("✅ Fechamento abaixo do suporte")
    if abs(close - suporte) / suporte < 0.001 and (rsi < 50 or volume > volume_medio):
        score_sell += 1; logging.info("✅ Proximidade do suporte com RSI<50 ou volume alto")

    # --- Regra de ambiguidade: ambos lados aprovados ---
    if score_buy >= min_score and score_sell >= min_score:
        delta_scores = abs(score_buy - score_sell)
        if delta_scores < DELTA_MINIMO_BREAKOUT:
            logging.warning(f"⚖️ MERCADO INDEFINIDO | BUY={score_buy}, SELL={score_sell}, Δ={delta_scores:.2f} < {DELTA_MINIMO_BREAKOUT}")
            return None
        lado_vencedor = "buy" if score_buy > score_sell else "sell"
        logging.info(f"🏆 LADO VENCEDOR: {lado_vencedor.upper()} | BUY={score_buy}, SELL={score_sell}, Δ={delta_scores:.2f}")

    # === Net Score ===
    net_score = score_buy - score_sell
    logging.info(
        f"Scores → BUY={score_buy}, SELL={score_sell}, NET={net_score} | "
        f"Rigor={rigor} | Min score={min_score}"
    )

    resultado_final = None
    if net_score >= min_score:
        resultado_final = "bullish"; logging.info("🔥 Breakout de COMPRA confirmado!")
    elif net_score <= -min_score:
        resultado_final = "bearish"; logging.info("🔥 Breakout de VENDA confirmado!")
    else:
        logging.info(f"⚠️ Nenhum breakout confirmado (net score={net_score}, min={min_score})")
        return None

    # === Confirmações adicionais obrigatórias ===
    ema_curta = df[col_ema_curta].iloc[-1] if col_ema_curta in df.columns else close
    ema_media = df[col_ema_media].iloc[-1] if col_ema_media in df.columns else close
    ema_longa = df[col_ema_longa].iloc[-1] if col_ema_longa in df.columns else close
    ema_trend = df[col_ema_trend].iloc[-1] if col_ema_trend in df.columns else ema_longa

    cond_volume = volume > volume_medio
    cond_rsi = (rsi > 50 if resultado_final == "bullish" else rsi < 50)

    # Tendência dupla: local + global (agora parametrizadas!)
    if resultado_final == "bullish":
        cond_tendencia_local = ema_curta > ema_media
        cond_tendencia_global = ema_media > ema_trend
    else:
        cond_tendencia_local = ema_curta < ema_media
        cond_tendencia_global = ema_media < ema_trend

    cond_tendencia = cond_tendencia_local and cond_tendencia_global

    logging.info(
        f"📊 Tendência → EMA{EMA_CURTA}={ema_curta:.2f}, EMA{EMA_MEDIA}={ema_media:.2f}, EMA{EMA_TREND}={ema_trend:.2f} | "
        f"Local={cond_tendencia_local}, Global={cond_tendencia_global}"
    )

    # Lê do .env (ou padrão 2 se não setado)
    FILTRO_BREAKOUT_MIN_CONFIRMACOES = int(os.getenv("FILTRO_BREAKOUT_MIN_CONFIRMACOES", 2))

    # Soma quantos deram True
    num_confirmacoes = sum([cond_volume, cond_rsi, cond_tendencia])

    if num_confirmacoes < FILTRO_BREAKOUT_MIN_CONFIRMACOES:
        logging.warning(
            f"🚫 Breakout rejeitado: confirmações insuficientes "
            f"(Volume={cond_volume}, RSI={cond_rsi}, Tendência={cond_tendencia}) | "
            f"Requerido: {FILTRO_BREAKOUT_MIN_CONFIRMACOES}, Obtido: {num_confirmacoes}"
        )
        return None

    # 🔥 Se chegou aqui, todas as confirmações necessárias foram atendidas
    logging.info(f"✅ Breakout validado! Direção = {resultado_final.upper()}")
    return resultado_final



def confirm_breakout(df):
    logging.info("✅ Confirmando breakout apenas pelo preço. Volume não será um fator de bloqueio.")
    return True  # 🔥 Agora, sempre confirma o breakout baseado no preço




def check_volatility(df):
    """
    Avalia a volatilidade de forma adaptativa e inteligente:
    - Considera ATR relativo ao preço (em fração)
    - Ajusta limiares conforme corpo do candle e volume
    - Usa THRESHOLD_ATR_MIN, THRESHOLD_ATR_MAX e MODO_RIGOR do .env
    - Permite exceções com candles fortes mesmo fora da faixa
    """

    import os

    if df is None or df.empty or "ATR" not in df.columns:
        logging.warning("⚠️ DataFrame inválido ou sem ATR.")
        return False

    candle = df.iloc[-1]
    close = candle["close"]
    atr = candle["ATR"]

    # === Carrega thresholds e modo do .env ===
    atr_min_pct = float(os.getenv("THRESHOLD_ATR_MIN", 0.001))  # ex: 0.0001 → 0.01%
    atr_max_pct = float(os.getenv("THRESHOLD_ATR_MAX", 0.012))  # ex: 0.60 → 60%
    modo_rigor = os.getenv("MODO_RIGOR", "moderado").lower()

    # === Cálculos base ===
    atr_frac = atr / close  # fração real (ex: 0.0023 = 0.23%)
    atr_pct = atr_frac * 100  # só pra exibir
    corpo_frac = abs(candle["close"] - candle["open"]) / (candle["high"] - candle["low"] + 1e-9)
    corpo_pct = corpo_frac * 100
    volume_ratio = candle["volume"] / (df["volume"].rolling(20).mean().iloc[-1] + 1e-9)

    # === Threshold dinâmico ===
    atr_min_dinamico = atr_min_pct * (1 - min(volume_ratio, 2.0) * 0.3) * (1 - min(corpo_frac, 0.4))

    logging.info(
        f"🎚️ Thresholds de volatilidade → Min: {atr_min_dinamico:.4f} | Max: {atr_max_pct:.4f} "
        f"(Modo: {modo_rigor})"
    )
    logging.debug({
        "ATR_frac": round(atr_frac, 6),
        "ATR_%": round(atr_pct, 3),
        "ATR_Min_frac": round(atr_min_dinamico, 6),
        "ATR_Max_frac": round(atr_max_pct, 6),
        "Corpo_%": round(corpo_pct, 2),
        "Volume_Ratio": round(volume_ratio, 2),
        "Modo_Rigor": modo_rigor
    })

    # === Caso 1: Volatilidade muito baixa ===
    if atr_frac < atr_min_dinamico:
        if corpo_frac > 0.6 or volume_ratio >= 1.1:
            logging.info(
                f"✅ ATR baixo ({atr_pct:.2f}%), mas corpo/volume fortes — mercado com impulso. Permitindo entrada."
            )
            return True
        else:
            logging.warning(
                f"⚠️ Baixa volatilidade (ATR={atr_pct:.2f}% < {atr_min_dinamico*100:.2f}%) "
                f"| Corpo={corpo_pct:.2f}% | Volume={volume_ratio:.2f}x média — mercado lateral."
            )
            return False

    # === Caso 2: Volatilidade muito alta ===
    if atr_frac > atr_max_pct:
        if modo_rigor == "agressivo" and (corpo_frac > 0.6 or volume_ratio >= 1.1):
            logging.info(
                f"🚀 ATR alto ({atr_pct:.2f}%), mas impulso confirmado e modo agressivo ativo — entrada permitida."
            )
            return True
        else:
            logging.info(
                f"🚫 ATR {atr_pct:.2f}% acima do máximo ({atr_max_pct*100:.2f}%) — mercado caótico, bloqueando entrada."
            )
            return False

    # === Caso 3: Volatilidade dentro da faixa ===
    logging.info(
        f"✅ Volatilidade OK (ATR={atr_pct:.2f}% dentro da faixa {atr_min_dinamico*100:.2f}%–{atr_max_pct*100:.2f}%) "
        f"| Corpo={corpo_pct:.2f}% | Volume={volume_ratio:.2f}x média"
    )
    return True





def calculate_stop_take_trailing(price, atr_value, side="buy", api=None, symbol=None):
    """
    Calcula Stop-Loss, Take-Profit e Trailing Stop com base nos parâmetros do .env.
    Corrige inversões de TP/SL, aplica arredondamento conforme tickSize e mantém
    compatibilidade com a Bybit (categoria linear).
    """
    import os
    import math

    # === Leitura de parâmetros do .env ===
    RRR = float(os.getenv("RRR", 1.9))                  # Relação risco/recompensa
    ATR_MULT = float(os.getenv("ATR_MULT", 1.0))        # Multiplicador do ATR
    SL_PCT_MIN = float(os.getenv("SL_PCT_MIN", 0.004))  # Stop mínimo
    SL_PCT_MAX = float(os.getenv("SL_PCT_MAX", 0.010))  # Stop máximo
    TRAILING_ATR_MULT = float(os.getenv("TRAILING_ATR_MULT", 0.8))

    # Sanitização
    price = float(price)
    atr_value = abs(float(atr_value))

    # === Cálculo da distância do stop ===
    # Se o ATR for muito grande comparado ao preço, normaliza automaticamente
    if atr_value > price * 0.2:
        atr_value = atr_value / 100  # reduz escala
    dist_atr = ATR_MULT * atr_value

    stop_dist = max(SL_PCT_MIN * price, min(dist_atr, SL_PCT_MAX * price))
    take_dist = stop_dist * RRR
    trailing_dist = stop_dist * TRAILING_ATR_MULT

    # === Cálculo coerente conforme o lado ===
    if side.lower() == "buy":
        stop_loss = price - stop_dist
        take_profit = price + take_dist
        trailing_stop = price - trailing_dist
    else:
        stop_loss = price + stop_dist
        take_profit = price - take_dist
        trailing_stop = price + trailing_dist

    # === Correções defensivas ===
    if side.lower() == "buy" and stop_loss >= price:
        stop_loss = price * (1 - SL_PCT_MIN)
    elif side.lower() == "sell" and stop_loss <= price:
        stop_loss = price * (1 + SL_PCT_MIN)

    # === Arredondamento conforme tickSize da Bybit ===
    tick_size = 0.01
    if api and symbol:
        try:
            info = api.get_instruments_info(category="linear", symbol=symbol)
            tick_size = float(info["result"]["list"][0]["priceFilter"]["tickSize"])
        except Exception:
            pass

    def ajustar_tick(valor):
        if tick_size <= 0:
            return round(valor, 2)
        casas = str(tick_size)[::-1].find('.')
        return round(round(valor / tick_size) * tick_size, casas)

    stop_loss = ajustar_tick(stop_loss)
    take_profit = ajustar_tick(take_profit)
    trailing_stop = ajustar_tick(trailing_stop)

    print(f"DEBUG: ATR={atr_value:.2f}, SL_DIST={stop_dist:.2f}, TP_DIST={take_dist:.2f}, RRR={RRR}, SIDE={side}")

    return stop_loss, take_profit, trailing_stop







def avaliar_respiro(df, side: str, lookback=30):
    """
    Detecta 'respiro' (pullback) após um impulso e diz se:
      - respiro_em_andamento: ainda voltando para média/zona
      - respiro_concluido: pullback rejeitou zona e tendência retomou
    Regras (versão objetiva):
      • Impulso recente > 1.5x ATR
      • Pullback retrai 38.2–61.8% do impulso
      • Volume do pullback < volume do impulso (média)
      • RSI volta à região 45–55 e vira a favor da tendência
      • Rejeição em zona (EMA20, VWAP, S→R / R→S, Fibo) com candle de rejeição
    Retorna dict com flags e score (0–10).
    """
    out = {"respiro_em_andamento": False, "respiro_concluido": False, "score": 0, "motivos": []}
    if len(df) < max(lookback, 25):
        return out

    # Base
    close = df["close"].iloc[-1]
    high  = df["high"].iloc[-lookback:].max()
    low   = df["low"].iloc[-lookback:].min()
    atr   = df.get("ATR", pd.Series([0])).iloc[-1]
    rsi_a = df.get("RSI", pd.Series([50,50])).iloc[-1]
    rsi_p = df.get("RSI", pd.Series([50,50])).iloc[-2]

    # Médias/Zonas
    ema20  = df["close"].ewm(span=20, adjust=False).mean().iloc[-1]
    ema50  = df["close"].ewm(span=50, adjust=False).mean().iloc[-1]
    v_med  = df["volume"].rolling(20, min_periods=1).mean().iloc[-1]
    v_atual= df["volume"].iloc[-1]

    # Suporte/Resistência recentes
    if "Resistance" not in df.columns or "Support" not in df.columns:
        tmp = calculate_breakout_levels(df.copy(), period=5)
    else:
        tmp = df
    res  = tmp["Resistance"].iloc[-2]
    sup  = tmp["Support"].iloc[-2]

    # 1) Identificar IMPULSO (lado)
    impulso_ok = False
    if side == "sell":
        # impulso de baixa = queda recente forte
        amplitude = (tmp["high"].iloc[-6:-1].max() - tmp["low"].iloc[-6:-1].min())
        impulso_ok = amplitude >= (1.5 * max(atr, 1))
    else:
        amplitude = (tmp["high"].iloc[-6:-1].max() - tmp["low"].iloc[-6:-1].min())
        impulso_ok = amplitude >= (1.5 * max(atr, 1))

    # 2) Fibo do impulso (aproximação robusta sem pivôs complexos)
    swing_high = tmp["high"].iloc[-6:-1].max()
    swing_low  = tmp["low"].iloc[-6:-1].min()
    if side == "sell":
        fib382 = swing_low + 0.382 * (swing_high - swing_low)
        fib618 = swing_low + 0.618 * (swing_high - swing_low)
        retrai_faixa = fib382 <= close <= fib618
    else:
        fib382 = swing_high - 0.382 * (swing_high - swing_low)
        fib618 = swing_high - 0.618 * (swing_high - swing_low)
        retrai_faixa = fib618 <= close <= fib382  # compra

    # 3) Volume do pullback vs impulso
    vol_pullback_ok = v_atual <= (v_med * 1.05)

    # 4) RSI volta p/ 45–55 e vira a favor do lado da operação
    rsi_na_faixa = 45 <= rsi_a <= 55
    if side == "sell":
        rsi_virou = rsi_p > rsi_a  # inclinando p/ baixo
    else:
        rsi_virou = rsi_a > rsi_p  # inclinando p/ cima

    # 5) Rejeição em zona (EMA20/50 ou S↔R) – candle com pavio contra
    open_ = df["open"].iloc[-1]
    high_ = df["high"].iloc[-1]
    low_  = df["low"].iloc[-1]
    corpo = abs(close - open_)
    range_ = max(high_ - low_, 1e-9)
    pavio_superior = high_ - max(close, open_)
    pavio_inferior = min(close, open_) - low_

    if side == "sell":
        rejeicao = (close < ema20 or close < res) and (pavio_superior / range_ > 0.4) and (corpo / range_ < 0.6)
    else:
        rejeicao = (close > ema20 or close > sup) and (pavio_inferior / range_ > 0.4) and (corpo / range_ < 0.6)

    # Montar estado
    if impulso_ok:
        # Está respirando se ainda não confirmou retomada
        out["respiro_em_andamento"] = True

    # Score: cada critério vale 2 pontos
    score = 0
    for ok, nome in [
        (impulso_ok, "impulso"),
        (retrai_faixa, "retracao_382_618"),
        (vol_pullback_ok, "volume_pullback_baixo"),
        (rsi_na_faixa and rsi_virou, "rsi_volta_e_vira"),
        (rejeicao, "rejeicao_em_zona")
    ]:
        if ok:
            score += 2
        else:
            out.setdefault("falhas", []).append(nome)

    out["score"] = score
    # Considera respiro “concluído” quando há rejeição + (retrai_faixa ou RSI vira)
    out["respiro_concluido"] = rejeicao and (retrai_faixa or (rsi_na_faixa and rsi_virou))
    return out



def consultar_IA_para_saida(dados_mercado: dict) -> str:
    """
    Envia dados de mercado para a IA e recebe a decisão de saída.
    Retorna 'fechar' ou 'manter'.
    """
    API_IA_URL = os.getenv("API_IA_URL", "http://localhost:8000")
    url = f"{API_IA_URL}/decidir_saida"

    try:
        logging.info("🤖 Consultando IA para decisão de saída...")
        inicio = time.time()

        response = requests.post(url, json=dados_mercado, timeout=5)
        duracao = round(time.time() - inicio, 2)

        if response.status_code != 200:
            logging.warning(f"⚠️ Resposta inesperada da IA: {response.status_code}")
            return "manter"

        resposta_ia = response.json()
        decisao = resposta_ia.get("decision", "manter")

        logging.info(f"✅ IA respondeu em {duracao}s: {decisao.upper()}")
        return decisao

    except Exception as e:
        logging.error(f"❌ Erro ao consultar IA: {e}")
        return "manter"



@app.post("/decidir_saida")
def decidir_saida(dados: dict):
    """
    Exemplo simples: se preço atual >= take_profit, IA diz "fechar", senão "manter".
    """
    current_price = dados.get("current_price", 0)
    take_profit = dados.get("take_profit", 999999)

    # Lógica de IA: 
    if current_price >= take_profit:
        return {"decision": "fechar"}
    else:
        return {"decision": "manter"}





def monitorar_ordem(api, symbol, order_id, entry_price, stop_loss, take_profit, side, trailing_offset=None, **kwargs):
    print("✅ Ordem enviada com sucesso para a Bybit com Stop-Loss e Take-Profit já definidos.")
    print("ℹ️ Nenhum monitoramento contínuo será feito pelo sistema local.\n")
    
    print(f"📄 Detalhes da Ordem:")
    print(f"🔍 Ordem ID:         {order_id}")
    print(f"🪙 Par de Ativos:    {symbol}")
    print(f"🎯 Preço de Entrada: {entry_price:.2f}")
    print(f"🛑 Stop-Loss:        {stop_loss:.2f}")
    print(f"🎯 Take-Profit:      {take_profit:.2f}")
    print(f"🔄 Trailing Stop:    {trailing_offset:.2f}" if trailing_offset else "🔄 Trailing Stop:  -")
    print(f"🧭 Direção:          {side.upper()}")



def get_entry_price(trade_info):
    # Retorna o melhor preço possível para a ordem, ou 0.0 se não houver nenhum
    for key in ["entryPrice", "avgPrice", "price", "triggerPrice"]:
        valor = trade_info.get(key)
        if valor not in (None, '', 0, '0'):
            try:
                return float(valor)
            except Exception:
                continue
    return 0.0  # Valor padrão seguro




def converter_colunas_para_float(df, colunas):
    for col in colunas:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df



def executar_backtest_breakout(df_original, lado_permitido=None):
    trades = []
    import logging
    from copy import deepcopy
    import numpy as np

    logging.info("🚀 Iniciando backtest otimizado da estratégia Breakout...")

    df = deepcopy(df_original).reset_index(drop=True)
    df = calculate_breakout_levels(df)
    df = calculate_indicators(df)

    # === BLINDAGEM DE TIPOS ===
    colunas_numericas = [
        "open", "high", "low", "close", "volume", "turnover",
        "RSI", "ATR", "EMA50", "EMA200", "SMA_10", "SMA_50",
        "Bollinger_Upper", "Bollinger_Lower", "Resistance", "Support"
    ]
    df = converter_colunas_para_float(df, colunas_numericas)

    saldo = 10000
    saldo_inicial = saldo
    resultados = []
    lucros = []
    perdas = []

    num_trades = 0
    num_wins = 0
    num_losses = 0
    maior_pico = saldo
    max_drawdown = 0


    for i in range(20, len(df)):
        df_slice = df.iloc[:i+1].copy()
        #df_slice = aplicar_regras_validacao_breakout(df_slice)

        #if not df_slice['validacao_final'].iloc[-1]:
            #logging.info("⚠️ Breakout não validado. Ignorando candle no backtest.")
            #continue

        preco_atual = df_slice["close"].iloc[-1]
        breakout = detect_breakout(df_slice)
        if not breakout:
            continue

        confirmacao = confirm_breakout(df_slice)
        if not confirmacao:
            continue

        # ✅ Define o lado do trade com base no breakout
        side = "buy" if breakout == "bullish" else "sell"

        # 🛡️ FILTRO CRÍTICO: Obedecer apenas o lado aprovado
        if lado_permitido is not None:
            if side != lado_permitido:
                logging.info(
                    f"🚫 Breakout gerou {side.upper()}, mas lado permitido é {lado_permitido.upper()}. "
                    f"Ignorando sinal."
                )
                continue


        # 🔒 Confirmação adicional: candle anterior não rompeu e o atual rompeu
        resistencia = df_slice["Resistance"].iloc[-2]
        suporte = df_slice["Support"].iloc[-2]
        close_atual = df_slice["close"].iloc[-1]
        close_anterior = df_slice["close"].iloc[-2]

        # 🔥 Relaxar validação de pullback conforme o MODO_VALIDACAO (.env)
        modo_validacao = os.getenv("MODO_VALIDACAO", "moderado").strip().lower()

        # ⚠️ Apenas exige confirmação de pullback se NÃO for agressivo
        if modo_validacao != "agressivo":
            if side == "buy":
                if not (close_anterior < resistencia and close_atual > resistencia):
                    logging.warning("⚠️ Pullback inválido para BUY — Fechamento anterior não rompeu resistência.")
                    continue
            elif side == "sell":
                if not (close_anterior > suporte and close_atual < suporte):
                    logging.warning("⚠️ Pullback inválido para SELL — Fechamento anterior não rompeu suporte.")
                    continue
            else:
                logging.warning(f"⚠️ Direção '{side}' desconhecida ao validar pullback.")
        else:
            logging.info("🧪 Modo agressivo ativo: ignorando validação de pullback para testes ou IA.")


        #if not confirmar_entrada_com_criterios(df_slice, side, is_backtest=True):
        entrada_valida = confirmar_entrada_com_criterios(df_slice, side, is_backtest=True)

        logging.info(f"✅ Confirmação de entrada para {side.upper()} no candle {i}: {entrada_valida}")
        if not entrada_valida:
            logging.warning(f"🚫 Entrada rejeitada no candle {i}. Score insuficiente ou falha em critério.")

            criterios = calcular_score_completo(df_slice, side=side, sentimento_geral="positivo")
            score_final = criterios.pop("score", 0)
            
            # ✅ Substitui o modo_relaxado por configuração do .env
            score_minimo, _, modo_validacao = obter_config_validacao()

            logging.warning(f"🔎 Score obtido: {score_final}/{score_minimo} (Modo de Validação: {modo_validacao})")

            for crit, status in criterios.items():
                logging.info(f"{'✅' if status else '❌'} Critério: {crit} -> {'Passou' if status else 'Falhou'}")

            continue




        atr_value = df_slice["ATR"].iloc[-1]
        # ✅ Corrige direção e arredondamento antes de enviar
        stop_loss, take_profit, trailing_stop = calculate_stop_take_trailing(
            preco_atual, atr_value, side
        )


        # 📊 Calcular e validar RRR
        rrr = calcular_rrr(preco_atual, stop_loss, take_profit, min_rrr=1.5)
        modo_operacao = os.getenv("MODO_VALIDACAO", "agressivo").lower()

        # --- Verificação robusta de RRR ---
        if rrr is None:
            logging.warning({
                "timestamp": str(datetime.now()),
                "motivo": "RRR não pôde ser calculado (None)",
                "rrr": rrr,
                "preco_atual": preco_atual,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "side": side,
                "EMA50": ema50,
                "EMA200": ema200,
            })
            return None, None, None

        # --- Aceita RRR ≥1 no modo agressivo, mantém 1.5 nos demais ---
        if (modo_operacao == "agressivo" and rrr < 1.0) or (modo_operacao != "agressivo" and rrr < 1.5):
            logging.warning({
                "timestamp": str(datetime.now()),
                "motivo": f"RRR ({rrr:.2f}) abaixo do mínimo permitido para modo '{modo_operacao}'",
                "preco_atual": preco_atual,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "side": side,
                "EMA50": ema50,
                "EMA200": ema200,
            })
            # bloqueia apenas se não for agressivo
            if modo_operacao != "agressivo":
                return None, None, None
            else:
                logging.info(f"⚠️ RRR ({rrr:.2f}) baixo, mas aceito em modo agressivo para teste/volatilidade.")
        else:
            logging.info(f"✅ RRR válido: {rrr:.2f} | Modo: {modo_operacao}")

        # prossegue normalmente
        logging.info(f"📈 Entrada {side.upper()} no candle {i} em {preco_atual:.2f}")
        num_trades += 1

        # --- Inicializações obrigatórias ---
        resultado = 0.0
        atingiu_tp = False
        atingiu_sl = False

        # === TIMEOUT DINÂMICO POR VOLATILIDADE (ATR) ===
        atr = df["ATR"].iloc[i]
        close_atual = df["close"].iloc[i]

        # Tempo de vida do trade (em candles) ajustado pela volatilidade
        # Nunca menos do que 15, nunca mais que 120 candles
        duracao_timeout = int(
            np.clip(
                (atr / close_atual) * 1_500_000,   # multiplicador profissional
                40,                                # mínimo realista
                400                                # máximo realista para M1
            )
        )

        limite_j = min(i + duracao_timeout, len(df))

        # === LOOP PRINCIPAL DO TRADE ===
        for j in range(i+1, limite_j):
            preco_futuro = df["close"].iloc[j]

            # BUY
            if side == "buy":
                if preco_futuro >= take_profit:
                    resultado = preco_futuro - preco_atual
                    saldo += resultado
                    lucros.append(resultado)
                    num_wins += 1
                    atingiu_tp = True
                    logging.info(f"✅ LUCRO SIMULADO (candle {j}): +{resultado:.2f} | Saldo: {saldo:.2f}")
                    trades.append({
                        "data": str(df["time"].iloc[j]),
                        "side": side,
                        "preco_entrada": preco_atual,
                        "preco_saida": preco_futuro,
                        "lucro": resultado,
                        "motivo_saida": "take"
                    })
                    break

                elif preco_futuro <= stop_loss:
                    resultado = preco_atual - preco_futuro
                    saldo -= resultado
                    perdas.append(resultado)
                    num_losses += 1
                    atingiu_sl = True
                    logging.info(f"❌ PREJUÍZO SIMULADO (candle {j}): -{resultado:.2f} | Saldo: {saldo:.2f}")
                    trades.append({
                        "data": str(df["time"].iloc[j]),
                        "side": side,
                        "preco_entrada": preco_atual,
                        "preco_saida": preco_futuro,
                        "lucro": -resultado,
                        "motivo_saida": "stop"
                    })
                    break

            # SELL
            else:
                if preco_futuro <= take_profit:
                    resultado = preco_atual - preco_futuro
                    saldo += resultado
                    lucros.append(resultado)
                    num_wins += 1
                    atingiu_tp = True
                    logging.info(f"✅ LUCRO SIMULADO (candle {j}): +{resultado:.2f} | Saldo: {saldo:.2f}")
                    trades.append({
                        "data": str(df["time"].iloc[j]),
                        "side": side,
                        "preco_entrada": preco_atual,
                        "preco_saida": preco_futuro,
                        "lucro": resultado,
                        "motivo_saida": "take"
                    })
                    break

                elif preco_futuro >= stop_loss:
                    resultado = preco_futuro - preco_atual
                    saldo -= resultado
                    perdas.append(resultado)
                    num_losses += 1
                    atingiu_sl = True
                    logging.info(f"❌ PREJUÍZO SIMULADO (candle {j}): -{resultado:.2f} | Saldo: {saldo:.2f}")
                    trades.append({
                        "data": str(df["time"].iloc[j]),
                        "side": side,
                        "preco_entrada": preco_atual,
                        "preco_saida": preco_futuro,
                        "lucro": -resultado,
                        "motivo_saida": "stop"
                    })
                    break



        # ⚠️ Esse bloco vem DEPOIS do loop!
        if not atingiu_tp and not atingiu_sl:
            preco_final = df["close"].iloc[min(i+10, len(df)-1)]  # preço após 10 candles
            resultado = preco_final - preco_atual if side == "buy" else preco_atual - preco_final

            saldo += resultado
            if resultado > 0:
                lucros.append(resultado)
                num_wins += 1
                logging.info(f"✅ LUCRO FORÇADO após timeout: +{resultado:.2f} | Saldo: {saldo:.2f}")
            elif resultado < 0:
                perdas.append(abs(resultado))
                num_losses += 1
                logging.info(f"❌ PREJUÍZO FORÇADO após timeout: -{abs(resultado):.2f} | Saldo: {saldo:.2f}")
            else:
                logging.info(f"🔸 Encerramento neutro após timeout. Saldo: {saldo:.2f}")            
            trades.append({
                "data": str(df["time"].iloc[min(i+10, len(df)-1)]),
                "side": side,
                "preco_entrada": preco_atual,
                "preco_saida": preco_final,
                "lucro": resultado,
                "motivo_saida": "timeout"
            })

        # Max Drawdown cálculo
        maior_pico = max(maior_pico, saldo)
        drawdown_atual = (maior_pico - saldo) / maior_pico
        max_drawdown = max(max_drawdown, drawdown_atual)

        resultados.append(saldo)

    win_rate = (num_wins / num_trades) * 100 if num_trades else 0
    lucro_medio = np.mean(lucros) if lucros else 0
    perda_media = np.mean(perdas) if perdas else 0

    logging.info(f"🏁 Backtest finalizado. Saldo final: {saldo:.2f}")
    operacao_em_andamento = False
    ordem_ativa = False
    logging.info("🔄 Reset de estado após backtest. Sistema liberado para entrada real.")
    liberar_lock_fisico(symbol)


    # ============================================================
    # 📄 RELATÓRIO COMPLETO PARA O RANKING E PARA LOG DETALHADO
    # ============================================================
    relatorio_completo = {
        "saldo_inicial": saldo_inicial,
        "saldo_final": saldo,
        "lucro_total": saldo - saldo_inicial,
        "num_trades": num_trades,
        "num_wins": num_wins,
        "num_losses": num_losses,
        "winrate": round(win_rate, 2),
        "lucro_medio": round(lucro_medio, 2),
        "perda_media": round(perda_media, 2),
        "max_drawdown_pct": round(max_drawdown * 100, 2),
        "trades": trades
    }


    return {
        "estrategia": "BREAKOUT",                   # ⭐ ADICIONADO
        "direcao": side if num_trades > 0 else None,   # ⭐ ADICIONADO

        "lucro": saldo - saldo_inicial,
        "saldo_final": saldo,
        "saldo_inicial": saldo_inicial,             # ⭐ IMPORTANTE (usado nos logs)
        "num_trades": num_trades,
        "num_wins": num_wins,
        "num_losses": num_losses,
        "win_rate": round(win_rate, 2),
        "lucro_medio": round(lucro_medio, 2),
        "perda_media": round(perda_media, 2),
        "max_drawdown": round(max_drawdown * 100, 2),
        "trades": trades,
        "relatorio_completo": {
            "estrategia": "BREAKOUT",
            "direcao": side if num_trades > 0 else None,
            "saldo_inicial": saldo_inicial,
            "saldo_final": saldo,
            "lucro_total": saldo - saldo_inicial,
            "num_trades": num_trades,
            "num_wins": num_wins,
            "num_losses": num_losses,
            "winrate": round(win_rate, 2),
            "lucro_medio": round(lucro_medio, 2),
            "perda_media": round(perda_media, 2),
            "max_drawdown_pct": round(max_drawdown * 100, 2),
            "trades": trades
        }
    }







def existe_ordem_aberta(api, symbol):
    """
    Verifica se existe alguma posição ativa para o par especificado.
    """
    try:
        response = api.get_positions(category="linear", symbol=symbol)
        if response.get("retCode") == 0:
            posicoes = response.get("result", {}).get("list", [])
            for pos in posicoes:
                if float(pos.get("size", 0)) > 0:
                    logging.info(f"⚠️ Posição ativa detectada para {symbol}.")
                    return True
        return False
    except Exception as e:
        logging.error(f"❌ Erro ao verificar posições abertas: {e}")
        return False




def safe_float(value, default=0.0):
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def obter_detalhes_ordem_ativa(api, symbol):
    try:
        response = api.get_positions(category="linear", symbol=symbol)
        posicoes = response.get("result", {}).get("list", [])
        for p in posicoes:
            size = safe_float(p.get("size", 0))
            if size > 0:
                # Busca o preço em todos os campos possíveis!
                entry_price = (
                    safe_float(p.get("entryPrice")) or
                    safe_float(p.get("avgPrice")) or
                    safe_float(p.get("price")) or
                    safe_float(p.get("triggerPrice")) or
                    0.0
                )
                trade_id = (
                    p.get("orderId")
                    or p.get("positionId")
                    or str(p.get("positionIdx"))
                    or "desconhecido"
                )
                return {
                    "orderId": trade_id,
                    "trade_id": trade_id,
                    "entry_price": entry_price,      # <-- ESSENCIAL
                    "triggerPrice": entry_price,     # compatibilidade
                    "qty": size,
                    "stopLoss": safe_float(p.get("stopLoss", 0)),
                    "takeProfit": safe_float(p.get("takeProfit", 0)),
                    "trailingStop": safe_float(p.get("trailingStop", 0)),
                    "side": p.get("side", "buy"),
                }
    except Exception as e:
        logging.error(f"❌ Erro ao obter detalhes da ordem ativa: {e}")
    return None




def aplicar_regras_validacao_breakout(df):
    """
    Aplica filtros técnicos para Breakout Trading com critérios flexíveis:
    - Volume acima da média (ou afrouxado no agressivo)
    - Tendência favorável (ou afrouxado no agressivo)
    - Rompimento confirmado (ou afrouxado no agressivo)
    - Força do candle (ou afrouxado no agressivo)
    Considera o modo de validação definido no .env.
    """
    try:
        # 🔹 Volume
        df['media_volume_20'] = df['volume'].rolling(window=20, min_periods=1).mean()
        df['volume_confirmado'] = df['volume'] > df['media_volume_20']

        # 🔹 Tendência (EMA50 vs EMA200)
        df['EMA50'] = df['close'].ewm(span=50, adjust=False).mean()
        df['EMA200'] = df['close'].ewm(span=200, adjust=False).mean()
        df['tendencia_favoravel'] = df['EMA50'] > df['EMA200']

        # 🔹 Rompimento (margem permissiva)
        if "Resistance" not in df.columns:
            df = calculate_breakout_levels(df)

        df['rompimento_confirmado'] = (
            (df['close'] > df['Resistance']) &
            (df['close'] > df['Resistance'] * 1.0001)
        )

        # 🔹 Força do candle
        df['forca_candle'] = (df['close'] - df['open']) / (df['high'] - df['low'])
        df['forca_candle_confirmada'] = df['forca_candle'] > 0.4

        # ===== PATCH MODO AGRESSIVO (libera filtros!) =====
        modo_validacao = os.getenv("MODO_VALIDACAO", "moderado").strip().lower()
        if modo_validacao == "agressivo":
            # Afrouxa TODOS os critérios principais (true para tudo)
            df['volume_confirmado'] = True
            df['tendencia_favoravel'] = True
            df['rompimento_confirmado'] = True
            df['forca_candle_confirmada'] = True
        # ===== FIM PATCH =====

        # 🔹 Validação adaptativa
        criterios = [
            'volume_confirmado',
            'tendencia_favoravel',
            'rompimento_confirmado',
            'forca_candle_confirmada'
        ]
        mapa_criterios = {
            "agressivo": 1,
            "moderado": 3,
            "conservador": 4
        }
        criterios_minimos = mapa_criterios.get(modo_validacao, 2)
        df['soma_criterios'] = df[criterios].sum(axis=1)
        df['validacao_final'] = df['soma_criterios'] >= criterios_minimos

        # 🔍 LOGS por candle
        for i in range(len(df)):
            soma = int(df['soma_criterios'].iloc[i])
            if not df['validacao_final'].iloc[i]:
                logging.info(f"❌ Candle {i}: Validação final falhou — critérios somados: {soma} (mínimo: {criterios_minimos})")
                for crit in criterios:
                    status = df[crit].iloc[i]
                    logging.info(f"🔸 {crit.replace('_', ' ').capitalize()}: {'✅' if status else '❌'}")
            else:
                logging.info(f"✅ Candle {i}: Validação final passou — critérios somados: {soma} (mínimo: {criterios_minimos})")

        return df

    except Exception as e:
        logging.error(f"❌ Erro ao aplicar regras de validação de breakout: {e}")
        return df










def calcular_score_completo(df, side, sentimento_geral):
    """
    Calcula o score total para validação de entrada.
    Cada critério possui seu próprio bloco, protegido contra falta de coluna.
    Se faltar algum indicador, usa valor default ou ignora o critério.
    """

    score = 0
    score_maximo = 0
    criterios = {}
    motivos_rejeicao = []
    score_pct = 0  # Valor default

    import os

    # --- Definição dos PESOS dos critérios (carrega do .env ou usa default) ---
    pesos = {
        "volume": float(os.getenv("PESO_VOLUME", 1)),
        "rsi": float(os.getenv("PESO_RSI", 1.5)),
        "sma": float(os.getenv("PESO_SMA", 1)),
        "rompimento": float(os.getenv("PESO_ROMPIMENTO", 2)),
        "ema": float(os.getenv("PESO_EMA", 1)),
        "momentum": float(os.getenv("PESO_MOMENTUM", 1)),
        "volume_medio": float(os.getenv("PESO_VOLUME_MEDIO", 0.5)),
        "bollinger": float(os.getenv("PESO_BOLLINGER", 0.5)),
        "atr": float(os.getenv("PESO_ATR", 1)),
        "sentimento": float(os.getenv("PESO_SENTIMENTO", 2)),
        "respiro": float(os.getenv("PESO_RESPIRO", 2))
    }

    # --- Definição dos THRESHOLDS/Configurações (idem acima) ---
    THRESHOLD_CORPO_PCT_ROMPIMENTO = float(os.getenv("THRESHOLD_CORPO_PCT_ROMPIMENTO", 0.33))
    THRESHOLD_VOLUME_FORTE        = float(os.getenv("THRESHOLD_VOLUME_FORTE", 0.95))
    THRESHOLD_BOLLINGER_BUY       = float(os.getenv("THRESHOLD_BOLLINGER_BUY", 0.995))
    THRESHOLD_BOLLINGER_SELL      = float(os.getenv("THRESHOLD_BOLLINGER_SELL", 1.005))
    THRESHOLD_ATR_MIN             = float(os.getenv("THRESHOLD_ATR_MIN", 0.003))

 

    try:
        # --- 1. Extração segura dos indicadores e preços básicos ---
        close = df["close"].iloc[-1] if "close" in df.columns else 0

        # RSI
        rsi_atual = df["RSI"].iloc[-1] if "RSI" in df.columns else 50
        rsi_anterior = df["RSI"].iloc[-2] if "RSI" in df.columns else 50

        # SMA
        sma_atual = df["SMA_10"].iloc[-1] if "SMA_10" in df.columns else close
        sma_anterior = df["SMA_10"].iloc[-2] if "SMA_10" in df.columns else close

        # Resistência/Suporte
        resistencia = df["Resistance"].iloc[-2] if "Resistance" in df.columns else close
        suporte = df["Support"].iloc[-2] if "Support" in df.columns else close

        # EMA
        ema50 = df["EMA50"].iloc[-1] if "EMA50" in df.columns else close
        ema200 = df["EMA200"].iloc[-1] if "EMA200" in df.columns else close

        # Momentum
        momentum = df["momentum"].iloc[-1] if "momentum" in df.columns else 0

        # Volume
        volume = df["volume"].iloc[-1] if "volume" in df.columns else 1

        # Volume médio (preferência para coluna já calculada, fallback para rolling mean)
        if "Volume_Media" in df.columns:
            volume_medio = df["Volume_Media"].iloc[-1]
        elif "volume" in df.columns:
            volume_medio = df["volume"].rolling(20, min_periods=1).mean().iloc[-1]
        else:
            volume_medio = 1

        # Bollinger
        boll_upper = df["Bollinger_Upper"].iloc[-2] if "Bollinger_Upper" in df.columns else close * 1.01
        boll_lower = df["Bollinger_Lower"].iloc[-2] if "Bollinger_Lower" in df.columns else close * 0.99

        # ATR
        atr = df["ATR"].iloc[-1] if "ATR" in df.columns else 1

        # --- 2. Critérios de validação individualmente protegidos ---

        # Volume
        criterios["volume"] = (
            volume > df["volume"].iloc[-2]
            if "volume" in df.columns and len(df) > 1
            else True  # fallback: sempre passa se não tem info
        )

        # RSI cruzando neutro
        if side == "buy":
            criterios["rsi"] = rsi_anterior < 50 and rsi_atual > 50
        else:
            criterios["rsi"] = rsi_anterior > 50 and rsi_atual < 50

        # SMA direção
        criterios["sma"] = (
            sma_atual > sma_anterior if side == "buy" else sma_atual < sma_anterior
        )

        # Rompimento (corpo do candle + rompimento + volume forte)
        try:
            open_ = df["open"].iloc[-1] if "open" in df.columns else close
            high = df["high"].iloc[-1] if "high" in df.columns else close
            low = df["low"].iloc[-1] if "low" in df.columns else close
            corpo = abs(close - open_)
            corpo_pct = corpo / (high - low) if (high - low) > 0 else 0
            volume_forte = volume > (volume_medio * THRESHOLD_VOLUME_FORTE)

            rompimento_valido = (
                (side == "buy" and close > resistencia and open_ < resistencia)
                or (side == "sell" and close < suporte and open_ > suporte)
            )

            criterios["rompimento"] = (
                rompimento_valido and corpo_pct > THRESHOLD_CORPO_PCT_ROMPIMENTO and volume_forte
            )
        except Exception as e:
            criterios["rompimento"] = False
            logging.error(f"Erro ao avaliar critério 'rompimento': {e}")

        # EMA tendência
        criterios["ema"] = ema50 > ema200 if side == "buy" else ema50 < ema200

        # Momentum
        criterios["momentum"] = momentum > 0 if side == "buy" else momentum < 0

        # Volume médio
        criterios["volume_medio"] = volume > volume_medio

        # Bollinger
        criterios["bollinger"] = (
            close > boll_upper * THRESHOLD_BOLLINGER_BUY if side == "buy" else close < boll_lower * THRESHOLD_BOLLINGER_SELL

        )

        # ATR
        # === VOLATILIDADE ADAPTATIVA INTELIGENTE ===
        ultimo = df.iloc[-1]
        atr_pct = (atr / ultimo["close"]) * 100
        corpo_pct = abs(ultimo["close"] - ultimo["open"]) / (ultimo["high"] - ultimo["low"] + 1e-9) * 100
        volume_ratio = ultimo["volume"] / (df["volume"].rolling(20).mean().iloc[-1] + 1e-9)

        # 🔄 Verifica direção dos últimos candles
        ultimos = df.tail(4)
        seq_alta = all(ultimos["close"].diff().dropna() > 0)
        seq_baixa = all(ultimos["close"].diff().dropna() < 0)

        # Threshold dinâmico
        atr_min_dinamico = (
            THRESHOLD_ATR_MIN
            * (1 - min(volume_ratio, 2.0) * 0.3)
            * (1 - min(corpo_pct / 100, 0.4))
        )

        logging.debug({
            "ATR_%": round(atr_pct, 4),
            "Corpo_%": round(corpo_pct, 2),
            "Volume_Ratio": round(volume_ratio, 2),
            "ATR_Min_Dinamico_%": round(atr_min_dinamico * 100, 4),
            "Seq_Alta": seq_alta,
            "Seq_Baixa": seq_baixa
        })

        # 🧠 Avaliação adaptativa inteligente
        if atr_pct < atr_min_dinamico * 100:
            if corpo_pct > 60 or seq_alta or seq_baixa or volume_ratio >= 1.1:
                logging.info(
                    f"✅ Volatilidade baixa ({atr_pct:.2f}%), mas direção e corpo confirmam impulso. Permitindo entrada."
                )
                criterios["atr"] = True
            else:
                logging.warning(
                    f"⚠️ Baixa volatilidade adaptativa (ATR={atr_pct:.2f}% < {atr_min_dinamico*100:.2f}%) "
                    f"| Corpo={corpo_pct:.2f}% | Volume={volume_ratio:.2f}x média | Mercado lateral detectado."
                )
                criterios["atr"] = False
        else:
            criterios["atr"] = True
            logging.info(
                f"✅ Volatilidade OK (ATR={atr_pct:.2f}% >= {atr_min_dinamico*100:.2f}%) "
                f"| Corpo={corpo_pct:.2f}% | Volume={volume_ratio:.2f}x média"
            )






        # Sentimento
        criterios["sentimento"] = (
            sentimento_geral == "positivo" if side == "buy" else sentimento_geral == "negativo"
        )

        # Respiro (pullback detectado)
        try:
            info_respiro = avaliar_respiro(df, side=side, lookback=30)
            criterios["respiro"] = bool(info_respiro["respiro_concluido"])
        except Exception as e:
            criterios["respiro"] = True  # fallback: permite se não avaliar
            logging.warning(f"Erro ao avaliar respiro: {e}")

        # --- 3. Cálculo do Score ponderado e log detalhado ---
        for nome, valido in criterios.items():
            peso = pesos.get(nome, 1)
            score_maximo += peso
            if valido:
                score += peso
                logging.info(f"✅ Critério '{nome}' validado (+{peso})")
            else:
                motivos_rejeicao.append(nome)
                logging.warning(f"❌ Critério '{nome}' falhou (peso {peso})")

        score_pct = (score / score_maximo) * 100 if score_maximo else 0
        logging.info(
            f"📊 Score final ponderado: {score:.2f}/{score_maximo:.2f} ({score_pct:.1f}%)"
        )

        # --- 3.1. Lógica de confirmação adaptativa (mínimo de confirmações principais) ---
        principais = ["volume", "ema", "rsi"]
        confirmados = sum(criterios.get(c, False) for c in principais)

        modo_rigor = os.getenv("MODO_VALIDACAO", "moderado").lower()
        if modo_rigor == "agressivo":
            minimo_confirmacoes = 0
        elif modo_rigor == "moderado":
            minimo_confirmacoes = 2
        else:
            minimo_confirmacoes = 3

        if confirmados < minimo_confirmacoes:
            logging.warning(
                f"🚫 Falha nas confirmações principais ({confirmados}/{minimo_confirmacoes}) "
                f"| Volume={criterios.get('volume')} | EMA={criterios.get('ema')} | RSI={criterios.get('rsi')}"
            )
            score *= 0.5  # penaliza o score final
        else:
            logging.info(
                f"✅ Confirmações principais atingidas ({confirmados}/{minimo_confirmacoes}) "
                f"| Volume={criterios.get('volume')} | EMA={criterios.get('ema')} | RSI={criterios.get('rsi')}"
            )

    except Exception as e:
        logging.error(f"❌ Erro ao calcular score: {e}")

    # --- 4. Retorno estruturado ---
    return {
        "score": round(score, 2),
        "criterios": criterios,
        "motivos_rejeicao": motivos_rejeicao,
        "score_pct": round(score_pct, 2),
    }





def validar_rompimento_atual(df, side, resistencia=None, suporte=None):
    """
    Valida se o rompimento ainda é válido em tempo real.
    Retorna True se o preço confirmou o rompimento.
    """
    try:
        close = df["close"].iloc[-1]
        if side == "buy" and resistencia and close > resistencia:
            logging.info(f"✅ Rompimento BUY ainda válido: close={close} > resistencia={resistencia}")
            return True
        elif side == "sell" and suporte and close < suporte:
            logging.info(f"✅ Rompimento SELL ainda válido: close={close} < suporte={suporte}")
            return True
        else:
            logging.warning(f"❌ Rompimento não validado: close={close}, side={side}")
            return False
    except Exception as e:
        logging.error(f"Erro ao validar rompimento: {e}")
        return False





def criar_backtest_breakout(session, api_key, api_secret):
    def executar_backtest(df, direcao, capital_inicial=1000):
        """
        Estratégia Breakout com filtros simples:
        - Compra se o candle atual romper a máxima dos últimos N candles.
        - Venda se romper a mínima.
        """
        lucro = 0
        N = 20  # número de candles para o breakout

        for i in range(N, len(df) - 2):
            max_prev = df["high"].iloc[i - N:i].max()
            min_prev = df["low"].iloc[i - N:i].min()

            close = df["close"].iloc[i]
            atr = df["ATR"].iloc[i]

            if atr <= 0:
                continue

            OFFSET_CONFIRMACAO = 0.15  # mesmo conceito do robô real

            # ===============================
            # 📈 BREAKOUT DE COMPRA
            # ===============================
            if direcao == "buy" and close > max_prev:
                preco_limit = close + (atr * OFFSET_CONFIRMACAO)

                # confirmação no candle seguinte
                if df["high"].iloc[i + 1] >= preco_limit:
                    preco_entrada = preco_limit
                    preco_saida = df["close"].iloc[i + 1]
                    lucro += preco_saida - preco_entrada

            # ===============================
            # 📉 BREAKOUT DE VENDA
            # ===============================
            elif direcao == "sell" and close < min_prev:
                preco_limit = close - (atr * OFFSET_CONFIRMACAO)

                if df["low"].iloc[i + 1] <= preco_limit:
                    preco_entrada = preco_limit
                    preco_saida = df["close"].iloc[i + 1]
                    lucro += preco_entrada - preco_saida


        return lucro
    return executar_backtest






def executar_ordem_fluxo(api, session, symbol, side, preco_entrada, tamanho_lote,
                         stop_loss, take_profit, trailing_stop,
                         entry_mode="limit", entry_score=0, sentiment_score=0, indicadores_df=None):
    import uuid, math, time, threading, logging

    trade_id = str(uuid.uuid4())  # 🔑 ID único local (não é o orderId da corretora)

    # ============== PRECISÃO DO INSTRUMENTO (tickSize/qtyStep) ==============
    def _instrument_precision(api, symbol):
        try:
            info = api.get_instruments_info(category="linear", symbol=symbol)
            item = (info.get("result", {}).get("list") or [])[0]
            tick = float(item["priceFilter"]["tickSize"])
            step = float(item["lotSizeFilter"]["qtyStep"])
            return tick, step
        except Exception:
            return 0.01, 0.001  # fallback seguro

    tick_size, qty_step = _instrument_precision(api, symbol)

    def _decimals(step):
        s = f"{step:.12f}".rstrip("0")
        return len(s.split(".")[1]) if "." in s else 0

    price_dec = _decimals(tick_size)
    qty_dec   = _decimals(qty_step)

    def _round_to(value, step):
        if step <= 0:
            return float(value)
        return round(round(float(value) / step) * step, _decimals(step))

    def _fmt(value, decimals):
        return f"{float(value):.{decimals}f}"

    # ============== NORMALIZAÇÃO SL/TP POR LADO ==============
    def _normalize_sl_tp(side, entry, last, sl, tp):
        side = side.lower()
        entry = float(entry); last = float(last); sl = float(sl); tp = float(tp)

        if side == "buy":
            # SL precisa ser < entry; TP precisa ser > entry
            if sl >= entry:
                sl = entry * 0.99
            if tp <= entry:
                tp = entry * 1.002  # alvo mínimo 0,2%
        else:
            # SL precisa ser > entry; TP precisa ser < entry
            if sl <= entry:
                sl = entry * 1.01
            if tp >= entry:
                tp = entry * 0.998  # alvo mínimo 0,2% abaixo

        # Arredonda por tick
        sl = _round_to(sl, tick_size)
        tp = _round_to(tp, tick_size)
        return sl, tp

    # ============== ARREDONDAMENTOS INICIAIS ==============
    preco_entrada = _round_to(preco_entrada, tick_size)
    tamanho_lote  = max(_round_to(tamanho_lote, qty_step), qty_step)

    # Se não tiver último preço à mão, usa o próprio preço de entrada para coerência
    last_price_ref = preco_entrada
    stop_loss, take_profit = _normalize_sl_tp(side, preco_entrada, last_price_ref, stop_loss, take_profit)

    # Trailing: garantir positivo e arredondado
    trailing_stop = float(trailing_stop or 0)
    if trailing_stop > 0:
        trailing_stop = max(_round_to(trailing_stop, tick_size), tick_size)
    else:
        trailing_stop = 0.0

    # ============== MONTAGEM DO PARAMS ==============
    params = {
        "category": "linear",
        "symbol": symbol,
        "side": "Buy" if side.lower() == "buy" else "Sell",
        "orderType": entry_mode,
        "qty": _fmt(tamanho_lote, qty_dec),
        "timeInForce": "GTC",
        # SL/TP no create_order são aceitos pela Bybit v5 para perp linear
        "stopLoss": _fmt(stop_loss, price_dec),
        "slTriggerBy": "LastPrice",
        "takeProfit": _fmt(take_profit, price_dec),
        "tpTriggerBy": "LastPrice",
        "triggerDirection": 1,  # mantém como no seu fluxo
    }

    # price só em limit
    if entry_mode.lower() == "limit":
        params["price"] = _fmt(preco_entrada, price_dec)

    # trailingStop só se > 0 (Bybit espera string)
    if trailing_stop and trailing_stop > 0:
        params["trailingStop"] = _fmt(trailing_stop, price_dec)

    logging.info(f"📝 Enviando ordem PERPÉTUA com parâmetros normalizados: {params}")

    # ============== CRIAÇÃO DA ORDEM ==============
    try:
        resposta = api.create_order(**params)
        order_id = resposta.get("result", {}).get("orderId")
    except Exception as e:
        logging.error(f"❌ Erro ao criar ordem: {e}")
        return None

    # ============== PREPARO DO MONITORAMENTO (IA) ==============
    if order_id and indicadores_df is not None:
        try:
            from pos_execucao_monitoramento import preparar_trade_para_monitoramento
            indicadores_formatados = {
                "RSI": float(indicadores_df["RSI"].iloc[-1]),
                "ATR": float(indicadores_df["ATR"].iloc[-1]),
                "EMA50": float(indicadores_df["EMA50"].iloc[-1]),
                "SMA_10": float(indicadores_df["SMA_10"].iloc[-1]),
                "close": float(indicadores_df["close"].iloc[-1]),
                "timestamp": indicadores_df["time"].iloc[-1]
            }
            preparar_trade_para_monitoramento(
                session=session,
                order_id=order_id,
                symbol=symbol,
                trade_id=trade_id,
                strategy="breakout",
                qty=float(tamanho_lote),
                indicadores_df=indicadores_formatados
            )
        except Exception as e:
            logging.error(f"❌ Erro ao preparar monitoramento inteligente: {e}")
    else:
        logging.warning("⚠️ indicadores_df ausente ou order_id não gerado.")

    # ============== CONFIRMAÇÃO DE EXECUÇÃO PARA LIMIT ==============
    # (Se Market, normalmente já executa instantâneo)
    executed_qty = float(resposta.get("result", {}).get("cumExecQty", 0))
    order_status = resposta.get("result", {}).get("orderStatus", "Unknown")

    if entry_mode.lower() == "limit":
        start = time.time()
        max_wait = 20
        while executed_qty == 0 and (time.time() - start < max_wait):
            time.sleep(2)
            status = api.get_order_status(order_id, symbol)
            executed_qty = float(status.get("cumExecQty", 0))
            order_status = status.get("orderStatus", "Unknown")
            if executed_qty > 0:
                break
        if executed_qty == 0:
            logging.warning("⏳ Ordem LIMIT não executada após timeout. Cancelando...")
            try:
                api.cancel_order(order_id, symbol)
            except Exception as e:
                logging.error(f"Erro ao cancelar ordem: {e}")
            return None

    # ============== AJUSTE INICIAL DE SL/TP/TS PÓS-EXECUÇÃO ==============
    # (Mantém mesmos valores já normalizados)
    try:
        pos = api.get_positions(category="linear", symbol=symbol)
        lista = pos.get("result", {}).get("list", [])
        tem_posicao = any(float(p.get("size") or 0) > 0 for p in lista)
        if tem_posicao:
            try:
                payload = {
                    "category": "linear",
                    "symbol": symbol,
                    "stopLoss": _fmt(stop_loss, price_dec),
                    "takeProfit": _fmt(take_profit, price_dec),
                    "slTriggerBy": "LastPrice",
                    "tpTriggerBy": "LastPrice",
                    "triggerDirection": 1,
                }
                if trailing_stop and trailing_stop > 0:
                    payload["trailingStop"] = _fmt(trailing_stop, price_dec)

                api.set_trading_stop(**payload)
                logging.info("✅ SL/TP/TS ajustados na posição aberta (pós-execução).")
            except Exception as e:
                logging.warning(f"⚠️ Falha ao ajustar SL/TP/TS na posição: {e}")
        else:
            logging.warning("⚠️ Nenhuma posição aberta para ajuste de SL/TP/TS.")
    except Exception as e:
        logging.warning(f"⚠️ Falha ao checar posição antes de ajustar SL/TP/TS: {e}")

    # ============== MONITORAMENTO EM THREAD ==============
    trade_data = {
        "trade_id": order_id or trade_id,  # usa order_id real se disponível
        "symbol": symbol,
        "side": side or "buy",  # fallback defensivo
        "entry_price": float(preco_entrada),
        "position_size": float(tamanho_lote),
        "risk_parameters": {
            "stop_loss": float(stop_loss),
            "take_profit": float(take_profit),
            "trailing_stop": float(trailing_stop)
        },
        "indicators": {
            "entry_score": float(entry_score or 0),
            "sentiment_score": float(sentiment_score or 0)
        }
    }

    for campo in ("trade_id", "symbol", "entry_price", "position_size", "side"):
        if trade_data.get(campo) in (None, "", 0):
            logging.error(f"❌ Campo obrigatório ausente no trade_data: {campo}")
            return None

    threading.Thread(
        target=monitorar_ordem_ate_encerrar,
        args=(
            trade_data["trade_id"],
            symbol,
            trade_data["position_size"],
            trade_data["entry_price"],
            side,
            api,
            trade_data["risk_parameters"],
            trade_data["indicators"]
        ),
        daemon=True
    ).start()

    logging.info(f"✅ Ordem {trade_data['trade_id']} criada e monitoramento iniciado.")
    return trade_data["trade_id"]




def breakout_filters_summary(motivos_rejeicao=None):
    """
    Gera um resumo legível dos filtros aplicados na última análise da estratégia Breakout.
    Se receber `motivos_rejeicao`, detalha o motivo de cada filtro falhar.
    """
    try:
        # Esses valores devem ser definidos após a última análise do breakout
        global filtro_rsi, filtro_volume, filtro_media, resultado_final

        resumo = []

        if filtro_rsi:
            resumo.append("RSI favorável ✅")
        else:
            motivo = ""
            if motivos_rejeicao and "rsi" in motivos_rejeicao:
                motivo = f" (motivo: {motivos_rejeicao['rsi']})"
            resumo.append(f"RSI desfavorável ❌{motivo}")

        if filtro_volume:
            resumo.append("Volume acima da média ✅")
        else:
            motivo = ""
            if motivos_rejeicao and "volume" in motivos_rejeicao:
                motivo = f" (motivo: {motivos_rejeicao['volume']})"
            resumo.append(f"Volume baixo ❌{motivo}")

        if filtro_media:
            resumo.append("Média Móvel inclinada a favor ✅")
        else:
            motivo = ""
            if motivos_rejeicao and "media" in motivos_rejeicao:
                motivo = f" (motivo: {motivos_rejeicao['media']})"
            resumo.append(f"Média Móvel neutra ou contra ❌{motivo}")

        resumo.append(f"Score Final Breakout: {resultado_final} pontos")

        # Se quiser mostrar todos motivos de rejeição recebidos:
        if motivos_rejeicao and isinstance(motivos_rejeicao, dict):
            resumo.append("🟠 Motivos de bloqueio detalhados:")
            for k, v in motivos_rejeicao.items():
                resumo.append(f" - {k}: {v}")

        return resumo
    except Exception as e:
        return [f"Erro ao gerar resumo dos filtros breakout: {e}"]


def atualizar_globais_score(resultado_score):
    """
    Atualiza as variáveis globais utilizadas no resumo dos filtros Breakout
    com base no resultado do cálculo de score.
    """
    global resultado_final, filtro_rsi, filtro_volume, filtro_media
    resultado_final = resultado_score['score']
    filtro_rsi = resultado_score['criterios'].get('rsi', False)
    filtro_volume = resultado_score['criterios'].get('volume', False)
    filtro_media = resultado_score['criterios'].get('sma', False)

def analisar_e_mostrar_filtros(df, side, sentimento_geral):
    resultado_score = calcular_score_completo(df, side, sentimento_geral)
    atualizar_globais_score(resultado_score)
    print("\n".join(breakout_filters_summary(resultado_score['motivos_rejeicao'])))




import threading
import logging
import os
import requests
import pandas as pd

# Variável global para armazenar o último candle analisado
ultimo_candle_processado = None
ULTIMO_CANDLE_PATH = "ultimo_candle_breakout.txt"

def get_ultimo_candle():
    try:
        with open(ULTIMO_CANDLE_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return None

def set_ultimo_candle(valor):
    with open(ULTIMO_CANDLE_PATH, "w", encoding="utf-8") as f:
        f.write(str(valor))



def get_env_float(name, default):
    try:
        return float(os.getenv(name, default))
    except Exception:
        return default


def obter_preco_mercado(symbol, df=None, intervalo="5"):
    """
    Retorna preço de mercado confiável (close do último candle) sem uso de IA.
    """
    try:
        if df is None or df.empty:
            df = get_market_data_bybit(symbol=symbol, session=session, interval=intervalo, limit=2)
        if df is None or df.empty:
            logging.error(f"🚫 PREÇO INVÁLIDO | {symbol} | market data vazio")
            return None
        return float(df["close"].iloc[-1])
    except Exception as e:
        logging.error(f"🚫 PREÇO INVÁLIDO | {symbol} | erro ao obter preço de mercado: {e}")
        return None

from dotenv import load_dotenv
load_dotenv(override=True)



def liberar_lock_fisico(symbol):
    path = get_lock_path(symbol)
    if os.path.exists(path):
        os.remove(path)
        logging.info(f"🔓 Lock físico liberado para {symbol}.")
    else:
        logging.info(f"✅ Nenhum lock ativo encontrado para {symbol}.")



def _breakout(symbol, session, api):
    global ultimo_candle_processado
    logging.info(f"🚀 Iniciando Breakout Trading para {symbol}")
    logging.info(f"🔎 ENV: ATR_MIN={os.getenv('THRESHOLD_ATR_MIN')} | ATR_MAX={os.getenv('THRESHOLD_ATR_MAX')}")

    # --- FLAGS GERAIS (antes do try) ---
    lock_criado = False
    posicao_aberta = False

    # 🔒 ANTI-DUPLICIDADE: Checagem do lock físico
    if existe_lock_fisico(symbol):
        logging.warning(f"⛔ Lock físico ativo para {symbol}. NÃO entra no mercado.")
        return None, None, None

    if BACKTEST_MODE:
        logger.info("🔎 BACKTEST_MODE ativo — pulando envio de ordens reais para BREAKOUT")
        return None, None, None

    # 1. Verifica se já existe ordem ativa
    if existe_ordem_aberta(api, symbol):
        logging.info(f"🚫 Ordem já ativa para {symbol}. Iniciando monitoramento da posição existente...")
        trade_info = obter_detalhes_ordem_ativa(api, symbol)
        logging.info(f"🔎 DEBUG trade_info retornado: {trade_info}")
        if not trade_info:
            logging.warning("⚠️ Não foi possível recuperar os detalhes da ordem ativa.")
            return None, None, None

        entry_price = get_entry_price(trade_info)
        logging.info(
            f"📋 Fields importantes: orderId={trade_info.get('orderId')}, "
            f"entry_price={entry_price}, qty={trade_info.get('qty')}, "
            f"stopLoss={trade_info.get('stopLoss')}, takeProfit={trade_info.get('takeProfit')}, "
            f"side={trade_info.get('side')}"
        )
        if entry_price == 0.0:
            logging.warning(f"entry_price ausente ({entry_price}) | trade_info={trade_info}")
            return None, None, None

        position_size = float(trade_info.get("qty", 0))
        trade_id = (
            trade_info.get("orderId")
            or trade_info.get("trade_id")
            or trade_info.get("positionIdx")
            or trade_info.get("positionId")
            or "desconhecido"
        )
        risk_parameters = {
            "stop_loss": float(trade_info.get("stopLoss", 0)),
            "take_profit": float(trade_info.get("takeProfit", 0)),
            "trailing_stop": float(trade_info.get("trailingStop", 0)),
        }
        threading.Thread(
            target=monitorar_ordem_ate_encerrar,
            args=(
                trade_id,
                symbol,
                position_size,
                entry_price,
                trade_info.get("side", "Buy").lower(),
                api,
                risk_parameters,
                {"entry_score": 0, "sentiment_score": 0},
            ),
        ).start()
        return None, None, None

    # 2. Carrega dados de mercado
    # M5
    TF_INTERVAL = os.getenv("INTERVAL", "5")  # Bybit: "5" = 5 minutos
    TF_LIMIT = int(os.getenv("TF_LIMIT", 300))  # candles para indicadores

    df = get_market_data_bybit(symbol=symbol, session=session, interval=TF_INTERVAL, limit=TF_LIMIT)

    if df is None or df.empty:
        logging.error("❌ ERRO: get_market_data_bybit() retornou DataFrame vazio. Abortando.")
        return None, None, None

    # Extrair resistência e suporte para validação
    resistencia = df["Resistance"].iloc[-2] if "Resistance" in df.columns and len(df) > 1 else None
    suporte = df["Support"].iloc[-2] if "Support" in df.columns and len(df) > 1 else None

    # 🔄 Verificação de candle novo
    df = df.rename(columns={"time": "timestamp"})
    candle_atual = df["timestamp"].iloc[-1]
    ultimo = get_ultimo_candle()
    if str(candle_atual) == ultimo:
        logging.info("⏳ Nenhum novo candle detectado. Aguardando próximo fechamento...")
        return None, None, None
    set_ultimo_candle(str(candle_atual))

    # 3. Indicadores e validação
    df = calculate_indicators(df)
    df = aplicar_regras_validacao_breakout(df)
    ema50 = float(df["EMA50"].iloc[-1]) if "EMA50" in df.columns else float(df["close"].iloc[-1])
    ema200 = float(df["EMA200"].iloc[-1]) if "EMA200" in df.columns else float(df["close"].iloc[-1])

        
    # 🚦 Filtro de regime de mercado (volatilidade mínima)
    if not check_volatility(df):
        logging.warning("🚫 Volatilidade insuficiente. Mercado lateral.")
        return None, None, None

    # 4. Detecta sinal
    breakout_signal = detect_breakout(df)
    if breakout_signal == "bullish":
        side = "buy"
    elif breakout_signal == "bearish":
        side = "sell"
    else:
        logging.warning("⚠️ Nenhum breakout confirmado neste candle. Sinal inconclusivo.")
        return None, None, None

    # 🎯 Preço de SINAL (fechamento do candle M5)
    preco_sinal = obter_preco_mercado(symbol, df=df, intervalo=TF_INTERVAL)

    if not validar_preco(preco_sinal, symbol):
        return None, None, None
    logging.info(f"📌 PREÇO MERCADO | {symbol} | close={preco_sinal:.4f}")

    # 🎯 ATR do candle (base da confirmação)
    atr = float(df["ATR"].iloc[-1]) if "ATR" in df.columns else 0.0
    tr_valor = float(df["TrueRange"].iloc[-1]) if "TrueRange" in df.columns else atr

    if atr <= 0 or tr_valor <= 0:
        logging.warning("⚠️ ATR/TR inválido/zero — abortando.")
        return None, None, None

    preco_confirmacao = calcular_preco_confirmacao(
        preco_breakout=preco_sinal,
        side=side,
        tr=tr_valor,
        fator_confirmacao=FATOR_CONFIRMACAO_TR,
    )

    logging.info(
        f"🎯 CONFIRMAÇÃO BREAKOUT | Side={side.upper()} | "
        f"Sinal={preco_sinal:.2f} | Limit={preco_confirmacao:.2f} | ATR={atr:.2f}"
    )


    # ===========================================================
    # 🚫 NOVA REGRA ABSOLUTA: BREAKOUT OBEDECE O SCORE GLOBAL
    # ===========================================================
    direcao_global = os.getenv("DIRECAO_GLOBAL", "").strip().lower()

    if direcao_global in ["buy", "sell"]:
        if side != direcao_global:
            logging.info(
                f"❌ Breakout gerou {side.upper()}, mas direção global é "
                f"{direcao_global.upper()} → sinal ignorado."
            )
            return None, None, None
        else:
            logging.info(
                f"✅ Breakout alinhado com direção global ({direcao_global.upper()})."
            )
    else:
        logging.warning("⚠️ DIRECAO_GLOBAL não definida — seguindo fluxo normal.")


    # 5.1) Anti-entrada precoce (respiro)
    info_respiro = avaliar_respiro(df, side=side, lookback=30)
    modo_validacao_atual = os.getenv("MODO_VALIDACAO", "moderado").strip().lower()
    limiar_score_por_modo = {
        "agressivo": int(os.getenv("SCORE_RESPIRO_MINIMO_AGRESSIVO", 4)),
        "moderado": int(os.getenv("SCORE_RESPIRO_MINIMO_MODERADO", 6)),
        "conservador": int(os.getenv("SCORE_RESPIRO_MINIMO_CONSERVADOR", 8)),
    }
    limiar_score = limiar_score_por_modo.get(modo_validacao_atual, 6)

    if info_respiro["respiro_em_andamento"] and not info_respiro["respiro_concluido"]:
        if not (modo_validacao_atual == "agressivo" and info_respiro["score"] >= limiar_score):
            logging.warning({
                "timestamp": str(datetime.now()),
                "motivo": "Respiro em andamento sem confirmação de rejeição",
                "score_respiro": info_respiro["score"],
                "modo_validacao": modo_validacao_atual,
            })
            return None, None, None

    # 5. Score e confirmações
    criterios = calcular_score_completo(df, side=side, sentimento_geral="positivo")
    score_final = criterios.get("score", 0)
    score_pct = criterios.get("score_pct", 0)

    # 🔑 AUTORIDADE ÚNICA: REGIME MANDA
    detector = RegimeDetector()
    regime_id = detector.detectar(df, symbol=symbol)  # mesmo método do backtest

    configurator = RegimeConfigurator()
    regime = configurator.get(regime_id)

    score_minimo = regime["score_min"]
    score_pct_requerido = regime["score_pct"]
    modo = regime["modo"]

    logging.info(
        f"🔑 REGIME ATIVO → {modo.upper()} | "
        f"ScoreMin={score_minimo} | ScorePctMin={score_pct_requerido}%"
    )

    if score_final < score_minimo or score_pct < score_pct_requerido:
        logging.warning(
            f"❌ Entrada bloqueada por score.\n"
            f" - Score final: {score_final} (min: {score_minimo})\n"
            f" - Score percentual: {score_pct:.2f}% (min: {score_pct_requerido}%)"
        )
        return None, None, None



    # 6. Cálculo de TP/SL/Trailing/lote
    try:
        logging.debug("[Inicialização] ATR será calculado a partir do DataFrame")

        # --- Preço e saldo ---
        try:
            saldo_info = session.get_wallet_balance(accountType="UNIFIED")
            wallet = saldo_info["result"]["list"][0]["coin"]
            usdt = next((x for x in wallet if x["coin"] == "USDT"), None)
            valor_raw = usdt.get("walletBalance", None)
            saldo_disponivel = (
                float(valor_raw)
                if valor_raw and str(valor_raw).replace(".", "", 1).isdigit()
                else 100.0
            )
        except Exception as e:
            logging.warning(f"⚠️ Falha ao obter saldo da conta UNIFIED: {e}")
            saldo_disponivel = 100.0

        if saldo_disponivel <= 0:
            saldo_disponivel = 100.0
        logging.info(f"💰 Saldo disponível: {saldo_disponivel:.2f} USDT")

        # --- SL/TP via lógica central adaptativa ---
        preco_base_sl = float(preco_confirmacao)
        stop_loss, take_profit, tipo_stop, contexto = calcular_stop_take_adaptativo(df, preco_base_sl, side)

        logging.info(f"[BREAKOUT - SL/TP Adaptativo] {contexto=} | {tipo_stop=} | EntradaPlanejada={preco_base_sl:.2f}| SL={stop_loss} | TP={take_profit}")

        trailing_offset_calculado = 0


        # --- Gestão de risco (BASEADA NA ENTRADA CONFIRMADA) ---
        risco_percentual = float(os.getenv("RISCO_PERCENTUAL", 0.04))
        risco_em_usdt = saldo_disponivel * risco_percentual

        # ✅ entrada planejada = preço LIMIT de confirmação
        preco_planejado = float(preco_confirmacao)

        stop_distance = abs(preco_planejado - stop_loss)
        if stop_distance <= 0:
            logging.warning("⚠️ Distância de stop inválida. Abortando entrada.")
            return None, None, None

        qty = min(
            risco_em_usdt / stop_distance,
            saldo_disponivel / preco_planejado
        )
        qty = ajustar_para_min_qty(symbol, qty)
        qty = round(qty, 3)

        logging.info(
            f"📏 RISCO | EntradaPlanejada={preco_planejado:.2f} | "
            f"SL={stop_loss:.2f} | Distância={stop_distance:.2f} | Qty={qty}"
        )

        # --- Lock fisico ---
        cria_lock_fisico(symbol)
        lock_criado = True
        logging.info(f"Lock fisico criado para {symbol}")

        trade_id = None
        entry_price_real = None
        tamanho_lote_real = qty
        trade_state = {}

        if USAR_ENTRADA_CONFIRMADA:
            # Estado explícito: LIMIT pendente de confirmação
            trade_state = {
                "estado": "LIMIT_PENDENTE",
                "limit_price": preco_planejado,
                "timestamp_envio": datetime.now().isoformat(),
            }
            logging.info(f"Estado do trade: {trade_state}")
            try:
                send_telegram_message(
                    f"🚧 LIMIT PENDENTE\nPar: {symbol}\nDireção: {side.upper()}\nPreço limite: {preco_planejado:.4f}\nQtd: {qty}"
                )
            except Exception as e:
                logging.warning(f"Falha ao notificar LIMIT pendente: {e}")

            confirmacao = executar_entrada_breakout_confirmada(
                api=api,
                symbol=symbol,
                side=side,
                preco_breakout=preco_sinal,
                tr=tr_valor,
                qty=qty,
                fator_confirmacao=FATOR_CONFIRMACAO_TR,
                timeout=TIMEOUT_ENTRADA_SEGUNDOS,
                intervalo=INTERVALO_VERIFICACAO_ENTRADA,
            )

            if not confirmacao:
                trade_state["estado"] = "CANCELADA_TIMEOUT"
                logging.warning("Breakout nao confirmou dentro do timeout. Encerrando tentativa.")
                return None, None, None

            entry_price_real = float(confirmacao.get("entry_price", 0) or 0)
            trade_state["estado"] = "ENTRADA_EXECUTADA"
            trade_id = confirmacao.get("orderId")
            tamanho_lote_real = float(confirmacao.get("qty", qty))
            posicao_aberta = True
            logging.info(f"Entrada confirmada via limite | Ordem={trade_id} | Preco={entry_price_real:.4f}")
        else:
            resposta = executar_ordem_dinamica(
                acao=side,
                symbol=symbol,
                qty=qty,
                entry_price=preco_planejado,   # so entra se CONFIRMAR
                entry_score=score_final,
                sentiment_score=0,
                strategy="breakout",
                stop_loss=None,
                take_profit=None,
                trailing_stop=0,
                order_type="limit_confirmacao",
            )

            if not resposta or "orderId" not in resposta:
                logging.error("Falha na criacao da ordem. Abortando.")
                return None, None, None

            trade_id = resposta["orderId"]

            def validate_breakout_callback():
                return validar_rompimento_atual(df, side, resistencia, suporte)

            ordem_executada = esperar_ordem_executada(
                api,
                symbol,
                trade_id,
                timeout=60,
                extend_if_price_within=0.004,
                max_total_timeout=450,
                allow_market_fallback=False,
                validate_breakout=validate_breakout_callback,
                estrategia="breakout",
            )
            if not ordem_executada:
                logging.warning(f"Ordem {trade_id} nao foi executada, cancelando...")
                try:
                    api.cancel_order(category="linear", symbol=symbol, orderId=trade_id)
                    logging.info(f"??? Ordem {trade_id} cancelada por timeout.")
                except Exception as e:
                    logging.warning(f"Falha ao cancelar ordem pendente: {e}")
                return None, None, None

            posicao_aberta = True
            logging.info(f"Posicao aberta para {symbol}. Lock mantido ate encerramento.")

            trade_info = obter_detalhes_ordem_ativa(api, symbol)
            if not trade_info or float(trade_info.get("qty", 0)) == 0:
                logger.error("Ordem executada, mas posicao nao aberta.")
                return None, None, None

            entry_price_real = get_entry_price(trade_info)
            try:
                tamanho_lote_real = float(trade_info.get("qty", 0))
            except (ValueError, TypeError):
                tamanho_lote_real = 0.0
                logger.warning(f"Campo qty ausente: {trade_info}")

        if entry_price_real is None or entry_price_real <= 0:
            logging.error("Preco de entrada nao definido apos confirmacao.")
            return None, None, None

        from compra1 import aplicar_trading_stop_com_retry
        stop_loss, take_profit, tipo_stop, contexto = calcular_stop_take_adaptativo(df, entry_price_real, side)

        resp_tp_sl = aplicar_trading_stop_com_retry(session, symbol, stop_loss, take_profit)
        if not resp_tp_sl or resp_tp_sl.get("retCode") != 0:
            logging.error(f"Falha ao aplicar SL/TP apos execucao: {resp_tp_sl}")
        else:
            logging.info(f"SL/TP aplicados apos execucao: SL={stop_loss}, TP={take_profit}")

        try:
            msg = (
                f"*Trade Breakout Executado*\n"
                f"Par: {symbol}\nDirecao: {side.upper()}\nEntradaConfirmada(LIMIT): {entry_price_real:.2f}\n"
                f"Qtd: {tamanho_lote_real}\nSL: {stop_loss}\nTP: {take_profit}\n"
                f"Score: {score_final}\nHora: {datetime.now().strftime('%H:%M:%S')}"
            )
            send_telegram_message(msg)
        except Exception as e:
            logging.warning(f"Falha ao enviar mensagem Telegram: {e}")

        rsi_value = float(df["RSI"].iloc[-1]) if "RSI" in df.columns else 0.0
        atr_value = atr
        side_value = side.lower()

        trade_data = {
            "trade_id": trade_id,
            "symbol": symbol,
            "side": side_value,
            "entry_price": entry_price_real,
            "position_size": tamanho_lote_real,
            "risk_parameters": {
                "stop_loss": stop_loss or 0.0,
                "take_profit": take_profit or 0.0,
                "trailing_stop": trailing_offset_calculado or 0.0,
            },
            "indicators": {"RSI": rsi_value, "ATR": atr_value},
        }

        API_IA_URL = os.getenv("API_IA_URL", "http://localhost:8000")
        try:
            requests.post(f"{API_IA_URL}/analisar_trade", json=trade_data)
        except Exception as e:
            logging.warning(f"Falha ao enviar dados para IA: {e}")

        threading.Thread(
            target=monitorar_ordem_ate_encerrar,
            args=(
                trade_id,
                symbol,
                tamanho_lote_real,
                entry_price_real,
                side,
                api,
                trade_data["risk_parameters"],
                trade_data["indicators"],
            ),
        ).start()

        logging.info("Ordem criada, EXECUTADA e monitoramento iniciado.")
        return trade_id, entry_price_real, tamanho_lote_real


    except Exception as e:
        logging.error(f"❌ Erro no bloco de risco/lote/execução: {e}", exc_info=True)
        return None, None, None

    finally:
        if lock_criado and not locals().get("posicao_aberta", False):
            liberar_lock_fisico(symbol)
            logging.info(f"🔓 Lock físico liberado automaticamente para {symbol}.")


if __name__ == "__main__":
    # 0) Força override de modo para testes (opcional)
    os.environ["BACKTEST_MODE"] = "false"  # ou true
    # os.environ["MODO_RELAXADO"] = "false"  # ❌ Removido: substituído por MODO_VALIDACAO

    # 1) Carrega variáveis de ambiente e configura logger
    configurar_logger_global("breakout_filtros.log")
    logger = logging.getLogger(__name__)

    # 1.1) Painel de status de modo de execução
    from utils.config_flags import BACKTEST_MODE

    score_minimo, score_pct, modo_validacao = obter_config_validacao()

    logger.info("🧭 CONFIGURAÇÃO DE MODO DE EXECUÇÃO")
    logger.info(f"🟢 MODO PRODUÇÃO: {'ATIVO' if not BACKTEST_MODE else 'INATIVO'}")
    logger.info(f"🔐 MODO DE VALIDAÇÃO: {modo_validacao.upper()} (score mínimo: {score_minimo}, percentual mínimo: {score_pct}%)")
    logger.info("⚙️ Estratégias habilitadas: Breakout, Reversão, Market Making")


    # 2) Inicializa sessão/API
    session = HTTP(testnet=BYBIT_TESTNET, api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET)
    api = session

    # 3) Verifica se é modo backtest
    if BACKTEST_MODE:
        df = get_market_data_bybit(symbol=symbol, session=session, interval=interval, limit=500)
        stats = executar_backtest_breakout(df)
        logger.info("🏁 Backtest BREAKOUT concluído: %s", stats)
        sys.exit(0)

    # 4) Modo produção
    # 4) Modo produção (M5)
    import time

    POLL_SECONDS = int(os.getenv("POLL_SECONDS", 45))
    if POLL_SECONDS < 15:
        POLL_SECONDS = 15  # piso de segurança

    while True:
        load_dotenv(override=True)  # 🔄 recarrega o .env a cada iteração (permite ajustes sem reiniciar)

        try:
            _breakout(symbol, session, api)  # Usa o symbol dinâmico
        except Exception as e:
            logger.error(f"❌ Erro no loop principal do BREAKOUT: {e}", exc_info=True)

        # M5: pode consultar a cada 30–60s sem repetir candle (já tem proteção por arquivo)
        time.sleep(POLL_SECONDS)

 
