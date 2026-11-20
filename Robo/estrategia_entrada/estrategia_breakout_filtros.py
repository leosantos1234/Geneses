import os
import math
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
import uuid
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
from config import ACTIVE_EXCHANGE, BYBIT_API_KEY, BYBIT_API_SECRET, TESTNET as BYBIT_TESTNET
from utils.ml_core import validar_com_modelo, carregar_modelo_com_fallback
from compra1 import executar_ordem_dinamica, get_saldo_disponivel, ajustar_para_min_qty
from utils.utils_market import get_market_data_bybit
from estrategia_entrada.pos_execucao_monitoramento import preparar_trade_para_monitoramento
from utils.utils_telegram import send_telegram_message
from utils.parse_env import get_ativos_env
from regime.regime_detector import RegimeDetector
from regime.regime_configurator import RegimeConfigurator
from regime.entry_profile import get_entry_profile
from utils.entrada_breakout_confirmada import (
    calcular_preco_confirmacao,
    executar_entrada_breakout_confirmada,
)
from utils.validacoes import validar_preco
from utils.breakout_observability import (
    BreakoutEvaluation,
    breakout_regime_eligible,
    derive_candle_identity,
    deterministic_breakout_evaluation_id,
)
from utils.breakout_runtime_context import build_breakout_evaluation_kwargs, regime_authority_metadata
from utils.closed_candles import select_closed_candles
from utils.trend_forensics import build_trend_forensics
from utils.strategy_profiles import resolve_strategy_profile
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

def _safe_env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "")
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        value = float(raw)
    except Exception:
        logging.warning(f"[ENV] Valor invalido para {name}='{raw}'. Usando default={default}.")
        return float(default)
    if not np.isfinite(value):
        logging.warning(f"[ENV] Valor nao-finito para {name}='{raw}'. Usando default={default}.")
        return float(default)
    return value


def _safe_env_int(name: str, default: int) -> int:
    value = _safe_env_float(name, float(default))
    try:
        return int(value)
    except Exception:
        return int(default)


ATR_BREAKOUT_MIN_PCT = _safe_env_float("THRESHOLD_ATR_MIN", 0.002)  # Default: 0.2%
DELTA_MINIMO_BREAKOUT = _safe_env_float("DELTA_MINIMO_BREAKOUT", 0.7)
# BREAKOUT: execu??o condicionada ao rompimento confirmado (STOP-LIMIT)
# Observa??o: USAR_ENTRADA_CONFIRMADA ? ignorado por requisito (RN/RT)
USAR_ENTRADA_CONFIRMADA = True
FATOR_CONFIRMACAO_TR = _safe_env_float("FATOR_CONFIRMACAO_TR", _safe_env_float("OFFSET_CONFIRMACAO_ATR", 0.12))
TIMEOUT_ENTRADA_SEGUNDOS = _safe_env_int("TIMEOUT_ENTRADA_SEGUNDOS", 180)
INTERVALO_VERIFICACAO_ENTRADA = _safe_env_int("INTERVALO_VERIFICACAO_ENTRADA", 3)
BREAKOUT_MIN_RRR = _safe_env_float("BREAKOUT_MIN_RRR", 1.5)
# Um unico alvo para todos os caminhos reais da Breakout.
BREAKOUT_RRR_ALVO = max(BREAKOUT_MIN_RRR, _safe_env_float("RRR", 2.2))

# 🔐 Variáveis globais de filtros (inicializadas para evitar NameError)
filtro_rsi = False
filtro_volume = False
filtro_media = False
resultado_final = 0



# 🔹 Inicializar sessão autenticada da Bybit antes de qualquer uso
if ACTIVE_EXCHANGE == "bybit":
    session = HTTP(
        testnet=BYBIT_TESTNET,
        api_key=BYBIT_API_KEY,
        api_secret=BYBIT_API_SECRET,
    )


# 🔹 Definir API corretamente
api = session

if BACKTEST_MODE:
    session = None

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
    # Fonte unica para score/regime (evita divergencia entre modulos).
    from utils.config_validacao import obter_config_validacao as _obter_config_validacao_global

    return _obter_config_validacao_global()





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
    # Filtro de tendencia real (ATR% e inclinacao de EMAs)
    try:
        if df is None or len(df) < 2:
            logging.info("[BREAKOUT] Trend gate: dados insuficientes.")
            return False

        close = float(df["close"].iloc[-1])
        atr = float(df["ATR"].iloc[-1])
        ema_fast = float(df["EMA_12"].iloc[-1])
        ema_slow = float(df["EMA_26"].iloc[-1])
        ema50 = float(df["EMA_50"].iloc[-1])
        ema200 = float(df["EMA_200"].iloc[-1])

        atr_pct = (atr / close) * 100 if close else 0.0
        incl_fast = abs(ema_fast - ema_slow) / close if close else 0.0
        incl_slow = abs(ema50 - ema200) / close if close else 0.0

        min_atr_pct = float(os.getenv("BREAKOUT_MIN_ATR_PCT", "0") or 0)
        min_fast = float(os.getenv("BREAKOUT_MIN_EMA_SLOPE_FAST", "0") or 0)
        min_slow = float(os.getenv("BREAKOUT_MIN_EMA_SLOPE_SLOW", "0") or 0)

        logging.info(
            "[BREAKOUT] Trend gate: ATR%%=%.2f (min %s), FAST=%.6f (min %s), SLOW=%.6f (min %s)",
            atr_pct,
            min_atr_pct,
            incl_fast,
            min_fast,
            incl_slow,
            min_slow,
        )

        if atr_pct < min_atr_pct or incl_fast < min_fast or incl_slow < min_slow:
            logging.info("[BREAKOUT] Trend gate: bloqueado por tendencia real.")
            return False
    except Exception as e:
        logging.warning("[BREAKOUT] Trend gate falhou: %s" % e)
        return False

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


def _apply_impulse_reset(df: pd.DataFrame, profile: dict | None = None) -> tuple[pd.DataFrame, bool]:
    """
    Reseta a estrutura apos candle de impulso (range >= X * ATR).
    Retorna (df_filtrado, pronto_para_breakout).
    """
    required = {"open", "high", "low", "close", "ATR"}
    if df is None or df.empty or not required.issubset(set(df.columns)):
        if df is not None and not df.empty:
            logging.warning(
                "[BREAKOUT] Recorte sem colunas OHLC/ATR; janela ignorada. missing=%s",
                sorted(required.difference(set(df.columns))),
            )
        return df, True

    try:
        atr_mult = float((profile or {}).get("breakout_impulse_atr_mult", os.getenv("BREAKOUT_IMPULSE_ATR_MULT", 1.5)))
    except Exception:
        atr_mult = 1.5
    try:
        body_min = float((profile or {}).get("breakout_impulse_body_min", os.getenv("BREAKOUT_IMPULSE_BODY_MIN", 0.55)))
    except Exception:
        body_min = 0.55
    try:
        min_bars = int((profile or {}).get("breakout_impulse_reset_min_bars", os.getenv("BREAKOUT_IMPULSE_RESET_MIN_BARS", 8)))
    except Exception:
        min_bars = 8

    # A configuração é a fonte de verdade: zero desativa o bloqueio
    # pós-impulso, sem impor um mínimo oculto no código.
    if min_bars <= 0:
        return df, True

    if len(df) < min_bars:
        return df, False

    high = df["high"]
    low = df["low"]
    open_ = df["open"]
    close = df["close"]
    atr = df["ATR"]

    range_ = (high - low).abs()
    body = (close - open_).abs()
    body_frac = body / (range_ + 1e-9)

    impulso = (range_ >= (atr_mult * atr)) & (body_frac >= body_min)
    if not impulso.any():
        return df, True

    # Usa posição, não rótulo. Nas janelas OOS o índice pode começar em 500,
    # e um slice por label podia devolver um objeto inconsistente após resets.
    last_pos = int(impulso.to_numpy().nonzero()[0][-1])
    df_reset = df.iloc[last_pos + 1 :].copy()
    if df_reset.empty or len(df_reset) < min_bars:
        logging.info(
            "[BREAKOUT] Reset por impulso em pos=%s | aguardando nova base (%d/%d candles)",
            last_pos,
            0 if df_reset is None else len(df_reset),
            min_bars,
        )
        return df_reset, False

    logging.info(
        "[BREAKOUT] Reset por impulso em pos=%s | nova janela=%d candles",
        last_pos,
        len(df_reset),
    )
    return df_reset, True


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


def _rsi_extreme_sell_block(df, profile=None) -> bool:
    """Evita novas vendas quando o movimento já está excessivamente esticado."""
    try:
        profile = profile or {}
        level = float(profile.get("rsi_extreme_sell_block", os.getenv("RSI_EXTREME_SELL_BLOCK", 25.0)))
        rsi = float(df["RSI"].iloc[-1])
        return rsi <= level
    except (KeyError, TypeError, ValueError, IndexError):
        return False


def _rsi_buy_reversal_confirmed(df, profile=None) -> bool:
    """Confirma recuperação do RSI; RSI baixo sozinho nunca gera compra."""
    try:
        profile = profile or {}
        max_level = float(profile.get("rsi_buy_reversal_max", os.getenv("RSI_BUY_REVERSAL_MAX", 35.0)))
        min_rise = float(profile.get("rsi_buy_reversal_min_rise", os.getenv("RSI_BUY_REVERSAL_MIN_RISE", 2.0)))
        rsi_now = float(df["RSI"].iloc[-1])
        rsi_prev = float(df["RSI"].iloc[-2])
        close_now = float(df["close"].iloc[-1])
        close_prev = float(df["close"].iloc[-2])
        open_now = float(df["open"].iloc[-1]) if "open" in df.columns else close_now
        return (
            rsi_prev <= max_level
            and rsi_now > rsi_prev
            and (rsi_now - rsi_prev) >= min_rise
            and close_now > open_now
            and close_now > close_prev
        )
    except (KeyError, TypeError, ValueError, IndexError):
        return False

def detect_breakout(
    df,
    breakout_margin=0.0005,
    rigor=None,
    regime=None,
    profile: dict | None = None,
    diagnostics: dict | None = None,
):
    """
    Detecta breakout com lógica de net score (score_buy - score_sell).
    Parametrizável via .env (min_score).
    Confirmações obrigatórias: Volume, RSI, EMAcurta/media (local) e EMAmedia/EMA_TREND (global, flexível!).
    """
    import os

    # O detector é a fonte canônica do estado estrutural do rompimento para
    # as etapas seguintes do mesmo ciclo. Sem isso, o score recalculava
    # `rompimento` com uma fórmula diferente e podia registrar CONTRA depois
    # de o detector ter confirmado o mesmo candle.
    try:
        df.attrs.pop("genesis_breakout", None)
    except Exception:
        pass

    def _set_failure(reason, **details):
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics.update({"primary_reason": reason, **details})

    # === Parâmetros das EMAs (agora totalmente flexíveis) ===
    profile = profile or {}
    EMA_CURTA = int(profile.get("ema_curta", os.getenv("EMA_CURTA", 20)))
    EMA_MEDIA = int(profile.get("ema_media", os.getenv("EMA_MEDIA", 50)))
    EMA_LONGA = int(profile.get("ema_longa", os.getenv("EMA_LONGA", 200)))
    EMA_TREND = int(profile.get("ema_trend", os.getenv("EMA_TREND", EMA_LONGA)))

    col_ema_curta = f"EMA{EMA_CURTA}"
    col_ema_media = f"EMA{EMA_MEDIA}"
    col_ema_longa = f"EMA{EMA_LONGA}"
    col_ema_trend = f"EMA{EMA_TREND}"

    # 🔁 Se rigor não for passado, usa o do .env
    if rigor is None:
        rigor = str(profile.get("modo_validacao", os.getenv("MODO_VALIDACAO", "normal"))).strip().lower()

    # 🔁 Parametrização do min_score via .env
    mapa_min_score = {
        "agressivo": int(profile.get("min_score_breakout_agressivo", os.getenv("MIN_SCORE_BREAKOUT_AGRESSIVO", 1))),
        "moderado": int(profile.get("min_score_breakout_moderado", os.getenv("MIN_SCORE_BREAKOUT_MODERADO", 2))),
        "conservador": int(profile.get("min_score_breakout_conservador", os.getenv("MIN_SCORE_BREAKOUT_CONSERVADOR", 3))),
    }
    min_score = mapa_min_score.get(rigor, 2)
    min_confirmacoes = int(profile.get("filtro_breakout_min_confirmacoes", os.getenv("FILTRO_BREAKOUT_MIN_CONFIRMACOES", 2)))
    if regime in (1, 2, 3, 4):
        perfil = get_entry_profile(regime)
        if not perfil["breakout_enabled"]:
            logging.info(
                "[BREAKOUT] Bloqueado pelo perfil do regime %s (%s).",
                regime,
                perfil["description"],
            )
            _set_failure("REGIME_NOT_ELIGIBLE", regime=regime)
            return None
        min_confirmacoes = int(perfil["breakout_confirmations"])

    # 🔒 Sanidade dos dados
    if "Resistance" not in df.columns or "Support" not in df.columns:
        logging.error("❌ Colunas 'Resistance' ou 'Support' não encontradas.")
        _set_failure("BREAKOUT_DATA_INVALID")
        return None
    if len(df) < 2:
        logging.warning("⚠️ Dados insuficientes para detectar breakout.")
        _set_failure("BREAKOUT_DATA_INSUFFICIENT")
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
            _set_failure("BREAKOUT_AMBIGUOUS", score_buy=score_buy, score_sell=score_sell)
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
        resultado_final = "bullish"; logging.info("🔥 [BREAKOUT_DETECTOR] COMPRA confirmada")
    elif net_score <= -min_score:
        resultado_final = "bearish"; logging.info("🔥 [BREAKOUT_DETECTOR] VENDA confirmada")
    else:
        logging.info(f"⚠️ Nenhum breakout confirmado (net score={net_score}, min={min_score})")
        _set_failure(
            "PRICE_LEVEL_NOT_BROKEN" if score_buy == 0 and score_sell == 0 else "BREAKOUT_SCORE_INSUFFICIENT",
            score_buy=score_buy,
            score_sell=score_sell,
            net_score=net_score,
            min_score=min_score,
        )
        return None

    # === Confirmações adicionais obrigatórias ===
    ema_curta = df[col_ema_curta].iloc[-1] if col_ema_curta in df.columns else close
    ema_media = df[col_ema_media].iloc[-1] if col_ema_media in df.columns else close
    ema_longa = df[col_ema_longa].iloc[-1] if col_ema_longa in df.columns else close
    ema_trend = df[col_ema_trend].iloc[-1] if col_ema_trend in df.columns else ema_longa

    cond_volume = volume > volume_medio
    if resultado_final == "bullish":
        # Recuperação de sobrevenda pode validar o componente RSI, mas exige
        # candle de reversão; sobrevenda isolada nunca compra.
        cond_rsi = rsi > 50 or _rsi_buy_reversal_confirmed(df, profile)
    else:
        # Uma venda com RSI extremo é tardia e fica bloqueada nesta etapa.
        cond_rsi = rsi < 50 and not _rsi_extreme_sell_block(df, profile)

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
    FILTRO_BREAKOUT_MIN_CONFIRMACOES = min_confirmacoes

    # Soma quantos deram True
    num_confirmacoes = sum([cond_volume, cond_rsi, cond_tendencia])

    if num_confirmacoes < FILTRO_BREAKOUT_MIN_CONFIRMACOES:
        logging.warning(
            f"🚫 Breakout rejeitado: confirmações insuficientes "
            f"(Volume={cond_volume}, RSI={cond_rsi}, Tendência={cond_tendencia}) | "
            f"Requerido: {FILTRO_BREAKOUT_MIN_CONFIRMACOES}, Obtido: {num_confirmacoes}"
        )
        failure_reasons = []
        if not cond_volume:
            failure_reasons.append("VOLUME_NOT_CONFIRMED")
        if not cond_rsi:
            failure_reasons.append("RSI_NOT_CONFIRMED")
        if not cond_tendencia:
            failure_reasons.append("TREND_NOT_CONFIRMED")
        primary_reason = (
            "TREND_NOT_CONFIRMED" if "TREND_NOT_CONFIRMED" in failure_reasons
            else failure_reasons[0] if failure_reasons
            else "BREAKOUT_CONFIRMATIONS_INSUFFICIENT"
        )
        _set_failure(
            primary_reason,
            failure_reasons=failure_reasons,
            confirmations=num_confirmacoes,
            required_confirmations=FILTRO_BREAKOUT_MIN_CONFIRMACOES,
        )
        return None

    # 🔥 Se chegou aqui, todas as confirmações necessárias foram atendidas.
    # Persistimos o estado no DataFrame para que o autorizador use exatamente
    # a mesma evidência, sem uma segunda definição conflitante de breakout.
    try:
        df.attrs["genesis_breakout"] = {
            "state": "CONFIRMED",
            "direction": resultado_final,
            "detector": "detect_breakout",
            "confirmations": num_confirmacoes,
            "required_confirmations": FILTRO_BREAKOUT_MIN_CONFIRMACOES,
            "score_buy": score_buy,
            "score_sell": score_sell,
            "net_score": net_score,
        }
    except Exception:
        logging.debug("Não foi possível anexar o estado canônico do breakout ao DataFrame", exc_info=True)
    logging.info(
        "✅ [BREAKOUT_STATE] detector=CONFIRMED | direcao=%s | confirmacoes=%s/%s",
        resultado_final.upper(),
        num_confirmacoes,
        FILTRO_BREAKOUT_MIN_CONFIRMACOES,
    )
    return resultado_final



def confirm_breakout(df, profile: dict | None = None):
    """Confirma o rompimento pelo fechamento do candle e pelo volume."""
    try:
        if df is None or len(df) < 2:
            return False
        close = float(df["close"].iloc[-1])
        resistance = float(df["Resistance"].iloc[-2])
        support = float(df["Support"].iloc[-2])
        volume = float(df["volume"].iloc[-1])
        volume_mean = float(df["volume"].rolling(20, min_periods=1).mean().iloc[-1])
        min_volume_ratio = float((profile or {}).get("breakout_min_volume_ratio", os.getenv("BREAKOUT_MIN_VOLUME_RATIO", 1.0)))
        close_breakout = close > resistance or close < support
        volume_confirmed = volume >= volume_mean * min_volume_ratio
        logging.info(
            "[BREAKOUT] Confirmação final | close_breakout=%s | volume=%.2fx média | requerido=%.2fx",
            close_breakout,
            (volume / volume_mean) if volume_mean > 0 else 0.0,
            min_volume_ratio,
        )
        return close_breakout and volume_confirmed
    except Exception as exc:
        logging.warning("[BREAKOUT] Falha na confirmação final: %s", exc)
        return False




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
    close = _safe_float(candle.get("close"), 0.0)
    atr = _safe_float(candle.get("ATR"), 0.0)
    if close <= 0 or atr < 0 or not np.isfinite(close) or not np.isfinite(atr):
        logging.warning("[VOLATILIDADE] close/ATR inválido; bloqueando avaliação para evitar NaN.")
        return False

    # === Carrega thresholds e modo do .env ===
    atr_min_pct = _safe_env_float("THRESHOLD_ATR_MIN", 0.001)  # ex: 0.0001 → 0.01%
    atr_max_pct = _safe_env_float("THRESHOLD_ATR_MAX", 0.012)  # ex: 0.60 → 60%
    force_aggressive = os.getenv("FORCE_AGGRESSIVE_MODE", "false").lower() == "true"
    modo_rigor = "agressivo" if force_aggressive else os.getenv("MODO_RIGOR", "moderado").lower()

    # === Cálculos base ===
    atr_frac = atr / close  # fração real (ex: 0.0023 = 0.23%)
    atr_pct = atr_frac * 100  # só pra exibir
    candle_open = _safe_float(candle.get("open"), close)
    candle_high = _safe_float(candle.get("high"), close)
    candle_low = _safe_float(candle.get("low"), close)
    corpo_frac = abs(close - candle_open) / (candle_high - candle_low + 1e-9)
    corpo_pct = corpo_frac * 100
    volume = _safe_float(candle.get("volume"), 0.0)
    volume_series = pd.to_numeric(df["volume"], errors="coerce")
    volume_mean = volume_series.rolling(20, min_periods=1).mean().iloc[-1]
    volume_mean = _safe_float(volume_mean, 0.0)
    volume_ratio = volume / (volume_mean + 1e-9) if volume_mean > 0 else 0.0
    if not np.isfinite(volume_ratio):
        volume_ratio = 0.0

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





def _validar_filtros_env_breakout(df):
    """
    Aplica filtros basicos do .env (ATR/RSI/VOLUME) para bloquear entrada.
    Usa ATR_PCT_MIN/MAX, RSI_MINIMO/MAXIMO e VOLUME_MINIMO quando definidos.
    """
    if df is None or df.empty:
        logging.warning("[BREAKOUT] Env filters: DataFrame vazio.")
        return False

    try:
        close = float(df["close"].iloc[-1])
        atr = float(df["ATR"].iloc[-1]) if "ATR" in df.columns else None
        rsi = float(df["RSI"].iloc[-1]) if "RSI" in df.columns else None
        volume = float(df["volume"].iloc[-1]) if "volume" in df.columns else None
    except Exception as e:
        logging.warning("[BREAKOUT] Env filters: falha ao ler indicadores: %s", e)
        return False

    atr_pct_min = os.getenv("ATR_PCT_MIN")
    atr_pct_max = os.getenv("ATR_PCT_MAX")
    rsi_min = os.getenv("RSI_MINIMO")
    rsi_max = os.getenv("RSI_MAXIMO")
    vol_min = os.getenv("VOLUME_MINIMO")

    motivos = []

    if atr is not None and close:
        atr_frac = atr / close
        if atr_pct_min:
            try:
                if atr_frac < float(atr_pct_min):
                    motivos.append(f"ATR_PCT_MIN ({atr_frac:.6f} < {atr_pct_min})")
            except Exception:
                pass
        if atr_pct_max:
            try:
                if atr_frac > float(atr_pct_max):
                    motivos.append(f"ATR_PCT_MAX ({atr_frac:.6f} > {atr_pct_max})")
            except Exception:
                pass

    if rsi is not None:
        if rsi_min:
            try:
                if rsi < float(rsi_min):
                    motivos.append(f"RSI_MINIMO ({rsi:.2f} < {rsi_min})")
            except Exception:
                pass
        if rsi_max:
            try:
                if rsi > float(rsi_max):
                    motivos.append(f"RSI_MAXIMO ({rsi:.2f} > {rsi_max})")
            except Exception:
                pass

    if volume is not None and vol_min:
        try:
            if volume < float(vol_min):
                motivos.append(f"VOLUME_MINIMO ({volume:.2f} < {vol_min})")
        except Exception:
            pass

    if motivos:
        logging.warning("[BREAKOUT] Env filters bloqueou entrada: %s", "; ".join(motivos))
        return False

    return True


def eth_bearish_buy_blocked(df, side, symbol=None):
    """Bloqueia BUY de Breakout no ETH contra a tendência longa baixista."""
    active_symbol = (symbol or os.getenv("SYMBOL", "") or os.getenv("ATIVOS", "") or os.getenv("PAR", "")).strip().upper()
    enabled = os.getenv("ETH_BLOCK_BUY_BREAKOUT_BEARISH", "false").strip().lower() in ("1", "true", "yes", "on")
    if not enabled or active_symbol not in {"ETH", "ETHUSDT"} or str(side).lower() != "buy":
        return False

    def _last(*names):
        for name in names:
            if name in df.columns:
                try:
                    value = float(df[name].iloc[-1])
                    if math.isfinite(value):
                        return value
                except (TypeError, ValueError, IndexError):
                    pass
        return None

    ema_trend_fast = _last("EMA_50", "EMA50")
    ema_trend_slow = _last("EMA_200", "EMA200")
    close = _last("close")
    bearish = (
        close is not None
        and ema_trend_fast is not None
        and ema_trend_slow is not None
        and ema_trend_fast < ema_trend_slow
        and close < ema_trend_fast
    )
    if bearish:
        logging.info(
            "[ETH PROTEÇÃO] BUY de BREAKOUT bloqueado: "
            "EMA50<EMA200 e preço abaixo da EMA50."
        )
    return bearish


def calculate_stop_take_trailing_legacy(price, atr_value, side="buy", api=None, symbol=None):
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


def calculate_stop_take_trailing(price, atr_value, side="buy", api=None, symbol=None, df=None):
    """
    Calcula Stop-Loss e Trailing Stop usando tese invalidada.
    Take-Profit fixo e removido (None).
    """
    import os

    TRAILING_ATR_MULT = float(os.getenv("TRAILING_ATR_MULT", 0.8))
    TESE_ATR_MULT = float(os.getenv("TESE_ATR_MULT", 2.0))
    SR_LOOKBACK = int(os.getenv("SR_LOOKBACK", 20))

    price = float(price)
    atr_value = abs(float(atr_value))

    if atr_value > price * 0.2:
        atr_value = atr_value / 100

    pivot_dist = 0.0
    if df is not None and "low" in df.columns and "high" in df.columns:
        try:
            if side.lower() == "buy":
                pivot = float(df["low"].rolling(SR_LOOKBACK).min().iloc[-2])
                pivot_dist = max(0.0, price - pivot)
            else:
                pivot = float(df["high"].rolling(SR_LOOKBACK).max().iloc[-2])
                pivot_dist = max(0.0, pivot - price)
        except Exception:
            pivot_dist = 0.0

    stop_dist = max(atr_value * TESE_ATR_MULT, pivot_dist)
    trailing_dist = stop_dist * TRAILING_ATR_MULT

    if side.lower() == "buy":
        stop_loss = price - stop_dist
        trailing_stop = price - trailing_dist
    else:
        stop_loss = price + stop_dist
        trailing_stop = price + trailing_dist

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
        casas = str(tick_size)[::-1].find(".")
        return round(round(valor / tick_size) * tick_size, casas)

    stop_loss = ajustar_tick(stop_loss)
    trailing_stop = ajustar_tick(trailing_stop)

    take_profit = None
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


def executar_backtest_breakout(
    df_original,
    lado_permitido=None,
    regime_override=None,
    symbol=None,
    capital_inicial=10000.0,
    record_provenance="BACKTEST_RUNTIME",
    evaluation_context="BACKTEST_RUNTIME",
    run_id=None,
    natural_runtime_context=None,
    impulse_reset_policy="CURRENT",
    trend_policy="CURRENT",
    persist_observability=True,
    decision_trace=None,
    trace_candle_open_time=None,
    strategy_profile=None,
):
    trades = []
    strategy_profile = strategy_profile or getattr(df_original, "attrs", {}).get("strategy_profile")
    strategy_profile = strategy_profile or resolve_strategy_profile(symbol, "5m")
    natural_runtime_context = natural_runtime_context if isinstance(natural_runtime_context, dict) else None
    natural_runtime = bool(
        natural_runtime_context
        and natural_runtime_context.get("record_provenance") == "PRODUCTION_RUNTIME"
        and natural_runtime_context.get("evaluation_context") == "PRODUCTION_RUNTIME"
    )
    breakout_candle_context = None
    if natural_runtime:
        # Production Breakout must never consume the currently-forming 1m row.
        # Historical/backtest paths intentionally remain unchanged.
        closed_1m = select_closed_candles(df_original, 1)
        if closed_1m is None:
            logging.warning("[BREAKOUT] No fully closed 1m candle available; skipping production evaluation.")
            return {"estrategia": "BREAKOUT", "sem_trade": True, "motivo": "NO_CLOSED_1M_CANDLE"}
        df_original, breakout_candle_context = closed_1m
    backtest_run_id = run_id or f"backtest:{uuid.uuid4().hex}"
    import logging
    from copy import deepcopy
    import numpy as np

    trace_target = str(trace_candle_open_time) if trace_candle_open_time is not None else None

    def _capture_decision(evaluation):
        if decision_trace is None:
            return
        candle_key = str(evaluation.candle_open_time) if evaluation.candle_open_time is not None else None
        if trace_target is not None and candle_key != trace_target:
            return
        decision_trace.append({
            "evaluation_id": evaluation.breakout_evaluation_id,
            "symbol": evaluation.symbol,
            "timeframe": evaluation.timeframe,
            "candle_open_time": evaluation.candle_open_time,
            "candle_close_time": evaluation.candle_close_time,
            "regime": int(evaluation.regime) if str(evaluation.regime).isdigit() else evaluation.regime,
            "stage_results": dict(evaluation.stage_results),
            "primary_blocker": evaluation.primary_blocker,
            "raw_breakout": bool(evaluation.raw_breakout),
            "direction_candidate": evaluation.direction_candidate,
            "final_state": evaluation.final_state,
            "feature_vector": dict(evaluation.feature_vector),
            "trend_forensics": dict(getattr(evaluation, "trend_forensics", {}) or {}),
        })

    logging.info("🚀 Iniciando backtest otimizado da estratégia Breakout...")

    df = deepcopy(df_original).reset_index(drop=True)
    df.attrs["strategy_profile"] = dict(strategy_profile)
    df = calculate_breakout_levels(df, period=int(strategy_profile.get("breakout_level_lookback", 5)))
    df = calculate_indicators(df, profile=strategy_profile)

    breakout_lineage = {}
    if natural_runtime:
        breakout_lineage = {
            "breakout_timeframe": "1",
            "breakout_candle_open_time": breakout_candle_context.get("candle_open_time"),
            "breakout_candle_close_time": breakout_candle_context.get("candle_close_time"),
            "breakout_candle_closed": True,
            "canonical_regime_timeframe": "5",
            "canonical_regime_candle_open_time": natural_runtime_context.get("candle_open_time"),
            "canonical_regime_candle_close_time": natural_runtime_context.get("candle_close_time"),
        }

    # ================= REGIME GATE - BREAKOUT =================
    try:
        symbol_env = (symbol or os.getenv("SYMBOL", "") or os.getenv("ATIVOS", "") or os.getenv("PAR", "")).strip().upper()
        if regime_override in (1, 2, 3, 4):
            regime_atual = int(regime_override)
            logging.info(f"[BREAKOUT] Regime override aplicado: {regime_atual}")
        else:
            regime_atual = RegimeDetector().detectar(df, symbol=symbol_env or "DESCONHECIDO")
    except Exception as e:
        logging.warning(f"[BREAKOUT] Falha ao detectar regime, usando fallback 2: {e}")
        regime_atual = 2

    regime_dinamico = regime_override not in (1, 2, 3, 4) and os.getenv("BACKTEST_DYNAMIC_REGIME", "true").strip().lower() in ("1", "true", "yes", "on")
    if regime_atual not in (1, 2) and not regime_dinamico:
        logging.info(
            f"[BREAKOUT] Bloqueado por regime: {regime_atual} "
            f"(somente 1/2 permitem breakout)."
        )
        if natural_runtime:
            # A natural closed candle is still an analytical observation when
            # the canonical regime blocks Breakout.  Persist the failed gate
            # instead of silently dropping the candle from observability.
            key = natural_runtime_context.get("analysis_key")
            evaluation = BreakoutEvaluation(**build_breakout_evaluation_kwargs(
                symbol=symbol_env or "UNKNOWN", timeframe=os.getenv("INTERVAL", "5"),
                regime=regime_atual,
                record_provenance="PRODUCTION_RUNTIME",
                evaluation_context="PRODUCTION_RUNTIME",
                run_id=natural_runtime_context.get("run_id"),
                breakout_evaluation_id=deterministic_breakout_evaluation_id(key) if key else None,
                persist=persist_observability,
                evaluation_candle=natural_runtime_context,
                natural_context=True,
                breakout_lineage=breakout_lineage,
            ))
            evaluation._trace_callback = _capture_decision
            evaluation.feature_vector = {
                name: (float(df[name].iloc[-1]) if name in df.columns and pd.notna(df[name].iloc[-1]) else None)
                for name in ("open", "high", "low", "close", "volume", "ATR", "RSI", "EMA12", "EMA26", "EMA50", "EMA200")
            }
            evaluation.stage("REGIME_ELIGIBLE", "FAIL", observed_value=regime_atual,
                             threshold="1|2", operator="in", reason_code="REGIME_NOT_ELIGIBLE")
            evaluation.finish("EVALUATED")
            return {
                "estrategia": "BREAKOUT",
                "sem_trade": True,
                "motivo": f"regime_{regime_atual}_bloqueado",
                "regime": regime_atual,
            }
        evaluation = BreakoutEvaluation(
            symbol=symbol_env or "UNKNOWN", timeframe=os.getenv("INTERVAL", "5"), regime=regime_atual,
            record_provenance=record_provenance, evaluation_context=evaluation_context,
            run_id=backtest_run_id,
            **derive_candle_identity(df, os.getenv("INTERVAL", "5"), symbol=symbol_env or "UNKNOWN", candle_closed=True),
        )
        evaluation._trace_callback = _capture_decision
        evaluation.stage("REGIME_ELIGIBLE", "FAIL", observed_value=regime_atual,
                         threshold="1|2", operator="in", reason_code="REGIME_NOT_ELIGIBLE")
        evaluation.finish("EVALUATED")
        return {
            "estrategia": "BREAKOUT",
            "sem_trade": True,
            "motivo": f"regime_{regime_atual}_bloqueado",
            "regime": regime_atual
        }

    # === BLINDAGEM DE TIPOS ===
    colunas_numericas = [
        "open", "high", "low", "close", "volume", "turnover",
        "RSI", "ATR", "EMA50", "EMA200", "SMA_10", "SMA_50",
        "Bollinger_Upper", "Bollinger_Lower", "Resistance", "Support"
    ]
    df = converter_colunas_para_float(df, colunas_numericas)

    saldo_inicial = float(capital_inicial)
    saldo = saldo_inicial
    resultados = []
    lucros = []
    perdas = []

    num_trades = 0
    num_wins = 0
    num_losses = 0
    maior_pico = saldo
    max_drawdown = 0
    trailing_activation_events = 0
    trailing_status_count = 0
    trailing_status_true_count = 0
    trailing_check_count = 0
    trailing_offset_samples = []
    exit_counts = {"take": 0, "stop": 0, "timeout": 0, "stop_gain": 0}
    sinais_autorizados = {"buy": 0, "sell": 0}
    ultimo_lado_autorizado = None
    ultimo_score_autorizado = 0.0
    ultimo_score_pct_autorizado = 0.0
    max_score_autorizado = 0.0
    max_score_pct_autorizado = 0.0
    melhor_lado_autorizado = None
    ultimo_motivo_final = "sem_sinal_autorizado"
    last_breakout_evaluation_id = None


    for i in range(20, len(df)):
        df_slice = df.iloc[:i+1].copy()
        natural_target = natural_runtime and i == len(df) - 1
        evaluation_context_data = natural_runtime_context if natural_target else None
        evaluation_values = evaluation_context_data or {}
        evaluation_candle = evaluation_context_data or derive_candle_identity(
            df_slice, os.getenv("INTERVAL", "5"), symbol=symbol_env or "UNKNOWN", candle_closed=True
        )
        evaluation_analysis_key = evaluation_values.get("analysis_key")
        evaluation_id = None
        if natural_target and evaluation_analysis_key:
            evaluation_id = deterministic_breakout_evaluation_id(evaluation_analysis_key)
        evaluation = BreakoutEvaluation(**build_breakout_evaluation_kwargs(
            symbol=symbol_env or "UNKNOWN",
            timeframe=os.getenv("INTERVAL", "5"),
            regime=regime_atual,
            record_provenance=(evaluation_values.get("record_provenance") if evaluation_context_data else record_provenance),
            evaluation_context=(evaluation_values.get("evaluation_context") if evaluation_context_data else evaluation_context),
            run_id=(evaluation_values.get("run_id") if evaluation_context_data else backtest_run_id),
            breakout_evaluation_id=evaluation_id,
            persist=(persist_observability and (not natural_runtime or natural_target)),
            evaluation_candle=evaluation_candle,
            natural_context=bool(evaluation_context_data),
            breakout_lineage=breakout_lineage if natural_target else None,
        ))
        evaluation._trace_callback = _capture_decision
        evaluation.feature_vector = {
            name: (float(df_slice[name].iloc[-1]) if name in df_slice.columns and pd.notna(df_slice[name].iloc[-1]) else None)
            for name in ("open", "high", "low", "close", "volume", "ATR", "RSI", "EMA12", "EMA26", "EMA50", "EMA200", "Resistance", "Support")
        }
        evaluation.feature_vector.update({
            "strategy_profile_version": strategy_profile.get("version"),
            "strategy_profile_hash": strategy_profile.get("config_hash"),
        })
        evaluation.trend_forensics = build_trend_forensics(
            df_slice, None, state="NOT_REACHED", failure_reason=None
        )
        authority = regime_authority_metadata(
            natural_runtime_context=evaluation_context_data,
            regime_recalculated=regime_dinamico,
        )
        evaluation.stage_results["REGIME_AUTHORITY"] = {
            "result": "PASS",
            "observed_value": regime_atual,
            "threshold": None,
            "operator": None,
            "reason_code": authority["regime_source"],
            **authority,
        }
        evaluation.stage("REGIME_ELIGIBLE", "NOT_APPLICABLE" if regime_dinamico else ("PASS" if breakout_regime_eligible(regime_atual) else "FAIL"),
                         observed_value=regime_atual, threshold="1|2", operator="in")
        shadow_no_impulse_reset = str(impulse_reset_policy or "CURRENT").strip().upper() == "SHADOW_NO_IMPULSE_RESET"
        if shadow_no_impulse_reset:
            # Shadow/paper comparison only. The production runtime keeps the
            # existing impulse reset policy because the default remains CURRENT.
            df_slice, pronto_reset = df_slice, True
        else:
            df_slice, pronto_reset = _apply_impulse_reset(df_slice, strategy_profile)
        if not pronto_reset:
            evaluation.trend_forensics = build_trend_forensics(
                df_slice, None, state="NOT_REACHED", failure_reason="IMPULSE_RESET_WAIT"
            )
            evaluation.primary_blocker = "IMPULSE_RESET_WAIT"
            evaluation.stage("STRUCTURE_AVAILABLE", "NOT_APPLICABLE", reason_code="IMPULSE_RESET_WAIT")
            evaluation.finish("EVALUATED")
            continue
        df_slice = calculate_breakout_levels(df_slice, period=int(strategy_profile.get("breakout_level_lookback", 5)))
        structure_ok = all(name in df_slice.columns for name in ("Resistance", "Support"))
        evaluation.stage("STRUCTURE_AVAILABLE", "PASS" if structure_ok else "FAIL",
                         reason_code=None if structure_ok else "NO_STRUCTURE")
        if not structure_ok:
            evaluation.finish("EVALUATED")
            continue
        #df_slice = aplicar_regras_validacao_breakout(df_slice)

        #if not df_slice['validacao_final'].iloc[-1]:
            #logging.info("⚠️ Breakout não validado. Ignorando candle no backtest.")
            #continue

        preco_atual = df_slice["close"].iloc[-1]
        regime_candle = regime_atual
        if regime_dinamico:
            try:
                regime_candle = RegimeDetector().detectar(df_slice, symbol=symbol_env or "DESCONHECIDO")
            except Exception:
                regime_candle = regime_atual
            if not breakout_regime_eligible(regime_candle):
                evaluation.stage("REGIME_ELIGIBLE", "FAIL", observed_value=regime_candle,
                                 threshold="1|2", operator="in", reason_code="REGIME_NOT_ELIGIBLE")
                evaluation.finish("EVALUATED")
                continue
            evaluation.stage("REGIME_ELIGIBLE", "PASS", observed_value=regime_candle,
                             threshold="1|2", operator="in")
        breakout_diagnostics = {}
        breakout = detect_breakout(
            df_slice,
            regime=regime_candle,
            profile=strategy_profile,
            diagnostics=breakout_diagnostics,
        )
        evaluation.stage("PRICE_BREAKOUT", "PASS" if breakout else "FAIL", observed_value=breakout,
                         operator="not_null",
                         reason_code=None if breakout else breakout_diagnostics.get("primary_reason", "PRICE_LEVEL_NOT_BROKEN"))
        if not breakout:
            evaluation.finish("EVALUATED")
            continue
        evaluation.raw_breakout = True
        evaluation.final_state = "RAW_BREAKOUT_DETECTED"

        confirmacao = confirm_breakout(df_slice, profile=strategy_profile)
        volume_value = float(df_slice["volume"].iloc[-1]) if "volume" in df_slice.columns else None
        volume_mean = float(df_slice["volume"].rolling(20, min_periods=1).mean().iloc[-1]) if "volume" in df_slice.columns else None
        evaluation.stage("VOLUME_CONFIRMATION", "PASS" if confirmacao else "FAIL",
                         observed_value=(volume_value / volume_mean if volume_mean else None),
                         threshold=str(strategy_profile.get("breakout_min_volume_ratio", 1.0)), operator=">=",
                         reason_code=None if confirmacao else "VOLUME_NOT_CONFIRMED")
        if not confirmacao:
            evaluation.finish("RAW_BREAKOUT_DETECTED")
            continue

        # ✅ Define o lado do trade com base no breakout
        side = "buy" if breakout == "bullish" else "sell"
        evaluation.direction_candidate = side.upper()
        evaluation.stage("DIRECTION_DETERMINED", "PASS", observed_value=side, operator="in")

        if eth_bearish_buy_blocked(df_slice, side, symbol_env):
            evaluation.primary_blocker = "ETH_PROTECTION_TREND"
            evaluation.stage("EMA_TREND_CONFIRMATION", "FAIL", reason_code="ETH_PROTECTION_TREND")
            evaluation.finish("RAW_BREAKOUT_DETECTED")
            continue

        # 🛡️ FILTRO CRÍTICO: Obedecer apenas o lado aprovado
        if lado_permitido is not None:
            if side != lado_permitido:
                logging.debug(
                    f"🚫 Breakout gerou {side.upper()}, mas lado permitido é {lado_permitido.upper()}. "
                    f"Ignorando sinal."
                )
                evaluation.primary_blocker = "DIRECTION_NOT_ALLOWED"
                evaluation.stage("EMA_TREND_CONFIRMATION", "NOT_APPLICABLE", reason_code="DIRECTION_NOT_ALLOWED")
                evaluation.finish("RAW_BREAKOUT_DETECTED")
                continue


        # 🔒 Confirmação adicional: candle anterior não rompeu e o atual rompeu
        resistencia = df_slice["Resistance"].iloc[-2]
        suporte = df_slice["Support"].iloc[-2]
        close_atual = df_slice["close"].iloc[-1]
        close_anterior = df_slice["close"].iloc[-2]

        # O pullback deixou de ser um bloqueio antecipado. Primeiro avaliamos
        # tendência e score; um rompimento forte pode entrar em continuação,
        # enquanto um sinal fraco ainda precisa do pullback.
        if side == "buy":
            pullback_confirmed = close_anterior < resistencia and close_atual > resistencia
        elif side == "sell":
            pullback_confirmed = close_anterior > suporte and close_atual < suporte
        else:
            pullback_confirmed = False
            logging.warning(f"⚠️ Direção '{side}' desconhecida ao validar pullback.")


        sentimento_geral = "neutro"
        if os.getenv("BACKTEST_USE_REAL_SENTIMENT", "0").strip().lower() in ("1", "true", "yes"):
            try:
                sentimento_geral = obter_sentimento_combinado()
            except Exception:
                sentimento_geral = "neutro"

        score_auth = autorizar_score_breakout(
            df_slice,
            side=side,
            sentimento_geral=sentimento_geral,
            symbol=symbol,
            regime_override=regime_override,
        )
        entrada_valida = bool(score_auth.get("autorizado"))
        score_value = float(score_auth.get("score_calculado", 0) or 0)
        score_min_value = float(score_auth.get("score_minimo", 0) or 0)
        score_pct = float(score_auth.get("confianca_percentual", 0) or 0)
        score_criteria = score_auth.get("criterios") or {}
        trend_policy_name = str(trend_policy or "CURRENT").strip().upper()
        if trend_policy_name == "SHADOW_FAST_TREND":
            ema_fast_col = "EMA_12" if "EMA_12" in df_slice.columns else "EMA12"
            ema_slow_col = "EMA_26" if "EMA_26" in df_slice.columns else "EMA26"
            fast_value = float(df_slice[ema_fast_col].iloc[-1]) if ema_fast_col in df_slice.columns else None
            slow_value = float(df_slice[ema_slow_col].iloc[-1]) if ema_slow_col in df_slice.columns else None
            if fast_value is None or slow_value is None:
                trend_ok = False
            elif side == "buy":
                trend_ok = fast_value > slow_value
            else:
                trend_ok = fast_value < slow_value
        else:
            trend_ok = bool(score_criteria.get("ema", False))
        evaluation.trend_forensics = build_trend_forensics(
            df_slice, side, state="PASS" if trend_ok else "FAIL",
            failure_reason=None if trend_ok else "TREND_NOT_CONFIRMED",
        )
        evaluation.stage("EMA_TREND_CONFIRMATION", "PASS" if trend_ok else "FAIL",
                         observed_value=trend_ok, threshold=True, operator="==",
                         reason_code=None if trend_ok else "TREND_NOT_CONFIRMED")
        evaluation.stage("WEIGHTED_SCORE", "PASS" if entrada_valida else "FAIL",
                         observed_value=score_value, threshold=score_min_value, operator=">=",
                         reason_code=None if entrada_valida else "SCORE_TOO_LOW")
        evaluation.stage("REGIME_MIN_SCORE", "PASS" if entrada_valida else "FAIL",
                         observed_value={"score": score_value, "score_pct": score_pct},
                         threshold={"score": score_min_value}, operator=">=",
                         reason_code=None if entrada_valida else "SCORE_TOO_LOW")

        strong_breakout = bool(entrada_valida and trend_ok and confirmacao)
        bypass_pullback = os.getenv(
            "BREAKOUT_STRONG_SIGNAL_BYPASS_PULLBACK", "1"
        ).strip().lower() in ("1", "true", "yes", "on")
        if not pullback_confirmed and not (bypass_pullback and strong_breakout):
            logging.warning(
                "⚠️ Breakout sem pullback confirmado e sem força suficiente; entrada bloqueada."
            )
            evaluation.primary_blocker = "PULLBACK_NOT_CONFIRMED"
            evaluation.finish("RAW_BREAKOUT_DETECTED")
            continue
        if not pullback_confirmed:
            logging.info(
                "⚡ Breakout forte autorizado sem pullback: tendência, score e volume confirmados."
            )

        logging.info(f"✅ Confirmação de entrada para {side.upper()} no candle {i}: {entrada_valida}")
        if not entrada_valida:
            logging.warning(f"🚫 Entrada rejeitada no candle {i}. Score insuficiente ou falha em critério.")

            criterios = calcular_score_completo(df_slice, side=side, sentimento_geral=sentimento_geral)
            score_final = criterios.pop("score", 0)

            score_minimo, _, modo_validacao = obter_config_validacao()
            logging.warning(f"🔎 Score obtido: {score_final}/{score_minimo} (Modo de Validação: {modo_validacao})")

            for crit, status in criterios.items():
                logging.info(f"{'✅' if status else '❌'} Critério: {crit} -> {'Passou' if status else 'Falhou'}")

            evaluation.finish("RAW_BREAKOUT_DETECTED")
            continue
        else:
            sinais_autorizados[side] += 1
            ultimo_lado_autorizado = side
            ultimo_score_autorizado = float(score_auth.get("score_calculado", 0) or 0)
            ultimo_score_pct_autorizado = float(score_auth.get("confianca_percentual", 0) or 0)
            if ultimo_score_autorizado >= max_score_autorizado:
                max_score_autorizado = float(ultimo_score_autorizado)
                max_score_pct_autorizado = float(ultimo_score_pct_autorizado)
                melhor_lado_autorizado = side
            ultimo_motivo_final = "sinal_autorizado"





        entry_delay = max(1, int(os.getenv("BACKTEST_ENTRY_DELAY_CANDLES", "1")))
        entry_idx = min(i + entry_delay, len(df) - 1)
        entry_price = float(df["open"].iloc[entry_idx])
        if str(os.getenv("USAR_ENTRADA_CONFIRMADA", "True")).strip().lower() in ("1", "true", "yes"):
            tr_valor = float(df_slice["TrueRange"].iloc[-1]) if "TrueRange" in df_slice.columns else float(df_slice["ATR"].iloc[-1])
            preco_confirmacao = calcular_preco_confirmacao(
                preco_breakout=preco_atual,
                side=side,
                tr=tr_valor,
                fator_confirmacao=FATOR_CONFIRMACAO_TR,
            )
            intervalo_min = int(os.getenv("INTERVAL", 5))
            max_wait_candles = max(1, int(TIMEOUT_ENTRADA_SEGUNDOS / max(intervalo_min, 1) / 60))
            trigger_hit = False
            for k in range(i + entry_delay, min(i + max_wait_candles, len(df) - 1) + 1):
                if side == "buy" and float(df["high"].iloc[k]) >= preco_confirmacao:
                    entry_idx = k
                    entry_price = preco_confirmacao
                    trigger_hit = True
                    break
                if side == "sell" and float(df["low"].iloc[k]) <= preco_confirmacao:
                    entry_idx = k
                    entry_price = preco_confirmacao
                    trigger_hit = True
                    break
            if not trigger_hit:
                logging.info("⏳ Entrada confirmada não disparou no backtest (timeout).")
                ultimo_motivo_final = "confirmacao_timeout_sem_execucao"
                evaluation.stage("TRIGGER_CONFIRMATION", "FAIL", observed_value=False,
                                 threshold=True, operator="==", reason_code="TRIGGER_TIMEOUT")
                evaluation.finish("QUALIFIED_SIGNAL")
                continue

            evaluation.stage("TRIGGER_CONFIRMATION", "PASS", observed_value=True, threshold=True, operator="==")
        else:
            evaluation.stage("TRIGGER_CONFIRMATION", "NOT_APPLICABLE", reason_code="CONFIRMATION_DISABLED")

        df_entry = df.iloc[: entry_idx + 1].copy()
        atr_value = df_entry["ATR"].iloc[-1]
        stop_loss, take_profit, tipo_stop, contexto = calcular_stop_take_adaptativo(
            df_entry,
            entry_price,
            side,
            params={"tese_stop": False, "rrr": BREAKOUT_RRR_ALVO},
        )


        # 📊 Calcular e validar RRR
        rrr = float(os.getenv("RRR_ALVO", os.getenv("RRR", 2.2))) if take_profit is None else calcular_rrr(entry_price, stop_loss, take_profit, min_rrr=1.5)
        modo_operacao = os.getenv("MODO_VALIDACAO", "agressivo").lower()

        # --- Verificação robusta de RRR ---
        if rrr is None:
            logging.warning({
                "timestamp": str(datetime.now()),
                "motivo": "RRR não pôde ser calculado (None)",
                "rrr": rrr,
                "preco_atual": entry_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "side": side,
                "EMA50": ema50,
                "EMA200": ema200,
            })
            logging.info("\u26a0\ufe0f [SCORE] Score N\u00c3O FOI CONSULTADO (fluxo vetado anteriormente)")
            evaluation.stage("RRR_VALIDATION", "FAIL", observed_value=None,
                             threshold=BREAKOUT_MIN_RRR, operator=">=", reason_code="RRR_UNAVAILABLE")
            evaluation.finish("TRIGGER_CONFIRMED")
            return None, None, None

        # BREAKOUT nunca opera abaixo do RRR mínimo, inclusive em modo agressivo.
        if rrr < BREAKOUT_MIN_RRR:
            logging.warning({
                "timestamp": str(datetime.now()),
                "motivo": f"RRR ({rrr:.2f}) abaixo do mínimo permitido para modo '{modo_operacao}'",
                "preco_atual": entry_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "side": side,
                "EMA50": ema50,
                "EMA200": ema200,
            })
            evaluation.stage("RRR_VALIDATION", "FAIL", observed_value=rrr,
                             threshold=BREAKOUT_MIN_RRR, operator=">=", reason_code="RRR_REJECTED")
            evaluation.finish("TRIGGER_CONFIRMED")
            return None, None, None
        else:
            logging.info(f"✅ RRR válido: {rrr:.2f} | Modo: {modo_operacao}")
            evaluation.stage("RRR_VALIDATION", "PASS", observed_value=rrr,
                             threshold=BREAKOUT_MIN_RRR, operator=">=")

        # --- Gestao de risco (tamanho do lote por trade) ---
        risco_percentual = float(os.getenv("RISCO_PERCENTUAL", 0.004))
        risco_em_usdt = saldo * risco_percentual
        stop_distance = abs(entry_price - stop_loss)
        if stop_distance <= 0:
            logging.warning("Stop distance invalida no backtest. Ignorando trade.")
            continue

        qty = calcular_tamanho_lote(
            risco_em_usdt=risco_em_usdt,
            stop_distance=stop_distance,
            preco_atual=entry_price,
            saldo_disponivel=saldo,
            fallback=0.0
        )
        if qty <= 0:
            logging.warning("Qty invalida no backtest. Ignorando trade.")
            evaluation.primary_blocker = "INVALID_QUANTITY"
            evaluation.finish("TRIGGER_CONFIRMED")
            continue

        # prossegue normalmente
        logging.info(f"📈 Entrada {side.upper()} no candle {entry_idx} em {entry_price:.2f}")
        ultimo_motivo_final = "entrada_disparada"
        num_trades += 1
        evaluation.stage("FINAL_ENTRY_ELIGIBLE", "PASS", observed_value=True,
                         threshold=True, operator="==")
        last_breakout_evaluation_id = evaluation.finish("TRIGGER_CONFIRMED")

        # --- Inicializações obrigatórias ---
        resultado = 0.0
        atingiu_tp = False
        atingiu_sl = False
        risk_dist = abs(entry_price - stop_loss)
        trailing_ativado = False
        be_ativado = False
        trailing_offset = risk_dist * float(os.getenv("TRAILING_R_MULT", os.getenv("TRAILING_ATR_MULT", 0.8)))
        be_r = float(os.getenv("BE_ACTIVATION_R", 1.2))
        trailing_r = float(os.getenv("TRAILING_ACTIVATION_R", 1.0))
        sr_lookback = int(os.getenv("SR_LOOKBACK", 20))
        last_top = None
        last_bottom = None
        if "high" in df.columns and "low" in df.columns and i - 1 >= 0:
            try:
                last_top = float(df["high"].rolling(sr_lookback).max().iloc[i-1])
                last_bottom = float(df["low"].rolling(sr_lookback).min().iloc[i-1])
            except Exception:
                last_top = None
                last_bottom = None

        trailing_status_count += 1
        logging.info(
            f"[TRAILING STATUS] entry_idx={entry_idx} side={side} ativo={trailing_ativado} "
            f"trailing_r={trailing_r:.2f} trailing_offset={trailing_offset:.6f} risk_dist={risk_dist:.6f}"
        )

        # === TIMEOUT DINÂMICO POR VOLATILIDADE (ATR) ===
        atr = df["ATR"].iloc[entry_idx]
        close_atual = df["close"].iloc[entry_idx]

        # Tempo de vida do trade (em candles) ajustado pela volatilidade
        # Nunca menos do que 15, nunca mais que 120 candles
        duracao_timeout = int(
            np.clip(
                (atr / close_atual) * 1_500_000,   # multiplicador profissional
                40,                                # mínimo realista
                400                                # máximo realista para M1
            )
        )

        limite_j = min(entry_idx + duracao_timeout, len(df))

        # === LOOP PRINCIPAL DO TRADE ===
        for j in range(entry_idx + 1, limite_j):
            preco_futuro = df["close"].iloc[j]
            movimento = (preco_futuro - entry_price) if side == "buy" else (entry_price - preco_futuro)
            r_mult = (movimento / risk_dist) if risk_dist > 0 else 0.0
            trailing_check_count += 1
            logging.info(
                f"[TRAILING CHECK] j={j} price={preco_futuro:.6f} r_mult={r_mult:.3f} "
                f"trailing_r={trailing_r:.2f} ativo={trailing_ativado} stop_loss={stop_loss:.6f} "
                f"trailing_offset={trailing_offset:.6f}"
            )

            if not be_ativado:
                be_trigger = r_mult >= be_r
                if side == "buy" and last_top is not None and preco_futuro > last_top:
                    be_trigger = True
                if side == "sell" and last_bottom is not None and preco_futuro < last_bottom:
                    be_trigger = True
                if be_trigger:
                    be_offset = float(os.getenv("BE_OFFSET_PCT", 0.0005))
                    if side == "buy":
                        stop_loss = max(stop_loss, entry_price * (1.0 + be_offset))
                    else:
                        stop_loss = min(stop_loss, entry_price * (1.0 - be_offset))
                    be_ativado = True

            if not trailing_ativado and r_mult >= trailing_r:
                trailing_ativado = True
                trailing_activation_events += 1
                trailing_status_true_count += 1
                trailing_offset_samples.append(trailing_offset)
                logging.info(
                    f"[TRAILING ATIVADO] j={j} side={side} r_mult={r_mult:.3f} "
                    f"offset={trailing_offset:.6f} stop_loss={stop_loss:.6f}"
                )

            if trailing_ativado and trailing_offset > 0:
                if side == "buy":
                    stop_loss = max(stop_loss, preco_futuro - trailing_offset)
                else:
                    stop_loss = min(stop_loss, preco_futuro + trailing_offset)

            # BUY
            qty_abs = abs(float(qty))

            if side == "buy":
                # Regra conservadora: se stop e take ocorrerem no mesmo candle,
                # assume-se o stop primeiro.
                if float(df["low"].iloc[j]) <= stop_loss:
                    resultado = (entry_price - stop_loss) * qty_abs
                    saldo -= resultado
                    perdas.append(resultado)
                    num_losses += 1
                    atingiu_sl = True
                    logging.info(f"[CONFLITO INTRABAR] BUY -> STOP primeiro (candle {j})")
                    trades.append({
                        "data": str(df["time"].iloc[j]),
                        "data_entrada": str(df["time"].iloc[entry_idx]),
                        "side": side,
                        "preco_entrada": entry_price,
                        "preco_saida": stop_loss,
                        "qty": qty,
                        "lucro": -resultado,
                        "motivo_saida": "stop",
                        "regime": regime_candle
                    })
                    exit_counts["stop"] += 1
                    break
                elif take_profit is not None and float(df["high"].iloc[j]) >= take_profit:
                    resultado = (take_profit - entry_price) * qty_abs
                    saldo += resultado
                    lucros.append(resultado)
                    num_wins += 1
                    atingiu_tp = True
                    logging.info(f"✅ LUCRO SIMULADO (candle {j}): +{resultado:.2f} | Saldo: {saldo:.2f}")
                    trades.append({
                        "data": str(df["time"].iloc[j]),
                        "data_entrada": str(df["time"].iloc[entry_idx]),
                        "side": side,
                        "preco_entrada": entry_price,
                        "preco_saida": take_profit,
                        "qty": qty,
                        "lucro": resultado,
                        "motivo_saida": "take"
                        ,"regime": regime_candle
                    })
                    exit_counts["take"] += 1
                    break

                elif False:
                    resultado = (entry_price - preco_futuro) * qty_abs
                    saldo -= resultado
                    perdas.append(resultado)
                    num_losses += 1
                    atingiu_sl = True
                    logging.info(f"❌ PREJUÍZO SIMULADO (candle {j}): -{abs(resultado):.2f} | Saldo: {saldo:.2f}")
                    trades.append({
                        "data": str(df["time"].iloc[j]),
                        "data_entrada": str(df["time"].iloc[entry_idx]),
                        "side": side,
                        "preco_entrada": entry_price,
                        "preco_saida": preco_futuro,
                        "qty": qty,
                        "lucro": -resultado,
                        "motivo_saida": "stop"
                        ,"regime": regime_candle
                    })
                    exit_counts["stop"] += 1
                    break

            # SELL
            else:
                # Regra conservadora: stop primeiro quando ambos os níveis
                # aparecem dentro do mesmo candle.
                if float(df["high"].iloc[j]) >= stop_loss:
                    resultado = (stop_loss - entry_price) * qty_abs
                    saldo -= resultado
                    perdas.append(resultado)
                    num_losses += 1
                    atingiu_sl = True
                    logging.info(f"[CONFLITO INTRABAR] SELL -> STOP primeiro (candle {j})")
                    trades.append({
                        "data": str(df["time"].iloc[j]),
                        "data_entrada": str(df["time"].iloc[entry_idx]),
                        "side": side,
                        "preco_entrada": entry_price,
                        "preco_saida": stop_loss,
                        "qty": qty,
                        "lucro": -resultado,
                        "motivo_saida": "stop",
                        "regime": regime_candle
                    })
                    exit_counts["stop"] += 1
                    break
                elif take_profit is not None and float(df["low"].iloc[j]) <= take_profit:
                    resultado = (entry_price - take_profit) * qty_abs
                    saldo += resultado
                    lucros.append(resultado)
                    num_wins += 1
                    atingiu_tp = True
                    logging.info(f"✅ LUCRO SIMULADO (candle {j}): +{resultado:.2f} | Saldo: {saldo:.2f}")
                    trades.append({
                        "data": str(df["time"].iloc[j]),
                        "data_entrada": str(df["time"].iloc[entry_idx]),
                        "side": side,
                        "preco_entrada": entry_price,
                        "preco_saida": take_profit,
                        "qty": qty,
                        "lucro": resultado,
                        "motivo_saida": "take"
                        ,"regime": regime_candle
                    })
                    exit_counts["take"] += 1
                    break

                elif False:
                    resultado = (preco_futuro - entry_price) * qty_abs
                    saldo -= resultado
                    perdas.append(resultado)
                    num_losses += 1
                    atingiu_sl = True
                    logging.info(f"❌ PREJUÍZO SIMULADO (candle {j}): -{abs(resultado):.2f} | Saldo: {saldo:.2f}")
                    trades.append({
                        "data": str(df["time"].iloc[j]),
                        "data_entrada": str(df["time"].iloc[entry_idx]),
                        "side": side,
                        "preco_entrada": entry_price,
                        "preco_saida": preco_futuro,
                        "qty": qty,
                        "lucro": -resultado,
                        "motivo_saida": "stop"
                        ,"regime": regime_candle
                    })
                    exit_counts["stop"] += 1
                    break



        # ⚠️ Esse bloco vem DEPOIS do loop!
        if not atingiu_tp and not atingiu_sl:
            preco_final = df["close"].iloc[min(entry_idx + 10, len(df) - 1)]  # preço após 10 candles
            qty_abs = abs(float(qty))
            resultado = (preco_final - entry_price) * qty_abs if side == "buy" else (entry_price - preco_final) * qty_abs

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
                "data": str(df["time"].iloc[min(entry_idx + 10, len(df) - 1)]),
                "data_entrada": str(df["time"].iloc[entry_idx]),
                "side": side,
                "preco_entrada": entry_price,
                "preco_saida": preco_final,
                "qty": qty,
                "lucro": resultado,
                "motivo_saida": "timeout"
                ,"regime": regime_candle
            })
            exit_counts["timeout"] += 1

        # Max Drawdown cálculo
        maior_pico = max(maior_pico, saldo)
        drawdown_atual = (maior_pico - saldo) / maior_pico
        max_drawdown = max(max_drawdown, drawdown_atual)

        resultados.append(saldo)

    # Recalcula estatisticas pelo PnL real dos trades para classificar corretamente
    # ganhos por stop/trailing (stop_gain) como vitorias.
    pnl_positivos = []
    pnl_negativos = []
    for t in trades:
        try:
            pnl_trade = float(t.get("lucro", 0) or 0)
        except Exception:
            pnl_trade = 0.0
        if pnl_trade > 0:
            pnl_positivos.append(pnl_trade)
        elif pnl_trade < 0:
            pnl_negativos.append(abs(pnl_trade))

    num_trades = len(trades)
    num_wins = len(pnl_positivos)
    num_losses = len(pnl_negativos)
    win_rate = (num_wins / num_trades) * 100 if num_trades else 0
    lucro_medio = np.mean(pnl_positivos) if pnl_positivos else 0
    perda_media = np.mean(pnl_negativos) if pnl_negativos else 0

    avg_trailing_offset = float(np.mean(trailing_offset_samples)) if trailing_offset_samples else 0.0
    logging.info(
        f"[TRAILING SUMMARY] ativacoes={trailing_activation_events} checks={trailing_check_count} "
        f"status={trailing_status_count} status_true={trailing_status_true_count} "
        f"avg_offset={avg_trailing_offset:.6f}"
    )
    logging.info(
        f"[EXIT SUMMARY] take={exit_counts['take']} stop={exit_counts['stop']} "
        f"timeout={exit_counts['timeout']} stop_gain={exit_counts['stop_gain']}"
    )

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

    # Complete only fields proven by the backtest.  No trade is attributed to
    # a candle when the engine did not retain an explicit source-candle link.
    # The trace is therefore safe for prospective comparison and never a
    # substitute for execution or paper records.


    # A direcao final deve acompanhar o sinal autorizado de maior score.
    # O ultimo trade do backtest pode ter lado diferente e nao deve sobrescrever
    # a direcao que sera usada pelo funil de execucao.
    direcao_final = melhor_lado_autorizado or ultimo_lado_autorizado

    autorizado_final = bool((sinais_autorizados["buy"] + sinais_autorizados["sell"]) > 0)
    entry_triggered = bool(num_trades > 0)
    score_final_out = float(max_score_autorizado if autorizado_final else 0.0)
    score_pct_final_out = float(max_score_pct_autorizado if autorizado_final else 0.0)
    motivo_final = "entrada_executada" if entry_triggered else (ultimo_motivo_final if autorizado_final else "sem_sinal_autorizado")

    logging.info(
        "[BREAKOUT SUMMARY] buy_signals=%d sell_signals=%d max_score=%.2f max_score_pct=%.2f",
        int(sinais_autorizados["buy"]),
        int(sinais_autorizados["sell"]),
        float(score_final_out),
        float(score_pct_final_out),
    )

    logging.info(
        "[BREAKOUT FINAL] direcao=%s autorizado=%s score=%.2f score_pct=%.2f entry_triggered=%s motivo_final=%s",
        (direcao_final or "NONE").upper(),
        str(autorizado_final),
        score_final_out,
        score_pct_final_out,
        str(entry_triggered),
        str(motivo_final),
    )


    return {
        "estrategia": "BREAKOUT",
        "direcao": direcao_final,
        "autorizado": autorizado_final,
        "score": score_final_out,
        "score_pct": score_pct_final_out,
        "entry_triggered": entry_triggered,
        "motivo_final": motivo_final,
        "breakout_evaluation_id": last_breakout_evaluation_id,

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
            "direcao": direcao_final,
            "autorizado": autorizado_final,
            "score": score_final_out,
            "score_pct": score_pct_final_out,
            "entry_triggered": entry_triggered,
            "motivo_final": motivo_final,
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

        # ?Y"? Filtro de tend??ncia real (ATR% + inclina????o de EMA)
        min_atr_pct = float(os.getenv("BREAKOUT_MIN_ATR_PCT", 0.08))  # em %
        min_ema_fast = float(os.getenv("BREAKOUT_MIN_EMA_SLOPE_FAST", 0.0012))
        min_ema_slow = float(os.getenv("BREAKOUT_MIN_EMA_SLOPE_SLOW", 0.002))

        close = df["close"]
        atr = df["ATR"] if "ATR" in df.columns else None
        ema_fast_col = "EMA_12" if "EMA_12" in df.columns else "EMA12"
        ema_slow_col = "EMA_26" if "EMA_26" in df.columns else "EMA26"

        if atr is not None and ema_fast_col in df.columns and ema_slow_col in df.columns:
            atr_pct = (atr / close) * 100.0
            incl_fast = (df[ema_fast_col] - df[ema_slow_col]).abs() / close
            incl_slow = (df["EMA50"] - df["EMA200"]).abs() / close
            df["tendencia_real"] = (
                (atr_pct >= min_atr_pct) &
                (incl_fast >= min_ema_fast) &
                (incl_slow >= min_ema_slow)
            )
        else:
            df["tendencia_real"] = False

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
            'forca_candle_confirmada',
            'tendencia_real'
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

    def _safe_float(value, default=0.0):
        try:
            v = float(value)
        except Exception:
            return default
        return v if np.isfinite(v) else default

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
    if BACKTEST_MODE:
        # Reduz impacto do sentimento no backtest para não vetar sinais técnicos válidos
        pesos["sentimento"] = pesos.get("sentimento", 0) * 0.5

    # --- Definição dos THRESHOLDS/Configurações (idem acima) ---
    THRESHOLD_CORPO_PCT_ROMPIMENTO = float(os.getenv("THRESHOLD_CORPO_PCT_ROMPIMENTO", 0.33))
    THRESHOLD_VOLUME_FORTE        = float(os.getenv("THRESHOLD_VOLUME_FORTE", 0.95))
    THRESHOLD_BOLLINGER_BUY       = float(os.getenv("THRESHOLD_BOLLINGER_BUY", 0.995))
    THRESHOLD_BOLLINGER_SELL      = float(os.getenv("THRESHOLD_BOLLINGER_SELL", 1.005))
    THRESHOLD_ATR_MIN             = _safe_env_float("THRESHOLD_ATR_MIN", 0.003)

 

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
        volume = _safe_float(volume, 1.0)

        # Volume médio (preferência para coluna já calculada, fallback para rolling mean)
        if "Volume_Media" in df.columns:
            volume_medio = df["Volume_Media"].iloc[-1]
        elif "volume" in df.columns:
            volume_medio = df["volume"].rolling(20, min_periods=1).mean().iloc[-1]
        else:
            volume_medio = 1
        if not np.isfinite(volume_medio) or volume_medio <= 0:
            volume_medio = max(volume, 1e-9)

        # Bollinger
        boll_upper = df["Bollinger_Upper"].iloc[-2] if "Bollinger_Upper" in df.columns else close * 1.01
        boll_lower = df["Bollinger_Lower"].iloc[-2] if "Bollinger_Lower" in df.columns else close * 0.99

        # ATR
        atr = df["ATR"].iloc[-1] if "ATR" in df.columns else 1
        atr = _safe_float(atr, 0.0)

        # --- 2. Critérios de validação individualmente protegidos ---

        # Volume
        criterios["volume"] = (
            volume > df["volume"].iloc[-2]
            if "volume" in df.columns and len(df) > 1
            else True  # fallback: sempre passa se não tem info
        )

        # RSI cruzando neutro
        if side == "buy":
            criterios["rsi"] = (
                (rsi_anterior < 50 and rsi_atual > 50)
                or _rsi_buy_reversal_confirmed(df)
            )
        else:
            criterios["rsi"] = (
                rsi_anterior > 50
                and rsi_atual < 50
                and not _rsi_extreme_sell_block(df)
            )

        # SMA direção
        criterios["sma"] = (
            sma_atual > sma_anterior if side == "buy" else sma_atual < sma_anterior
        )

        # Rompimento. Quando o detector canônico já avaliou este mesmo
        # DataFrame, seu resultado prevalece. O cálculo local permanece como
        # fallback para chamadas isoladas do score que não passaram pelo
        # detector.
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

            breakout_state = df.attrs.get("genesis_breakout", {}) if hasattr(df, "attrs") else {}
            canonical_direction = str(breakout_state.get("direction", "")).lower()
            canonical_confirmed = (
                str(breakout_state.get("state", "")).upper() == "CONFIRMED"
                and canonical_direction == ("bullish" if side == "buy" else "bearish")
            )
            local_breakout = (
                rompimento_valido and corpo_pct > THRESHOLD_CORPO_PCT_ROMPIMENTO and volume_forte
            )
            criterios["rompimento"] = canonical_confirmed if breakout_state else local_breakout
            if breakout_state:
                logging.info(
                    "[BREAKOUT_STATE] score=CANONICAL | detector=%s | direcao=%s | voto=%s | regra_local=%s",
                    breakout_state.get("detector", "unknown"),
                    canonical_direction.upper() or "NONE",
                    "FAVOR" if criterios["rompimento"] else "CONTRA",
                    local_breakout,
                )
            else:
                logging.info(
                    "[BREAKOUT_STATE] score=FALLBACK_LOCAL | voto=%s | regra_local=%s",
                    "FAVOR" if criterios["rompimento"] else "CONTRA",
                    local_breakout,
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
        ultimo_close = _safe_float(ultimo["close"], close)
        ultimo_open = _safe_float(ultimo["open"], ultimo_close)
        ultimo_high = _safe_float(ultimo["high"], ultimo_close)
        ultimo_low = _safe_float(ultimo["low"], ultimo_close)
        atr_pct = (atr / ultimo_close) * 100 if ultimo_close > 0 else 0.0
        corpo_pct = abs(ultimo_close - ultimo_open) / (ultimo_high - ultimo_low + 1e-9) * 100
        vol_media = _safe_float(df["volume"].rolling(20, min_periods=1).mean().iloc[-1] if "volume" in df.columns else volume_medio, volume_medio)
        volume_ratio = volume / (vol_media + 1e-9)
        if not np.isfinite(corpo_pct):
            corpo_pct = 0.0
        if not np.isfinite(volume_ratio):
            volume_ratio = 0.0

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
        logging.info("🧠 [VOTAÇÃO SCORE] Avaliando lado: %s", side.upper())
        for nome, peso in pesos.items():
            voto_favor = criterios.get(nome, False)
            contribuicao = peso if voto_favor else 0
            logging.info(
                "   ├─ %-15s voto=%s | peso=%.2f | contribuição=%+.2f",
                nome.upper(),
                "FAVOR" if voto_favor else "CONTRA",
                peso,
                contribuicao,
            )
        logging.info(
            "📊 [VOTAÇÃO SCORE] Total favorável: %.2f | Total possível: %.2f | Confiança: %.2f%%",
            score,
            score_maximo,
            score_pct,
        )
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


def autorizar_score_breakout(df, side, sentimento_geral="positivo", symbol=None, regime_override=None):
    """
    Usa o score apenas para autorizar ou vetar o lado proposto pelo Breakout.
    Nao compara BUY vs SELL.
    """
    if side == "sell" and _rsi_extreme_sell_block(df):
        return {
            "autorizado": False,
            "score_calculado": 0,
            "score_minimo": 0,
            "confianca_percentual": 0,
            "motivo_veto": "RSI_EXTREME_SELL_BLOCK",
            "criterios": {"rsi_extreme_sell_block": True},
        }
    if eth_bearish_buy_blocked(df, side, symbol):
        return {
            "autorizado": False,
            "score_calculado": 0,
            "score_minimo": 0,
            "confianca_percentual": 0,
            "motivo_veto": "ETH_PROTECAO_TENDENCIA_BAIXISTA",
            "criterios": {},
        }

    criterios = calcular_score_completo(df, side=side, sentimento_geral=sentimento_geral)
    score_final = criterios.get("score", 0)
    score_pct = criterios.get("score_pct", 0)

    if regime_override in (1, 2, 3, 4):
        regime_id = int(regime_override)
    else:
        detector = RegimeDetector()
        regime_id = detector.detectar(df, symbol=symbol)

    configurator = RegimeConfigurator()
    regime = configurator.get(regime_id)

    score_minimo = regime["score_min"]
    score_pct_requerido = regime["score_pct"]
    modo = regime["modo"]

    breakout_state = df.attrs.get("genesis_breakout", {}) if hasattr(df, "attrs") else {}
    logging.info(
        "[BREAKOUT_STATE] autorizador=RECEIVED | detector=%s | direcao=%s | estado=%s | lado=%s",
        breakout_state.get("detector", "none"),
        str(breakout_state.get("direction", "none")).upper(),
        breakout_state.get("state", "NOT_AVAILABLE"),
        side.upper(),
    )

    logging.info(
        f"💡 Lado proposto pelo Breakout: {side.upper()} | Regime: {modo.upper()} | "
        f"ScoreMin={score_minimo} | ScorePctMin={score_pct_requerido}%"
    )

    autorizado = score_final >= score_minimo and score_pct >= score_pct_requerido
    motivo_veto = None
    if not autorizado:
        motivo_veto = (
            f"Score insuficiente | score={score_final}/{score_minimo} | "
            f"score_pct={score_pct:.2f}%/{score_pct_requerido}%"
        )
    logging.info(
        "📊 [VOTAÇÃO SCORE] RESULTADO FINAL: %s",
        "AUTORIZADO" if autorizado else "VETADO",
    )
    logging.info(
        f"🧠 DECISÃO FINAL | side={side.upper()} | autorizado={autorizado} | "
        f"score={score_final} | score_min={score_minimo} | score_pct={score_pct:.2f}% "
        f"| score_pct_min={score_pct_requerido}%"
    )
    if motivo_veto:
        logging.warning(f"🚫 Entrada vetada pelo score | {motivo_veto}")

    return {
        "autorizado": autorizado,
        "score_calculado": score_final,
        "score_minimo": score_minimo,
        "confianca_percentual": score_pct,
        "motivo_veto": motivo_veto,
        "criterios": criterios,
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
    is_okx_native = (
        hasattr(api, "place_order")
        and hasattr(api, "set_trailing_stop")
        and not hasattr(api, "create_order")
    )
    if trailing_stop and trailing_stop > 0 and not is_okx_native:
        params["trailingStop"] = _fmt(trailing_stop, price_dec)

    logging.info(f"📝 Enviando ordem PERPÉTUA com parâmetros normalizados: {params}")

    # ============== CRIAÇÃO DA ORDEM ==============
    try:
        if is_okx_native:
            resposta = api.place_order(
                category="linear", symbol=symbol, side=side.lower(),
                order_type=entry_mode, qty=tamanho_lote,
                price=preco_entrada if entry_mode.lower() == "limit" else None,
            )
            order_rows = resposta.get("result", {}).get("list", []) or []
            order_id = order_rows[0].get("orderId") if order_rows else None
        else:
            resposta = api.create_order(**params)
            order_id = resposta.get("result", {}).get("orderId")
        if not order_id:
            raise RuntimeError("Exchange nao retornou identificador da ordem")
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
    order_rows = resposta.get("result", {}).get("list", []) or []
    first_order = order_rows[0] if order_rows else {}
    executed_qty = float(
        first_order.get("fillSz") or first_order.get("cumExecQty")
        or resposta.get("result", {}).get("cumExecQty", 0)
        or (tamanho_lote if is_okx_native and entry_mode.lower() == "market" else 0)
    )
    order_status = first_order.get("state") or first_order.get("orderStatus", "Unknown")

    if entry_mode.lower() == "limit":
        start = time.time()
        max_wait = 20
        while executed_qty == 0 and (time.time() - start < max_wait):
            time.sleep(2)
            if is_okx_native:
                status = api.get_order(category="linear", symbol=symbol, ordId=order_id)
                status_rows = status.get("result", {}).get("list", []) or []
                status_row = status_rows[0] if status_rows else {}
                executed_qty = float(status_row.get("fillSz") or status_row.get("cumExecQty") or 0)
                order_status = status_row.get("state") or "Unknown"
            else:
                status = api.get_order_status(order_id, symbol)
                executed_qty = float(status.get("cumExecQty", 0))
                order_status = status.get("orderStatus", "Unknown")
            if executed_qty > 0:
                break
        if executed_qty == 0:
            logging.warning("⏳ Ordem LIMIT não executada após timeout. Cancelando...")
            try:
                if is_okx_native:
                    api.cancel_order(category="linear", symbol=symbol, ordId=order_id)
                else:
                    api.cancel_order(order_id, symbol)
            except Exception as e:
                logging.error(f"Erro ao cancelar ordem: {e}")
            return None

    # ============== AJUSTE INICIAL DE SL/TP/TS PÓS-EXECUÇÃO ==============
    # (Mantém mesmos valores já normalizados)
    protection_confirmed = not is_okx_native
    try:
        pos = api.get_positions(category="linear", symbol=symbol)
        lista = pos.get("result", {}).get("list", [])
        tem_posicao = any(abs(float(p.get("size") or 0)) > 0 for p in lista)
        if tem_posicao:
            try:
                payload = {
                    "category": "linear",
                    "symbol": symbol,
                    "stopLoss": _fmt(stop_loss, price_dec),
                    "slTriggerBy": "LastPrice",
                    "triggerDirection": 1,
                }
                if take_profit is not None:
                    payload["takeProfit"] = _fmt(take_profit, price_dec)
                    payload["tpTriggerBy"] = "LastPrice"
                if is_okx_native:
                    # Na OKX a entrada e o OCO são duas operações distintas.
                    # Recalcula os gatilhos contra o último preço e só libera
                    # o monitoramento após confirmar closeOrderAlgo.
                    protection_error = None
                    for protection_attempt in range(1, 7):
                        try:
                            ticker_rows = api.get_tickers(category="linear", symbol=symbol).get("result", {}).get("list", [])
                            last_price = float(ticker_rows[0].get("lastPrice") or 0) if ticker_rows else 0.0
                            if last_price:
                                buffer = max(tick_size * 2, last_price * 0.0002)
                                if side.lower() == "sell":
                                    payload["stopLoss"] = _fmt(max(float(stop_loss), last_price + buffer), price_dec)
                                    payload["takeProfit"] = _fmt(min(float(take_profit), last_price - buffer), price_dec)
                                else:
                                    payload["stopLoss"] = _fmt(min(float(stop_loss), last_price - buffer), price_dec)
                                    payload["takeProfit"] = _fmt(max(float(take_profit), last_price + buffer), price_dec)
                            api.set_trading_stop(**payload)
                            for _ in range(4):
                                time.sleep(0.35)
                                ver = api.get_positions(category="linear", symbol=symbol)
                                vrows = ver.get("result", {}).get("list", []) if isinstance(ver, dict) else []
                                vpos = next((row for row in vrows if abs(float(row.get("size") or 0)) > 0), None)
                                algos = (vpos or {}).get("closeOrderAlgo") or []
                                active = [a for a in algos if str(a.get("state") or "").lower() not in ("canceled", "cancelled", "order_failed")]
                                has_sl = any(a.get("slTriggerPx") not in (None, "", "0", "0.0", 0) for a in active)
                                has_tp = any(a.get("tpTriggerPx") not in (None, "", "0", "0.0", 0) for a in active)
                                if has_sl and has_tp:
                                    protection_confirmed = True
                                    break
                            if protection_confirmed:
                                break
                            raise RuntimeError("OKX nao confirmou closeOrderAlgo com SL e TP")
                        except Exception as protection_exc:
                            protection_error = protection_exc
                            logging.warning("[PROTECTION_PENDING] %s tentativa %s/6: %s", symbol, protection_attempt, protection_exc)
                            if protection_attempt < 6:
                                time.sleep(1)
                    if not protection_confirmed:
                        raise RuntimeError(f"proteção não confirmada pela OKX: {protection_error}")
                    trailing_created = False
                    if trailing_stop and trailing_stop > 0:
                        trailing_response = api.set_trailing_stop(
                            category="linear",
                            symbol=symbol,
                            callbackSpread=_fmt(trailing_stop, price_dec),
                            side="sell" if side.lower() == "buy" else "buy",
                            replace_existing=True,
                        )
                        trailing_created = bool(
                            trailing_response.get("result", {}).get("list", []) or []
                        )
                    logging.info(
                        "✅ OKX SL/TP nativos ajustados | trailing_nativo=%s",
                        trailing_created,
                    )
                else:
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

    if is_okx_native and not protection_confirmed:
        logging.error(
            "[PROTECTION_PENDING] %s entrada preenchida, mas sem SL/TP confirmado; "
            "monitoramento não será iniciado.",
            symbol,
        )
        return None

    # ============== MONITORAMENTO EM THREAD ==============
    side_norm = (side or "").strip().lower() if isinstance(side, str) else ""
    if side_norm not in ("buy", "sell"):
        logging.error(f"❌ Side ausente/inválido ao iniciar monitoramento: {side}")
        return None

    trade_data = {
        "trade_id": order_id or trade_id,  # usa order_id real se disponível
        "symbol": symbol,
        "side": side_norm,
        "entry_price": float(preco_entrada),
        "position_size": float(tamanho_lote),
        "risk_parameters": {
            "stop_loss": float(stop_loss) if stop_loss is not None else None,
            "take_profit": float(take_profit) if take_profit is not None else None,
            "trailing_stop": float(trailing_stop) if trailing_stop is not None else None,
            "strategy": "continuation_trade",
        },
        "indicators": {
            "strategy": "continuation_trade",
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




def breakout_filters_summary(motivos_rejeicao=None, score_override=None, incluir_filtros=True):
    """
    Gera um resumo legível dos filtros aplicados na última análise da estratégia Breakout.
    Se receber `motivos_rejeicao`, detalha o motivo de cada filtro falhar.
    """
    try:
        # Esses valores devem ser definidos após a última análise do breakout
        global filtro_rsi, filtro_volume, filtro_media, resultado_final

        resumo = []

        if incluir_filtros:
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

        score_exibicao = resultado_final if score_override is None else score_override
        try:
            score_exibicao = float(score_exibicao)
            resumo.append(f"Score Final Breakout: {score_exibicao:.2f} pontos")
        except Exception:
            resumo.append(f"Score Final Breakout: {score_exibicao} pontos")

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
from utils.breakout_candle_state import get_last_candle, set_last_candle


def get_ultimo_candle(symbol=None):
    return get_last_candle(symbol)


def set_ultimo_candle(valor, symbol=None):
    set_last_candle(valor, symbol)



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


def _cooldown_breakout_path(symbol: str) -> str:
    return f"cooldown_breakout_{symbol}.json"


def _breakout_em_cooldown(symbol: str) -> float:
    try:
        if not os.path.exists(_cooldown_breakout_path(symbol)):
            return 0.0
        with open(_cooldown_breakout_path(symbol), "r") as f:
            raw = json.load(f)
        until = float(raw.get("cooldown_until", 0) or 0)
        if until > time.time():
            return until
        return 0.0
    except Exception:
        return 0.0



def liberar_lock_fisico(symbol):
    path = get_lock_path(symbol)
    if os.path.exists(path):
        os.remove(path)
        logging.info(f"🔓 Lock físico liberado para {symbol}.")
    else:
        logging.info(f"✅ Nenhum lock ativo encontrado para {symbol}.")



def _breakout(symbol, session, api, regime_override=None):
    global ultimo_candle_processado
    logging.info(f"🚀 Iniciando Breakout Trading para {symbol}")
    logging.info(f"🔎 ENV: ATR_MIN={os.getenv('THRESHOLD_ATR_MIN')} | ATR_MAX={os.getenv('THRESHOLD_ATR_MAX')}")

    # --- FLAGS GERAIS (antes do try) ---
    lock_criado = False
    posicao_aberta = False

    cooldown_until = _breakout_em_cooldown(symbol)
    if cooldown_until > time.time():
        mins = round((cooldown_until - time.time()) / 60, 1)
        logging.warning(f"[BREAKOUT] Cooldown ativo para {symbol} ({mins} min restantes).")
        return None, None, None

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
            "strategy": "bybit_existing",
            "timestamp_decisao_ia": datetime.utcnow().isoformat(),
        }
        side_ativo = str(trade_info.get("side") or "").strip().lower()
        if side_ativo not in ("buy", "sell"):
            logging.error("❌ Side ausente na posição existente da Bybit. Abortando monitoramento sem default.")
            return None, None, None
        threading.Thread(
            target=monitorar_ordem_ate_encerrar,
            args=(
                trade_id,
                symbol,
                position_size,
                entry_price,
                side_ativo,
                api,
                risk_parameters,
                {"entry_score": 0, "sentiment_score": 0, "strategy": "bybit_existing", "ATR": 0.0},
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
    ultimo = get_ultimo_candle(symbol)
    if str(candle_atual) == ultimo:
        now = time.time()
        last_log = float(getattr(_breakout, "_last_no_new_candle_log", 0.0))
        if now - last_log >= 60:
            logging.info("⏳ Nenhum novo candle detectado. Aguardando próximo fechamento...")
            setattr(_breakout, "_last_no_new_candle_log", now)
        return None, None, None
    set_ultimo_candle(str(candle_atual), symbol)

    # 3. Indicadores e validação
    df = calculate_indicators(df)
    df, pronto_reset = _apply_impulse_reset(df)
    if not pronto_reset:
        logging.warning("[BREAKOUT] Impulso recente: aguardando nova base antes de disparar.")
        return None, None, None
    df = calculate_breakout_levels(df)
    df = aplicar_regras_validacao_breakout(df)
    ema50 = float(df["EMA50"].iloc[-1]) if "EMA50" in df.columns else float(df["close"].iloc[-1])
    ema200 = float(df["EMA200"].iloc[-1]) if "EMA200" in df.columns else float(df["close"].iloc[-1])

        
    # 🚦 Filtro de regime de mercado (volatilidade mínima)
    if not check_volatility(df):
        logging.warning("🚫 Volatilidade insuficiente. Mercado lateral.")
        return None, None, None

    if not _validar_filtros_env_breakout(df):
        return None, None, None

    # 4. Detecta sinal
    regime_para_breakout = regime_override
    if regime_para_breakout not in (1, 2, 3, 4):
        try:
            regime_para_breakout = RegimeDetector().detectar(df, symbol=symbol)
        except Exception:
            regime_para_breakout = 2
    breakout_signal = detect_breakout(df, regime=regime_para_breakout)
    if breakout_signal == "bullish":
        side = "buy"
    elif breakout_signal == "bearish":
        side = "sell"
    else:
        logging.warning("⚠️ Nenhum breakout confirmado neste candle. Sinal inconclusivo.")
        return None, None, None

    logging.info("🧠 [BREAKOUT] Lado técnico definido: %s", side.upper())
    logging.info("🧠 [SCORE] Score atuará apenas como AUTORIZADOR/VETO do lado %s", side.upper())
    try:
        from estrategias.analise_sentimento import obter_sentimento_combinado
        sentimento_geral = obter_sentimento_combinado()
    except Exception:
        sentimento_geral = "neutro"

    score_auth = autorizar_score_breakout(
        df,
        side=side,
        sentimento_geral=sentimento_geral,
        symbol=symbol,
        regime_override=regime_override,
    )
    score_final = score_auth.get("score_calculado", 0)
    logging.info(
        "🧠 [SCORE] Resultado da autorização: %s",
        "AUTORIZADO" if score_auth.get("autorizado") else "VETADO",
    )
    if not score_auth.get("autorizado"):
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

    if preco_confirmacao is None or preco_confirmacao <= 0:
        logging.error(
            "\U0001f6ab PRE\u00c7O DE ENTRADA INV\u00c1LIDO | estrategia=breakout | side=%s | preco_confirmacao=%s",
            side.upper(),
            preco_confirmacao,
        )
        return None, None, None

    logging.info(
        f"🎯 CONFIRMAÇÃO BREAKOUT | Side={side.upper()} | "
        f"Sinal={preco_sinal:.2f} | Trigger={preco_confirmacao:.2f} | ATR={atr:.2f}"
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
            logging.info("⚠️ [SCORE] Score NÃO FOI CONSULTADO (fluxo vetado anteriormente)")
            return None, None, None

    # 5. Score apenas autoriza ou veta o lado proposto (sem decidir direção)
    score_auth = autorizar_score_breakout(
        df,
        side=side,
        sentimento_geral=sentimento_geral,
        symbol=symbol,
        regime_override=regime_override,
    )
    score_final = score_auth.get("score_calculado", 0)
    logging.info(
        "🧠 [SCORE] Resultado da autorização: %s",
        "AUTORIZADO" if score_auth.get("autorizado") else "VETADO",
    )
    if not score_auth.get("autorizado"):
        return None, None, None



    # 6. Cálculo de TP/SL/Trailing/lote
    try:
        logging.debug("[Inicialização] ATR será calculado a partir do DataFrame")

        # --- Preço e saldo ---
        try:
            saldo_info = session.get_wallet_balance(accountType="UNIFIED")
            wallet = saldo_info["result"]["list"][0]["coin"]
            usdt = next((x for x in wallet if x["coin"] == "USDT"), None)
            if not usdt:
                raise RuntimeError("Saldo USDT nao encontrado na conta OKX.")
            valor_raw = usdt.get("totalAvailableBalance") or usdt.get("walletBalance")
            saldo_disponivel = float(valor_raw or 0)
            if saldo_disponivel <= 0:
                raise RuntimeError("Saldo USDT disponivel igual a zero.")
        except Exception as e:
            logging.warning(f"⚠️ Falha ao obter saldo da conta UNIFIED: {e}")
            return None, None, None
        logging.info(f"💰 Saldo disponível: {saldo_disponivel:.2f} USDT")

        # --- SL/TP via lógica central adaptativa ---
        preco_base_sl = float(preco_confirmacao)
        stop_loss, take_profit, tipo_stop, contexto = calcular_stop_take_adaptativo(
            df,
            preco_base_sl,
            side,
            params={"tese_stop": False, "rrr": BREAKOUT_RRR_ALVO},
        )

        rrr = calcular_rrr(preco_base_sl, stop_loss, take_profit, min_rrr=BREAKOUT_MIN_RRR)
        if rrr is None or rrr < BREAKOUT_MIN_RRR:
            logging.warning(
                "[BREAKOUT] Entrada bloqueada: RRR inválido (rrr=%s, mínimo=%.2f).",
                rrr,
                BREAKOUT_MIN_RRR,
            )
            return None, None, None

        logging.info(f"[BREAKOUT - SL/TP Adaptativo] {contexto=} | {tipo_stop=} | EntradaPlanejada={preco_base_sl:.2f}| SL={stop_loss} | TP={take_profit}")

        trailing_offset_calculado = 0


        # --- Gestão de risco (BASEADA NA ENTRADA CONFIRMADA) ---
        risco_percentual = float(os.getenv("RISCO_PERCENTUAL", 0.004))
        risco_em_usdt = saldo_disponivel * risco_percentual

        # ✅ entrada planejada = preço STOP (trigger) de confirmação
        preco_planejado = float(preco_confirmacao)

        ajuste_avanco = False
        qty_mult = 1.0
        adv_thresh = float(os.getenv("ADVANCE_THRESH", 0.65))
        adv_qty_mult = float(os.getenv("ADVANCE_QTY_MULT", 0.7))
        if "Support" in df.columns and "Resistance" in df.columns:
            suporte_adv = df["Support"].iloc[-2]
            resistencia_adv = df["Resistance"].iloc[-2]
            range_total = max(resistencia_adv - suporte_adv, 1e-9)
            if side == "buy":
                avanco = (preco_planejado - suporte_adv) / range_total
            else:
                avanco = (resistencia_adv - preco_planejado) / range_total
            if avanco > adv_thresh:
                ajuste_avanco = True
                qty_mult = adv_qty_mult
                logging.info(
                    f"[BREAKOUT] Avanço alto ({avanco:.2%}) - entrada bloqueada para evitar compra esticada."
                )
                return None, None, None

        if stop_loss is None or stop_loss <= 0:
            logging.warning(
                "?? Stop inv?lido (<= 0). Ajuste ATR/TESE e tente novamente. Abortando entrada."
            )
            return None, None, None

        stop_distance = abs(preco_planejado - stop_loss)
        if stop_distance <= 0:
            logging.warning("?? Dist?ncia de stop inv?lida. Abortando entrada.")
            return None, None, None

        qty_raw = min(
            risco_em_usdt / stop_distance,
            saldo_disponivel / preco_planejado
        )
        if ajuste_avanco and qty_mult > 0:
            qty_raw = qty_raw * qty_mult

        qty = ajustar_para_min_qty(symbol, qty_raw)
        qty = round(qty, 3)

        logging.info(
            f"?? RISCO | EntradaPlanejada={preco_planejado:.2f} | "
            f"SL={stop_loss:.2f} | Dist?ncia={stop_distance:.2f} | Qty={qty}"
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
            # Estado expl?cito: STOP LIMIT pendente de confirma??o
            trade_state = {
                "estado": "STOP_LIMIT_PENDENTE",
                "trigger_price": preco_planejado,
                "timestamp_envio": datetime.now().isoformat(),
            }
            logging.info(f"Estado do trade: {trade_state}")
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
                trade_id=trade_id or f"breakout:{symbol}:{side}:{datetime.utcnow().isoformat()}",
                decision_timestamp=datetime.utcnow().isoformat(),
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
            logging.info(
                f"Entrada confirmada via STOP LIMIT | Ordem={trade_id} | Preco={entry_price_real:.4f}"
            )

        if entry_price_real is None or entry_price_real <= 0:
            logging.error("Preco de entrada nao definido apos confirmacao.")
            return None, None, None

        from compra1 import aplicar_trading_stop_com_retry
        stop_loss, take_profit, tipo_stop, contexto = calcular_stop_take_adaptativo(
            df,
            entry_price_real,
            side,
            params={"tese_stop": False, "rrr": BREAKOUT_RRR_ALVO},
        )

        rrr_real = calcular_rrr(entry_price_real, stop_loss, take_profit, min_rrr=BREAKOUT_MIN_RRR)
        if rrr_real is None or rrr_real < BREAKOUT_MIN_RRR:
            logging.error(
                "[BREAKOUT] RRR inválido após execução: rrr=%s mínimo=%.2f. Encerramento preventivo.",
                rrr_real,
                BREAKOUT_MIN_RRR,
            )
            return None, None, None

        resp_tp_sl = aplicar_trading_stop_com_retry(session, symbol, stop_loss, take_profit)
        if not resp_tp_sl or resp_tp_sl.get("retCode") != 0:
            logging.error(f"Falha ao aplicar SL/TP após execução: {resp_tp_sl}")
            # Fail-closed: não manter posição desprotegida.
            try:
                if hasattr(session, "close_position"):
                    session.close_position(category="linear", symbol=symbol, positionIdx=0)
                else:
                    close_side = "Sell" if str(side).lower() == "buy" else "Buy"
                    session.place_order(
                        category="linear",
                        symbol=symbol,
                        side=close_side,
                        orderType="Market",
                        qty=str(tamanho_lote_real),
                        reduceOnly=True,
                        positionIdx=0,
                    )
                logging.warning("[BREAKOUT] Posição encerrada por falha na proteção SL/TP.")
            except Exception as close_exc:
                logging.critical(
                    "[BREAKOUT] Falha ao encerrar posição sem proteção: %s",
                    close_exc,
                )
            return None, None, None
        else:
            logging.info(f"SL/TP aplicados apos execucao: SL={stop_loss}, TP={take_profit}")

        try:
            msg = (
                f"*Trade Breakout Executado*\n"
                f"Par: {symbol}\nDirecao: {side.upper()}\nEntradaConfirmada(STOP LIMIT): {entry_price_real:.2f}\n"
                f"Qtd: {tamanho_lote_real}\nSL: {stop_loss}\nTP: {take_profit}\n"
                f"Score: {score_final}\nHora: {datetime.now().strftime('%H:%M:%S')}"
            )
            send_telegram_message(msg)
        except Exception as e:
            logging.warning(f"Falha ao enviar mensagem Telegram: {e}")

    except Exception as e:
        logging.error(f"[BREAKOUT] Erro inesperado: {e}")
        try:
            if "lock_criado" in locals() and lock_criado:
                liberar_lock_fisico(symbol)
        except Exception:
            pass
        return None, None, None

