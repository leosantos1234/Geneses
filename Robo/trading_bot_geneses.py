import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# ForÃ§a UTF-8 no console para evitar crash com emojis em Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# FSM por sÃ­mbolo (IPC leve via arquivo) compartilhada com o processo da API.
try:
    _AGENTE_IA_DIR = os.path.join(os.path.expanduser("~"), "Documents", "Agente_IA")
    if os.path.isdir(_AGENTE_IA_DIR) and _AGENTE_IA_DIR not in sys.path:
        sys.path.append(_AGENTE_IA_DIR)
    from symbol_state import get_state, IDLE
except Exception:
    def get_state(symbol: str) -> str:  # type: ignore
        return "IDLE"
    IDLE = "IDLE"

# Aceita o nome do arquivo .env como argumento, ou usa ".env" padrÃ£o
dotenv_arg = sys.argv[1] if len(sys.argv) > 1 else ""
dotenv_path = os.path.abspath(dotenv_arg) if dotenv_arg else ""
if dotenv_path and os.path.isfile(dotenv_path):
    os.environ["ROBO_ENV_FILE"] = dotenv_path
    load_dotenv(dotenv_path=dotenv_path)
else:
    os.environ["ROBO_ENV_FILE"] = ""
    print("ENV por arquivo não encontrado; usando variáveis do ambiente")

# Fail-safe para a inicialização controlada pelo launcher do anexo. O modo
# paper deve prevalecer mesmo quando o perfil contém valores de produção.
if os.getenv("PAPER_MODE", "0").strip().lower() in ("1", "true", "yes", "on"):
    os.environ["ENABLE_LIVE_TRADING"] = "false"
    os.environ["ALLOW_EXCHANGE_WRITE"] = "false"
    os.environ["OKX_READ_ONLY"] = "true"
    os.environ["ALLOW_MAINNET_TRADING"] = "false"
    os.environ["ALLOW_MAINNET_FROM_ENVFILE"] = "false"

# Garante que o serviço LSTM configurado para este worker esteja disponível
# antes do primeiro ciclo. O helper é idempotente: se outro worker já iniciou
# a mesma porta, apenas retorna ``already_running``. Falha no serviço não deve
# impedir o worker de iniciar, pois o motor possui fallback explícito sem LSTM.
try:
    from ui_backend.lstm_manager import ensure_lstm_service

    _lstm_bootstrap = ensure_lstm_service(dotenv_path)
    print(f"LSTM bootstrap: {_lstm_bootstrap}")
except Exception as exc:
    print(f"LSTM bootstrap indisponivel; seguindo com fallback: {exc}")

modo_estrategia = os.getenv("MODO_ESTRATEGIA", "").strip().upper()
if modo_estrategia == "LEAD_LAG":
    from estrategia_entrada.lead_lag_runner import run_lead_lag
    run_lead_lag(dotenv_path)
    sys.exit(0)


# EXEMPLO: Mostra qual ativo estÃ¡ carregado para garantir
ATIVOS = os.getenv("ATIVOS", "BTCUSDT")
print(f"Ativo carregado do ENV: {ATIVOS}")

from utils.logger_config import configurar_logger_global
configurar_logger_global("trading_bot_geneses.log")

import logging  # Esse deve vir apÃ³s a configuraÃ§Ã£o
logger = logging.getLogger(__name__)
from genesis_runtime import connect_operational_database, log_runtime_startup
log_runtime_startup(logger, role="worker")

modo_validacao = os.getenv("MODO_VALIDACAO", "normal").strip().lower()
logging.info(f"ðŸ” Modo de ValidaÃ§Ã£o: {modo_validacao.upper()} (score e critÃ©rios adaptados)")

from utils.parse_env import get_ativos_env


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "moderado"
    par = sys.argv[2] if len(sys.argv) > 2 else os.getenv("ATIVOS")

    print(f"Robo Fluxo iniciado com MODO={modo.upper()} e PAR={par}")
    # Aqui continua sua lÃ³gica de trading...


# === IMPORTAÃ‡Ã•ES PADRÃƒO ===

import os
import sys
import time
import glob
import pickle
import threading
import warnings
import traceback
import hmac
import hashlib


# === IMPORTAÃ‡Ã•ES DE TERCEIROS ===
import requests
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")

# === IMPORTAÃ‡Ã•ES INTERNAS ===
from utils.ml_core import treinar_modelo
from utils.validadores import validar_indicadores_com_reset, verificar_parametros_ordem

from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from dateutil.parser import parse
from collections import Counter
from multiprocessing import Process, set_start_method

from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split, GridSearchCV, RandomizedSearchCV
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import classification_report, confusion_matrix
from imblearn.over_sampling import SMOTE
from xgboost import XGBClassifier


import joblib
from openai import OpenAI
import plotly.graph_objects as go

# === IMPORTS DO PROJETO (MODULARES) ===

#Dashboard
import argparse


# ðŸ§  EstratÃ©gias de entrada e execuÃ§Ã£o
from estrategia_entrada.entrada_mercado import should_enter_market, checar_oportunidade_entrada
from estrategia_entrada.estrategia_breakout_filtros import _breakout, existe_ordem_aberta, executar_backtest_breakout, breakout_filters_summary
from estrategia_entrada.estrategia_mean_reversion import mean_reversion_sl_tp, monitorar_ordens
from estrategia_entrada.estrategia_mean_reversion import criar_backtest_reversao, executar_mean_reversion_realtime
from estrategia_entrada.estrategia_market_making_lateral import executar_market_making as executar_market_making_lateral

# MR nova (FSM+Engine) - funÃ§Ã£o stateful chamada apenas quando a MR for vencedora.
from estrategias.mean_reversion.engine_runner import executar_mean_reversion_nova_tick

# Early Trend (Swing/RecuperaÃ§Ã£o) - RN/RT 1.0 (stateful por tick)
from estrategias.early_trend.early_trend_backtest import backtest_early_trend
from estrategias.early_trend.engine_runner import executar_early_trend_tick

# Arquitetura: edge (funil) vs timing (realtime) via intenÃ§Ãµes
from utils.intent_manager import IntentManager
# === CONFIG GLOBAL DO MARKET MAKING ===
config_marketmaking = {
    "capital_inicial": 10000,
    "order_qty": 0.001,
    "spread_min": 0.05,
    "spread_multiplier": 1.2,
    "inventory_limit": 0.005,
    "cancel_before_repost": True   # â† ADICIONAR ESTA LINHA
}

from estrategia_entrada.modulo_volatilidade import (
    avaliar_condicao_mercado, calcular_atr, calcular_bollinger_bands,
    calcular_desvio_padrao_volatilidade, monitorar_mercado
)
from estrategia_entrada.backtesting_geral import (
    carregar_dados_historicos,
    executar_backtest_completo
)
from estrategia_entrada.swing_trend_following.strategy import carregar_timeframes_swing
from estrategia_entrada.swing_trend_following.live_executor import executar_swing_okx
from estrategia_entrada.pullback_trend_following.live_executor import executar_pullback_okx
from utils.shadow_decision_telemetry import build_comparison_payload, persist_shadow_comparison


def _load_market_making_reference_data(symbol):
    """Carrega o histórico completo opcional do backtest de market making."""
    configured_path = os.getenv("BACKTEST_REFERENCE_DATA_CSV", "").strip()
    if not configured_path:
        return None
    try:
        symbol_key = str(symbol or "").upper().replace("-USDT-SWAP", "")
        if symbol_key and symbol_key not in {"BTCUSDT", "BTC"}:
            return None
        path = Path(configured_path)
        if not path.is_absolute():
            path = Path(__file__).resolve().parent / path
        if not path.exists():
            logging.warning("[MM][REFERENCE] Arquivo não encontrado: %s", path)
            return None
        data = pd.read_csv(path)
        required = {"time", "open", "high", "low", "close", "volume"}
        if not required.issubset(data.columns):
            logging.warning("[MM][REFERENCE] Colunas ausentes em %s", path)
            return None
        data["time"] = pd.to_datetime(data["time"], utc=True, errors="coerce")
        for column in ("open", "high", "low", "close", "volume"):
            data[column] = pd.to_numeric(data[column], errors="coerce")
        data = data.dropna(subset=list(required)).sort_values("time").reset_index(drop=True)
        logging.info("[MM][REFERENCE] Histórico carregado | arquivo=%s | candles=%s", path, len(data))
        return data
    except Exception as exc:
        logging.warning("[MM][REFERENCE] Falha não bloqueante ao carregar histórico: %s", exc)
        return None

#RISCO
from utils.risco import calcular_rrr

# ðŸ“Š EstratÃ©gias auxiliares
# LSTM Ã© carregado sob demanda no fluxo que realmente o utiliza. Importar
# TensorFlow no boot bloqueava o primeiro ciclo do worker por tempo excessivo.
from estrategias.analise_sentimento import obter_sentimento_combinado
from estrategias.validador_ensemble import validar_com_ensemble
from estrategias.analise_sentimento import NEWS_API_KEY, FONTES, capturar_noticias, analisar_sentimento
from utils.ml_core import (
    carregar_dados,
    treinar_modelo,
    carregar_modelo_com_fallback,
    validar_com_modelo
)

base_url = os.getenv("LSTM_API_URL", "http://127.0.0.1:8505")

# Normaliza completamente
base_url = base_url.rstrip("/")

# Se jÃ¡ termina com /predict â†’ mantÃ©m
if base_url.endswith("/predict"):
    LSTM_API_URL = base_url
else:
    LSTM_API_URL = base_url + "/predict"




# ðŸ§® LÃ³gica de Stop-Loss e Take-Profit
from logica.metodo_stop_take import escolher_metodo_stop_take
from logica.calcular_stop_take import calcular_stop_take_dinamico, obter_maximas_minimas

# ðŸ§  Tomada de decisÃ£o com IA
from decision_engine import avaliar_decisao_entrada

# ðŸ§¾ Monitoramento de operaÃ§Ãµes
# Import lazy: monitoramento importa a camada de exchange e nao deve bloquear
# o boot do worker antes do primeiro candle.
def monitorar_ordem_ate_encerrar(*args, **kwargs):
    from monitoramento import monitorar_ordem_ate_encerrar as _monitorar
    return _monitorar(*args, **kwargs)

# ðŸ” SessÃ£o da Bybit
from compra1 import initialize_session, executar_compra, executar_venda

CANDLES_MINIMOS = 200  # Ajuste para o mÃ­nimo que quer (recomendado >= 100)

logging.info("[STARTUP_STAGE] criando sessao de execucao")
api = initialize_session()
logging.info("[STARTUP_STAGE] sessao de execucao criada=%s", api is not None)
if api is None:
    logging.error("NÃ£o foi possÃ­vel inicializar a sessÃ£o da Bybit. Abortando robÃ´.")
    exit(1)

# ðŸ› ï¸ UtilitÃ¡rios (nova estrutura modular)
from utils.utils_market import obter_preco_mercado, get_market_data_bybit
from utils.utils_telegram import send_telegram_message
from utils.timestamp import obter_diferenca_timestamp
from utils.openai_module import analisar_mercado_openai as consultar_openai
from utils.utils_trade_monitoring import OrdemUtils
from utils.relatorio_validacao import gerar_relatorio_validacao
from utils.config_validacao import obter_config_validacao
from utils.risk_guard import evaluate_risk_limits, write_kill_switch_flag, process_risk_transition
from utils.entry_idempotency import is_registered, mark_registered
from lineage.decision_lineage import clear_current_decision, create_decision, finalize_decision, finalize_decision_if_open, record_event, reconcile_stale_decisions, set_current_decision_id
from utils.breakout_observability import link_breakout_evaluation
from utils.closed_candles import select_closed_candles
from regime.regime_observability import record_regime_evaluation
from utils.analytical_telemetry import record_canonical_candle, record_closed_1m_candle
from utils.breakout_runtime_context import resolve_breakout_regime_override
from utils.execution_path_telemetry import emit_cycle, mark_exit, new_cycle
from utils.strategy_profiles import resolve_strategy_profile, attach_strategy_profile
from utils.model_risk_policy import model_fallback_policy
from utils.heartbeat import write_heartbeat
from utils.performance_metrics import record_timing

# ðŸ§± Banco de dados
from banco_trade.db_trades import inicializar_banco, salvar_previsao_lstm, salvar_trade_sqlite
from db_manager import salvar_trade
import uuid 

# ðŸ“Š Dash e Dashboard modularizado
from dashboard.callbacks import register_callbacks
from dashboard.dash_launcher import iniciar_dashboard

# ðŸš€ Indicadores tÃ©cnicos e backtest
from utils.indicadores import calculate_indicators, analisar_multiplos_timeframes
from backtest_module import carregar_dados_historicos, executar_backtesting, gerar_relatorio, estrategia_cruzamento_medias


# ANALISE DE MERCADO
from analise.market_regime import detectar_regime
from analise.gestao_dinamica import calcular_exposicao_dinamica

# Inicializar o banco de dados
logging.info("[STARTUP_STAGE] inicializando banco operacional")
inicializar_banco()
logging.info("[STARTUP_STAGE] banco operacional inicializado")

from config import (
    ACTIVE_EXCHANGE,
    TESTNET,
    TRADING_SYMBOL,
    MAX_TRADES_PER_DAY,
    LOSS_LIMIT_PERCENT,
    ATR_MULTIPLICADOR_STOP_LOSS,
    ATR_MULTIPLICADOR_TAKE_PROFIT,
    TIME_BETWEEN_TRADES,
    TIME_BETWEEN_CHECKS,    
)
from genesis_commander.runtime_bridge import run_shadow_decision_sync
from genesis_paper import (
    PaperRepository,
    PaperRiskConfig,
    PaperSimulatorConfig,
    create_portfolio,
    route_candidate_to_paper,
)
from genesis_commander.persistence import update_commander_decision_context
from exchange.factory import create_exchange_client

session = api
logging.info("Exchange ativa no fluxo principal: %s", ACTIVE_EXCHANGE.upper())

symbol = os.getenv("ATIVOS").upper()
df = pd.DataFrame()

def get_market_data_filename(symbol):
    return f"market_data_{symbol}.csv"





def verificar_colunas_criticas(df):
    colunas_criticas = ["Bollinger_Upper", "Bollinger_Lower", "ATR", "RSI"]
    if df is None or df.empty:
        return True
    ausentes = [col for col in colunas_criticas if col not in df.columns]
    if ausentes:
        logger.warning("âš ï¸ Colunas crÃ­ticas ainda nÃ£o calculadas: %s", ", ".join(ausentes))
        return True
    for col in colunas_criticas:
        if col in df.columns and df[col].isnull().any():
            logger.warning(f"âš ï¸ Coluna '{col}' contÃ©m NaN apÃ³s cÃ¡lculo de indicadores.")
    if df[colunas_criticas].isnull().any().any():
        logger.error("âŒ Ainda existem valores nulos em colunas crÃ­ticas apÃ³s tratamento. Abortando ciclo.")
        logger.debug(f"ðŸ” DataFrame com NaNs:\n{df.tail(5)}")
        time.sleep(10)
        sys.exit(1)  # ðŸ” Troca segura de continue
    return True

if not df.empty and not verificar_colunas_criticas(df):
    sys.exit(1)



OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

if not OPENAI_API_KEY:
    print("AVISO: OPENAI_API_KEY nao encontrada no .env â€” verifique o arquivo e o caminho.")
else:
    print("OK: OPENAI_API_KEY carregada com sucesso.")
OPENAI_CONTEXT_TIMEOUT_SECONDS = float(os.getenv("OPENAI_CONTEXT_TIMEOUT_SECONDS", "5"))
OPENAI_CLIENT = (
    OpenAI(
        api_key=OPENAI_API_KEY,
        timeout=OPENAI_CONTEXT_TIMEOUT_SECONDS,
        max_retries=0,
    )
    if OPENAI_API_KEY
    else None
)
OPENAI_CONTEXT_MIN_INTERVAL = float(os.getenv("OPENAI_CONTEXT_MIN_INTERVAL", "60"))
_openai_context_last_call = 0.0
_openai_context_last_response = None
_openai_context_disabled_until = 0.0


def consultar_openai_contexto_com_cooldown(prompt):
    """Evita repetir a consulta contextual em todos os ciclos do worker."""
    global _openai_context_last_call, _openai_context_last_response
    agora = time.monotonic()
    if (
        _openai_context_last_response is not None
        and agora - _openai_context_last_call < OPENAI_CONTEXT_MIN_INTERVAL
    ):
        logging.info("OpenAI contextual em cooldown; reutilizando resposta anterior.")
        return _openai_context_last_response

    resposta = consultar_openai(prompt)
    _openai_context_last_call = time.monotonic()
    _openai_context_last_response = resposta
    return resposta



# JÃ¡ estÃ¡ vindo da config.py como API_KEY, API_SECRET e TESTNET
# Reutiliza a sessao criada no inicio do modulo; nao inicializar a exchange duas vezes.



# O endpoint e responsabilidade do cliente da exchange ativa.
# Nao manter URL fixa da Bybit neste fluxo, pois a operacao atual usa OKX.
logger.info("Endpoint da exchange ativa gerenciado pelo cliente: %s", ACTIVE_EXCHANGE.upper())

# Log para verificar a carga da chave da API
logger.info("API Key carregada no Sistema Principal")


# URL da API do Agente de IA
API_URL = os.getenv("API_IA_URL", "http://127.0.0.1:8000")


def reconstruir_contexto_indicadores(symbol: str, interval: str, category: str = "linear") -> dict | None:
    """
    ReconstrÃ³i indicadores para posiÃ§Ã£o existente (bybit_existing), garantindo ATR real.
    Retorna dict mÃ­nimo para monitoramento ou None em falha.
    """
    try:
        logger.info(
            f"ðŸ” RECOVERY: reconstruindo indicadores para posiÃ§Ã£o existente | "
            f"symbol={symbol} | interval={interval} | category={category}"
        )
        candles = get_market_data_bybit(symbol, session, interval=interval, limit=CANDLES_MINIMOS)
        if candles is None or candles.empty:
            logger.error(
                f"âŒ RECOVERY FAIL: motivo=df_vazio | symbol={symbol} | interval={interval}"
            )
            return None

        df_ind = calculate_indicators(candles)
        if df_ind is None or df_ind.empty:
            logger.error(
                f"âŒ RECOVERY FAIL: motivo=indicadores_vazios | symbol={symbol} | interval={interval}"
            )
            return None

        last = df_ind.iloc[-1]
        if "ATR" not in df_ind.columns or pd.isna(last.get("ATR")):
            logger.error(
                f"âŒ RECOVERY FAIL: motivo=atr_ausente_ou_nan | symbol={symbol} | interval={interval}"
            )
            return None

        atr = float(last.get("ATR"))
        if atr <= 0 or pd.isna(atr):
            logger.error(
                f"âŒ RECOVERY FAIL: motivo=atr_invalido atr={last.get('ATR')} | "
                f"symbol={symbol} | interval={interval}"
            )
            return None

        rsi = float(last.get("RSI")) if "RSI" in df_ind.columns and pd.notna(last.get("RSI")) else None
        sma_10 = float(last.get("SMA_10")) if "SMA_10" in df_ind.columns and pd.notna(last.get("SMA_10")) else None
        sma_50 = float(last.get("SMA_50")) if "SMA_50" in df_ind.columns and pd.notna(last.get("SMA_50")) else None

        recovered = {
            "ATR": atr,
            "RSI": rsi,
            "SMA_10": sma_10,
            "SMA_50": sma_50,
            "strategy": "bybit_existing_recovered",
            "timeframe": interval,
        }

        logger.info(
            f"âœ… RECOVERY OK: ATR={atr:.6f} | RSI={rsi if rsi is not None else 'N/A'} | "
            f"SMA_10={sma_10 if sma_10 is not None else 'N/A'} | "
            f"SMA_50={sma_50 if sma_50 is not None else 'N/A'}"
        )
        return recovered
    except Exception as e:
        logger.error(
            f"âŒ RECOVERY FAIL: motivo=exception | symbol={symbol} | interval={interval} | erro={e}",
            exc_info=True,
        )
        return None

def analyze_market(market_data):
    """Consulta a IA para decidir se deve entrar no mercado"""
    response = requests.post(f"{API_URL}/analyze_market", json=market_data)
    return response.json()

def send_trade_data(trade_data):
    """Envia os dados do trade para a IA analisar e ajustar"""
    response = requests.post(f"{API_URL}/analyze_trade", json=trade_data)
    return response.json()

def manage_trade(trade_data):
    """Solicita ajustes para um trade em andamento"""
    response = requests.post(f"{API_URL}/manage_trade", json=trade_data)
    return response.json()

def close_trade(trade_data):
    """Consulta a IA para verificar se o trade deve ser encerrado"""
    response = requests.post(f"{API_URL}/close_trade", json=trade_data)
    return response.json()

def send_trade_history(trade_history):
    """Envia o histÃ³rico de trades para a IA avaliar e sugerir melhorias"""
    response = requests.post(f"{API_URL}/trade_ia", json=trade_history)
    return response.json()


# ConfiguraÃ§Ã£o de risco avanÃ§ado
CONFIG_RISCO = 0.4  # Percentual do saldo total 30%
EXPOSICAO_MAXIMA = 0.50  # Limite de exposiÃ§Ã£o total (50% do saldo total)
DRAW_DOWN_LIMITE = 0.10  # Limite de drawdown (10% do saldo total)
RRR_MINIMA = 2  # RelaÃ§Ã£o Risco/Recompensa mÃ­nima



# FunÃ§Ã£o para verificar drawdown
def verificar_drawdown(saldo_inicial, saldo_atual):
    drawdown = (saldo_inicial - saldo_atual) / saldo_inicial
    if drawdown >= DRAW_DOWN_LIMITE:
        logging.warning("Limite de drawdown atingido. Encerrando operaÃ§Ãµes temporariamente.")
        return True
    return False




# O carregamento de mercado ocorre exclusivamente em `main()`.
# Este módulo também é importado pelo gerenciador de workers; chamadas à exchange
# durante o import impedem o processo de chegar ao primeiro ciclo.
# ConfiguraÃ§Ã£o de logs
def log_detalhado(mensagem, dados=None):
    try:
        if dados:
            mensagem = f"{mensagem} | Dados: {dados}"
        logging.info(mensagem)
    except Exception as e:
        # Evitar recursÃ£o, apenas exibir erro no console se o log falhar
        print(f"Erro ao registrar log: {e}")
        print(f"Mensagem de log: {mensagem}")


def clean_numeric_columns(df, columns):
    """
    Limpa e valida colunas numÃ©ricas em um DataFrame.
    """
    for column in columns:
        df[column] = pd.to_numeric(df[column], errors='coerce')  # Converte invÃ¡lidos para NaN
    df.dropna(subset=columns, inplace=True)  # Remove linhas com NaN em colunas especÃ­ficas
    return df


def analisar_mercado_openai(dados_mercado):
    """
    Analisa o mercado usando OpenAI e retorna um JSON estruturado:
    {
        "action": "buy" | "sell" | "hold",
        "confianÃ§a": float,
        "justificativa": str
    }
    """

    import logging, json

    # ============================================================
    # ðŸ” 1. VALIDAÃ‡ÃƒO DO INPUT â€” OBRIGA SER DICIONÃRIO
    # ============================================================
    if not isinstance(dados_mercado, dict):
        logging.error(f"âŒ dados_mercado invÃ¡lido! Recebido: {dados_mercado}")
        return {
            "action": "hold",
            "confianÃ§a": 0,
            "justificativa": "dados_mercado invÃ¡lido. Esperado: dict."
        }

    campos_obrigatorios = ["rsi", "atr", "sma10", "sma50", "volume"]
    for campo in campos_obrigatorios:
        if campo not in dados_mercado:
            logging.error(f"âŒ Campo ausente em dados_mercado: {campo}")
            return {
                "action": "hold",
                "confianÃ§a": 0,
                "justificativa": f"Campo ausente: {campo}"
            }

    # ============================================================
    # ðŸ§± 2. MONTAGEM DO PROMPT â€” SEM RISCO DE FORMATAÃ‡ÃƒO
    # ============================================================
    rsi = float(dados_mercado["rsi"])
    atr = float(dados_mercado["atr"])
    sma10 = float(dados_mercado["sma10"])
    sma50 = float(dados_mercado["sma50"])
    volume = float(dados_mercado["volume"])

    prompt = (
        "Analise os seguintes dados de mercado e responda SOMENTE com um JSON vÃ¡lido:\n\n"
        f"- RSI: {rsi}\n"
        f"- ATR: {atr}\n"
        f"- SMA_10: {sma10}\n"
        f"- SMA_50: {sma50}\n"
        f"- Volume: {volume}\n\n"
        "Regras:\n"
        "1. A resposta deve ser APENAS um JSON puro.\n"
        "2. Campo 'action' deve ser 'buy', 'sell' ou 'hold'.\n"
        "3. Campo 'confianÃ§a' deve ser um nÃºmero entre 0 e 1.\n"
        "4. Campo 'justificativa' deve ter no mÃ¡ximo 150 caracteres.\n\n"
        "Formato esperado:\n"
        "{\n"
        '  "action": "buy",\n'
        '  "confianÃ§a": 0.73,\n'
        '  "justificativa": "RSI baixo e tendÃªncia de alta."\n'
        "}\n"
    )

    # ============================================================
    # ðŸ¤– 3. CHAMADA Ã€ OPENAI
    # ============================================================
    try:
        logging.info("ðŸ”Ž Consultando OpenAI...")

        if OPENAI_CLIENT is None:
            raise RuntimeError("OPENAI_API_KEY nao configurada")

        resposta = OPENAI_CLIENT.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "VocÃª Ã© um analista quantitativo profissional. Responda sempre em JSON puro."},
                {"role": "user", "content": prompt},
            ]
        )
        conteudo = (resposta.choices[0].message.content or "").strip()
        conteudo_limpo = limpar_json_openai(conteudo)

        # ============================================================
        # ðŸ“Œ 4. PARSE JSON COM BLINDAGEM
        # ============================================================
        try:
            dados_json = json.loads(conteudo_limpo)
            return {
                "action": dados_json.get("action", "hold"),
                "confianÃ§a": float(dados_json.get("confianÃ§a", 0)),
                "justificativa": dados_json.get("justificativa", "")
            }

        except json.JSONDecodeError:
            logging.error(f"âŒ OpenAI retornou algo que nÃ£o Ã© JSON: {conteudo_limpo}")
            return {
                "action": "hold",
                "confianÃ§a": 0,
                "justificativa": "OpenAI nÃ£o retornou JSON vÃ¡lido."
            }

    except Exception as e:
        logging.error(f"âŒ Erro ao acessar OpenAI: {e}")
        return {
            "action": "hold",
            "confianÃ§a": 0,
            "justificativa": "Erro de comunicaÃ§Ã£o com a OpenAI."
        }





_analisar_mercado_openai_original = analisar_mercado_openai


def analisar_mercado_openai(dados_mercado):
    """Executa a analise contextual com circuit breaker apos falha."""
    global _openai_context_disabled_until
    agora = time.monotonic()
    if agora < _openai_context_disabled_until:
        logging.info("OpenAI contextual em cooldown; usando hold sem nova chamada.")
        return {
            "action": "hold",
            "confianca": 0,
            "justificativa": "OpenAI em cooldown apos falha de conexao.",
        }

    resultado = _analisar_mercado_openai_original(dados_mercado)
    justificativa = str(resultado.get("justificativa", "")) if isinstance(resultado, dict) else ""
    if "Erro de comunicacao com a OpenAI" in justificativa or "Erro de comunica" in justificativa:
        _openai_context_disabled_until = agora + OPENAI_CONTEXT_TIMEOUT_SECONDS * 12
        logging.warning(
            "OpenAI contextual bloqueada por %.0fs apos falha; usando hold nos proximos ciclos.",
            OPENAI_CONTEXT_TIMEOUT_SECONDS * 12,
        )
    return resultado


def validar_indicadores(df: pd.DataFrame) -> bool:
    if df is None or len(df) < 20:
        logging.warning("âš ï¸ Dados insuficientes para cÃ¡lculo dos indicadores.")
        return False

    if "ATR" not in df.columns or df["ATR"].isnull().all():
        logging.error("ðŸš¨ ATR ausente ou completamente nula.")
        return False

    df["ATR"] = df["ATR"].bfill()

    if pd.isna(df["ATR"].iloc[-1]) or df["ATR"].iloc[-1] == 0:
        logging.error("ðŸš¨ ATR no Ãºltimo candle estÃ¡ nula ou zero.")
        return False

    return True





def avaliar_mercado_com_modelo(df, modelo):
    """
    Avalia o mercado usando indicadores tÃ©cnicos, modelo preditivo e integraÃ§Ã£o com OpenAI e AI Commander.
    Camada 1 â†’ ConsciÃªncia de Mercado
    Camada 2 â†’ Sistema de VotaÃ§Ã£o entre Agentes (AI Commander)
    Camada 3 â†’ GestÃ£o DinÃ¢mica de Risco e ExposiÃ§Ã£o
    """
    import logging
    import pandas as pd
    import requests

    try:
        logging.info("ðŸ§  Iniciando avaliaÃ§Ã£o de mercado...")
        df = calculate_indicators(df)

        # ðŸ§  Camada 1 â€“ ConsciÃªncia de Mercado
        from analise.market_regime import detectar_regime
        regime = detectar_regime(df)
        logging.info(f"ðŸ“Š Regime de mercado detectado: {regime.upper()}")
        tendencia_macro = regime


        # ðŸ§© Camada 2 â€“ Sistema de VotaÃ§Ã£o entre Agentes (AI Commander via FastAPI)
        ativo = os.getenv("ATIVOS", "BTCUSDT")
        logging.info(f"ðŸ§© Ativo em operaÃ§Ã£o: {ativo}")

        # ... depois
        payload = {
            "symbol": ativo,
            "rsi": float(df["RSI"].iloc[-1]),
            "atr": float(df["ATR"].iloc[-1]),
            "tendencia_macro": tendencia_macro,
            "preco": float(df["close"].iloc[-1]),
            "volume": float(df["volume"].iloc[-1]),
            "boll_upper": float(df["Bollinger_Upper"].iloc[-1]),
            "boll_lower": float(df["Bollinger_Lower"].iloc[-1]),
        }

        try:
            resposta = requests.post(f"{API_IA_URL}/tomar_decisao_comandante", json=payload, timeout=10)
            if resposta.status_code == 200:
                dados_decisao = resposta.json()
                logging.info(f"ðŸ§­ [Comandante] DecisÃ£o recebida: {dados_decisao}")
                decisao_votacao = dados_decisao.get("decisao_final", "hold")
            else:
                logging.warning(f"âš ï¸ [Comandante] Falha ao obter decisÃ£o: {resposta.text}")
                decisao_votacao = "hold"
        except Exception as e:
            logging.error(f"âŒ Erro ao consultar Comandante: {e}")
            decisao_votacao = "hold"

        logging.info(f"ðŸ§  Camada 2 concluÃ­da â€“ decisÃ£o do Comandante: {decisao_votacao.upper()}")

        # ðŸš¿ LIMPEZA DE NaN
        if df is not None:
            nan_cols = df.columns[df.isna().any()].tolist()
            if nan_cols:
                logging.warning(f"âš ï¸ Indicadores com NaN detectados: {nan_cols}")
            df = df.dropna()
            if df.columns[df.isna().any()].tolist():
                logging.error(f"âŒ Ainda hÃ¡ NaN apÃ³s limpeza!")
            else:
                logging.info("âœ… Nenhum NaN no DataFrame apÃ³s limpeza.")
        else:
            return {"decisao": "hold", "fonte": "erro", "explicacao": "Erro nos indicadores"}

        colunas = [
            "close", "volume", "RSI", "ATR",
            "Bollinger_Upper", "Bollinger_Lower",
            "EMA12", "EMA26", "EMA50",
            "SMA_10", "SMA_50"
        ]
        if not all(col in df.columns and df[col].notnull().all() for col in colunas):
            return {"decisao": None, "fonte": "erro", "explicacao": "Indicadores incompletos"}

        # ðŸ“Œ Prepara o dado mais recente para o modelo
        ultimo_dado = pd.DataFrame([df.iloc[-1][colunas]])
        predicao = modelo.predict(ultimo_dado)
        acao_modelo = "buy" if predicao[0] == 1 else "sell"
        logging.info(f"ðŸ”Ž AÃ§Ã£o sugerida pelo modelo: {acao_modelo}")

        # ðŸ§  Prompt para OpenAI (Camada de validaÃ§Ã£o cognitiva)
        dados_openai = {
            "close": float(df["close"].iloc[-1]),
            "rsi": float(df["RSI"].iloc[-1]),
            "atr": float(df["ATR"].iloc[-1]),
            "sma10": float(df["SMA_10"].iloc[-1]),
            "sma50": float(df["SMA_50"].iloc[-1]),
            "volume": float(df["volume"].iloc[-1]),
        }

        logging.info("ðŸ§  Consultando OpenAI com JSON estruturado...")
        resposta_openai = analisar_mercado_openai(dados_openai)

        acao_openai = resposta_openai.get("action", "hold")
        confianca_openai = resposta_openai.get("confianÃ§a", 0)
        justificativa_openai = resposta_openai.get("justificativa", "")

        acao_openai = resposta_openai.get("action", "hold")
        

        # ðŸ“Œ Fallback para o modelo se a OpenAI nÃ£o for clara
        if not acao_openai:
            logging.warning("âš ï¸ Resposta da OpenAI ambÃ­gua. Usando prediÃ§Ã£o do modelo.")
            acao_openai = acao_modelo
            fonte = "modelo"
        else:
            fonte = "openai"

        logging.info(f"ðŸ“£ DECISÃƒO FINAL: {acao_openai} | Fonte: {fonte}")

        # ðŸ§® Camada 3 â€“ GestÃ£o DinÃ¢mica de Risco e ExposiÃ§Ã£o
        from analise.gestao_dinamica import calcular_exposicao_dinamica
        try:
            saldo_response = session.get_wallet_balance(accountType="UNIFIED")
            saldo_disponivel = 0.0
            for account in saldo_response.get("result", {}).get("list", []):
                for coin in account.get("coin", []):
                    if str(coin.get("coin", "")).upper() == "USDT":
                        saldo_disponivel = float(
                            coin.get("totalAvailableBalance")
                            or coin.get("availableToWithdraw")
                            or coin.get("walletBalance")
                            or coin.get("equity")
                            or 0
                        )
                        break
            if saldo_disponivel <= 0:
                raise RuntimeError("Saldo USDT disponivel igual a zero.")
        except Exception as exc:
            logging.error(f"Saldo real indisponivel; entrada bloqueada: {exc}")
            return {"decisao": "hold", "fonte": "risk_guard", "explicacao": "Saldo real indisponivel"}
        confianca_ia = 0.8     # Futuro: derivar do score do comandante
        ajustes_risco = calcular_exposicao_dinamica(acao_openai, df, saldo_disponivel, confianca_ia)

        if ajustes_risco:
            logging.info(f"ðŸ§® Camada 3 aplicada com sucesso â†’ {ajustes_risco}")
        else:
            logging.warning("âš ï¸ Falha ao aplicar gestÃ£o dinÃ¢mica de risco.")

        # âœ… Retorno completo
        return {
            "decisao": acao_openai,
            "fonte": fonte,
            "acao_modelo": acao_modelo,
            "explicacao": resposta_openai,
            "justificativa_openai": justificativa_openai,
            "confianca_openai": confianca_openai,
            "tendencia_macro": tendencia_macro,
            "ajustes_risco": ajustes_risco,
            "decisao_comandante": decisao_votacao
        }


    except Exception as e:
        logging.error(f"âŒ Erro ao avaliar mercado com modelo + OpenAI: {e}")
        return {
            "decisao": None,
            "fonte": "erro",
            "explicacao": str(e)
        }




def checar_dataframe_para_IA(df, colunas_esperadas, janela_minima=5):
    print("\n--- Checando DataFrame enviado para a IA ---")
    print("Shape:", df.shape)
    print("Colunas:", list(df.columns))
    print("Primeiras linhas:")
    print(df.head())

    # Checar colunas
    colunas_faltando = [c for c in colunas_esperadas if c not in df.columns]
    if colunas_faltando:
        print("âŒ Faltando colunas:", colunas_faltando)
    else:
        print("âœ… Todas as colunas esperadas estÃ£o presentes.")

    # Checar tamanho mÃ­nimo
    if len(df) < janela_minima:
        print(f"âŒ Tamanho insuficiente ({len(df)}) para janela mÃ­nima ({janela_minima})")
    else:
        print("âœ… Tamanho suficiente para anÃ¡lise.")






# FunÃ§Ã£o para cancelar a ordem
def cancelar_ordem(session, order_id, symbol):
    try:
        log_detalhado(
            f"Enviando requisiÃ§Ã£o para cancelar ordem {order_id}...",
            {"symbol": symbol}
        )

        # CORREÃ‡ÃƒO AQUI: category="linear"
        response = session.cancel_order(
            orderId=order_id,
            category="linear",
            symbol=symbol
        )

        if response and response.get("retCode") == 0:
            log_detalhado(
                f"Ordem {order_id} cancelada com sucesso.",
                {}
            )
        else:
            log_detalhado(
                f"Erro ao cancelar ordem {order_id}: {response.get('retMsg')}",
                {}
            )

    except Exception as e:
        log_detalhado(
            f"Erro ao cancelar ordem {order_id}: {str(e)}",
            {}
        )



def cancelar_ordens_abertas(symbol, session, tempo_limite=60000, continuar_fluxo=None):
    """
    Cancela ordens abertas que nÃ£o atendem aos critÃ©rios definidos.
    
    - Se nenhuma ordem for encontrada e `continuar_fluxo` nÃ£o for passado, o sistema automaticamente
      aguarda e reinicia o robÃ´ apÃ³s 30 segundos.
    """
    try:
        log_detalhado("Listando ordens abertas...", {"symbol": symbol})
        response = session.get_open_orders(category="linear", symbol=symbol)
        
        if response and response.get("retCode") == 0:
            ordens_abertas = response["result"]["list"]
            if not ordens_abertas:
                log_detalhado("Nenhuma ordem aberta encontrada.", {"symbol": symbol})

                # âœ… LIMPA A FLAG DE OPERAÃ‡ÃƒO ATIVA, SE EXISTIR
                import os
                if os.path.exists("operacao_ativa.flag"):
                    os.remove("operacao_ativa.flag")
                    logging.info("ðŸ§¹ Flag de operaÃ§Ã£o ativa removida apÃ³s confirmar que nÃ£o hÃ¡ ordens abertas.")

                # âœ… Callback definido? Executa
                if callable(continuar_fluxo):
                    log_detalhado("Executando callback de continuidade de fluxo...", {})
                    continuar_fluxo()
                else:
                    # ðŸ§  Fallback automÃ¡tico: reinicia o loop principal
                    logging.info("â³ Nenhuma ordem. Aguardando 30s para reiniciar anÃ¡lise...")
                    time.sleep(30)

                    # ðŸ” Loop principal do robÃ´ (ajuste o nome do arquivo se necessÃ¡rio)
                    from trading_bot_geneses import iniciar_robo
                    iniciar_robo(session=session, symbol=symbol, max_trades_per_day=5, loss_limit_percent=3)

                return

            for ordem in ordens_abertas:
                log_detalhado(f"Analisando ordem: {ordem['orderId']}", {"status": ordem["orderStatus"]})

                tempo_atual = int(time.time() * 1000)
                tempo_criacao = int(ordem["createdTime"])
                if tempo_atual - tempo_criacao > tempo_limite:
                    log_detalhado(f"Ordem expirada. Cancelando ordem {ordem['orderId']}...", {})
                    cancelar_ordem(session, ordem["orderId"], symbol)
                    continue

                if ordem.get("stopLoss") or ordem.get("takeProfit"):
                    log_detalhado(f"Ordem de proteÃ§Ã£o identificada. Mantendo ordem {ordem['orderId']}.", {})
                    continue

                if ordem["orderStatus"] == "New" and ordem["symbol"] == symbol:
                    if not verificar_parametros_ordem(ordem):
                        log_detalhado(f"Ordem {ordem['orderId']} nÃ£o atende aos parÃ¢metros definidos. Cancelando...", {})
                        cancelar_ordem(session, ordem["orderId"], symbol)
                    else:
                        log_detalhado(f"Ordem {ordem['orderId']} estÃ¡ em conformidade e serÃ¡ mantida.", {})
                    continue

                if not verificar_parametros_ordem(ordem):
                    log_detalhado(f"Ordem com parÃ¢metros desatualizados. Cancelando ordem {ordem['orderId']}...", {})
                    cancelar_ordem(session, ordem["orderId"], symbol)

        else:
            log_detalhado(f"Erro ao listar ordens abertas: {response.get('retMsg')}", {})

    except Exception as e:
        log_detalhado(f"Erro ao processar cancelamento de ordens: {str(e)}", {})





def preparar_dados(filename):
    """
    Prepara os dados para o treinamento do modelo, sem balanceamento SMOTE.
    Garante todas as features esperadas pelo modelo.
    """
    try:
        logging.info("ðŸ“‚ Carregando dados...")
        df = pd.read_csv(filename)

        # âœ… Calcular indicadores tÃ©cnicos se necessÃ¡rio
        if "SMA_10" not in df.columns or "SMA_50" not in df.columns:
            logging.info("ðŸ“Š Calculando indicadores tÃ©cnicos...")
            df = calculate_indicators(df)

        # âœ… Adiciona colunas esperadas com valor default se estiverem ausentes
        colunas_treinamento = [
            'close', 'volume', 'RSI', 'ATR',
            'Bollinger_Upper', 'Bollinger_Lower',
            'EMA12', 'EMA26', 'EMA50',
            'SMA_10', 'SMA_50'
        ]

        for col in colunas_treinamento:
            if col not in df.columns:
                logging.warning(f"âš ï¸ Coluna ausente: {col}. Preenchendo com 0.0.")
                df[col] = 0.0

        if not validar_indicadores_com_reset(df):
            logging.error("âŒ Indicadores invÃ¡lidos apÃ³s cÃ¡lculo.")
            return None, None

        # âœ… Remover linhas com valores nulos
        df.dropna(inplace=True)

        # âœ… Normalizar todas as features esperadas
        logging.info("âš¡ Normalizando os dados...")
        scaler = MinMaxScaler()
        X = scaler.fit_transform(df[colunas_treinamento])
        X = pd.DataFrame(X, columns=colunas_treinamento)

        # âœ… Criar a coluna target (1 = alta, 0 = queda)
        df['target'] = (df['close'].shift(-1) > df['close']).astype(int)
        y = df['target'].iloc[:len(X)]  # Garante alinhamento com X

        logging.info("âœ… Dados preparados com sucesso.")
        return X, y

    except Exception as e:
        logging.error(f"âŒ Erro ao preparar os dados: {e}")
        return None, None




def clean_numeric_columns(df, columns):
    """
    Limpa e valida colunas numÃ©ricas em um DataFrame.
    """
    for column in columns:
        df[column] = pd.to_numeric(df[column], errors='coerce')  # Converte invÃ¡lidos para NaN
    df.dropna(subset=columns, inplace=True)  # Remove linhas com NaN em colunas especÃ­ficas
    return df



# FunÃ§Ã£o para decidir o tipo de ordem
def decidir_tipo_ordem(df, atr, rsi):
    """
    Decide se a ordem serÃ¡ Market ou Limit com base nos indicadores tÃ©cnicos.
    :param df: DataFrame com os indicadores calculados.
    :param atr: Valor do ATR (Average True Range).
    :param rsi: Valor do RSI (Relative Strength Index).
    :return: 'Market' ou 'Limit'
    """
    # Obter os Ãºltimos valores de fechamento, SMA_10 e SMA_50
    ultimo_preco = df['close'].iloc[-1]
    sma_10 = df['SMA_10'].iloc[-1]
    sma_50 = df['SMA_50'].iloc[-1]
    
    # Logando as informaÃ§Ãµes de entrada
    logging.info(f"Ãšltimo preÃ§o: {ultimo_preco}, SMA_10: {sma_10}, SMA_50: {sma_50}, ATR: {atr}, RSI: {rsi}")
    
    # DecisÃ£o com base nos indicadores
    if rsi < 30 or ultimo_preco < sma_10 < sma_50:
        logging.info("Mercado estÃ¡ sobrevendido ou em retraÃ§Ã£o. Usando ordem Limit.")
        return "Limit"
    elif rsi > 70 or ultimo_preco > sma_10 > sma_50:
        logging.info("Mercado estÃ¡ sobrecomprado ou em tendÃªncia de alta. Usando ordem Market.")
        return "Market"
    else:
        logging.info("CondiÃ§Ãµes neutras detectadas. Usando ordem Limit como padrÃ£o.")
        return "Limit"

# Exemplo de configuraÃ§Ã£o global
INTERVALO_AVALIACAO = "5"  # ConfigurÃ¡vel: "1" (1 minuto), "5" (5 minutos), "15" (15 minutos), "60" (1 hora), etc.


def reavaliar_ordens_abertas(session, symbol):
    try:
        logging.info("ðŸ”„ Reavaliando ordens abertas apÃ³s reinÃ­cio do sistema...")
        response = session.get_open_orders(category="linear", symbol=symbol)
        if response.get("retCode") != 0:
            logging.error(f"âŒ Erro na API da Bybit: {response.get('retMsg')}")
            return True  # Reinicia ciclo, pois nÃ£o Ã© seguro operar

        open_orders = response.get("result", {}).get("list", [])
        if not open_orders:
            logging.info("âœ… Nenhuma ordem aberta encontrada. Loop principal seguirÃ¡ normalmente.")
            return True  # Sinaliza para reiniciar ciclo

        for ordem in open_orders:
            logging.info(f"ðŸ“‹ Analisando ordem: {ordem['orderId']}")
            df = get_market_data_bybit(ordem['symbol'], session)
            if df is None or df.empty:
                logging.warning(f"âš ï¸ Dados indisponÃ­veis para {ordem['symbol']}")
                continue

            df = calculate_indicators(df)
            if not validar_indicadores_com_reset(df):
                continue

            atr = df['ATR'].iloc[-1]
            rsi = df['RSI'].iloc[-1]
            ordem_tipo = decidir_tipo_ordem(df, atr, rsi)

            if ordem_tipo == "Limit" and ordem['orderStatus'] == "New":
                cancelar_ordem(session, ordem['orderId'], ordem['symbol'])
                logging.info(f"ðŸ›‘ Ordem {ordem['orderId']} cancelada.")
            else:
                logging.info(f"âœ… Ordem {ordem['orderId']} mantida.")
        return False  # Sinaliza que ainda hÃ¡ ordens abertas

    except Exception as e:
        logging.error(f"âŒ Erro durante a reavaliaÃ§Ã£o de ordens: {e}")
        return True  # Por seguranÃ§a, reinicia ciclo



# FunÃ§Ã£o para preparar os parÃ¢metros com assinatura e timestamp
def preparar_params(endpoint, params, api_secret):
    """
    Prepara os parÃ¢metros para a API Bybit com assinatura e timestamp.
    """
    try:
        # Verificar se a chave secreta foi fornecida
        if not api_secret:
            logging.error("API secret nÃ£o fornecido.")
            return None

        # Sincronizar tempo com o servidor da Bybit
        diferenca = obter_diferenca_timestamp()
        params["timestamp"] = int(time.time() * 1000) + diferenca  # Ajusta o timestamp
        params["recvWindow"] = 30000  # Janela de recebimento

        # Ordenar os parÃ¢metros e gerar a string para assinatura
        param_string = "&".join([f"{key}={value}" for key, value in sorted(params.items())])
        signature = hmac.new(api_secret.encode(), param_string.encode(), hashlib.sha256).hexdigest()
        params["sign"] = signature

        # Log para auditoria e debugging
        logging.info(f"Endpoint: {endpoint}")
        logging.info(f"ParÃ¢metros antes da assinatura: {param_string}")
        logging.info(f"Assinatura gerada: {signature}")

        return params
    except Exception as e:
        logging.error(f"âŒ Erro ao preparar os parÃ¢metros: {e}")
        return None




def tomar_decisao_com_ia(market_data):
    try:
        if not LEGACY_TOMAR_DECISAO_ENABLED:
            logging.info(
                "[LEGACY_AI] /tomar_decisao desativado; "
                "decisao pertence ao Commander canonico."
            )
            return {
                "decision": "hold",
                "enter_market": False,
                "motivo": "legacy_endpoint_disabled",
                "preserve": True,
            }

        url = f"{API_IA_URL}/tomar_decisao"

        # Converte tipos numpy para float, se necessÃ¡rio
        for k, v in market_data.items():
            if isinstance(v, (np.generic,)):
                market_data[k] = float(v)

        # ValidaÃ§Ã£o rigorosa do payload
        required_fields = {"symbol", "side", "preco_entrada"}
        missing_fields = required_fields - set(market_data.keys())
        if missing_fields:
            logging.error(f"âŒ Payload invÃ¡lido (faltando campos {missing_fields}): {market_data}")

            # â— IMPORTANTE: NUNCA ZERAR LSTM E SCORE
            return {
                "decision": "hold",
                "enter_market": False,
                "preserve": True
            }

        response = requests.post(url, json=market_data)
        response.raise_for_status()
        return response.json()

    except Exception as e:
        logging.error(f"âŒ Erro ao consultar decisÃ£o da IA: {e}", exc_info=True)

        # â— IMPORTANTE: NUNCA ZERAR LSTM E SCORE
        return {
        "decision": "hold",
        "enter_market": False,
        "motivo": "erro_ia_ou_payload",
        "preserve": True
    }



    



from datetime import datetime
import requests
import traceback

API_IA_URL = os.getenv("API_IA_URL", "http://localhost:8000")
LEGACY_TOMAR_DECISAO_ENABLED = os.getenv(
    "LEGACY_TOMAR_DECISAO_ENABLED", "false"
).strip().lower() == "true"

def chamar_manage_trade(trade_info):
    print("ðŸ” DEBUG - trade_info recebido:", trade_info)

    # ðŸ§± ExtraÃ§Ã£o de dados com fallback seguro
    symbol = trade_info.get("symbol")
    qty = float(trade_info.get("qty", 0))
    entry_price = float(
        trade_info.get("entry_price") or
        trade_info.get("avgPrice") or
        trade_info.get("triggerPrice") or
        0
    )
    stop_loss = float(trade_info.get("stop_loss", 0))
    take_profit = float(trade_info.get("take_profit", 0))
    trailing_stop = float(trade_info.get("trailing_stop", 0))
    indicators = trade_info.get("indicators", {})  # Deve conter os dados que a IA usa para calcular o score

    if not symbol or qty == 0 or entry_price == 0:
        print("âš ï¸ Campos obrigatÃ³rios ausentes ou invÃ¡lidos: sÃ­mbolo, entrada ou quantidade.")
        return


    if not indicators or not isinstance(indicators, dict):
        print("âŒ Indicadores ausentes ou mal formatados.")
        return

    
    # âœ… Obter entry_score da IA via /trade_ia
    # âœ… Obter entry_score da IA via /trade_ia
    try:
        campos_esperados = [
            "RSI", "ATR", "SMA_10", "SMA_50",
            "MACD", "Signal_Line", "Volume_Media"
        ]

        indicadores_para_ia = {
            campo: float(indicators.get(campo, 0.0)) for campo in campos_esperados
        }

        # Adiciona o preÃ§o atual como 'preco_atual' (corrigido)
        indicadores_para_ia["preco_atual"] = float(entry_price or 0.0)
        indicadores_para_ia["close"] = float(indicators.get("close", entry_price or 0.0))  # âœ… OBRIGATÃ“RIO


    except Exception as e:
        print(f"âŒ Erro ao preparar indicadores para a IA: {e}")
        return

    # âœ… Envio seguro para o endpoint /trade_ia
    try:
        score_payload = {
            "symbol": symbol,
            "indicators": indicadores_para_ia  # <- obrigatÃ³rio estar assim
        }
        print(f"ðŸ“¤ Enviando para /trade_ia: {score_payload}")
        score_response = requests.post(f"{API_IA_URL}/trade_ia", json=score_payload, timeout=5)
        entry_score = score_response.json().get("entry_score", 0.0)
        print(f"ðŸ“Š entry_score recebido da IA: {entry_score}")
    except Exception as e:
        print(f"âš ï¸ Erro ao obter entry_score da IA: {e}")
        entry_score = 0.0



        # ðŸ“¦ Payload enviado para o endpoint /manage_trade
    timestamp_envio = datetime.utcnow().isoformat()
    payload = {
        "trade_id": trade_info.get("id", "desconhecido"),
        "timestamp": timestamp_envio,
        "symbol": symbol,
        "entry_price": entry_price,
        "position_size": qty,
        "strategy": trade_info.get("strategy", "default"),
        "risk_parameters": {
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "trailing_stop": trailing_stop
        },
        "indicators": indicators,
        "entry_score": entry_score
    }

    # ðŸ›°ï¸ Log para auditoria antes do envio
    print(f"ðŸ“¤ Enviando para /manage_trade: {payload}")

    try:
        dt_inicio = datetime.utcnow()
        response = requests.post(f"{API_IA_URL}/manage_trade", json=payload, timeout=10)

        if response.status_code == 200:
            resultado = response.json()
            ajustes = resultado.get("adjustments", {})
            ts_resposta = resultado.get("timestamp")

            try:
                if isinstance(ts_resposta, str) and "T" in ts_resposta:
                    dt_resposta = datetime.fromisoformat(ts_resposta)
                elif isinstance(ts_resposta, (int, float)) or (isinstance(ts_resposta, str) and ts_resposta.isdigit()):
                    dt_resposta = datetime.utcfromtimestamp(int(ts_resposta) / 1000)
                else:
                    raise ValueError(f"Formato invÃ¡lido para timestamp: {ts_resposta}")
            except Exception as e:
                dt_resposta = datetime.utcnow()
                print(f"âš ï¸ Timestamp mal formatado vindo da IA: '{ts_resposta}' | Usando fallback local. Erro: {e}")

            tempo_execucao = dt_resposta - dt_inicio
            print(f"â±ï¸ Tempo de execuÃ§Ã£o da IA: {tempo_execucao}")
            print(f"[IA] Ajustes recebidos: {ajustes}")
            aplicar_ajustes_na_bybit(trade_info, ajustes)

        else:
            print(f"[IA] Erro HTTP {response.status_code}: {response.text}")

    except Exception as e:
        print(f"[IA] Falha ao chamar a IA: {e}")
        traceback.print_exc()






    # â±ï¸ Registrar inÃ­cio da execuÃ§Ã£o
    dt_inicio = datetime.utcnow()

    url = f"{API_IA_URL}/ajustar_parametros"  # ou o endpoint real da sua IA

    # ðŸ” Chamada para o endpoint da IA
    response = requests.post(url, json=payload, timeout=10)

    if response.status_code == 200:
        try:
            resultado = response.json()

            if not isinstance(resultado, dict):
                raise ValueError(f"âŒ Resposta inesperada da IA. Tipo: {type(resultado)} | ConteÃºdo: {resultado}")

            ajustes = resultado.get("adjustments", {})
            ts_resposta = resultado.get("timestamp")

            # ðŸ•’ Tentativa segura de calcular o tempo de execuÃ§Ã£o
            try:
                if isinstance(ts_resposta, str) and "T" in ts_resposta:
                    dt_resposta = datetime.fromisoformat(ts_resposta)
                elif isinstance(ts_resposta, (int, float)) or (isinstance(ts_resposta, str) and ts_resposta.isdigit()):
                    dt_resposta = datetime.utcfromtimestamp(int(ts_resposta) / 1000)
                else:
                    raise ValueError(f"Formato invÃ¡lido para timestamp: {ts_resposta}")
            except Exception as e:
                dt_resposta = datetime.utcnow()
                print(f"âš ï¸ Timestamp mal formatado vindo da IA: '{ts_resposta}' | Usando fallback local. Erro: {e}")

            tempo_execucao = dt_resposta - dt_inicio
            print(f"â±ï¸ Tempo de execuÃ§Ã£o da IA: {tempo_execucao}")
            print(f"[IA] Ajustes recebidos: {ajustes}")
            aplicar_ajustes_na_bybit(trade_info, ajustes)

        except Exception as e:
            print(f"[IA] Erro ao interpretar resposta: {e}")
            traceback.print_exc()
    else:
        print(f"[IA] Erro HTTP {response.status_code}: {response.text}")
                





def aplicar_ajustes_na_bybit(trade_info, ajustes):
    """
    Atualiza a ordem ou posiÃ§Ã£o na Bybit com novos valores de SL/TP e Trailing Stop.
    """
    trade_id = trade_info.get("id") or trade_info.get("orderId", "desconhecido")
    stop_loss = ajustes.get("new_stop_loss")
    take_profit = ajustes.get("new_take_profit")
    trailing_stop = ajustes.get("new_trailing_stop")

    print(f"[EXCHANGE] Atualizando trade {trade_id} com novos valores:")
    print(f" - ðŸ›‘ Novo Stop-Loss:        {stop_loss}")
    print(f" - ðŸŽ¯ Novo Take-Profit:      {take_profit}")
    print(f" - ðŸ”„ Novo Trailing Stop:    {trailing_stop}")

    # Aqui entraria sua lÃ³gica para alterar via API, como:
    # session.set_trading_stop(...)
    # ou: cancelar e recriar ordem com novos parÃ¢metros




def obter_trades_em_aberto(session, symbol):
    try:
        response = session.get_open_orders(category="linear", symbol=symbol)
        return response.get("result", {}).get("list", [])
    except Exception as e:
        print(f"Erro ao consultar trades abertos: {e}")
        return []


def ativar_modo_producao():
    global BACKTEST_MODE
    live_guard = str(
        os.getenv("ENABLE_LIVE_TRADING", os.getenv("CONFIRMAR_MODO_PRODUCAO", "false"))
    ).strip().lower()
    if live_guard not in {"1", "true", "yes", "on", "sim"}:
        BACKTEST_MODE = True
        os.environ["BACKTEST_MODE"] = "true"
        logging.error(
            "Modo PRODUCAO bloqueado: defina ENABLE_LIVE_TRADING=true (ou CONFIRMAR_MODO_PRODUCAO=true) para liberar ordens reais."
        )
        return False

    # Guardrail adicional para mainnet: exige consentimento explicito.
    if not TESTNET:
        mainnet_guard = str(os.getenv("ALLOW_MAINNET_TRADING", "false")).strip().lower()
        if mainnet_guard not in {"1", "true", "yes", "on", "sim"}:
            BACKTEST_MODE = True
            os.environ["BACKTEST_MODE"] = "true"
            logging.error(
                "Modo PRODUCAO bloqueado em MAINNET: defina ALLOW_MAINNET_TRADING=true para liberar ordens reais."
            )
            return False

        env_file = os.path.basename(str(os.getenv("ROBO_ENV_FILE", ""))).lower()
        if env_file.startswith(".env-") and env_file not in {".env", ".env-prod", ".env-production"}:
            allow_envfile = str(os.getenv("ALLOW_MAINNET_FROM_ENVFILE", "false")).strip().lower()
            if allow_envfile not in {"1", "true", "yes", "on", "sim"}:
                BACKTEST_MODE = True
                os.environ["BACKTEST_MODE"] = "true"
                logging.error(
                    f"Modo PRODUCAO bloqueado: ENV '{env_file}' em MAINNET requer ALLOW_MAINNET_FROM_ENVFILE=true."
                )
                return False

    BACKTEST_MODE = False
    os.environ["BACKTEST_MODE"] = "false"
    logging.info("ðŸŸ¢ MODO PRODUÃ‡ÃƒO ativado! Agora as ordens serÃ£o enviadas para o mercado real.")

    # ProteÃ§Ã£o contra cancelamento indevido
    if not os.path.exists("operacao_ativa.flag"):
        # Se houver uma intenÃ§Ã£o ativa (estratÃ©gia pendente/stateful), nÃ£o cancelar ordens abertas.
        if _INTENTS.has_active(symbol):
            logging.info("[INTENT] Ordem pendente mantida (intenÃ§Ã£o ativa).")
            return True
        if (_ULTIMA_ESTRATEGIA_ESCOLHIDA or "").replace(" ", "").upper() != "MEAN_REVERSION":
            cancelar_ordens_abertas(
                symbol=symbol,
                session=session,
                continuar_fluxo=lambda: logging.info("ðŸ” Nenhuma ordem aberta. Prosseguindo com o fluxo.")
            )
        else:
            logging.info("[MR-NOVA] Ordem pendente mantida (stop-limit ativa).")
    else:
        logging.info("ðŸ”’ Ordem ativa detectada. Cancelamento nÃ£o serÃ¡ executado.")
    return True



def verificar_e_corrigir_flag_operacao(session, symbol):
    """
    Verifica se existe a flag de operaÃ§Ã£o ativa para o sÃ­mbolo informado.
    Se nÃ£o houver ordens reais em aberto, remove a flag e reseta o estado interno.
    """
    global operacao_em_andamento

    flag_path = f"ordem_ativa_{symbol}.flag"  # Flag dinÃ¢mica por ativo

    if os.path.exists(flag_path):
        trades_ativos = obter_trades_em_aberto(session, symbol)

        if not trades_ativos or all(
            (t.get("orderStatus") or "").lower() in ["filled", "cancelled", "rejected"]
            for t in trades_ativos
        ):
            try:
                os.remove(flag_path)
                operacao_em_andamento = False
                logging.info(f"ðŸ§¹ Flag removida: {flag_path} â€” nenhuma ordem ativa encontrada.")
            except Exception as e:
                logging.warning(f"âš ï¸ Falha ao remover flag {flag_path}: {e}")
        else:
            logging.info(f"ðŸ”’ Ordem ainda ativa para {symbol}. Flag mantida.")
    else:
        logging.info(f"âš™ï¸ Nenhuma flag ativa encontrada para {symbol}.")





def momento_ideal_para_entrar(df, estrategia):
    rsi = df["RSI"].iloc[-1]
    macd = df["MACD"].iloc[-1]
    signal = df["Signal_Line"].iloc[-1]
    close = df["close"].iloc[-1]
    sma = df["SMA_10"].iloc[-1]

    if estrategia == "Breakout":
        if macd > signal and rsi > 45 and close > sma:
            logging.info("âœ… Breakout (BUY): CenÃ¡rio promissor para compra detectado.")
            return True
        elif macd < signal and rsi < 55 and close < sma:
            logging.info("âœ… Breakout (SELL): CenÃ¡rio promissor para venda detectado.")
            return True
        else:
            logging.info("âš ï¸ Breakout: Nenhum lado claro ainda. Aguardando confirmaÃ§Ã£o.")
            return False

    elif estrategia == "Mean Reversion":
        if rsi < 30 or rsi > 70:
            logging.info("âœ… Mean Reversion: RSI extremo identificado.")
            return True
        return False

    elif estrategia == "marketmaking":
        if abs(macd - signal) < 1:
            logging.info("âœ… Market Making: Mercado estÃ¡vel, propÃ­cio para MM.")
            return True
        return False

    return False




def monitorar_ate_momento_certo(session, symbol, estrategia, delay=15, max_espera_min=10):
    logging.info(f"â³ Modo espera ativado. Aguardando momento ideal para {estrategia.upper()}...")
    inicio = datetime.now()

    while True:
        df = get_market_data_bybit(symbol, session, interval="5")
        df = calculate_indicators(df)
        if not validar_indicadores_com_reset(df):
            continue

        if momento_ideal_para_entrar(df, estrategia):
            logging.info("âœ… Momento ideal identificado. Prosseguindo com entrada na estratÃ©gia.")
            break

        tempo_passado = (datetime.now() - inicio).total_seconds() / 60
        if tempo_passado > max_espera_min:
            logging.warning("â±ï¸ Tempo mÃ¡ximo de espera esgotado. Saindo do modo monitoramento.")
            break

        time.sleep(delay)


def verificar_ordens_ativas(session, symbol):
    try:
        response = session.get_positions(category="linear", symbol=symbol)
        posicoes = response.get("result", {}).get("list", [])
        return any(abs(float(p.get("size", 0) or 0)) > 0 for p in posicoes)
    except Exception as e:
        logging.error(f"âŒ Erro ao verificar ordens ativas: {e}")
        return False




def verificar_parametros_ordem(ordem: dict, session) -> bool:
    try:
        if "symbol" not in ordem:
            logging.warning(f"Ordem sem sÃ­mbolo: {ordem}")
            return False

        quantidade = float(ordem.get("qty", 0))
        preco_ordem = float(ordem.get("price", 0))
        try:
            preco_mercado = obter_preco_mercado(session, ordem["symbol"])
        except Exception as e:
            logging.warning(f"Erro ao obter preÃ§o de mercado: {e}")
            return False

        if quantidade <= 0:
            return False

        if ordem.get("orderType") not in ["Limit", "Market"]:
            return False

        if preco_ordem > 0 and abs(preco_ordem - preco_mercado) / preco_mercado > 0.05:
            return False  # fora do range de 5%

        return True

    except Exception as e:
        logging.warning(f"âš ï¸ Erro na verificaÃ§Ã£o da ordem: {e}")
        return False



def cache_expirado(arquivo, minutos=60):
    if not os.path.exists(arquivo):
        return True
    modificado_em = os.path.getmtime(arquivo)
    agora = time.time()
    return (agora - modificado_em) > (minutos * 60)




# FunÃ§Ã£o para dispnibilizar relatÂ´roio do Backtest
def converter_numpy_para_nativo(obj):
    if isinstance(obj, np.generic):
        return obj.item()
    elif isinstance(obj, dict):
        return {k: converter_numpy_para_nativo(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [converter_numpy_para_nativo(i) for i in obj]
    else:
        return obj


def checar_dataframe(df, nome="df"):
    if df is None or df.empty:
        logging.warning(f"âš ï¸ {nome} estÃ¡ vazio ou None.")
        return False
    # Se nÃ£o tem 'time', mas tem 'timestamp', renomeia!
    if "time" not in df.columns:
        if "timestamp" in df.columns:
            df.rename(columns={"timestamp": "time"}, inplace=True)
            logging.info(f"ðŸ”„ {nome}: coluna 'timestamp' renomeada para 'time'.")
        else:
            logging.warning(f"âš ï¸ {nome} nÃ£o contÃ©m 'time' nem 'timestamp'. Colunas: {df.columns.tolist()}")
            return False
    if len(df) == 0:
        logging.warning(f"âš ï¸ {nome} nÃ£o possui linhas.")
        return False
    return True




def log_relatorio_backtest(relatorio, logger=logging):
    if not relatorio:
        logger.warning("âš ï¸ RelatÃ³rio de backtest vazio ou invÃ¡lido.")
        return

    # Se vier lista, pega o primeiro elemento
    if isinstance(relatorio, list):
        if len(relatorio) == 0:
            logger.warning("âš ï¸ RelatÃ³rio de backtest vazio ou invÃ¡lido.")
            return
        r = relatorio[0]
    # Se vier dict (CASO REAL), usa direto
    elif isinstance(relatorio, dict):
        r = relatorio
    else:
        logger.warning("âš ï¸ Formato inesperado de relatÃ³rio de backtest.")
        return

    # Recalcula mÃ©tricas a partir dos trades quando disponÃ­veis para evitar inconsistÃªncias.
    trades = list(r.get('trades', []) or [])
    calc = None
    if trades:
        lucros = []
        perdas = []
        for t in trades:
            try:
                lucro = float(t.get('lucro', 0))
            except Exception:
                lucro = 0.0
            if lucro > 0:
                lucros.append(lucro)
            elif lucro < 0:
                perdas.append(lucro)

        num_trades = len(trades)
        num_wins = len(lucros)
        num_losses = len(perdas)
        winrate = (num_wins / num_trades) * 100 if num_trades else 0.0
        lucro_medio = (sum(lucros) / num_wins) if num_wins else 0.0
        perda_media = (sum(perdas) / num_losses) if num_losses else 0.0
        calc = {
            "num_trades": num_trades,
            "num_wins": num_wins,
            "num_losses": num_losses,
            "winrate": winrate,
            "lucro_medio": lucro_medio,
            "perda_media": perda_media,
        }

    logger.info("\nðŸ“Š ==== RESUMO DO BACKTEST ====")
    logger.info(f"EstratÃ©gia      : {r.get('estrategia', '')}")
    logger.info(f"DireÃ§Ã£o         : {r.get('direcao', '')}")
    saldo_inicial = float(r.get('saldo_inicial', 0) or 0)
    saldo_final = float(r.get('saldo_final', 0) or 0)
    lucro_total = float(r.get('lucro_total', r.get('lucro', 0)) or 0)
    retorno_calc = (lucro_total / saldo_inicial) * 100 if saldo_inicial else 0.0

    logger.info(f"Saldo Inicial   : {saldo_inicial:.2f}")
    logger.info(f"Saldo Final     : {saldo_final:.2f}")
    logger.info(f"Lucro Total     : {lucro_total:.4f}")
    logger.info(f"Retorno (%)     : {retorno_calc:.2f}")
    logger.info(f"Trades Feitos   : {(calc or {}).get('num_trades', r.get('num_trades', 0))}")
    logger.info(f"Wins            : {(calc or {}).get('num_wins', r.get('num_wins', 0))}")
    logger.info(f"Losses          : {(calc or {}).get('num_losses', r.get('num_losses', 0))}")
    logger.info(f"Win Rate (%)    : {(calc or {}).get('winrate', r.get('winrate', r.get('win_rate', 0))):.2f}")
    logger.info(f"Lucro MÃ©dio     : {(calc or {}).get('lucro_medio', r.get('lucro_medio', 0)):.2f}")
    logger.info(f"Perda MÃ©dia     : {(calc or {}).get('perda_media', r.get('perda_media', 0)):.2f}")
    logger.info(f"Max Drawdown    : {r.get('max_drawdown_pct', r.get('max_drawdown', 0)):.2f}")

    logger.info("\nðŸ“‹ ==== TRADES EXECUTADOS ====")
    logger.info(f"{'N':>3} | {'Data':19} | {'Side':^4} | {'Entrada':>7} | {'SaÃ­da':>7} | {'Lucro':>7} | {'Motivo':>7}")
    logger.info("-" * 75)

    for i, t in enumerate(trades, 1):
        motivo = t.get('motivo_saida', '')
        try:
            lucro_val = float(t.get('lucro', 0))
        except Exception:
            lucro_val = 0.0
        # Se houver lucro com "stop", provavelmente foi stop/trailing com ganho.
        if motivo == "stop" and lucro_val > 0:
            motivo = "stop_gain"
        logger.info(
            f"{i:3} | {t.get('data',''):19} | {t.get('side',''):^4} | "
            f"{t.get('preco_entrada',0):7.4f} | {t.get('preco_saida',0):7.4f} | "
            f"{t.get('lucro',0):7.4f} | {motivo[:12]:>12}"
        )


def _strategy_logs_v2_enabled() -> bool:
    try:
        return os.getenv("STRATEGY_LOGS_V2", "true").strip().lower() in ("1", "true", "yes", "y", "on")
    except Exception:
        return True


def log_validacao_por_estrategia(
    estrategia: str,
    melhor: dict,
    resultados: dict,
    decisao_final: bool,
    etapa: str,
    df: "pd.DataFrame | None" = None,
    relatorio_vencedora: object = None,
    logger=logging,
):
    """
    Telemetria (somente logs) especÃ­fica por estratÃ©gia.
    NÃ£o altera regras/thresholds/decisÃ£o; apenas explica o que jÃ¡ existe no fluxo.
    """
    estrategia_norm = (estrategia or "").replace(" ", "").upper()

    if not estrategia_norm:
        logger.warning("[VALIDAÃ‡ÃƒO] EstratÃ©gia vazia; sem logs especÃ­ficos.")
        return

    lucro_total = float(melhor.get("lucro_total", melhor.get("lucro", 0)) or 0)
    winrate = float(melhor.get("winrate", melhor.get("win_rate (%)", 0)) or 0)
    direcao = (melhor.get("direcao", "N/A") or "N/A").upper()
    regime = resultados.get("regime")
    candidatas = resultados.get("candidatas", [])
    sem_trade = resultados.get("sem_trade", [])

    # CabeÃ§alho comum
    logger.info(f"\n[{estrategia_norm}][VALIDAÃ‡ÃƒO] Etapa={etapa}")
    logger.info(f"[{estrategia_norm}][VALIDAÃ‡ÃƒO] Resultado={'AUTORIZADA' if decisao_final else 'BLOQUEADA'}")
    logger.info(f"[{estrategia_norm}][VALIDAÃ‡ÃƒO] DireÃ§Ã£o={direcao} | LucroTotal={lucro_total:.2f} | WinRate={winrate:.1f}% | Regime={regime}")
    logger.info(f"[{estrategia_norm}][VALIDAÃ‡ÃƒO] Candidatas={candidatas} | SemTrade={sem_trade}")

    # Checks mÃ­nimos por estratÃ©gia (sem inventar thresholds; sÃ³ expor o que jÃ¡ estÃ¡ no fluxo)
    if estrategia_norm == "MEAN_REVERSION":
        logger.info(f"[MEAN_REVERSION][VALIDAÃ‡ÃƒO] âœ“ EstratÃ©gia stateful (FSM) â€” execuÃ§Ã£o depende do prÃ³ximo tick/candle.")
        # Mostra status de intent (se existir) sem alterar comportamento
        try:
            intent = _INTENTS.get_active(melhor.get("symbol") or locals().get("symbol")) if "_INTENTS" in globals() else None
            if intent and getattr(intent, "strategy", None) == "MEAN_REVERSION":
                ttl = int(getattr(intent, "expires_ts", 0) - time.time())
                logger.info(f"[MEAN_REVERSION][VALIDAÃ‡ÃƒO] âœ“ Intent ativa | expira_em={max(0, ttl)}s | reason={getattr(intent, 'reason', '')}")
        except Exception:
            pass

    elif estrategia_norm == "EARLY_TREND":
        enabled = os.getenv("EARLY_TREND_ENABLED", "false").strip().lower() in ("1", "true", "yes", "y", "on")
        logger.info(f"[EARLY_TREND][VALIDAÃ‡ÃƒO] {'âœ“' if enabled else 'âœ—'} EARLY_TREND_ENABLED={enabled}")
        logger.info(f"[EARLY_TREND][VALIDAÃ‡ÃƒO] âœ“ EstratÃ©gia stateful/presente no funil apenas quando elegÃ­vel.")

    elif estrategia_norm == "MARKET_MAKING_LATERAL":
        logger.info("[MARKET_MAKING_LATERAL][VALIDAÃ‡ÃƒO] âœ“ EstratÃ©gia de lateralidade/range â€” elegÃ­vel tipicamente em Regime 3.")

    # Contexto de mercado (apenas valores observados; nÃ£o cria novas regras)
    if df is not None and not df.empty:
        try:
            last = df.dropna(subset=["close"]).iloc[-1]
            close = float(last.get("close"))
            atr = float(last.get("ATR")) if "ATR" in df.columns and pd.notna(last.get("ATR")) else None
            rsi = float(last.get("RSI")) if "RSI" in df.columns and pd.notna(last.get("RSI")) else None
            logger.info(f"[{estrategia_norm}][VALIDAÃ‡ÃƒO] Snapshot | close={close:.2f} | ATR={atr if atr is not None else 'N/A'} | RSI={rsi if rsi is not None else 'N/A'}")
        except Exception:
            pass

    # RelatÃ³rio bruto (quando existir) â€” sem tentar formatar como breakout
    if isinstance(relatorio_vencedora, str) and relatorio_vencedora.strip():
        logger.info(f"[{estrategia_norm}][VALIDAÃ‡ÃƒO] RelatÃ³rio (raw) disponÃ­vel | chars={len(relatorio_vencedora)}")


def fallback_df_curto(df, direcao_tecnica, score_tecnico):
    """
    Fallback quando o DataFrame estÃ¡ curto demais para o mÃ³dulo tÃ©cnico decidir.
    NÃƒO interfere no sistema principal â€” apenas substitui o trecho que trava a decisÃ£o.
    """
    import logging

    MIN_CANDLES = 10  # antes era 12â€“13, agora mais flexÃ­vel
    qtd = len(df)

    logging.info(f"ðŸ” [FALLBACK] Candles disponÃ­veis: {qtd}")

    # -----------------------
    # 1) DF realmente curto
    # -----------------------
    if qtd < MIN_CANDLES:
        logging.warning(f"âš ï¸ DF curto ({qtd}). Aplicando fallback inteligente.")

        # 1.1 â€” Se jÃ¡ existe direÃ§Ã£o tÃ©cnica com score â†’ confiar nela
        if score_tecnico is not None and score_tecnico > 0:
            logging.info(f"âš ï¸ Usando direÃ§Ã£o tÃ©cnica detectada: {direcao_tecnica}")
            return {
                "action": direcao_tecnica,
                "fonte": "fallback_tecnico",
                "confianca": 0.55,
                "justificativa": f"DF curto ({qtd} candles), mas tÃ©cnica indicou direÃ§Ã£o."
            }

        # 1.2 â€” Se nÃ£o houver tÃ©cnica confiÃ¡vel â†’ usar RSI
        rsi = df["RSI"].iloc[-1]

        if rsi > 70:
            return {
                "action": "sell",
                "fonte": "fallback_rsi",
                "confianca": 0.52,
                "justificativa": "RSI alto indica sobrecompra â†’ SELL."
            }

        if rsi < 30:
            return {
                "action": "buy",
                "fonte": "fallback_rsi",
                "confianca": 0.52,
                "justificativa": "RSI baixo indica sobrevenda â†’ BUY."
            }

        # 1.3 â€” Nada claro â†’ HOLD
        return {
            "action": "hold",
            "fonte": "fallback_neutro",
            "confianca": 0.40,
            "justificativa": "DF curto e indicadores neutros."
        }


def validar_resultado_backtest(
    status_funil,
    num_trades,
    retorno,
    direcao,
    score_estrategia,
    regime_favoravel,
    min_trades=5,
    setup_confirmado=False,
):
    """
    Estados de validação pós-backtest:
    - APROVADO
    - AGUARDANDO_TRIGGER
    - NEGADO
    """
    status_funil = str(status_funil or "").strip().lower()
    direcao = str(direcao or "").strip().lower()
    num_trades = int(num_trades or 0)
    retorno = float(retorno or 0.0)
    score_ausente = score_estrategia is None
    score_estrategia = float(score_estrategia or 0.0)
    regime_favoravel = bool(regime_favoravel)
    min_trades = max(int(min_trades or 1), 1)
    setup_confirmado = bool(setup_confirmado)

    if score_ausente and direcao in ("buy", "sell"):
        return "NEGADO"

    # Um setup confirmado no candle atual não deve ser apagado por um
    # backtest curto sem operações. Nesse caso, aguarda-se o gatilho real;
    # não se aprova a ordem automaticamente.
    if num_trades == 0 and setup_confirmado and direcao in ("buy", "sell") and regime_favoravel:
        return "AGUARDANDO_TRIGGER"

    if status_funil == "sem_evidencia":
        return "NEGADO"

    if num_trades > 0:
        if num_trades < min_trades:
            return "NEGADO"
        if retorno > 0:
            return "APROVADO"
        if score_estrategia >= 6.0 and regime_favoravel:
            return "AGUARDANDO_TRIGGER"
        return "NEGADO"

    if num_trades == 0 and status_funil == "evidencia" and direcao in ("buy", "sell"):
        return "AGUARDANDO_TRIGGER"

    return "NEGADO"


def limpar_json_openai(resposta):
    if not resposta:
        return None
    resposta = str(resposta).strip()
    if resposta.startswith("```"):
        resposta = resposta.replace("```json", "")
        resposta = resposta.replace("```", "")
        resposta = resposta.strip()
    return resposta

    # -----------------------
    # 2) DF ok â†’ NÃƒO usa fallback
    # -----------------------
    logging.info("ðŸŸ¢ DF suficiente â€” fallback nÃ£o necessÃ¡rio.")
    return None  # quer dizer: â€œsegue o fluxo normalâ€




import os
import logging
import requests
import pandas as pd

logger = logging.getLogger(__name__)


def obter_previsao_next_candle(df_lstm: pd.DataFrame):
    """
    Envia a janela (10x5) para o serviÃ§o LSTM (FastAPI do LSTM)
    e retorna o preÃ§o previsto da prÃ³xima candle.
    Usa a variÃ¡vel de ambiente LSTM_API_URL.
    """
    if os.getenv("LSTM_ENABLED", "1").strip().lower() in {"0", "false", "no", "off"}:
        logger.info("[LSTM SERVICE] desativado para este ativo; seguindo sem previsão LSTM.")
        return None

    url = os.getenv("LSTM_API_URL")

    if not url:
        logger.error("âŒ LSTM_API_URL nÃ£o definida no .env â€” impossÃ­vel chamar o serviÃ§o LSTM.")
        return None

    try:
        payload = {"data": df_lstm.values.tolist()}
        logger.info(f"[LSTM SERVICE] Enviando janela para {url} | shape={df_lstm.shape}")

        r = requests.post(url, json=payload, timeout=3)

        if r.status_code != 200:
            logger.error(f"[LSTM SERVICE] HTTP {r.status_code} â†’ {r.text}")
            return None

        data = r.json()
        # Um input fora do domínio de treino torna a previsão não confiável
        # (principalmente quando o serviço precisou aplicar clipping). Não
        # deixar esse valor contaminar a decisão enquanto o auto-retrain roda.
        if data.get("out_of_range") and os.getenv("LSTM_ALLOW_OUT_OF_RANGE", "0") != "1":
            logger.warning("[LSTM SERVICE] fallback_poucos_dados: input fora do range; previsao ignorada")
            return None
        predicted_price = data.get("predicted_price") or data.get("prediction")

        if predicted_price is None:
            logger.error(f"[LSTM SERVICE] Resposta sem 'predicted_price': {data}")
            return None

        logger.info(f"[LSTM SERVICE] ðŸ”® PrevisÃ£o recebida: {predicted_price}")
        return float(predicted_price)

    except Exception as e:
        logger.error(f"[LSTM SERVICE] Erro ao chamar serviÃ§o LSTM: {e}")
        return None


def preparar_previsao_lstm_persistencia(runtime_prediction, acao, real_price):
    """Map a runtime LSTM result without silently fabricating numeric zeros."""
    candidate = runtime_prediction
    if candidate is None and isinstance(acao, dict):
        candidate = acao.get("predicted_price")
    try:
        predicted_price = float(candidate)
    except (TypeError, ValueError) as exc:
        raise ValueError("LSTM prediction missing or non-numeric") from exc
    if not np.isfinite(predicted_price):
        raise ValueError("LSTM prediction is not finite")

    explicit_direction = acao.get("direction") if isinstance(acao, dict) else None
    confidence_raw = acao.get("confidence") if isinstance(acao, dict) else None
    try:
        confidence_score = float(confidence_raw) if confidence_raw is not None else None
    except (TypeError, ValueError):
        confidence_score = None

    real_price = float(real_price)
    direction_predicted = explicit_direction or (
        "buy" if predicted_price > real_price
        else "sell" if predicted_price < real_price
        else "hold"
    )
    error_percent = abs((predicted_price - real_price) / real_price) * 100 if real_price else None
    return {
        "predicted_price": predicted_price,
        "direction_predicted": direction_predicted,
        "confidence_score": confidence_score,
        "error_percent": error_percent,
    }


global operacao_em_andamento
operacao_em_andamento = False  # Fora da funÃ§Ã£o (topo do cÃ³digo)

# ETAPA 3: guarda a ultima estratÃ©gia vencedora para evitar cancelamentos/limpeza indevida
# enquanto a MR nova estiver armada (REVERSAO_EM_FORMACAO).
_ULTIMA_ESTRATEGIA_ESCOLHIDA: str | None = None
_INTENTS = IntentManager()

INTERVAL = os.getenv("INTERVAL", "15")  # padrÃ£o Ã© M15 se nÃ£o definir




def _okx_active_protection(pos):
    """Retorna (tem_SL, tem_TP, tem_algo) usando o estado real do OCO."""
    algos = pos.get("closeOrderAlgo") or []
    if not isinstance(algos, list):
        algos = []
    active = [a for a in algos if str(a.get("state") or "").lower() not in
              ("canceled", "cancelled", "order_failed")]
    has_sl = any(a.get("slTriggerPx") not in (None, "", "0", "0.0", 0) for a in active)
    has_tp = any(a.get("tpTriggerPx") not in (None, "", "0", "0.0", 0) for a in active)
    return has_sl, has_tp, bool(active)


def _okx_price_and_tick(session, symbol):
    last = None
    tick = None
    try:
        rows = session.get_tickers(category="linear", symbol=symbol).get("result", {}).get("list", [])
        if rows:
            last = float(rows[0].get("lastPrice") or 0) or None
    except Exception:
        pass
    try:
        rows = session.get_instruments_info(category="linear", symbol=symbol).get("result", {}).get("list", [])
        if rows:
            tick = float((rows[0].get("priceFilter") or {}).get("tickSize") or 0) or None
    except Exception:
        pass
    return last, tick or 0.001


def _armar_protecao_posicao_okx(session, symbol, pos, entry_price, side):
    """Arma e confirma SL/TP; nunca considera a posição protegida só pela resposta HTTP."""
    if not hasattr(session, "set_trading_stop") or not entry_price:
        return False

    side_norm = str(side).strip().lower()
    if side_norm not in ("sell", "buy"):
        logging.error("[PROTECTION_PENDING] %s lado inválido: %r", symbol, side)
        return False
    close_side = "buy" if side_norm == "sell" else "sell"
    retries = max(1, int(os.getenv("PROTECTION_RETRIES", "6")))
    delay = max(0.2, float(os.getenv("PROTECTION_RETRY_DELAY_SECONDS", "1.0")))
    buffer_ticks = max(1, int(os.getenv("PROTECTION_PRICE_BUFFER_TICKS", "2")))

    for tentativa in range(1, retries + 1):
        try:
            atual = session.get_positions(category="linear", symbol=symbol)
            rows = atual.get("result", {}).get("list", []) if isinstance(atual, dict) else []
            aberta = next((row for row in rows if abs(float(row.get("size") or 0)) > 0), None)
            if not aberta:
                logging.warning("[PROTECTION_PENDING] %s posição ainda não refletida (%s/%s)", symbol, tentativa, retries)
                time.sleep(delay)
                continue

            tem_sl, tem_tp, tem_algo = _okx_active_protection(aberta)
            if tem_sl and tem_tp:
                logging.info("[PROTECTION_CONFIRMED] %s SL e TP confirmados pela OKX", symbol)
                return True

            entry = float(aberta.get("entryPrice") or aberta.get("avgPrice") or entry_price)
            last, tick = _okx_price_and_tick(session, symbol)
            if not last:
                raise RuntimeError("preço atual indisponível")
            sl_pct = max(float(os.getenv("SL_PCT_MIN", "0.0045")), 0.001)
            tp_pct = sl_pct * max(float(os.getenv("RRR", "1.8")), 1.0)
            if side_norm == "sell":
                stop_loss = max(entry * (1 + sl_pct), last + buffer_ticks * tick)
                take_profit = min(entry * (1 - tp_pct), last - buffer_ticks * tick)
                stop_loss = float((Decimal(str(stop_loss)) / Decimal(str(tick))).to_integral_value(rounding=ROUND_CEILING) * Decimal(str(tick)))
                take_profit = float((Decimal(str(take_profit)) / Decimal(str(tick))).to_integral_value(rounding=ROUND_FLOOR) * Decimal(str(tick)))
            else:
                stop_loss = min(entry * (1 - sl_pct), last - buffer_ticks * tick)
                take_profit = max(entry * (1 + tp_pct), last + buffer_ticks * tick)
                stop_loss = float((Decimal(str(stop_loss)) / Decimal(str(tick))).to_integral_value(rounding=ROUND_FLOOR) * Decimal(str(tick)))
                take_profit = float((Decimal(str(take_profit)) / Decimal(str(tick))).to_integral_value(rounding=ROUND_CEILING) * Decimal(str(tick)))

            pos_side = str(aberta.get("posSide") or "").lower()
            if pos_side not in ("long", "short"):
                pos_side = None
            resposta = session.set_trading_stop(
                category="linear", symbol=symbol, stopLoss=stop_loss,
                takeProfit=take_profit, side=close_side, posSide=pos_side,
                replace_existing=tem_algo,
            )
            if isinstance(resposta, dict) and resposta.get("retCode") not in (None, 0):
                raise RuntimeError(resposta.get("retMsg") or str(resposta))

            for _ in range(4):
                time.sleep(0.35)
                ver = session.get_positions(category="linear", symbol=symbol)
                vrows = ver.get("result", {}).get("list", []) if isinstance(ver, dict) else []
                vpos = next((row for row in vrows if abs(float(row.get("size") or 0)) > 0), None)
                if vpos:
                    vsl, vtp, _ = _okx_active_protection(vpos)
                    if vsl and vtp:
                        logging.warning("[PROTECTION_CONFIRMED] %s | lado=%s | SL=%s | TP=%s", symbol, side_norm, stop_loss, take_profit)
                        return True
            raise RuntimeError("OKX não confirmou closeOrderAlgo com SL e TP")
        except Exception as exc:
            logging.warning("[PROTECTION_PENDING] %s tentativa %s/%s: %s", symbol, tentativa, retries, exc)
            if tentativa < retries:
                time.sleep(delay)

    logging.error("[PROTECTION_PENDING] %s permanece bloqueada até a OKX confirmar SL e TP", symbol)
    return False


def iniciar_robo(session, symbol, max_trades_per_day, loss_limit_percent, interval=INTERVAL):
    """
    Inicia o robÃ´ de trading apenas quando fecha um novo candle no timeframe principal.

    :param session: SessÃ£o da API Bybit.
    :param symbol: Par de negociaÃ§Ã£o.
    :param max_trades_per_day: Limite diÃ¡rio de trades.
    :param loss_limit_percent: Limite de perda por operaÃ§Ã£o.
    :param interval: Timeframe em minutos ('1', '5', '15', '60' etc).
    """

    # ðŸ”’ Valores padrÃ£o para evitar UnboundLocalError
    acao = None
    stop_loss = None
    take_profit = None
    trailing_stop = None

    # ðŸ”„ InicializaÃ§Ãµes gerais
    loop_counter = 0
    ultimo_timestamp = None
    ultimo_ts_1m = None
    ultimo_ts_5m = None
    ultimo_ts_15m = None
    ultimo_log_sem_novo_candle = 0.0
    ultimo_log_risk_block = 0.0
    ultimo_watchdog = 0.0
    ultimo_inicio_ciclo = None
    ultimo_candle_visto_em = time.time()
    ultimo_alerta_candle_parado = 0.0
    candle_stale_after_seconds = max(60.0, float(os.getenv("GENESIS_CANDLE_STALE_SECONDS", "600")))
    candle_alert_interval_seconds = max(30.0, float(os.getenv("GENESIS_CANDLE_ALERT_INTERVAL_SECONDS", "60")))

    logging.info(f"ðŸŸ¢ RobÃ´ iniciado para {symbol} | Timeframe principal: {interval}m")
    logging.info(f"âš™ï¸ ParÃ¢metros de execuÃ§Ã£o: symbol={symbol}, max_trades_per_day={max_trades_per_day}, loss_limit_percent={loss_limit_percent}, interval={interval}")

    # Controla tempo do Ãºltimo candle fora do loop, se nÃ£o existir ainda
    if 'tempo_ultimo_candle' not in globals():
        global tempo_ultimo_candle
        tempo_ultimo_candle = time.time()

    while True:  # Loop infinito para monitoramento contÃ­nuo
        inicio_ciclo = time.perf_counter()
        if ultimo_inicio_ciclo is not None:
            try:
                record_timing(
                    "GENESIS_CYCLE",
                    inicio_ciclo - ultimo_inicio_ciclo,
                    role="worker",
                    symbol=symbol,
                    operation="iniciar_robo.loop",
                    extra={"loop": loop_counter},
                )
            except Exception as metrics_exc:
                logging.warning("[PERFORMANCE] Falha ao registrar duração do ciclo: %s", metrics_exc)
        ultimo_inicio_ciclo = inicio_ciclo
        loop_counter += 1
        idade_candle = max(0.0, time.time() - ultimo_candle_visto_em)
        candle_stale = idade_candle > candle_stale_after_seconds
        write_heartbeat(
            symbol,
            state="stale_candle" if candle_stale else "running",
            loop=loop_counter,
            last_candle_age_seconds=round(idade_candle, 3),
            candle_stale_after_seconds=candle_stale_after_seconds,
        )
        if candle_stale and time.time() - ultimo_alerta_candle_parado >= candle_alert_interval_seconds:
            logging.warning(
                "[CANDLE_STALE] Nenhum novo candle observado por %.1fs | limite=%.1fs | symbol=%s | timeframe=%sm",
                idade_candle,
                candle_stale_after_seconds,
                symbol,
                interval,
            )
            ultimo_alerta_candle_parado = time.time()
        logging.info(f"ðŸ”„ Loop ativo {loop_counter}: aguardando novo candle ou evento...")

        agora_watchdog = time.time()
        if agora_watchdog - ultimo_watchdog >= 60:
            reconcile_stale_decisions()
            ultimo_watchdog = agora_watchdog

        # Risk guard: block new entries if daily/weekly loss limits were reached.
        try:
            risk_state = evaluate_risk_limits(symbol=symbol)
            process_risk_transition(risk_state, symbol=symbol, source="trading_bot_geneses.main_loop")
            if risk_state.get("blocked"):
                now = time.time()
                if now - ultimo_log_risk_block >= 60:
                    logging.warning(
                        "[RISK_GUARD] ENTRADAS BLOQUEADAS | motivo=%s | day_loss_global=%.2f | week_loss_global=%.2f | day_loss_symbol=%.2f",
                        risk_state.get("reason"),
                        float(risk_state.get("day_loss_usdt_global", 0.0)),
                        float(risk_state.get("week_loss_usdt_global", 0.0)),
                        float(risk_state.get("day_loss_usdt_symbol", 0.0)),
                    )
                    ultimo_log_risk_block = now
                write_kill_switch_flag(risk_state)
                time.sleep(7)
                # O limite bloqueia novas entradas, mas nÃ£o pode interromper
                # o monitoramento/fechamento de uma posiÃ§Ã£o jÃ¡ aberta.
            else:
                # Clear stale kill switch file when guard is healthy again.
                if os.path.exists("risk_kill.flag"):
                    try:
                        os.remove("risk_kill.flag")
                    except Exception:
                        pass
        except Exception as e:
            logging.warning(f"[RISK_GUARD] Falha ao avaliar limites de risco: {e}")
        
        # === ðŸ” Verifica posiÃ§Ã£o aberta na Bybit ===        
        try:
            posicoes = session.get_positions(category="linear", symbol=symbol)
            if posicoes.get("retCode") == 0:
                aberta = any(abs(float(p.get("size") or p.get("positionAmt") or 0)) > 0 for p in posicoes["result"]["list"])
                if aberta:
                    pos = posicoes["result"]["list"][0]
                    entry = float(pos.get("entryPrice") or pos.get("avgPrice") or 0)
                    if entry == 0:
                        try:
                            entry = OrdemUtils.get_entry_price(session, symbol)
                            logging.warning(f"âš ï¸ entry_price nÃ£o veio da Bybit, recuperado via histÃ³rico: {entry}")
                        except Exception as e:
                            logging.warning(f"âš ï¸ Falha ao recuperar entry_price pelo histÃ³rico: {e}")
                    size = float(pos.get("size") or 0)
                    side = pos.get("side") or pos.get("posSide")
                    side_norm = str(side or "").strip().lower()
                    if side_norm in ("long", "buy"):
                        side = "Buy"
                    elif side_norm in ("short", "sell"):
                        side = "Sell"
                    else:
                        side = None
                    if not side and size != 0:
                        # Fallback para respostas OKX em modo net, que podem
                        # não trazer posSide/side explicitamente.
                        side = "Sell" if size < 0 else "Buy"
                    if not side:
                        raise ValueError("lado da posição ausente")
                    trade_id = OrdemUtils.get_last_filled_order_id(session, symbol) or "from_bybit"
                    recovered_entry = float(entry)
                    recovered_quantity = abs(float(size))
                    try:
                        history = session.get_order_history(category="linear", symbol=symbol, limit=20)
                        history_rows = (history.get("result", {}).get("list", []) or []) if isinstance(history, dict) else []
                        recovered_order = next(
                            (item for item in history_rows
                             if trade_id != "from_bybit"
                             and str(item.get("orderId") or item.get("ordId") or "") == str(trade_id)
                             and str(item.get("orderStatus") or item.get("state") or "").lower() in ("filled", "partiallyfilled")),
                            None,
                        )
                        if recovered_order:
                            recovered_entry = float(
                                recovered_order.get("avgPrice")
                                or recovered_order.get("avgPx")
                                or recovered_entry
                            )
                            recovered_quantity = abs(float(
                                recovered_order.get("cumExecQty")
                                or recovered_order.get("accFillSz")
                                or recovered_order.get("fillSz")
                                or recovered_quantity
                            ))
                    except Exception as exc:
                        logging.warning("[RECOVERY] Falha ao ler detalhes da ordem preenchida: %s", type(exc).__name__)

                    # Recupera uma posição real que já existia antes do
                    # worker iniciar. Esse caminho não passa pelo executor
                    # normal; materialize-o no PostgreSQL para não perder a
                    # entrada observada na corretora.
                    recovered_trade_id = f"recovered:{symbol}:{trade_id}"
                    try:
                        salvar_trade_sqlite({
                            "trade_id": recovered_trade_id,
                            "timestamp": datetime.utcnow().isoformat(),
                            "symbol": symbol,
                            "side": str(side).lower(),
                            "quantity": recovered_quantity,
                            "entry_price": recovered_entry,
                            "strategy": "bybit_existing_recovered",
                            "decision_source": "exchange_position_recovery",
                            "status": "aberto",
                            "order_id": trade_id if trade_id != "from_bybit" else None,
                        })
                        logging.info(
                            "[RECOVERY] Posição real materializada no banco | symbol=%s | trade_id=%s",
                            symbol,
                            recovered_trade_id,
                        )
                    except Exception as exc:
                        logging.error("[RECOVERY] Falha ao materializar posição real: %s", type(exc).__name__)

                    logging.info("ðŸ“Œ JÃ¡ existe uma operaÃ§Ã£o ativa na Bybit. Entrando em modo de monitoramento.")
                    try:
                        sl_pos = pos.get("stopLoss")
                        tp_pos = pos.get("takeProfit")
                    except Exception:
                        sl_pos = None
                        tp_pos = None

                    risk_params = {
                        "stop_loss": float(sl_pos) if sl_pos not in (None, "", "0", "0.0", 0) else None,
                        "take_profit": float(tp_pos) if tp_pos not in (None, "", "0", "0.0", 0) else None,
                        "strategy": "bybit_existing",
                    }
                    # A posição recuperada só entra em monitoramento depois
                    # que a OKX confirmar o OCO real (closeOrderAlgo).
                    # Sem essa confirmação, este worker permanece no ciclo de
                    # recuperação e não libera novas entradas para o símbolo.
                    protecao_ok = _armar_protecao_posicao_okx(session, symbol, pos, entry, side)
                    if not protecao_ok:
                        logging.error(
                            "[PROTECTION_PENDING] %s posição ativa sem SL/TP confirmado; "
                            "monitoramento e novas entradas permanecem bloqueados.",
                            symbol,
                        )
                        time.sleep(3)
                        continue
                    indicators_recovered = reconstruir_contexto_indicadores(symbol, interval)
                    if indicators_recovered is None:
                        indicators_recovered = {
                            "strategy": "bybit_existing_no_context",
                            "ALLOW_TRAILING": 0,
                            "timeframe": interval,
                        }
                        logging.error(
                            f"ðŸ›‘ FAIL-SAFE: sem contexto reconstruÃ­do para {symbol}. "
                            f"Trailing/BE devem permanecer bloqueados."
                        )

                    monitorar_ordem_ate_encerrar(
                        trade_id=trade_id,
                        symbol=symbol,
                        position_size=size,
                        entry_price=entry,
                        side=side,
                        api=session,
                        risk_parameters=risk_params,
                        indicators=indicators_recovered,
                    )
                    continue  # volta pro inÃ­cio do loop depois do monitoramento
        except Exception as e:
            logging.warning(f"âš ï¸ Falha ao verificar posiÃ§Ã£o ativa: {e}")

        # 1. Busca os Ãºltimos candles do timeframe principal
        df = get_market_data_bybit(symbol, session, interval=interval, limit=2)
        if df is None or df.empty:
            logging.warning(f"âš ï¸ NÃ£o foi possÃ­vel obter candle de {interval}m. Tentando novamente...")
            time.sleep(10)
            continue

        ts_atual = df["time"].iloc[-1]

        # 2. SÃ³ roda a lÃ³gica se o candle mudou
        if ts_atual == ultimo_timestamp:
            now = time.time()
            if now - ultimo_log_sem_novo_candle >= 60:
                logging.info(f"â³ Nenhum novo candle ({interval}m). Aguardando prÃ³ximo fechamento...")
                ultimo_log_sem_novo_candle = now
            time.sleep(7)
            continue

        # Atualiza o timestamp principal
        ultimo_timestamp = ts_atual
        ultimo_candle_visto_em = time.time()
        ultimo_alerta_candle_parado = 0.0
        cycle_telemetry = new_cycle(symbol, cycle_time=datetime.now(timezone.utc).isoformat())
        cycle_telemetry["last_5m_candle"] = str(ts_atual)
        emit_cycle(cycle_telemetry, "cycle_start")

        # Gate FSM cross-process: nÃ£o roda funil decisÃ³rio enquanto houver ordem pendente/posiÃ§Ã£o aberta.
        try:
            state = get_state(symbol)
            if state != IDLE:
                logging.info(f"â›” FSM gate (state={state}) ativo para {symbol}. Pulando decisÃ£o neste candle.")
                mark_exit(cycle_telemetry, "FSM_GATE", f"ACTIVE_STATE:{state}")
                emit_cycle(cycle_telemetry, "cycle_exit")
                continue
        except Exception as e:
            logging.warning(f"âš ï¸ Falha ao ler FSM do sÃ­mbolo {symbol}: {e}")

        # 3. ObtÃ©m mÃºltiplos timeframes
        df_1m = get_market_data_bybit(symbol, session, interval="1", limit=CANDLES_MINIMOS)
        # Keep one extra row so removing a still-forming candle still leaves
        # the full indicator history required by the closed-candle regime.
        df_5m = get_market_data_bybit(symbol, session, interval="5", limit=CANDLES_MINIMOS + 1)
        df_15m = get_market_data_bybit(symbol, session, interval="15", limit=CANDLES_MINIMOS)
        cycle_telemetry["df_1m_loaded"] = bool(df_1m is not None and not df_1m.empty)
        cycle_telemetry["df_5m_loaded"] = bool(df_5m is not None and not df_5m.empty)
        cycle_telemetry["df_15m_loaded"] = bool(df_15m is not None and not df_15m.empty)

        # Primeiro, faÃ§a o log dos dados crus, antes de calcular indicadores
        logging.info(f"[PRODUÃ‡ÃƒO] df_1m RAW: {list(df_1m.columns)} | Shape: {df_1m.shape if df_1m is not None else None}")
        logging.info(f"[PRODUÃ‡ÃƒO] df_5m RAW: {list(df_5m.columns)} | Shape: {df_5m.shape if df_5m is not None else None}")
        logging.info(f"[PRODUÃ‡ÃƒO] df_15m RAW: {list(df_15m.columns)} | Shape: {df_15m.shape if df_15m is not None else None}")

        if any(df is None or df.empty or len(df) < CANDLES_MINIMOS for df in [df_1m, df_5m, df_15m]):
            logging.warning("âŒ Dados insuficientes em algum timeframe. Abortando anÃ¡lise.")
            mark_exit(cycle_telemetry, "DATA_VALIDATION", "PRE_BREAKOUT_DATA_INVALID")
            emit_cycle(cycle_telemetry, "cycle_exit")
            time.sleep(10)
            continue

        # SÃ³ calcula indicadores se passou pelo filtro de candles mÃ­nimos
        closed_5m = select_closed_candles(df_5m, 5)
        if closed_5m is None:
            logging.warning("[REGIME] Candle 5m fechado não pôde ser comprovado; análise abortada com fail-safe.")
            mark_exit(cycle_telemetry, "CLOSED_CANDLE", "PRE_BREAKOUT_5M_NOT_CLOSED")
            emit_cycle(cycle_telemetry, "cycle_exit")
            time.sleep(10)
            continue
        df_5m_regime_raw, regime_candle = closed_5m
        if len(df_5m_regime_raw) < CANDLES_MINIMOS:
            logging.warning("[REGIME] Histórico 5m fechado insuficiente para indicadores; análise abortada.")
            mark_exit(cycle_telemetry, "DATA_VALIDATION", "PRE_BREAKOUT_5M_HISTORY_INSUFFICIENT")
            emit_cycle(cycle_telemetry, "cycle_exit")
            time.sleep(10)
            continue

        closed_1m = select_closed_candles(df_1m, 1)
        if closed_1m is not None:
            df_1m_closed_raw, candle_1m = closed_1m
            last_closed_1m = df_1m_closed_raw.iloc[-1]
            for field in ("open", "high", "low", "close", "volume", "turnover"):
                if field in df_1m_closed_raw.columns:
                    candle_1m[field] = last_closed_1m.get(field)
            record_closed_1m_candle(
                symbol=symbol,
                candle=candle_1m,
                exchange=str(ACTIVE_EXCHANGE or "BYBIT"),
                evaluation_time=datetime.now(timezone.utc),
            )
            cycle_telemetry["closed_1m_persisted"] = True
            cycle_telemetry["last_closed_1m_candle"] = str(candle_1m.get("candle_open_time"))

        # The closed-candle identity comes from select_closed_candles.  Attach
        # only the already-fetched OHLCV values to that same identity so
        # analytical telemetry cannot create a second candle interpretation.
        last_closed = df_5m_regime_raw.iloc[-1]
        for field in ("open", "high", "low", "close", "volume"):
            if field in df_5m_regime_raw.columns:
                regime_candle[field] = last_closed.get(field)

        strategy_profile = resolve_strategy_profile(symbol, "5m")
        df_1m = attach_strategy_profile(calculate_indicators(df_1m, profile=strategy_profile), strategy_profile)
        df_5m = attach_strategy_profile(calculate_indicators(df_5m, profile=strategy_profile), strategy_profile)
        df_15m = attach_strategy_profile(calculate_indicators(df_15m, profile=strategy_profile), strategy_profile)
        df_5m_regime = attach_strategy_profile(calculate_indicators(df_5m_regime_raw, profile=strategy_profile), strategy_profile)
        df_5m_regime = df_5m_regime.bfill().ffill().dropna()

        # Loga a presenÃ§a de NaN apÃ³s calcular os indicadores
        logging.info(f"[PRODUÃ‡ÃƒO] Indicadores df_1m: {list(df_1m.columns)} | NaN: {df_1m.isnull().sum().sum()}")
        logging.info(f"[PRODUÃ‡ÃƒO] Indicadores df_5m: {list(df_5m.columns)} | NaN: {df_5m.isnull().sum().sum()}")
        logging.info(f"[PRODUÃ‡ÃƒO] Indicadores df_15m: {list(df_15m.columns)} | NaN: {df_15m.isnull().sum().sum()}")


        # Captura timestamps atuais de cada timeframe
        # Dentro do loop:
        if not (checar_dataframe(df_1m, "df_1m") and checar_dataframe(df_5m, "df_5m") and checar_dataframe(df_15m, "df_15m")):
            logging.warning("â© Dados de algum timeframe invÃ¡lidos. Pulando anÃ¡lise deste ciclo.")
            mark_exit(cycle_telemetry, "DATA_VALIDATION", "PRE_BREAKOUT_DATA_INVALID")
            emit_cycle(cycle_telemetry, "cycle_exit")
            time.sleep(10)
            continue

        # SÃ³ acessa se TODOS estÃ£o OK!
        ts_1m = df_1m["time"].iloc[-1]
        ts_5m = df_5m["time"].iloc[-1]
        ts_15m = df_15m["time"].iloc[-1]


        # ComparaÃ§Ã£o com os Ãºltimos valores
        if (
            ts_1m == ultimo_ts_1m and
            ts_5m == ultimo_ts_5m and
            ts_15m == ultimo_ts_15m and
            ts_atual == ultimo_timestamp
        ):
            if existe_ordem_aberta(session, symbol):  # <- funÃ§Ã£o que verifica posiÃ§Ã£o na Bybit
                logging.info("â³ OperaÃ§Ã£o em andamento (confirmaÃ§Ã£o na Bybit). Aguardando novo candle...")
                time.sleep(10)
                continue
            else:
                # HEARTBEAT ativo para monitorar o ciclo
                logging.info(f"ðŸ”„ [CICLO PASSIVO] Nenhum candle novo detectado e nenhuma ordem ativa. Ãšltimo candle: {ts_atual} | Agora: {datetime.utcnow().isoformat()}")

                # DiagnÃ³stico de contexto para debug ao vivo
                # DiagnÃ³stico de contexto para debug ao vivo
                try:
                    # ðŸ”’ ProteÃ§Ã£o total contra DF vazio ou None
                    if df is None or df.empty:
                        logging.warning("âš ï¸ DF vazio no HEARTBEAT â€” pulando diagnÃ³stico.")
                        time.sleep(5)
                        continue

                    # ðŸ”’ ProteÃ§Ã£o contra ausÃªncia de colunas essenciais
                    colunas_minimas = ["close", "RSI", "ATR"]
                    for c in colunas_minimas:
                        if c not in df.columns:
                            logging.warning(f"âš ï¸ DF sem coluna '{c}' no HEARTBEAT â€” pulando diagnÃ³stico.")
                            time.sleep(5)
                            continue

                    # ðŸ” Agora Ã© 100% seguro acessar iloc[-1]
                    preco_atual = df["close"].iloc[-1]
                    rsi = df["RSI"].iloc[-1]
                    atr = df["ATR"].iloc[-1]

                    logging.info(
                        f"ðŸ“Š DiagnÃ³stico tÃ©cnico atual | "
                        f"PreÃ§o: {preco_atual:.2f} | "
                        f"RSI: {rsi:.2f} | "
                        f"ATR: {atr:.2f}"
                    )

                except Exception as e:
                    logging.warning(f"âš ï¸ Erro ao exibir diagnÃ³stico tÃ©cnico: {e}")


                # Verifica quanto tempo estÃ¡ sem candle novo
                tempo_sem_candle = time.time() - tempo_ultimo_candle

                # Se ficar mais de 15 minutos sem candle novo, forÃ§ar anÃ¡lise!
                if tempo_sem_candle > 900:  # 900s = 15 min, ajuste como preferir
                    logging.warning("âš ï¸ Mais de 15 minutos sem candle novo. ForÃ§ando anÃ¡lise mesmo assim!")
                    tempo_ultimo_candle = time.time()  # Reseta o timer
                    # NÃƒO faz continue! Deixe o fluxo seguir para anÃ¡lise principal abaixo deste bloco
                else:
                    # Manda um heartbeat a cada ciclo passivo
                    logging.info(f"â¤ï¸ Heartbeat: aguardando novo candle... Tempo parado: {int(tempo_sem_candle)}s")
                    time.sleep(10)
                    continue
        else:
            # Se candle novo chegou, atualiza tempo
            tempo_ultimo_candle = time.time()


        # Atualiza os Ãºltimos timestamps para a prÃ³xima rodada
        ultimo_ts_1m = ts_1m
        ultimo_ts_5m = ts_5m
        ultimo_ts_15m = ts_15m

        # [ ... segue o restante da lÃ³gica do robÃ´ ... ]


        # 4. Checagem de flags/posiÃ§Ã£o ativa (proteÃ§Ã£o dupla)
        if os.path.exists("operacao_ativa.flag"):
            try:
                posicoes = session.get_positions(category="linear", symbol=symbol)
                if posicoes.get("retCode") == 0:
                    em_posicao = any(
                        abs(float(p.get("size") or p.get("positionAmt") or 0)) > 0
                        for p in posicoes["result"]["list"]
                    )
                    if not em_posicao:
                        os.remove("operacao_ativa.flag")
                        logging.info("âœ… Nenhuma posiÃ§Ã£o ativa encontrada. Flag removida com sucesso.")
                    else:
                        logging.info("â³ PosiÃ§Ã£o ainda ativa. Aguardando encerramento...")
                        time.sleep(10)
                        continue
                else:
                    logging.warning("âš ï¸ Erro ao consultar posiÃ§Ãµes na Bybit. Mantendo flag por precauÃ§Ã£o.")
                    time.sleep(10)
                    continue
            except Exception as e:
                logging.error(f"âŒ Erro ao verificar posiÃ§Ãµes: {e}")
                time.sleep(10)
                continue

        # 5. MemÃ³ria interna para proteger execuÃ§Ãµes simultÃ¢neas
        global operacao_em_andamento
        if operacao_em_andamento:
            logging.info("â³ OperaÃ§Ã£o em andamento (memÃ³ria interna). Aguardando monitoramento encerrar antes de nova anÃ¡lise.")
            time.sleep(10)
            continue

        operacao_em_andamento = True  # Marca operaÃ§Ã£o em andamento

        try:
            # ConfiguraÃ§Ã£o do modelo LSTM
            config_lstm = {
                "lstm_model_path": "estrategias/lstm_btc_4features.h5",
                "window_size": 30    # O valor exato que usou no treino
            }

            # ObtÃ©m dados de mercado
            logging.info("Obtendo dados de mercado da exchange ativa...")
            # symbol = get_ativos_env()[0]  # jÃ¡ no topo do seu script/principal
            df = get_market_data_bybit(symbol, session, "60")

            # Reusa os dataframes 1m/5m/15m ja coletados no inicio do ciclo

            # â— Verifica se todos foram obtidos com sucesso
            if any(df is None or df.empty for df in [df_1m, df_5m, df_15m]):
                logging.warning("âš ï¸ Erro ao obter dados de mÃºltiplos timeframes. Ignorando anÃ¡lise dessa rodada.")
            else:
                from utils.indicadores import analisar_multiplos_timeframes
                resultado = analisar_multiplos_timeframes(df_1m, df_5m, df_15m)

                # ðŸŽ¯ CondiÃ§Ã£o de entrada
                if resultado["score_5m"] >= 4 and resultado["rompimento_1m"]:
                    if resultado["tendencia_15m"] == "buy":
                        logging.info("ðŸ“ˆ Entrada de COMPRA confirmada pelos 3 timeframes.")
                        # ðŸ‘‰ chamar funÃ§Ã£o de entrada BUY aqui
                    elif resultado["tendencia_15m"] == "sell":
                        logging.info("ðŸ“‰ Entrada de VENDA confirmada pelos 3 timeframes.")
                        # ðŸ‘‰ chamar funÃ§Ã£o de entrada SELL aqui
                    else:
                        logging.info("âš ï¸ Sinal forte, mas tendÃªncia 15m neutra. Aguardando confirmaÃ§Ã£o.")
                else:
                    logging.info("âŒ AnÃ¡lise de mÃºltiplos timeframes nÃ£o confirmou entrada.")


            # âœ… Verifica se os dados estÃ£o vÃ¡lidos
            if df is None or df.empty:
                logging.error("Erro ao obter dados de mercado. Encerrando execuÃ§Ã£o.")
                operacao_em_andamento = False
                continue

            # âœ… VerificaÃ§Ã£o individual por timeframe com checagem de integridade
            if not all(checar_dataframe(df_temp, nome_df) 
                    for nome_df, df_temp in [("df_1m", df_1m), ("df_5m", df_5m), ("df_15m", df_15m), ("df", df)]):
                logging.warning("â© Algum timeframe invÃ¡lido. Pulando anÃ¡lise deste ciclo.")
                time.sleep(10)
                continue

            # Agora Ã© seguro acessar!
            ts_1m = df_1m["time"].iloc[-1]
            ts_5m = df_5m["time"].iloc[-1]
            ts_15m = df_15m["time"].iloc[-1]
            ts_principal = df["time"].iloc[-1]


            if (
                ts_1m == ultimo_ts_1m and
                ts_5m == ultimo_ts_5m and
                ts_15m == ultimo_ts_15m and
                ts_principal == ultimo_timestamp
            ):
                if existe_ordem_aberta(api, symbol):  # <--- ESSA FUNÃ‡ÃƒO OLHA NA BYBIT!
                    logging.info("â³ OperaÃ§Ã£o em andamento (confirmaÃ§Ã£o na Bybit). Aguardando monitoramento encerrar antes de nova anÃ¡lise.")
                    time.sleep(10)
                    continue
                else:
                    logging.info("â³ Nenhum candle mudou e nÃ£o existe ordem aberta. Esperando novo candle...")
                    time.sleep(10)
                    continue


            # Logs dos dados obtidos
            logging.info("Dados de mercado obtidos com sucesso:")
            logging.info(f"{df.head()}")

            if df.isnull().any().any():
                operacao_em_andamento = False
                logging.warning("Valores nulos detectados nos dados de mercado:")
                logging.warning(f"Valores nulos por coluna:\n{df.isnull().sum()}")
                continue

            
            termo = "Bitcoin"
            logging.info(f"Iniciando anÃ¡lise de notÃ­cias para o termo: {termo}")

            # Capturar notÃ­cias
            noticias = capturar_noticias(termo)
            if not isinstance(noticias, list):
                logging.error("Erro ao capturar notÃ­cias: retorno invÃ¡lido.")
                return

            if not noticias:
                logging.warning("Nenhuma notÃ­cia encontrada.")
                return

            # Analisar sentimentos
            resultados = []
            for noticia in noticias:
                if isinstance(noticia, dict):
                    texto_completo = f"{noticia.get('titulo', '')} - {noticia.get('descricao', '')}"
                    sentimento = analisar_sentimento(texto_completo)
                    resultados.append(f"Texto: {texto_completo} | Sentimento: {sentimento}")
                    logging.info(f"Analisado: {texto_completo} | Sentimento: {sentimento}")
                else:
                    logging.warning(f"Formato invÃ¡lido de notÃ­cia: {noticia}")

            # Determinar o sentimento geral
            if resultados:
                sentimentos = [r.split("| Sentimento: ")[-1].strip() for r in resultados]
                sentimento_geral = (
                    "positivo" if sentimentos.count("positivo") > sentimentos.count("negativo")
                    else "negativo"
                )
                logging.info(f"Sentimento geral: {sentimento_geral}")


            # Executa a estratÃ©gia LSTM
            # ðŸ“Š Calcula os indicadores tÃ©cnicos a partir do DataFrame de candles
            df = calculate_indicators(df)
            if not validar_indicadores_com_reset(df):
                continue
         
            # Verifique quantas colunas estÃ£o presentes no DataFrame
            logging.info(f"Colunas do DataFrame antes de calcular indicadores: {df.columns}")

            # Verifique se algum valor nos indicadores Ã© NaN e preencha com 0
            df['std_dev'] = df['std_dev'].fillna(0)
            df['Bollinger_Upper'] = df['Bollinger_Upper'].fillna(0)
            df['Bollinger_Lower'] = df['Bollinger_Lower'].fillna(0)
            df['TrueRange'] = df['TrueRange'].fillna(0)
            df['ATR'] = df['ATR'].fillna(0)
            df['momentum'] = df['momentum'].fillna(0)

            # ðŸ” CHECAGEM DE INTEGRIDADE DO DATAFRAME PARA IA/MODELO
            checar_dataframe_para_IA(
                df,
                colunas_esperadas=[
                    "close", "volume", "SMA_10", "SMA_50", "RSI", "ATR",
                    "high", "low", "open", "Bollinger_Upper", "Bollinger_Lower", "std_dev"
                ],
                janela_minima=10  # Ajuste para o mÃ­nimo que seu modelo aceita
            )

            # Verifique se as colunas estÃ£o agora preenchidas corretamente
            logging.info(f"DataFrame apÃ³s preenchimento de NaN: {df[['std_dev', 'Bollinger_Upper', 'Bollinger_Lower', 'TrueRange', 'ATR', 'momentum']].tail()}")


            # ðŸŽ¯ Filtrar apenas colunas necessÃ¡rias pro modelo
            colunas_necessarias = [
                'close', 'volume', 'RSI', 'ATR',
                'Bollinger_Upper', 'Bollinger_Lower',
                'EMA12', 'EMA26', 'EMA50',
                'SMA_10', 'SMA_50'
            ]
            df_modelo = df[colunas_necessarias].copy()
            df_modelo.dropna(inplace=True)

            # (Opcional) Print para debug
            logging.debug(f"ðŸ“ˆ DataFrame limpo para modelo: {df_modelo.shape} colunas: {list(df_modelo.columns)}")




            df_lstm = df[['open','high','low','close','volume']].iloc[-10:].copy()

            # ðŸ”¥ ObtÃ©m a previsÃ£o diretamente do serviÃ§o LSTM
            previsao_lstm = obter_previsao_next_candle(df_lstm)

            if previsao_lstm is None:
                if os.getenv("LSTM_ENABLED", "1").strip().lower() in {"0", "false", "no", "off"}:
                    logging.info("[LSTM] desativado para este ativo; sem previsão neste ciclo.")
                else:
                    logging.warning("âš ï¸ LSTM nÃ£o retornou previsÃ£o vÃ¡lida.")
            else:
                logging.info(f"ðŸ”® PrevisÃ£o LSTM recebida: {previsao_lstm}")


                # ðŸ’¾ Salvar previsÃ£o LSTM em variÃ¡vel global (ENV)
                os.environ["LSTM_PREVISAO_ATUAL"] = str(previsao_lstm)
                logging.info(f"ðŸ’¾ LSTM_PREVISAO_ATUAL definida: {previsao_lstm}")

            
            # =========================================
            # ðŸ’¾ Salva previsÃ£o LSTM no banco (nova tabela)
            # =========================================
            if previsao_lstm is None:
                # None Ã© o resultado esperado do fallback quando o scaler
                # estÃ¡ fora do domÃ­nio. NÃ£o tentar converter/persistir um
                # valor inexistente como se fosse erro de banco.
                logging.info("â„¹ï¸ PrevisÃ£o LSTM nÃ£o persistida: fallback_poucos_dados ativo.")
            else:
                try:
                    from banco_trade.db_trades import salvar_previsao_lstm
                    # Valor real no momento da previsÃ£o
                    real_price = float(df["close"].iloc[-1])
                    direction_real = "buy" if df["close"].iloc[-1] > df["open"].iloc[-1] else "sell"
                    from utils.lstm_persistence import map_prediction
                    mapped_prediction = map_prediction(
                        previsao_lstm, acao, real_price
                    )
                    predicted_price = mapped_prediction["predicted_price"]
                    direction_predicted = mapped_prediction["direction_predicted"]
                    confidence_score = mapped_prediction["confidence_score"]
                    error_percent = mapped_prediction["error_percent"]

                    salvar_previsao_lstm({
                        "trade_id": str(uuid.uuid4()),
                        "symbol": symbol,
                        "model_name": config_lstm.get("nome_modelo", "LSTM_GÃªneses"),
                        "timestamp": datetime.utcnow().isoformat(),
                        "predicted_price": predicted_price,
                        "real_price": real_price,
                        "direction_predicted": direction_predicted,
                        "direction_real": direction_real,
                        "error_percent": error_percent,
                        "confidence_score": confidence_score,
                        "window_size": config_lstm.get("window_size", 50),
                        "sequence_input": str(df_modelo.tail(10).to_dict())
                    })
                    logging.info("âœ… PrevisÃ£o LSTM salva com sucesso no banco de dados.")
                except Exception as e:
                    logging.error(f"âŒ Falha ao salvar previsÃ£o LSTM: {e}", exc_info=True)


            # Reavaliar ordens abertas
            logging.info("Reavaliando ordens abertas apÃ³s reinÃ­cio do sistema...")
            reavaliar_ordens_abertas(session, symbol)
            # FunÃ§Ã£o para reavaliar ordens abertas
            # VocÃª pode agora implementar a funÃ§Ã£o `reavaliar_ordens_abertas` que
            # irÃ¡ cancelar ou manter as ordens baseadas nos dados mais recentes

            # Calcula indicadores tÃ©cnicos
            logging.info("Calculando indicadores tÃ©cnicos...")
            df = calculate_indicators(df)
            if not validar_indicadores_com_reset(df):
                continue
            
            if df is None:
                logging.error("Erro ao calcular indicadores tÃ©cnicos. Encerrando execuÃ§Ã£o.")
                continue

            # Logs apÃ³s cÃ¡lculo de indicadores
            logging.info("Indicadores tÃ©cnicos calculados com sucesso:")
            logging.info(f"{df[['close', 'SMA_10', 'SMA_50', 'RSI', 'ATR']].tail()}")

            # Calcular o ATR (Average True Range) necessÃ¡rio para Stop-Loss e Take-Profit
            atr = df['ATR'].iloc[-1]
            logging.info(f"ATR calculado: {atr}")

            # Treina o modelo LSTM ao iniciar o robÃ´          
            # ðŸ“Œ **Modulo - AnÃ¡lise de Volatilidade antes da pontuaÃ§Ã£o**
            logging.info("ðŸ” Iniciando anÃ¡lise de volatilidade antes de qualquer entrada no mercado...")

            monitorar_mercado(df, tempo_espera=60)  # Passamos o df jÃ¡ carregado!

            logging.info(f"ðŸŸ¡ [ANTES DO SHOULD_ENTER_MARKET] QTD candles: {df.shape[0]}")
            print(f"ðŸŸ¡ [ANTES DO SHOULD_ENTER_MARKET] QTD candles: {df.shape[0]}")
            print(df.tail(3))
            print("Contagem de NaN por coluna:\n", df.isnull().sum())


            # ============================================================
            # ðŸ”¥ REGIME DE MERCADO â€” O PORTEIRO DO SISTEMA
            # ============================================================
            from regime.regime_detector import RegimeDetector
            from regime.regime_configurator import RegimeConfigurator
            from regime.regime_controller import aplicar, get_parametros

            try:
                detector = RegimeDetector()
                configurador = RegimeConfigurator()

                # 1) Detectar o regime com o df principal jÃ¡ preparado
                regime = detector.detectar(df_5m_regime, symbol=symbol)
                logging.info(f"ðŸ“Š Regime detectado para {symbol}: {regime}")
                try:
                    record_regime_evaluation(
                        symbol=symbol,
                        timeframe="5",
                        candle=regime_candle,
                        diagnostics=detector.last_diagnostics,
                        regime=regime,
                    )
                    record_canonical_candle(
                        symbol=symbol,
                        timeframe="5",
                        candle=regime_candle,
                        diagnostics=detector.last_diagnostics,
                        regime=regime,
                    )
                except Exception as exc:
                    logging.warning("[REGIME_OBSERVABILITY] Falha não bloqueante: %s", type(exc).__name__)

                # 2) Obter parÃ¢metros oficiais da tabela
                parametros_regime = configurador.get(regime)

                # 3) Aplicar ao estado interno
                aplicar(symbol, parametros_regime)

                logging.info(
                    f"ðŸŽ¯ Regime aplicado â†’ "
                    f"Modo={parametros_regime['modo']} | "
                    f"Score_min={parametros_regime['score_min']} | "
                    f"Score_pct={parametros_regime['score_pct']} | "
                    f"Permite operar? {parametros_regime['permissao_operar']}"
                )

                # 4) Roteamento soberano por regime
                from regime.entry_profile import runtime_strategy_for_regime

                configured_strategy_router = (
                    os.getenv("GENESIS_STRATEGY_SELECTION", "")
                    .strip()
                    .upper()
                    .replace(" ", "_")
                )
                fixed_non_mm_strategy = configured_strategy_router in {
                    "SWING_TREND_FOLLOWING",
                    "PULLBACK_TREND_FOLLOWING",
                    "SWING_AND_PULLBACK",
                }

                pullback_regime_4_enabled = (
                    configured_strategy_router == "PULLBACK_TREND_FOLLOWING"
                    and os.getenv("PULLBACK_ALLOW_REGIME_4", "0").strip().lower()
                    in ("1", "true", "yes", "on")
                )

                if regime == 4 and not pullback_regime_4_enabled:
                    logging.warning("[DEFESA] Regime 4 (caos). Cancelando ordens e limpando locks.")
                    try:
                        session.cancel_all_orders(category="linear", symbol=symbol)
                    except Exception as e:
                        logging.warning(f"[DEFESA] Falha ao cancelar ordens: {e}")
                    try:
                        if os.path.exists("operacao_ativa.flag"):
                            os.remove("operacao_ativa.flag")
                        lock_alt = f"ordem_ativa_{symbol}.flag"
                        if os.path.exists(lock_alt):
                            os.remove(lock_alt)
                    except Exception as e:
                        logging.warning(f"[DEFESA] Falha ao remover flag de opera??o: {e}")
                    operacao_em_andamento = False
                    time.sleep(10)
                    continue

                if (
                    regime == 3
                    and runtime_strategy_for_regime(regime) is None
                    and not fixed_non_mm_strategy
                ):
                    logging.info("[BREAKOUT_ONLY] regime=3 -> no eligible strategy")
                    operacao_em_andamento = False
                    time.sleep(10)
                    continue

                # A fixed worker strategy has precedence over the generic
                # regime router.  In particular, a SOL Pullback worker must
                # never be rerouted to MM merely because the market is in
                # regime 3; the same applies to Swing and combined workers.
                if regime == 3 and not fixed_non_mm_strategy:
                    from estrategia_entrada.estrategia_market_making_lateral import (
                        ativar_market_making,
                        desativar_market_making,
                        executar_market_making,
                    )

                    cfg_mm = {
                        "fator_lateralidade": float(os.getenv("FATOR_LATERALIDADE", 0.25)),
                        "time_stop_segundos": int(os.getenv("MM_TIME_STOP", 30)),
                        "atr_pct_max": float(os.getenv("MM_ATR_PCT_MAX", 0.08)),
                        "spread_real_max_mult": float(os.getenv("MM_SPREAD_REAL_MAX_MULT", 2.0)),
                    }

                    # O MM entra antes do fluxo direcional legado. Cria aqui
                    # o contexto de decisão para vincular ordem e fill.
                    clear_current_decision()
                    mm_decision_id = create_decision(
                        symbol=symbol,
                        timeframe=str(interval),
                        strategy="MARKET_MAKING_LATERAL",
                        context={"regime": regime, "source": "regime_router"},
                    )
                    record_event(
                        mm_decision_id,
                        "STRATEGY_SELECTED",
                        "STRATEGY",
                        component="regime_router",
                        status="SELECTED",
                        payload={"strategy": "MARKET_MAKING_LATERAL", "regime": regime},
                    )

                    ativar_market_making(symbol)
                    resultado_mm = executar_market_making(
                        session=session,
                        symbol=symbol,
                        df=df,
                        regime_atual=regime,
                        regime_getter=None,
                        cfg=cfg_mm,
                        decision_id=mm_decision_id,
                    )
                    desativar_market_making(symbol, motivo=str(resultado_mm), session=session)
                    operacao_em_andamento = False
                    time.sleep(5)
                    continue

                if not parametros_regime["permissao_operar"] and not pullback_regime_4_enabled:
                    logging.warning(
                        f"[REGIME] Regime {regime} impede operaÃ§ao. Aguardando prÃ³ximo candle..."
                    )
                    operacao_em_andamento = False
                    time.sleep(10)
                    continue

            except Exception as e:
                logging.error(f"âŒ Erro ao detectar regime: {e}", exc_info=True)
                time.sleep(10)
                continue


            # Agora sim, chama a funÃ§Ã£o unificada de anÃ¡lise
            clear_current_decision()
            strict_swing_parity = (
                os.getenv("GENESIS_STRATEGY_SELECTION", "").strip().upper().replace(" ", "_")
                == "SWING_TREND_FOLLOWING"
                and os.getenv("SWING_STRICT_BACKTEST_PARITY", "0").strip().lower()
                in ("1", "true", "yes", "on")
            )
            strict_pullback_parity = (
                os.getenv("GENESIS_STRATEGY_SELECTION", "").strip().upper().replace(" ", "_")
                == "PULLBACK_TREND_FOLLOWING"
                and os.getenv("PULLBACK_BACKTEST_PARITY", "0").strip().lower()
                in ("1", "true", "yes", "on")
            )
            strict_strategy_parity = strict_swing_parity or strict_pullback_parity
            if strict_swing_parity:
                logging.info(
                    "[SWING][PARIDADE] Modo estrito ativo: backtest Swing Ã© a autoridade da entrada; "
                    "score/LSTM/sentimento genÃ©ricos nÃ£o alteram o setup."
                )
            if strict_pullback_parity:
                logging.info(
                    "[PULLBACK][PARIDADE] Modo estrito ativo: backtest Pullback Ã© a autoridade da entrada; "
                    "score/LSTM/sentimento genÃ©ricos nÃ£o alteram o setup."
                )
            prosseguir, lado, resultados = checar_oportunidade_entrada(df)
            decision_id = None
            # Every valid candle evaluation is a decision, including HOLD/no-entry.
            # The previous condition dropped no-entry results because the legacy
            # scorer does not always populate score_pct in that branch.
            if isinstance(resultados, dict):
                decision_id = create_decision(
                    symbol=symbol,
                    timeframe=str(interval),
                    context={
                        "technical_side": lado,
                        "prosseguir": bool(prosseguir),
                        "score_pct": resultados.get("score_pct"),
                        "buy_score": (resultados.get("buy") or {}).get("score"),
                        "sell_score": (resultados.get("sell") or {}).get("score"),
                    },
                )
                record_event(decision_id, "MARKET_CONTEXT_CAPTURED", "MARKET_CONTEXT",
                             component="trading_bot_geneses",
                             payload={"side": lado, "prosseguir": bool(prosseguir)})

                model_policy = model_fallback_policy(resultados)
                if model_policy["active"] and not strict_strategy_parity:
                    # Estado de poucos dados não pode chegar à execução normal.
                    # O modo padrão é bloqueio; a redução fica disponível para
                    # uma futura política de sizing integrada às estratégias.
                    record_event(
                        decision_id,
                        "EXECUTION_BLOCKED",
                        "RISK",
                        component="model_risk_policy",
                        status="BLOCKED",
                        reason_code=model_policy["reason_code"],
                        payload={
                            "action": model_policy["action"],
                            "risk_multiplier": model_policy["risk_multiplier"],
                        },
                    )
                    finalize_decision(
                        decision_id,
                        "BLOCKED",
                        model_policy["reason_code"],
                        "Modelo em fallback_poucos_dados; entrada bloqueada por política de risco",
                    )
                    logging.warning(
                        "[MODEL_RISK] Entrada bloqueada: fallback_poucos_dados | action=%s | multiplier=%.2f",
                        model_policy["action"], model_policy["risk_multiplier"],
                    )
                    operacao_em_andamento = False
                    time.sleep(10)
                    continue

            # A direcao tecnica orienta o gate, mas nao trava o ativo em um
            # unico lado. O padrao e permitir BUY e SELL; a estrategia
            # vencedora define a direcao final apos as validacoes.
            operar_ambos_lados = os.getenv(
                "OPERAR_AMBOS_LADOS", "true"
            ).strip().lower() in ("1", "true", "yes", "on")
            logging.info(
                "[DIRECTION-GATE] %s | lado tecnico=%s",
                "BUY+SELL habilitados" if operar_ambos_lados else "lado unico habilitado",
                str(lado).upper(),
            )

            # ðŸ” Extrai score_pct do resultado consolidado
            score_pct = resultados.get("score_pct")
            score_pct = resultados.get("score_pct", 0.0)


            
            # Logs detalhados e decisao
            score_buy = float(resultados.get("buy", {}).get("score", 0))
            score_sell = float(resultados.get("sell", {}).get("score", 0))
            lado_aprovado = str(lado).lower() if str(lado).lower() in ("buy", "sell") else None

            if prosseguir:
                logging.info(f"Lado liberado: {lado.upper()} | BUY={score_buy} | SELL={score_sell}")
                logging.info(f"Sentimentos -> BUY={resultados['buy']['sentimento']} | SELL={resultados['sell']['sentimento']}")
                logging.info(f"Predicoes LSTM -> BUY={resultados['buy']['pred']} | SELL={resultados['sell']['pred']}")

                # Recupera o trade_id_temp gerado na previsao
                if 'trade_id_temp' not in locals():
                    trade_id_temp = str(uuid.uuid4())  # fallback de seguranca
                    logging.warning("trade_id_temp nao encontrado, gerando novo temporario.")

                # Apenas registro da direcao tecnica; execucao vira apos validacoes
                logging.info(f"Direcao tecnica sugerida: {lado.upper()} (pre-validacao)")
                logging.info("Aguardar confirmacao da IA e do backtest antes da execucao real...")
                if lado_aprovado is None:
                    logging.warning(
                        f"[DECISION ENGINE] Lado aprovado ambiguo ({lado}). Funil seguira em AUTO (BUY/SELL) e a validacao pos-backtest decidira."
                    )
                logging.info(
                    f"[DECISION ENGINE] Macro/Score aprovou lado={str(lado).upper() if lado_aprovado else 'AUTO'} | direcao sera passada explicitamente ao funil"
                )
            else:
                # Mantem pipeline completo: backtest + validacao pos-backtest antes da decisao final.
                if score_buy > score_sell:
                    lado_aprovado = "buy"
                elif score_sell > score_buy:
                    lado_aprovado = "sell"
                else:
                    lado_aprovado = None
                lado = lado_aprovado if lado_aprovado else "AUTO"
                logging.info("Nenhum lado atingiu o score minimo no gate tecnico. Prosseguindo para backtest + validacao pos-backtest.")
                logging.info(
                    f"[DECISION ENGINE] Fallback de direcao para funil: {(lado_aprovado or 'AUTO').upper()} | BUY={score_buy} | SELL={score_sell}"
                )

            # ðŸ› ï¸ Modulo Entrada_mercado
            # ðŸ› ï¸ Verificar se `df` foi carregado corretamente antes da entrada no mercado
            if df is None:
                logging.error("âŒ ERRO CRÃTICO: `df` estÃ¡ None antes de chamar `should_enter_market()`!")
                sys.exit(1)

            if df.empty:
                logging.error("âš ï¸ ERRO CRÃTICO: `df` estÃ¡ vazio antes de chamar `should_enter_market()`!")
                print("âŒ DEBUG: DataFrame `df` estÃ¡ vazio. Verifique a API ou os parÃ¢metros da consulta.")
                sys.exit(1)

            # ðŸ“Œ Verificar se `df` contÃ©m as colunas esperadas
            colunas_esperadas = ["close", "high", "low", "volume"]
            for coluna in colunas_esperadas:
                if coluna not in df.columns:
                    logging.error(f"âŒ ERRO: A coluna '{coluna}' estÃ¡ ausente no `df`! Encerrando execuÃ§Ã£o.")
                    sys.exit(1)


            # ðŸ”§ ForÃ§ar conversÃ£o das colunas numÃ©ricas para float
            colunas_numericas = ["open", "high", "low", "close", "volume", "turnover"]
            for coluna in colunas_numericas:
                df[coluna] = pd.to_numeric(df[coluna], errors="coerce")

            # ðŸ” Validar apÃ³s conversÃ£o
            if df[["high", "low"]].isnull().any().any():
                logging.error("âŒ ERRO: Valores nulos detectados apÃ³s conversÃ£o para float nas colunas 'high' ou 'low'.")
                logging.debug(df[["high", "low"]].tail())
                sys.exit(1)

            logging.info("âœ… `df` carregado corretamente antes de should_enter_market():")
            logging.info(f"{df.head()}")  # Exibir as primeiras linhas para conferÃªncia




            # ðŸ› ï¸ Tentar recarregar `df` se estiver vazio
            if df.empty:
                logging.warning("âš ï¸ Tentando recarregar `df` antes de encerrar...")
                df = carregar_dados_historicos(session, symbol, "1", limite=50) # Era limite=200

                # ðŸ”¥ FORÃ‡AR QUE O `df` TENHA NO MÃXIMO 50 CANDLES
                df = df.tail(50)  # ðŸ”¥ Mesmo que tenha carregado 200, agora sÃ³ manterÃ¡ os Ãºltimos 50

                logging.info(f"ðŸ” DEBUG - NÃºmero final de candles antes do Backtest: {len(df)}")  # âœ… ConfirmaÃ§Ã£o final



                if df is None or df.empty:
                    logging.error("âŒ ERRO CRÃTICO: Mesmo apÃ³s recarregar, `df` continua vazio!")
                    sys.exit(1)

            # ðŸ† Agora podemos chamar `should_enter_market()` com seguranÃ§a
            mode = "moderado"  # Pode ser ajustado para "conservador" ou "moderado"
            
            # --------------------------------------
            # 1ï¸âƒ£ LSTM â€” APENAS INFORMATIVO (NÃƒO DECIDE LADO)
            # --------------------------------------

            try:
                close_atual = float(df["close"].iloc[-1])

                df_lstm = df[["open", "high", "low", "close", "volume"]].iloc[-10:].copy()

                if df_lstm.shape != (10, 5):
                    logging.warning(f"âš ï¸ Janela LSTM invÃ¡lida: {df_lstm.shape}")
                    previsao_lstm = None
                    lado_lstm = None
                else:
                    previsao_lstm = obter_previsao_next_candle(df_lstm)

                    if previsao_lstm is not None and np.isfinite(previsao_lstm):
                        lado_lstm = "buy" if previsao_lstm > close_atual else "sell"
                        logging.info(
                            f"ðŸ§  LSTM (informativo): prev={previsao_lstm:.2f} | atual={close_atual:.2f} | lado={lado_lstm.upper()}"
                        )
                    else:
                        previsao_lstm = None
                        lado_lstm = None
                        if os.getenv("LSTM_ENABLED", "1").strip().lower() in {"0", "false", "no", "off"}:
                            logging.info("[LSTM] desativado para este ativo; sem previsão neste ciclo.")
                        else:
                            logging.warning("âš ï¸ LSTM nÃ£o retornou previsÃ£o vÃ¡lida.")

            except Exception as e:
                previsao_lstm = None
                lado_lstm = None
                logging.error(f"âŒ Erro ao obter previsÃ£o LSTM: {e}")


            # Gera prompt para a OpenAI com os indicadores
            prompt = f"""
            Os dados do mercado sÃ£o:
            Fechamento: {df['close'].iloc[-1]}
            Volume: {df['volume'].iloc[-1]}
            RSI: {df['RSI'].iloc[-1]}
            ATR: {atr}
            SMA_10: {df['SMA_10'].iloc[-1]}
            SMA_50: {df['SMA_50'].iloc[-1]}

            Baseado nisso, qual seria sua recomendaÃ§Ã£o? 'buy' ou 'sell'?
            """
            logging.info("Consultando OpenAI para anÃ¡lise de mercado...")
            # Esta recomendacao nao participa da decisao final e causava uma
            # chamada OpenAI redundante a cada ciclo. Mantemos o campo para
            # compatibilidade, sem bloquear o robô por uma API externa.
            logging.info("Analise contextual OpenAI ignorada; decisao segue o funil local.")
            recomendacao = {
                "action": "hold",
                "confianca": 0,
                "justificativa": "Analise contextual opcional desativada.",
            }
          
          
            # ðŸ“Œ DicionÃ¡rio de EstratÃ©gias para Backtest        

            # ðŸ“Œ Backtest simples para Market Making Lateral (catÃ¡logo)
            def backtest_market_making_lateral(df_mm, capital_inicial=10000):
                return {
                    "estrategia": "MARKET_MAKING_LATERAL",
                    "lucro_total": 0,
                    "winrate": 0,
                    "saldo_inicial": capital_inicial,
                    "saldo_final": capital_inicial,
                    "trade_log": [],
                    "relatorio_completo": "",
                }

            # ðŸ“Œ EstratÃ©gias para Backtest        
            saldo_inicial = 10000

            estrategias_backtest = {
                "BREAKOUT": executar_backtest_breakout,
                "MEAN_REVERSION": criar_backtest_reversao,
                "MARKET_MAKING_LATERAL": backtest_market_making_lateral,
                "EARLY_TREND": lambda df_et, capital_inicial=saldo_inicial: backtest_early_trend(
                    df_original=df_et, direcao=str(lado).lower() if str(lado).lower() in ("buy", "sell") else None, capital_inicial=capital_inicial
                ),
            }

            # Estrategias para Execucao Continua
            estrategias_execucao = {
                "BREAKOUT": lambda symbol, session: _breakout(symbol, session, session, regime_override=regime),
                "MEAN_REVERSION": lambda symbol, session: executar_mean_reversion_realtime(
                    session=session, symbol=symbol, regime_atual=regime
                ),
                "MARKET_MAKING_LATERAL": lambda symbol, session: executar_market_making_lateral(
                    session=session, symbol=symbol, df=df, regime_atual=regime
                ),
                "EARLY_TREND": lambda symbol, session: executar_early_trend_tick(
                    session=session,
                    symbol=symbol,
                    df=df,
                    interval=str(os.getenv("INTERVAL", "5")),
                    qty=float(os.getenv("EARLY_TREND_QTY", "0.001")),
                    regime_override=regime,
                ),
                "SWING_TREND_FOLLOWING": lambda symbol, session: executar_swing_okx(
                    session=session,
                    symbol=symbol,
                    timeframes=swing_timeframes or {},
                    entry_timeframe=os.getenv("SWING_ENTRY_TIMEFRAME", "1h"),
                ),
                "PULLBACK_TREND_FOLLOWING": lambda symbol, session: executar_pullback_okx(
                    session=session,
                    symbol=symbol,
                    timeframes=swing_timeframes or {},
                ),
            }
        

            # ðŸ“Œ **Executa o Backtest para Selecionar a Melhor EstratÃ©gia**
            df_real = carregar_dados_historicos(session, symbol, "1")
            if df_real is None or df_real.empty:
                logging.error("âŒ Falha ao carregar os dados histÃ³ricos.")
                mark_exit(cycle_telemetry, "DATA_LOAD", "PRE_BREAKOUT_DATA_INVALID")
                emit_cycle(cycle_telemetry, "cycle_exit")
                time.sleep(60)
                continue

            # âœ… NormalizaÃ§Ã£o extra defensiva das chaves
            estrategias_execucao = {
                chave.replace(" ", "").upper(): valor for chave, valor in estrategias_execucao.items()
            }

            # ðŸ“Œ ExecuÃ§Ã£o do Backtest para selecionar a melhor estratÃ©gia
            df_real = carregar_dados_historicos(session, symbol, "1")
            if df_real is None or df_real.empty:
                logging.error("âŒ Falha ao carregar os dados histÃ³ricos.")
                mark_exit(cycle_telemetry, "DATA_LOAD", "PRE_BREAKOUT_DATA_INVALID")
                emit_cycle(cycle_telemetry, "cycle_exit")
                time.sleep(60)
                continue            

            # âœ… CONVERSÃƒO FORÃ‡ADA PARA NUMÃ‰RICOS
            colunas_numericas = ["open", "high", "low", "close", "volume", "turnover"]
            for coluna in colunas_numericas:
                df_real[coluna] = pd.to_numeric(df_real[coluna], errors="coerce")

            # âœ… CHECAGEM DE NULOS ANTES DE CONTINUAR
            if df_real[colunas_numericas].isnull().any().any():
                logging.error("âŒ Erro: Dados histÃ³ricos com valores nulos apÃ³s conversÃ£o. Abortando backtest.")
                logging.debug(df_real[colunas_numericas].tail())
                mark_exit(cycle_telemetry, "DATA_VALIDATION", "PRE_BREAKOUT_DATA_INVALID")
                emit_cycle(cycle_telemetry, "cycle_exit")
                continue




            # âœ… CALCULA INDICADORES AQUI
            df_real = calculate_indicators(df_real)

            # ======================================================
            # ðŸš€ BACKTEST COMPLETO (3 retornos)
            # ======================================================
            sentimento_global = obter_sentimento_combinado()
            # Recalcula o regime no mesmo df_real do backtest. O override do
            # regime live fica opt-in para evitar divergencias entre ciclos.
            usar_override_regime = os.getenv(
                "BACKTEST_USE_LIVE_REGIME_OVERRIDE", "false"
            ).strip().lower() in ("1", "true", "yes", "on")
            natural_runtime_context = None
            if regime_candle.get("candle_closed") is True:
                candle_open = regime_candle.get("candle_open_time")
                candle_key_time = candle_open.isoformat() if hasattr(candle_open, "isoformat") else str(candle_open)
                natural_runtime_context = {
                    "record_provenance": "PRODUCTION_RUNTIME",
                    "evaluation_context": "PRODUCTION_RUNTIME",
                    "run_id": f"worker:{symbol}:{os.getpid()}",
                    "candle_open_time": regime_candle.get("candle_open_time"),
                    "candle_close_time": regime_candle.get("candle_close_time"),
                    "candle_closed": True,
                    "analysis_key": f"{str(symbol).upper()}|5|{candle_key_time}|PRODUCTION_RUNTIME",
                    "canonical_regime": regime,
                    "regime_source": "CANONICAL_RUNTIME",
                    "regime_recalculated": False,
                }
            cycle_telemetry["runtime_context"] = "PRODUCTION_RUNTIME" if natural_runtime_context else "UNKNOWN"
            configured_regime_override = locals().get("regime", None) if usar_override_regime else None
            regime_override = resolve_breakout_regime_override(
                canonical_regime=regime,
                natural_runtime_context=natural_runtime_context,
                configured_override=configured_regime_override,
            )
            logging.info(
                "[REGIME][BACKTEST] fonte=%s | valor=%s",
                "CANONICAL_RUNTIME" if natural_runtime_context else ("live_override" if usar_override_regime else "df_real"),
                regime_override if regime_override is not None else "recalcular",
            )
            current_trace = []
            natural_trace_candle = natural_runtime_context.get("candle_open_time") if isinstance(natural_runtime_context, dict) else None
            cycle_telemetry["backtest_started"] = True
            emit_cycle(cycle_telemetry, "backtest_start")
            swing_timeframes = None
            if (
                os.getenv("SWING_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
                or os.getenv("PULLBACK_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
            ):
                try:
                    swing_timeframes = carregar_timeframes_swing(session, symbol)
                    logging.info(
                        "[SWING] Timeframes carregados | 1h=%s | 4h=%s | diário=%s",
                        len(swing_timeframes["1h"]),
                        len(swing_timeframes["4h"]),
                        len(swing_timeframes["1d"]),
                    )
                except Exception as exc:
                    logging.warning("[SWING] Falha ao carregar timeframes; candidato não será avaliado: %s", exc)
            melhor, relatorio, relatorios_por_estrategia = executar_backtest_completo(
                df_real,
                sentimento_global,
                regime_override=regime_override,
                approved_side=(
                    None
                    if operar_ambos_lados
                    else (str(lado).lower() if str(lado).lower() in ("buy", "sell") else None)
                ),
                natural_runtime_context=natural_runtime_context,
                breakout_decision_trace=current_trace,
                breakout_trace_candle_open_time=natural_trace_candle,
                swing_timeframes=swing_timeframes,
                reference_data=(
                    _load_market_making_reference_data(symbol)
                    if os.getenv("GENESIS_STRATEGY_SELECTION", "").strip().upper().replace(" ", "_")
                    == "MARKET_MAKING_LATERAL"
                    else None
                ),
            )
            # O segundo retorno do backtest e o contexto estruturado do funil
            # (inclui setup_now/no_trade_reason). Preserve-o como fonte unica
            # para a telemetria e para a validacao posterior deste ciclo.
            resultados = relatorio if isinstance(relatorio, dict) else {}
            cycle_telemetry["backtest_completed"] = True
            configured_strategy_name = os.getenv("GENESIS_STRATEGY_SELECTION", "").strip().upper().replace(" ", "_")
            cycle_telemetry["selected_strategy"] = (
                (melhor or {}).get("estrategia") if isinstance(melhor, dict) else configured_strategy_name or None
            )
            cycle_telemetry["selected_strategy_profit"] = (melhor or {}).get("lucro_total") if isinstance(melhor, dict) else None
            selected_strategy_name = str(cycle_telemetry.get("selected_strategy") or configured_strategy_name).strip().upper()
            selected_requires_historical_validation = selected_strategy_name in {
                "SWING_TREND_FOLLOWING",
                "PULLBACK_TREND_FOLLOWING",
            }
            cycle_telemetry["backtest_valid"] = bool(
                melhor
                and isinstance(melhor, dict)
                and (not selected_requires_historical_validation or melhor.get("backtest_valid") is True)
            )
            if selected_strategy_name == "SWING_TREND_FOLLOWING":
                logging.info(
                    "[SWING][BACKTEST-VALIDATION] valid=%s details=%s",
                    (melhor or {}).get("backtest_valid"),
                    (melhor or {}).get("historical_validation"),
                )
            elif selected_strategy_name == "PULLBACK_TREND_FOLLOWING":
                logging.info(
                    "[PULLBACK][BACKTEST-VALIDATION] valid=%s details=%s",
                    (melhor or {}).get("backtest_valid"),
                    (melhor or {}).get("historical_validation"),
                )
            selected_strategy_upper = str(cycle_telemetry.get("selected_strategy") or "").replace(" ", "").upper()
            cycle_telemetry["breakout_execution_requested"] = selected_strategy_upper == "BREAKOUT"
            cycle_telemetry["pullback_execution_requested"] = selected_strategy_upper == "PULLBACK_TREND_FOLLOWING"
            # The backtest is the analytical path.  Record its result before
            # Macro validation so NEGADO remains an execution block, not an
            # observability block.
            cycle_telemetry["analytical_breakout_created"] = bool(current_trace)
            cycle_telemetry["breakout_evaluation_created"] = bool(current_trace)
            setup_now_map = resultados.get("setup_now", {}) if isinstance(resultados, dict) else {}
            selected_setup = setup_now_map.get(selected_strategy_upper, {}) if isinstance(setup_now_map, dict) else {}
            if not isinstance(selected_setup, dict):
                selected_setup = {}
            cycle_telemetry["setup_probe_ok"] = bool(selected_setup.get("ok", False))
            cycle_telemetry["setup_probe_side"] = selected_setup.get("side")
            cycle_telemetry["setup_probe_reason"] = selected_setup.get("reason")
            logging.info(
                "[SETUP-PROBE] estrategia=%s ok=%s lado=%s motivo=%s",
                selected_strategy_upper or "NONE",
                cycle_telemetry["setup_probe_ok"],
                cycle_telemetry["setup_probe_side"],
                cycle_telemetry["setup_probe_reason"],
            )
            if not cycle_telemetry["backtest_valid"]:
                if resultados.get("no_trade_reason") == "sem_setup_atual":
                    mark_exit(
                        cycle_telemetry,
                        "SETUP_PROBE",
                        f"CURRENT_{selected_strategy_upper or 'STRATEGY'}_SETUP_NOT_CONFIRMED",
                    )
                else:
                    mark_exit(cycle_telemetry, "BACKTEST", f"PRE_{selected_strategy_upper or 'STRATEGY'}_BACKTEST_INVALID")
            elif not (cycle_telemetry["breakout_execution_requested"] or cycle_telemetry["pullback_execution_requested"]):
                mark_exit(cycle_telemetry, "STRATEGY_SELECTION", "PRE_STRATEGY_NOT_SELECTED")
            emit_cycle(cycle_telemetry, "backtest_complete")

            # Shadow-only comparison for the configured workers. This path
            # never participates in strategy selection, Commander, risk,
            # paper, or exchange execution.
            shadow_enabled = (
                os.getenv("BREAKOUT_SHADOW_ENABLED", "0").strip().lower()
                in ("1", "true", "yes", "on")
                and configured_strategy_name in {"", "BREAKOUT"}
            )
            if shadow_enabled:
                try:
                    current_result = relatorios_por_estrategia.get("BREAKOUT") or {}
                    def _shadow_metric(source, *names, default=None):
                        for name in names:
                            if isinstance(source, dict) and source.get(name) is not None:
                                return source.get(name)
                        return default
                    configured_variants = {
                        item.strip().upper()
                        for item in os.getenv(
                            "BREAKOUT_SHADOW_VARIANTS", "BASELINE,FAST_TREND,ADAPTIVE"
                        ).split(",")
                        if item.strip()
                    }
                    variants = [
                        ("BASELINE", "CURRENT", "CURRENT"),
                        ("FAST_TREND", "CURRENT", "SHADOW_FAST_TREND"),
                        ("ADAPTIVE", "CURRENT", "SHADOW_FAST_TREND"),
                    ]
                    for variant_name, impulse_policy, trend_policy_name in variants:
                        if variant_name not in configured_variants:
                            continue
                        shadow_trace = []
                        shadow_profile = None
                        if variant_name == "ADAPTIVE":
                            base_profile = dict(getattr(df_real, "attrs", {}).get("strategy_profile") or {})
                            if base_profile:
                                shadow_profile = dict(base_profile)
                                shadow_profile["breakout_min_volume_ratio"] = min(
                                    float(shadow_profile.get("breakout_min_volume_ratio", 1.0)), 0.85
                                )
                                shadow_profile["version"] = f"{shadow_profile.get('version', 'env')}-SHADOW_ADAPTIVE"
                                shadow_profile["config_hash"] = hashlib.sha256(
                                    repr(sorted(shadow_profile.items())).encode("utf-8")
                                ).hexdigest()
                        shadow_result = executar_backtest_breakout(
                            df_real.copy(),
                            lado_permitido=(
                                None
                                if operar_ambos_lados
                                else (str(lado).lower() if str(lado).lower() in ("buy", "sell") else None)
                            ),
                            regime_override=regime_override,
                            symbol=symbol,
                            capital_inicial=10000.0,
                            record_provenance="SHADOW_PAPER",
                            evaluation_context="SHADOW_PAPER",
                            run_id=f"shadow:{variant_name.lower()}:{symbol}:{os.getpid()}:{int(time.time())}",
                            impulse_reset_policy=impulse_policy,
                            trend_policy=trend_policy_name,
                            persist_observability=False,
                            decision_trace=shadow_trace,
                            trace_candle_open_time=natural_trace_candle,
                            strategy_profile=shadow_profile,
                        ) or {}
                        shadow_policy = f"SHADOW_{variant_name}"
                        if current_trace and shadow_trace:
                            telemetry_payload = build_comparison_payload(
                                current=current_trace[-1],
                                shadow=shadow_trace[-1],
                                feature_vector=current_trace[-1].get("feature_vector") or {},
                                shadow_policy=shadow_policy,
                            )
                            cycle_telemetry["shadow_created"] = bool(
                                persist_shadow_comparison(telemetry_payload)
                            ) or cycle_telemetry.get("shadow_created", False)
                        shadow_payload = converter_numpy_para_nativo({
                            "symbol": str(symbol).upper(),
                            "policy_current": "CURRENT",
                            "policy_shadow": shadow_policy,
                            "current_trades": _shadow_metric(current_result, "num_trades", "trades", default=0),
                            "shadow_trades": _shadow_metric(shadow_result, "num_trades", default=0),
                            "current_wins": _shadow_metric(current_result, "num_wins", default=0),
                            "shadow_wins": _shadow_metric(shadow_result, "num_wins", default=0),
                            "current_losses": _shadow_metric(current_result, "num_losses", default=0),
                            "shadow_losses": _shadow_metric(shadow_result, "num_losses", default=0),
                            "current_pnl": _shadow_metric(current_result, "lucro", "lucro_total", default=0),
                            "shadow_pnl": _shadow_metric(shadow_result, "lucro", "lucro_total", default=0),
                            "current_drawdown": _shadow_metric(current_result, "max_drawdown", default=0),
                            "shadow_drawdown": _shadow_metric(shadow_result, "max_drawdown", default=0),
                            "paper_only": True,
                            "execution_authority": "NONE",
                            "record_provenance": "SHADOW_PAPER",
                        })
                        logging.info(
                            "[BREAKOUT_SHADOW][%s] current_trades=%s shadow_trades=%s "
                            "current_pnl=%s shadow_pnl=%s current_dd=%s shadow_dd=%s",
                            variant_name, shadow_payload["current_trades"], shadow_payload["shadow_trades"],
                            shadow_payload["current_pnl"], shadow_payload["shadow_pnl"],
                            shadow_payload["current_drawdown"], shadow_payload["shadow_drawdown"],
                        )
                        if decision_id:
                            record_event(
                                decision_id,
                                "BREAKOUT_SHADOW_COMPARISON",
                                "STRATEGY",
                                component="breakout_shadow_paper",
                                status="SHADOW",
                                reason_code=f"{variant_name}_COMPARISON",
                                payload=shadow_payload,
                            )
                except Exception as exc:
                    logging.error("[BREAKOUT_SHADOW] comparison failed: %s", type(exc).__name__)
                    if decision_id:
                        record_event(
                            decision_id,
                            "BREAKOUT_SHADOW_COMPARISON",
                            "STRATEGY",
                            component="breakout_shadow_paper",
                            status="ERROR",
                            reason_code="SHADOW_COMPARISON_ERROR",
                            payload={"error_type": type(exc).__name__, "paper_only": True, "execution_authority": "NONE"},
                        )
            if decision_id:
                configured_strategy_for_event = os.getenv("GENESIS_STRATEGY_SELECTION", "").strip().upper().replace(" ", "_")
                no_selection_reason = None
                if not melhor and configured_strategy_for_event:
                    no_selection_reason = (
                        "STRATEGY_SETUP_NOT_CONFIRMED"
                        if resultados.get("no_trade_reason") == "sem_setup_atual"
                        else "CONFIGURED_STRATEGY_NOT_READY"
                    )
                record_event(decision_id, "STRATEGY_EVALUATED", "STRATEGY", component="backtest",
                             status="SELECTED" if melhor else "NO_SELECTION",
                             reason_code=None if melhor else (no_selection_reason or "NO_STRATEGY_SELECTED"),
                             payload={
                                 "winner": (melhor or {}).get("estrategia"),
                                 "configured_strategy": configured_strategy_for_event or None,
                                 "fixed_strategy_mode": bool(configured_strategy_for_event),
                             })


            # ======================================================
            # ðŸ“„ RELATÃ“RIO DA ESTRATÃ‰GIA VENCEDORA
            # ======================================================
            if melhor and melhor.get("estrategia"):
                if decision_id:
                    set_current_decision_id(decision_id)
                    record_event(decision_id, "STRATEGY_SELECTED", "STRATEGY", component="backtest",
                                 status="SELECTED", payload={"strategy": melhor.get("estrategia")})
                    try:
                        update_commander_decision_context(
                            decision_id,
                            strategy_name=str(melhor.get("estrategia")),
                            reference_price=(
                                float(df_real["close"].dropna().iloc[-1])
                                if "close" in df_real.columns and not df_real["close"].dropna().empty
                                else None
                            ),
                            market_regime=str(regime) if regime else None,
                        )
                    except Exception as exc:
                        logging.error("[COMMANDER_CONTEXT] Falha ao persistir identidade da estrategia: %s", type(exc).__name__)
                estrategia_vencedora = melhor["estrategia"].upper()
                relatorio_vencedora = relatorios_por_estrategia.get(estrategia_vencedora)

                logging.info("ðŸ“„ RelatÃ³rio detalhado da estratÃ©gia vencedora:")
                # BREAKOUT permanece 100% intocado: mantÃ©m o formatter atual.
                if (estrategia_vencedora or "").replace(" ", "").upper() == "BREAKOUT" or not _strategy_logs_v2_enabled():
                    log_relatorio_backtest(relatorio_vencedora, logging)
                else:
                    # Para MR / EARLY_TREND / MM, nÃ£o tentar formatar como relatÃ³rio de breakout.
                    if isinstance(relatorio_vencedora, str) and relatorio_vencedora.strip():
                        logging.info(
                            f"[{estrategia_vencedora}][BACKTEST] RelatÃ³rio raw disponÃ­vel | chars={len(relatorio_vencedora)}"
                        )
                    else:
                        logging.info(f"[{estrategia_vencedora}][BACKTEST] Sem relatÃ³rio estruturado para exibir.")
            else:
                logging.warning("âš ï¸ Nenhuma estratÃ©gia vencedora encontrada.")
                relatorio_vencedora = None
                if decision_id:
                    configured_strategy_for_event = os.getenv("GENESIS_STRATEGY_SELECTION", "").strip().upper().replace(" ", "_")
                    finalize_decision(
                        decision_id,
                        "BLOCKED",
                        "STRATEGY_SETUP_NOT_CONFIRMED" if configured_strategy_for_event else "NO_STRATEGY_SELECTED",
                    )

            # Commander is created only after the strategy boundary is known.
            # It remains shadow-only; candidates enter the paper domain only.
            canonical_shadow = run_shadow_decision_sync(
                subject=f"market_decision:{symbol}",
                symbol=symbol,
                analytical_context={
                    "technical_side": lado,
                    "prosseguir": bool(prosseguir),
                    "score_pct": resultados.get("score_pct"),
                    "buy_score": (resultados.get("buy") or {}).get("score"),
                    "sell_score": (resultados.get("sell") or {}).get("score"),
                    "strategy_name": (melhor or {}).get("estrategia"),
                    "reference_price": float(df_real["close"].dropna().iloc[-1]) if "close" in df_real.columns and not df_real["close"].dropna().empty else None,
                    "market_regime": str(regime) if regime else None,
                    "record_provenance": "PRODUCTION_RUNTIME",
                },
            )
            canonical_decision = str(canonical_shadow.get("decision", "INSUFFICIENT_DATA"))
            canonical_confidence = str(canonical_shadow.get("confidence", "NOT_APPLICABLE"))
            canonical_commander_decision_id = canonical_shadow.get("commander_decision_id")
            breakout_evaluation_id = (melhor or {}).get("breakout_evaluation_id") if isinstance(melhor, dict) and str((melhor or {}).get("estrategia", "")).replace(" ", "").upper() == "BREAKOUT" else None
            if breakout_evaluation_id:
                link_breakout_evaluation(
                    breakout_evaluation_id,
                    commander_decision_id=canonical_commander_decision_id,
                    final_state="CANDIDATE_CREATED" if canonical_decision in {"CANDIDATE_LONG", "CANDIDATE_SHORT"} else "QUALIFIED_SIGNAL",
                )
            logging.info(
                "[CANONICAL_COMMANDER] decision=%s confidence=%s skills=%d mcp_tools=%d rag_context=%d data_gaps=%s rationale=%s",
                canonical_decision,
                canonical_confidence,
                len(canonical_shadow.get("skills_used") or []),
                len(canonical_shadow.get("mcp_tools_used") or []),
                len(canonical_shadow.get("rag_context_used") or []),
                canonical_shadow.get("data_gaps") or [],
                canonical_shadow.get("rationale") or "",
            )
            record_event(decision_id, "CANONICAL_COMMANDER_SHADOW", "COMMANDER", component="genesis_commander", status="SHADOW", reason_code=canonical_decision, payload={"confidence": canonical_confidence, "skills_count": len(canonical_shadow.get("skills_used") or []), "mcp_tools_count": len(canonical_shadow.get("mcp_tools_used") or []), "rag_context_count": len(canonical_shadow.get("rag_context_used") or []), "shadow_only": True, "execution_authority": "NONE", "record_provenance": "PRODUCTION_RUNTIME"})

            if canonical_decision in {"CANDIDATE_LONG", "CANDIDATE_SHORT"}:
                paper_connection = None
                try:
                    paper_enabled = is_paper_trading_enabled()
                    paper_portfolio_id = f"runtime-paper|{symbol}"
                    paper_repository = None
                    paper_portfolio = None
                    if paper_enabled:
                        paper_connection = connect_operational_database()
                        paper_repository = PaperRepository(paper_connection)
                        paper_portfolio = create_portfolio(float(os.getenv("PAPER_START_BALANCE_USDT", "10000")), paper_portfolio_id=paper_portfolio_id)
                    paper_route = route_candidate_to_paper(
                        type("CanonicalDecision", (), {
                            "commander_decision_id": canonical_commander_decision_id,
                            "decision": canonical_decision,
                            "timestamp_utc": datetime.fromisoformat(canonical_shadow["timestamp_utc"]),
                            "symbol": symbol,
                        })(),
                        strategy_id=canonical_shadow.get("strategy_id"),
                        strategy_name=canonical_shadow.get("strategy_name") or (melhor or {}).get("estrategia"),
                        quantity=float(os.getenv("PAPER_DEFAULT_QTY", "0.001")),
                        reference_price=canonical_shadow.get("reference_price") or float(df_real["close"].dropna().iloc[-1]),
                        current_price=float(df_real["close"].dropna().iloc[-1]),
                        current_symbol_exposure=0,
                        current_portfolio_exposure=0,
                        available_cash=float(os.getenv("PAPER_START_BALANCE_USDT", "10000")),
                        paper_risk_config=PaperRiskConfig(10000, 20000, 10000, True, True),
                        simulator_config=PaperSimulatorConfig(0, 0, 10000, 20000, True, True),
                        paper_enabled=paper_enabled,
                        record_provenance="PRODUCTION_RUNTIME",
                        paper_portfolio_id=paper_portfolio_id,
                        event_callback=lambda event_type, payload: record_event(decision_id, event_type, "RISK", component="paper_candidate_pipeline", status=payload.get("risk_status", "INFO"), reason_code=(payload.get("risk_reason") or [None])[0], payload=payload),
                        repository=paper_repository,
                        portfolio=paper_portfolio,
                    )
                    if breakout_evaluation_id and paper_route is not None:
                        risk_status = getattr(getattr(paper_route, "risk", None), "decision", None)
                        risk_status = getattr(risk_status, "value", risk_status)
                        risk_reasons = getattr(getattr(paper_route, "risk", None), "reasons", []) or []
                        link_breakout_evaluation(
                            breakout_evaluation_id,
                            commander_decision_id=canonical_commander_decision_id,
                            risk_status=str(risk_status) if risk_status is not None else None,
                            risk_reason=";".join(map(str, risk_reasons)) if risk_reasons else None,
                            risk_evaluated_at=getattr(getattr(paper_route, "risk", None), "timestamp_utc", None),
                            paper_order_id=getattr(getattr(paper_route, "order", None), "paper_order_id", None),
                            paper_fill_id=getattr(getattr(paper_route, "fill", None), "paper_fill_id", None),
                            paper_effect_id=getattr(getattr(paper_route, "effect", None), "paper_effect_id", None),
                            final_state=("PAPER_FILL_CREATED" if getattr(paper_route, "fill", None) else "PAPER_ORDER_CREATED" if getattr(paper_route, "order", None) else "RISK_EVALUATED"),
                        )
                    record_event(decision_id, "EXECUTION_GATE_EVALUATED", "PAPER", component="paper_candidate_pipeline", status="EVALUATED", payload={"commander_decision_id": canonical_commander_decision_id, "paper_order_created": bool(paper_route and paper_route.order), "paper_fill_created": bool(paper_route and paper_route.fill), "portfolio_effect_created": bool(paper_route and paper_route.effect), "record_provenance": "PRODUCTION_RUNTIME", "paper_only": True})
                except Exception as exc:
                    record_event(decision_id, "EXECUTION_BLOCKED", "PAPER", component="paper_candidate_pipeline", status="BLOCKED", reason_code="PAPER_PIPELINE_ERROR", payload={"error_type": type(exc).__name__, "record_provenance": "PRODUCTION_RUNTIME", "paper_only": True})
                    logging.error("[PAPER_CANDIDATE] pipeline blocked: %s", type(exc).__name__)
                finally:
                    if paper_connection is not None:
                        paper_connection.close()

            # ======================================================
            # ðŸ“ ConversÃ£o para tipos nativos
            # ======================================================
            relatorio_convertido = converter_numpy_para_nativo(relatorio)

            # ======================================================
            # ðŸ”Ž LOG COMPLETO DO RELATÃ“RIO
            # ======================================================
            # ðŸš§ ProteÃ§Ã£o contra ausÃªncia de backtest executado
            if melhor is None or melhor.get("estrategia") is None:
                motivo_sem_trade = resultados.get("no_trade_reason") if isinstance(resultados, dict) else None
                estrategia_configurada = os.getenv(
                    "GENESIS_STRATEGY_SELECTION", ""
                ).strip().upper().replace(" ", "_") or "STRATEGY"
                if motivo_sem_trade == "sem_setup_atual":
                    logging.info(
                        "[NO-TRADE] %s analítico registrado; setup atual não confirmado. "
                        "Resultado histórico não será tratado como estratégia vencedora.",
                        estrategia_configurada,
                    )
                logging.info("[NO-TRADE] Nenhuma estratégia elegível neste candle. Aguardando próximo candle.\n")
                continue # <-- IMPORTANTE: STOP AQUI


            # ðŸ§© Ajuste de lucro total â€” garante que o valor reflita o saldo final
            if melhor and isinstance(melhor, dict):
                saldo_final = melhor.get("saldo_final") or melhor.get("saldo", 0)
                saldo_inicial = melhor.get("saldo_inicial", 10000)
                if "lucro_total" not in melhor or melhor.get("lucro_total", 0) == 0:
                    melhor["lucro_total"] = saldo_final - saldo_inicial

            relatorio_convertido = converter_numpy_para_nativo(relatorio)

            # ðŸ›¡ï¸ ProteÃ§Ã£o contra empate ou retorno inesperado
            if not melhor or not isinstance(melhor, dict) or relatorio == []:
                logging.warning("âš ï¸ Nenhuma estratÃ©gia foi aprovada ou houve empate no backtest.")
                logging.warning(f"ðŸ“Š RelatÃ³rio completo do backtest: {relatorio_convertido}")
                logging.warning(f"âš ï¸ Resultado do backtest invÃ¡lido ou inesperado: {melhor}")
                time.sleep(30)
                continue

            # ðŸ§  ============================================================
            # BLOCO DE LOG VISUAL â€” ANÃLISE E BACKTEST
            # ===============================================================
            logging.info("\nðŸ§  [ANÃLISE DE MERCADO - %s]\n%s", symbol, "-"*60)
            logging.info(f"ðŸ“… {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
            logging.info(f"ðŸ’¾ Candles processados: {len(df_real)}")

            # ðŸ”¥ Puxa valores reais do regime (ou ENV se nÃ£o houver regime)
            score_minimo, score_pct_minimo, modo_validacao = obter_config_validacao()
            logging.info(f"âš™ï¸ Modo de ValidaÃ§Ã£o: {modo_validacao.upper()} (score_min={score_minimo}, pct_min={score_pct_minimo}%)")

            logging.info("-"*60)

            # Extrai dados principais do backtest
            autorizado = melhor.get("autorizado", False)
            lucro_total = melhor.get("lucro_total", 0)
            winrate = melhor.get("winrate", melhor.get("win_rate (%)", 0))

            saldo_inicial_bt = melhor.get("saldo_inicial", 0) or 0
            retorno_pct = melhor.get("retorno_pct", None)
            if retorno_pct is None and saldo_inicial_bt:
                retorno_pct = (lucro_total / saldo_inicial_bt) * 100
            retorno_pct = float(retorno_pct or 0)
            direcao = melhor.get("direcao", "N/A").upper()
            estrategia_nome = melhor.get("estrategia", "DESCONHECIDA").upper()
            lado_aprovado_gate = str(lado).upper() if str(lado).lower() in ("buy", "sell") else "N/A"

            if (
                not operar_ambos_lados
                and not strict_strategy_parity
                and lado_aprovado_gate in ("BUY", "SELL")
                and direcao in ("BUY", "SELL")
                and direcao != lado_aprovado_gate
            ):
                logging.warning(
                    f"[DECISION ENGINE] Conflito detectado: Motor={lado_aprovado_gate} vs Estrategia={direcao}. Entrada bloqueada."
                )
                aprovado = False
                status_msg = "CONFLITO_DIRECAO_GATE"
                logging.warning(f"[DECISION ENGINE] STATUS FINAL: {status_msg} | NO_TRADE")
                time.sleep(30)
                continue

            logging.info("[RESULTADO_BACKTEST] " + "-" * 60)
            logging.info(f"[RESULTADO_BACKTEST] Estrategia: {estrategia_nome}")
            logging.info(f"[RESULTADO_BACKTEST] Direcao sugerida: {direcao}")
            logging.info(f"[RESULTADO_BACKTEST] Lucro total: {lucro_total:.2f}")
            logging.info(f"[RESULTADO_BACKTEST] WinRate: {winrate:.1f}%")
            logging.info("-" * 60)

            # Define status de decis?o
            from utils.relatorio_validacao import gerar_relatorio_validacao

            trade_log_bt = melhor.get("trade_log", []) if isinstance(melhor.get("trade_log", []), list) else []
            num_trades = int(melhor.get("num_trades", len(trade_log_bt)) or 0)
            min_trades_validacao = max(int(os.getenv("BACKTEST_MIN_TRADES", "5") or 5), 1)
            status_funil = str(melhor.get("status_funil", "") or "").strip().lower()
            if not status_funil:
                if num_trades > 0:
                    status_funil = "backtest_com_trades"
                elif bool(autorizado) and direcao in ("BUY", "SELL"):
                    status_funil = "evidencia"
                else:
                    status_funil = "sem_evidencia"

            score_macro_contexto = float(resultados.get(melhor.get("direcao", "").lower(), {}).get("score", 0) or 0)
            score_operacional = melhor.get("score", melhor.get("score_calculado", None))
            if score_operacional is not None:
                try:
                    score_operacional = float(score_operacional)
                except Exception:
                    score_operacional = None
            if score_operacional is not None and not np.isfinite(score_operacional):
                score_operacional = None
            score_pct_operacional = melhor.get("score_pct", None)
            if score_pct_operacional is not None:
                score_pct_operacional = float(score_pct_operacional)
            regime_favoravel = bool((parametros_regime or {}).get("permissao_operar", True))
            estrategia_nome_upper = (estrategia_nome or "").replace(" ", "").upper()
            score_source = str(melhor.get("score_source", "desconhecido"))

            setup_now_map = resultados.get("setup_now", {}) if isinstance(resultados, dict) else {}
            setup_info = setup_now_map.get(estrategia_nome_upper, {}) if isinstance(setup_now_map, dict) else {}
            if not isinstance(setup_info, dict):
                setup_info = {}
            setup_now_ok = bool(setup_info.get("ok", melhor.get("setup_now_ok", False)))
            setup_now_side = setup_info.get("side", melhor.get("setup_now_side"))
            setup_now_reason = setup_info.get("reason", melhor.get("setup_now_reason", "nao_informado"))
            if strict_swing_parity and estrategia_nome_upper == "SWING_TREND_FOLLOWING":
                # O setup dedicado usa exatamente 1h para entrada e 4h/diÃ¡rio
                # para regime, como no backtest_entrada_1h().
                if setup_now_side in ("buy", "sell"):
                    direcao = str(setup_now_side).upper()
                    lado_aprovado = str(setup_now_side).lower()
                if setup_now_ok:
                    status_funil = "evidencia"
                regime_favoravel = melhor.get("regime") in (1, 2)
                if score_operacional is None:
                    score_operacional = float(setup_info.get("score") or 3.0)
                logging.info(
                    "[SWING][PARIDADE] setup=%s lado=%s motivo=%s regime=%s score=%.2f",
                    setup_now_ok,
                    setup_now_side,
                    setup_now_reason,
                    melhor.get("regime"),
                    float(score_operacional),
                )
            elif strict_pullback_parity and estrategia_nome_upper == "PULLBACK_TREND_FOLLOWING":
                # O Pullback canônico usa entrada em 1h e confirmação em 4h/diário.
                # O resultado do backtest e o setup dedicado são a autoridade;
                # score genérico, LSTM e sentimento não podem substituí-los.
                if setup_now_side in ("buy", "sell"):
                    direcao = str(setup_now_side).upper()
                    lado_aprovado = str(setup_now_side).lower()
                if setup_now_ok:
                    status_funil = "evidencia"
                regime_favoravel = (
                    melhor.get("regime") != 4
                    or (
                        os.getenv("PULLBACK_ALLOW_REGIME_4", "0").strip().lower()
                        in ("1", "true", "yes", "on")
                    )
                )
                if score_operacional is None:
                    score_operacional = float(setup_info.get("score") or 3.0)
                logging.info(
                    "[PULLBACK][PARIDADE] setup=%s lado=%s motivo=%s regime=%s score=%.2f",
                    setup_now_ok,
                    setup_now_side,
                    setup_now_reason,
                    melhor.get("regime"),
                    float(score_operacional),
                )
            # Mantém a confirmação do probe junto do resultado vencedor para
            # que a validação final não consulte um estado vazio/defasado.
            if setup_now_ok and status_funil == "sem_evidencia" and direcao in ("BUY", "SELL"):
                status_funil = "evidencia"
                logging.info(
                    "[CONSISTENCY] Setup atual confirmado; status_funil ajustado para evidencia "
                    "apesar de num_trades=0 (aguardando gatilho real)."
                )
            logging.info(
                "[FUNIL][ORIGEM] winner_by_backtest=%s | setup_now_ok=%s | setup_side=%s | setup_reason=%s",
                estrategia_nome_upper,
                str(setup_now_ok),
                str(setup_now_side),
                str(setup_now_reason),
            )

            if score_operacional is None and direcao in ("BUY", "SELL"):
                logging.error(
                    "[CONSISTENCY] Score ausente para estrategia vencedora: estrategia=%s direcao=%s source=%s. Bloqueando execucao.",
                    estrategia_nome_upper,
                    direcao,
                    score_source,
                )
                time.sleep(30)
                continue

            # A estratégia vencedora pode ser uma simulação sem score operacional
            # (por exemplo, MARKET_MAKING_LATERAL com direção NONE).  Não deixe
            # que a telemetria derrube o worker ao formatar None como número.
            score_operacional_log = (
                f"{score_operacional:.2f}" if score_operacional is not None else "N/A"
            )
            score_macro_log = (
                f"{score_macro_contexto:.2f}" if score_macro_contexto is not None else "N/A"
            )
            logging.info(
                f"[SCORE] estrategia={score_operacional_log} macro_contexto={score_macro_log} "
                f"| source={score_source} | min_trades={min_trades_validacao}"
            )

            setup_gate_required = estrategia_nome_upper in {
                "SWING_TREND_FOLLOWING",
                "PULLBACK_TREND_FOLLOWING",
                "MARKET_MAKING_LATERAL",
            }
            if setup_gate_required and not setup_now_ok:
                # O resultado histórico valida a estratégia, mas nunca pode
                # autorizar uma ordem sem confirmação do setup/regime atual.
                estado_validacao = "AGUARDANDO_TRIGGER"
                logging.info(
                    "[VALIDAÇÃO FINAL] estratégia=%s backtest válido, "
                    "mas setup atual não confirmado; aguardando gatilho.",
                    estrategia_nome_upper,
                )
            else:
                estado_validacao = validar_resultado_backtest(
                    status_funil=status_funil,
                    num_trades=num_trades,
                    retorno=retorno_pct,
                    direcao=direcao,
                    score_estrategia=score_operacional,
                    regime_favoravel=regime_favoravel,
                    min_trades=min_trades_validacao,
                    setup_confirmado=setup_now_ok,
                )
            cycle_telemetry["macro_state"] = estado_validacao
            cycle_telemetry["macro_reason"] = (
                f"num_trades={num_trades}<{min_trades_validacao}"
                if estado_validacao == "NEGADO" and num_trades < min_trades_validacao
                else estado_validacao
            )
            cycle_telemetry["execution_authorized"] = estado_validacao == "APROVADO"
            emit_cycle(cycle_telemetry, "macro_validation")
            logging.info(
                f"[VALIDAÇÃO FINAL] estado={estado_validacao} num_trades={num_trades} "
                f"retorno={retorno_pct:.2f}% status_funil={status_funil}"
            )
            decisao_contexto_aprovada = estado_validacao == "APROVADO"
            if decisao_contexto_aprovada:
                inconsistencias = []
                direcao_norm = (direcao or "").upper()
                estrategia_validacao = (estrategia_nome or "").replace(" ", "").upper()
                mm_sem_score_direcional = estrategia_validacao == "MARKET_MAKING_LATERAL" and direcao_norm in ("BOTH", "NONE", "AUTO")
                if direcao_norm not in ("BUY", "SELL") and not mm_sem_score_direcional:
                    inconsistencias.append(f"direcao_invalida={direcao_norm or 'NONE'}")

                score_pct_ref = score_pct_operacional
                if score_pct_ref is None and score_operacional is not None:
                    score_pct_ref = (score_operacional / max(float(score_minimo), 1.0)) * 100.0
                if score_pct_ref is not None:
                    score_pct_ref = float(score_pct_ref)

                if num_trades < min_trades_validacao:
                    inconsistencias.append(f"num_trades={num_trades}<{min_trades_validacao}")
                if score_operacional is not None and not mm_sem_score_direcional and not (strict_strategy_parity and estrategia_nome_upper in ("SWING_TREND_FOLLOWING", "PULLBACK_TREND_FOLLOWING")) and score_operacional < float(score_minimo):
                    inconsistencias.append(f"score={score_operacional:.2f}<{float(score_minimo):.2f}")
                if score_pct_ref is not None and not mm_sem_score_direcional and not (strict_strategy_parity and estrategia_nome_upper in ("SWING_TREND_FOLLOWING", "PULLBACK_TREND_FOLLOWING")) and score_pct_ref < float(score_pct_minimo):
                    inconsistencias.append(f"score_pct={score_pct_ref:.2f}%<{float(score_pct_minimo):.2f}%")

                if inconsistencias:
                    logging.error(
                        "[CONSISTENCY] Contexto marcado como APROVADO, mas falhou checagem final: %s",
                        " | ".join(inconsistencias),
                    )
                    logging.error("[CONSISTENCY] Execucao real bloqueada para evitar ordem contraditoria.")
                    time.sleep(30)
                    continue

            if estado_validacao == "APROVADO":
                status_msg = "CONTEXTO APROVADO - AUTORIZADO PARA EXECUCAO"
                logging.info(f"[VALIDACAO] STATUS FINAL: {status_msg}")

                # âœ… BREAKOUT permanece intocado; demais estratÃ©gias ganham telemetria prÃ³pria (logging-only).
                estrategia_norm = (melhor.get("estrategia", "") or "").replace(" ", "").upper()
                if estrategia_norm == "BREAKOUT" or not _strategy_logs_v2_enabled():
                    # ? Relat?rio de valida??o ? caso positivo (formato atual)
                    gerar_relatorio_validacao(
                        estrategia=melhor.get("estrategia", "DESCONHECIDA"),
                        lado=melhor.get("direcao", "INDEFINIDO"),
                        score_lado=score_operacional,
                        score_pct_lado=score_pct_operacional,
                        backtest_gain=melhor.get("retorno_pct", 0),
                        decisao_final=True,
                        etapa="POS-BACKTEST",
                        num_trades=num_trades,
                        estado_validacao=estado_validacao,
                    )
                else:
                    log_validacao_por_estrategia(
                        estrategia=estrategia_norm,
                        melhor=melhor,
                        resultados=resultados,
                        decisao_final=True,
                        etapa="POS-BACKTEST",
                        df=df_real,
                        relatorio_vencedora=relatorio_vencedora,
                        logger=logging,
                    )

            else:
                if decision_id:
                    record_event(decision_id, "STRATEGY_EVALUATED", "STRATEGY", status="BLOCKED",
                                 component="validation", reason_code=str(estado_validacao))
                    finalize_decision(decision_id, "BLOCKED", str(estado_validacao))
                if estado_validacao == "AGUARDANDO_TRIGGER":
                    status_msg = "CONTEXTO NEUTRO - AGUARDANDO_TRIGGER"
                    logging.info(f"[VALIDACAO] STATUS FINAL: {status_msg}")
                    logging.info(
                        f"[VALIDACAO] Autorizado={autorizado} | LucroTotal={lucro_total:.2f} | "
                        f"status_funil={status_funil}. Monitorando gatilho real."
                    )
                else:
                    status_msg = "CONTEXTO BLOQUEADO PELO AVALIADOR MACRO"
                    logging.warning(f"[VALIDACAO] STATUS FINAL: {status_msg}")
                    logging.warning(
                        f"[VALIDACAO] Autorizado={autorizado} | LucroTotal={lucro_total:.2f}. Entrada bloqueada."
                    )
                    logging.warning("[VALIDACAO] O sistema aguardara o proximo ciclo antes de tentar nova entrada.")

                estrategia_norm = (melhor.get("estrategia", "") or "").replace(" ", "").upper()
                if estrategia_norm == "BREAKOUT" or not _strategy_logs_v2_enabled():
                    # ? Relat?rio de valida??o ? caso neutro/negativo (formato atual)
                    gerar_relatorio_validacao(
                        estrategia=melhor.get("estrategia", "DESCONHECIDA"),
                        lado=melhor.get("direcao", "INDEFINIDO"),
                        score_lado=score_operacional,
                        score_pct_lado=score_pct_operacional,
                        backtest_gain=melhor.get("retorno_pct", 0),
                        decisao_final=(estado_validacao == "APROVADO"),
                        etapa="POS-BACKTEST",
                        num_trades=num_trades,
                        estado_validacao=estado_validacao,
                    )
                else:
                    log_validacao_por_estrategia(
                        estrategia=estrategia_norm,
                        melhor=melhor,
                        resultados=resultados,
                        decisao_final=False,
                        etapa="POS-BACKTEST",
                        df=df_real,
                        relatorio_vencedora=relatorio_vencedora,
                        logger=logging,
                    )

                time.sleep(30)
                continue


            logging.info("="*60 + "\n")

            # Trava de direcao unica por ciclo + idempotencia por candle.
            direcao_exec = str(direcao or "").strip().upper()
            estrategia_exec_norm = (melhor.get("estrategia", "") or "").replace(" ", "").upper()
            if estrategia_exec_norm == "MARKET_MAKING_LATERAL" and direcao_exec in ("BOTH", "NONE", "AUTO", ""):
                direcao_exec = "BOTH"
            if direcao_exec not in ("BUY", "SELL"):
                if estrategia_exec_norm == "MARKET_MAKING_LATERAL" and direcao_exec == "BOTH":
                    logging.info("[EXEC-GATE] Market Making opera nos dois lados; direção= BOTH.")
                else:
                    logging.warning(f"[EXEC-GATE] Direcao invalida para execucao: {direcao_exec or 'NONE'}.")
                    finalize_decision_if_open(decision_id, "BLOCKED", "INVALID_DIRECTION")
                    time.sleep(30)
                    continue

            already_exec, exec_key = is_registered(
                symbol=symbol,
                timeframe=str(interval),
                candle_ts=ts_atual,
                side=direcao_exec,
            )
            if already_exec:
                logging.warning(f"[IDEMPOTENCIA] Execucao ja registrada para a chave: {exec_key}. Ignorando ciclo.")
                finalize_decision_if_open(decision_id, "BLOCKED", "DUPLICATE_EXECUTION")
                time.sleep(5)
                continue

            # ðŸ”¥ Ativa o modo produÃ§Ã£o apÃ³s o backtest
            if not ativar_modo_producao():
                logging.warning("ðŸš« ExecuÃ§Ã£o real interrompida pelo bloqueio de produÃ§Ã£o.")
                finalize_decision_if_open(decision_id, "BLOCKED", "LIVE_EXECUTION_DISABLED")
                time.sleep(30)
                continue


            # ðŸ“Š SCORE FINAL CONSOLIDADO (IA + Backtest + EstratÃ©gia)
            logging.info("\nðŸ“Š RESUMO FINAL DE ENTRADA")
            buy_score = resultados.get("buy", {}).get("score", 0)
            sell_score = resultados.get("sell", {}).get("score", 0)
            logging.info(f"ðŸ§  Score IA (modo moderado) â†’ BUY={buy_score} | SELL={sell_score}")

            winrate = melhor.get("winrate") or melhor.get("win_rate (%)", 0)
            num_trades_resumo = int(melhor.get("num_trades", len(melhor.get("trade_log", []) or [])) or 0)
            lucro_total_resumo = float(melhor.get("lucro_total", melhor.get("lucro", 0)) or 0)
            if num_trades_resumo == 0:
                status_backtest_resumo = "NEUTRO"
            else:
                status_backtest_resumo = "POSITIVO" if lucro_total_resumo > 0 else "NEGATIVO"
            estrategia_norm = (melhor.get("estrategia", "") or "").replace(" ", "").upper()
            if estrategia_norm == "BREAKOUT" or not _strategy_logs_v2_enabled():
                # MantÃ©m exatamente o log atual do BREAKOUT.
                logging.info(
                    f"ðŸ§ª Backtest: {status_backtest_resumo} | "
                    f"Lucro: {lucro_total_resumo:.2f} | Win Rate: {winrate:.1f}%"
                )
            else:
                logging.info(
                    f"[{estrategia_norm}] Backtest: {status_backtest_resumo} | "
                    f"LucroTotal: {lucro_total_resumo:.2f} | Win Rate: {float(winrate):.1f}%"
                )

            if melhor.get("estrategia", "").lower() == "breakout":
                from estrategia_entrada.estrategia_breakout_filtros import breakout_filters_summary
                score_breakout_oficial = float(
                    melhor.get("score", melhor.get("score_calculado", 0)) or 0
                )
                resumo = breakout_filters_summary(
                    score_override=score_breakout_oficial,
                    incluir_filtros=not decisao_contexto_aprovada,
                )
                for linha in resumo:
                    logging.info(f"ðŸ“ {linha}")
                if decisao_contexto_aprovada:
                    logging.info("ðŸ“ Resumo de filtros detalhado omitido para evitar divergÃªncia com score oficial pÃ³s-backtest.")

            # SubstituÃ­do MODO_RELAXADO por MODO_VALIDACAO
            modo = os.getenv("MODO_VALIDACAO", "normal").strip().lower()
            logging.info(f"ðŸ” Modo de ValidaÃ§Ã£o: {modo.upper()} (score e critÃ©rios adaptados)")
            logging.info(f"ðŸŽ¯ EstratÃ©gia Selecionada: {melhor.get('estrategia')} | DireÃ§Ã£o: {melhor.get('direcao')}")

            # ETAPA 3: registra a vencedora para proteger MR nova de cancelamentos/limpeza indevida
            global _ULTIMA_ESTRATEGIA_ESCOLHIDA
            _ULTIMA_ESTRATEGIA_ESCOLHIDA = (melhor.get("estrategia", "") or "").replace(" ", "").upper()

            
            executar_monitoramento = False  # â† inicia como False

            estrategia_execucao = estrategias_execucao.get(melhor.get("estrategia", "").upper())

            if estrategia_execucao:
                lado_gate = str(lado_aprovado if 'lado_aprovado' in locals() else "").strip().upper()
                if (
                    not operar_ambos_lados
                    and not strict_strategy_parity
                    and lado_gate in ("BUY", "SELL")
                    and direcao_exec in ("BUY", "SELL")
                    and lado_gate != direcao_exec
                ):
                    logging.warning(
                        f"[EXEC-GATE] Bloqueado por conflito de direcao no ciclo: gate={lado_gate} vs pos_backtest={direcao_exec}"
                    )
                    finalize_decision_if_open(decision_id, "BLOCKED", "DIRECTION_CONFLICT")
                    time.sleep(30)
                    continue

                # Hard gate right before execution.
                try:
                    risk_state_exec = evaluate_risk_limits(symbol=symbol)
                    process_risk_transition(risk_state_exec, symbol=symbol, source="trading_bot_geneses.pre_exec")
                    if risk_state_exec.get("blocked"):
                        logging.warning(
                            "[RISK_GUARD] Execucao bloqueada antes da estrategia | motivo=%s",
                            risk_state_exec.get("reason"),
                        )
                        write_kill_switch_flag(risk_state_exec)
                        finalize_decision_if_open(
                            decision_id,
                            "BLOCKED",
                            str(risk_state_exec.get("reason") or "RISK_BLOCKED"),
                        )
                        mark_exit(cycle_telemetry, "RISK_GATE", "PRE_BREAKOUT_RISK_BLOCKED")
                        emit_cycle(cycle_telemetry, "cycle_exit")
                        continue
                except Exception as e:
                    logging.warning(f"[RISK_GUARD] Falha no gate pre-execucao: {e}")

                # ======================================================
                # ARQUITETURA (edge vs timing): se existir intenÃ§Ã£o ativa
                # para estratÃ©gia stateful/pendente, ela tem prioridade e
                # o funil nÃ£o dispara outra execuÃ§Ã£o atÃ© resolver.
                # ======================================================
                intent_ativa = _INTENTS.get_active(symbol)
                estrategia_vencedora = (melhor.get("estrategia", "") or "").replace(" ", "").upper()

                # INTENT ? um mecanismo de timing stateful por s?mbolo.
                # Ela s? pode ter prioridade quando for da mesma estrat?gia vencedora.
                # Caso contr?rio, ? um res?duo de estado que pode bloquear/sequestrar a execu??o.
                if (
                    intent_ativa
                    and intent_ativa.strategy in ("MEAN_REVERSION", "EARLY_TREND")
                    and estrategia_vencedora
                    and intent_ativa.strategy != estrategia_vencedora
                ):
                    logging.warning(
                        f"[INTENT] Mismatch: intent={intent_ativa.strategy} vs vencedora={estrategia_vencedora} | limpando intent para respeitar a vencedora"
                    )
                    _INTENTS.clear(symbol, reason=f"intent_mismatch_{intent_ativa.strategy.lower()}_vs_{estrategia_vencedora.lower()}")
                    intent_ativa = None

                if intent_ativa and intent_ativa.strategy in ("MEAN_REVERSION", "EARLY_TREND"):
                    estrategia_escolhida = intent_ativa.strategy
                    logging.info(
                        f"[INTENT] Timing ativo | symbol={symbol} | strategy={estrategia_escolhida} | expira_em={int(intent_ativa.expires_ts - time.time())}s"
                    )
                else:
                    estrategia_escolhida = estrategia_vencedora

                # ======================================================
                # ETAPA 2: ATIVAÃ‡ÃƒO CONTROLADA DA MR NOVA (FSM + ENGINE)
                # - somente quando a vencedora for MEAN_REVERSION
                # - nÃ£o chama MR antiga
                # - nÃ£o espera retorno (trade_id, preco, tamanho)
                # - nÃ£o cancela ordens / nÃ£o mexe em flags aqui
                # ======================================================
                if estrategia_escolhida == "MEAN_REVERSION":
                    try:
                        # Cria/renova intenÃ§Ã£o quando o funil escolhe MR (edge aprovado).
                        if not intent_ativa:
                            ttl_candles = int(os.getenv("MR_INTENT_TTL_CANDLES", "5"))
                            ttl_s = max(60, ttl_candles * int(float(os.getenv("INTERVAL", "5"))) * 60)
                            _INTENTS.upsert(symbol=symbol, strategy="MEAN_REVERSION", ttl_seconds=ttl_s, reason="edge_aprovado_funil")

                        intervalo_exec = locals().get("interval") or os.getenv("INTERVAL", "5")
                        quantidade_exec = float(os.getenv("MR_QTY", "0.001"))
                        estado = executar_mean_reversion_nova_tick(
                            session=session,
                            symbol=symbol,
                            df=df,
                            interval=str(intervalo_exec),
                            qty=float(quantidade_exec),
                            regime_override=regime,
                        )
                        logging.info(f"[MR-NOVA] Tick executado | Estado atual: {estado}")
                        # Remove intenÃ§Ã£o quando MR voltar ao Idle (nÃ£o hÃ¡ mais pendÃªncia).
                        if str(estado).upper() == "IDLE":
                            _INTENTS.clear(symbol, reason="mr_idle")
                    except Exception as e:
                        logging.exception(f"[MR-NOVA] Falha ao executar tick: {e}")
                    resultado_estrategia = None
                elif estrategia_escolhida == "EARLY_TREND":
                    try:
                        if not intent_ativa:
                            ttl_candles = int(os.getenv("EARLY_TREND_INTENT_TTL_CANDLES", "3"))
                            ttl_s = max(60, ttl_candles * int(float(os.getenv("INTERVAL", "5"))) * 60)
                            _INTENTS.upsert(symbol=symbol, strategy="EARLY_TREND", ttl_seconds=ttl_s, reason="edge_aprovado_funil")

                        intervalo_exec = locals().get("interval") or os.getenv("INTERVAL", "5")
                        quantidade_exec = float(os.getenv("EARLY_TREND_QTY", "0.001"))

                        # Garantir DF suficiente para a fase (evita `fase_df_curto` no Early Trend).
                        # O core exige ~LOOKBACK+5 candles (default 80 -> ~85).
                        df_et = df
                        try:
                            lookback = int(os.getenv("EARLY_TREND_LOOKBACK", "80"))
                        except Exception:
                            lookback = 80
                        required_len = max(int(lookback) + 5, 30)
                        try:
                            if df_et is None or getattr(df_et, "empty", False) or len(df_et) < required_len:
                                df_et = get_market_data_bybit(symbol, session, interval=str(intervalo_exec), limit=max(required_len + 10, 200))
                        except Exception:
                            df_et = df

                        regime_override = locals().get("regime")
                        tick_res = executar_early_trend_tick(
                            session=session,
                            symbol=symbol,
                            df=df_et,
                            interval=str(intervalo_exec),
                            qty=float(quantidade_exec),
                            regime_override=regime_override,
                        )
                        logging.info(f"[EARLYTREND] Tick | status={tick_res.status} | order_id={tick_res.order_id} | msg={tick_res.message}")

                        if tick_res.status in ("EXPIRED", "INVALIDATED", "DISABLED", "ERROR", "NO_SIGNAL"):
                            _INTENTS.clear(symbol, reason=f"early_trend_{tick_res.status.lower()}")

                        if tick_res.status == "FILLED":
                            _INTENTS.clear(symbol, reason="early_trend_filled")
                            resultado_estrategia = (tick_res.order_id, tick_res.fill_price, tick_res.qty)
                        else:
                            resultado_estrategia = None
                    except Exception as e:
                        logging.exception(f"[EARLYTREND] Falha ao executar tick: {e}")
                        _INTENTS.clear(symbol, reason="early_trend_exception")
                        resultado_estrategia = None
                elif estrategia_escolhida == "SWING_TREND_FOLLOWING":
                    resultado_estrategia = executar_swing_okx(
                        session=session,
                        symbol=symbol,
                        timeframes=swing_timeframes or {},
                        entry_timeframe=os.getenv("SWING_ENTRY_TIMEFRAME", "1h"),
                    )
                    if resultado_estrategia is None:
                        record_event(
                            decision_id,
                            "EXECUTION_BLOCKED",
                            "STRATEGY",
                            component="swing_trend_following",
                            status="BLOCKED",
                            reason_code="SWING_LIVE_EXECUTOR_GUARD",
                            payload={"paper_only": True, "execution_authority": "OKX_GUARDED"},
                        )
                elif estrategia_escolhida == "PULLBACK_TREND_FOLLOWING":
                    resultado_estrategia = executar_pullback_okx(
                        session=session,
                        symbol=symbol,
                        timeframes=swing_timeframes or {},
                    )
                    if resultado_estrategia is None:
                        record_event(
                            decision_id,
                            "EXECUTION_BLOCKED",
                            "STRATEGY",
                            component="pullback_trend_following",
                            status="BLOCKED",
                            reason_code="PULLBACK_LIVE_EXECUTOR_GUARD",
                            payload={"paper_only": True, "execution_authority": "OKX_GUARDED"},
                        )
                else:
                    resultado_estrategia = estrategia_execucao(symbol, session)
                    if cycle_telemetry.get("breakout_execution_requested"):
                        cycle_telemetry["breakout_called"] = True

                if cycle_telemetry.get("breakout_execution_requested"):
                    cycle_telemetry["breakout_evaluation_created"] = bool(current_trace)
                    emit_cycle(cycle_telemetry, "breakout_complete")

                if estrategia_escolhida != "MEAN_REVERSION" and (
                    resultado_estrategia
                    and isinstance(resultado_estrategia, (list, tuple))
                    and len(resultado_estrategia) == 3
                    and resultado_estrategia[0]
                ):
                    trade_id, preco_entrada, tamanho = resultado_estrategia

                    logging.info(f"ðŸ†” Trade ID: {trade_id} | ðŸŽ¯ PreÃ§o de Entrada: {preco_entrada:.2f} | ðŸ§® Tamanho inicial: {tamanho}")
                    marked_new, marked_key = mark_registered(
                        symbol=symbol,
                        timeframe=str(interval),
                        candle_ts=ts_atual,
                        side=direcao_exec,
                    )
                    if marked_new:
                        logging.info(f"[IDEMPOTENCIA] Execucao registrada para a chave: {marked_key}")
                    else:
                        logging.warning(f"[IDEMPOTENCIA] Chave ja existia apos execucao: {marked_key}")
                    score_entrada = melhor.get("score", 1.0)
                    logging.info(f"ðŸ“Š Score de entrada final: {score_entrada}")

                    if score_entrada == 0.75:
                        tamanho = round(tamanho * 0.75, 6)
                        logging.info(f"âš ï¸ Entrada parcial com 75% da posiÃ§Ã£o: {tamanho}")
                    elif score_entrada < 0.5:
                        logging.warning(f"ðŸš« Score muito baixo ({score_entrada}). OperaÃ§Ã£o abortada.")
                    else:
                        logging.info("âœ… EstratÃ©gia executada com sucesso. Entrando em operaÃ§Ã£o.")
                        with open("operacao_ativa.flag", "w") as f:
                            f.write("1")
                        if ativar_modo_producao():
                            executar_monitoramento = True
                        else:
                            if os.path.exists("operacao_ativa.flag"):
                                os.remove("operacao_ativa.flag")
                            logging.warning("ðŸš« Monitoramento real nÃ£o iniciado: modo produÃ§Ã£o bloqueado por configuraÃ§Ã£o.")

                elif estrategia_escolhida != "MEAN_REVERSION":
                    logging.warning("âš ï¸ EstratÃ©gia chamada, mas sem sinal vÃ¡lido. Nenhuma operaÃ§Ã£o realizada.")
            else:
                logging.error("âŒ EstratÃ©gia nÃ£o encontrada no dicionÃ¡rio de execuÃ§Ã£o.")
                mark_exit(cycle_telemetry, "STRATEGY_SELECTION", "PRE_BREAKOUT_NO_STRATEGY_SELECTED")
                emit_cycle(cycle_telemetry, "cycle_exit")

            # ðŸ§  Gerenciamento com IA (executa se houver entrada vÃ¡lida)
            if executar_monitoramento:
                logging.info("ðŸ¤– Iniciando monitoramento de trades ativos via /manage_trade...")

                # Bloqueio: se jÃ¡ houver monitoramento/flag, nÃ£o varrer globalmente
                if operacao_em_andamento or os.path.exists("operacao_ativa.flag"):
                    logging.info("Monitoramento ativo detectado. Pulando scanner global de trades abertos.")
                else:
                    trades_ativos = obter_trades_em_aberto(session, symbol)
                    for trade in trades_ativos:
                        stop_type = (trade.get("stopOrderType") or "").lower()
                        if stop_type in ("takeprofit", "stoploss", "tpsl"):
                            logging.info(f"Ignorando evento condicional (TP/SL) para IA: {trade.get('orderId')}")
                            continue
                        chamar_manage_trade(trade)

            finalize_decision_if_open(decision_id)

            # ðŸ”„ Final do ciclo (reseta e espera)
            operacao_em_andamento = False
            logging.info("ðŸš¦ RobÃ´ liberado para prÃ³xima anÃ¡lise.")
            time.sleep(5)

            # ðŸ” Debug
            relatorio_convertido = converter_numpy_para_nativo(relatorio)
            logging.warning(f"ðŸ“Š RelatÃ³rio completo do backtest: {relatorio_convertido}")
            logging.warning(f"ðŸŽ¯ EstratÃ©gia selecionada: {melhor.get('estrategia')}")







        # âœ… ApÃ³s entrada ou tentativa, verificar se flag estÃ¡ correta
        
        finally:
            operacao_em_andamento = False
            try:
                trades_ativos = obter_trades_em_aberto(session, symbol)

                # ðŸ” Fallback seguro se None
                if not trades_ativos:
                    trades_ativos = []

                logging.debug(f"ðŸ“¦ Trades ativos detectados: {trades_ativos}")

                if os.path.exists("operacao_ativa.flag"):
                    if not trades_ativos:
                        os.remove("operacao_ativa.flag")
                        logging.info("ðŸ§¹ Flag removida: nenhuma ordem ativa foi encontrada.")
                    else:
                        status_finais = ["filled", "cancelled", "rejected"]
                        if all((t.get("orderStatus") or "").lower() in status_finais for t in trades_ativos):
                            os.remove("operacao_ativa.flag")
                            logging.info("ðŸ§¹ Flag removida: todas as ordens foram encerradas.")
                        else:
                            logging.info("ðŸš« Ainda hÃ¡ ordens ativas. Mantendo flag.")
                            logging.info("ðŸ¤– Chamando gestÃ£o da IA para monitoramento das ordens ativas...")
                            for trade in trades_ativos:
                                stop_type = (trade.get("stopOrderType") or "").lower()
                                if stop_type in ("takeprofit", "stoploss", "tpsl"):
                                    logging.info(f"Ignorando evento condicional (TP/SL) para IA: {trade.get('orderId')}")
                                    continue
                                if isinstance(trade, dict) and "symbol" in trade and "qty" in trade:
                                    chamar_manage_trade(trade)
                                else:
                                    logging.warning(f"âŒ Trade malformado detectado: {trade}")
                else:
                    logging.info("âœ… Nenhum flag ou ordem ativa. Sistema pronto para nova entrada.")

            except Exception as e:
                logging.error(f"âŒ ERRO AO GERENCIAR ORDENS ATIVAS (finally): {e}", exc_info=True)

            # âœ… Garantia extra: limpar flag se estamos fora do backtest e nÃ£o houver ordens
            try:
                if os.path.exists("operacao_ativa.flag"):
                    if (_ULTIMA_ESTRATEGIA_ESCOLHIDA or "").replace(" ", "").upper() != "MEAN_REVERSION":
                        os.remove("operacao_ativa.flag")
                        logging.info("ðŸ§¹ [FORÃ‡ADO] Flag operacao_ativa removida (limpeza preventiva).")
                    else:
                        logging.info("[MR-NOVA] Flag preservada (FSM gerencia o estado).")
            except Exception as e:
                logging.warning(f"âš ï¸ NÃ£o foi possÃ­vel remover operacao_ativa.flag: {e}")


        




# **4ï¸âƒ£ FunÃ§Ã£o principal otimizada**
def main():
    """Fluxo principal de inicializaÃ§Ã£o do sistema com anÃ¡lise, IA e backtest antes da operaÃ§Ã£o real."""

    global df

    logging.info("ðŸš€ Iniciando o fluxo principal do sistema RobÃ´ Fluxo...")

    # ðŸ”¹ 1. Carrega dados de candles com indicadores
    # âš ï¸ GovernanÃ§a de dados: NÃƒO usar df prÃ©-processado (ML) como dados de mercado.
    df = get_market_data_bybit(symbol, session, interval=str(INTERVAL), limit=CANDLES_MINIMOS)
    if df is None or df.empty:
        logging.error("âŒ Falha ao carregar os dados do mercado.")
        return

    logging.info("ðŸ“Š Dados de mercado carregados com sucesso.")

    df = calculate_indicators(df)
    logging.info("âœ… Indicadores tÃ©cnicos calculados.")

    # ðŸ”¹ 2. ValidaÃ§Ã£o dos indicadores
    if not validar_indicadores(df):
        logging.warning("âš ï¸ Indicadores invÃ¡lidos ou incompletos. Encerrando execuÃ§Ã£o.")
        return

    # ðŸ”¹ 3. AvaliaÃ§Ã£o de ordens abertas
    # ðŸ§± [DATA-GOV] Guardrails econÃ´micos mÃ­nimos (protege contra df ML/normalizado vazando para o trading)
    try:
        ultimo = df.dropna(subset=["open", "high", "low", "close"]).iloc[-1]
        open_ = float(ultimo["open"])
        high = float(ultimo["high"])
        low = float(ultimo["low"])
        close = float(ultimo["close"])

        if close <= 0 or high <= 0 or low <= 0 or open_ <= 0:
            logging.error(
                f"âš ï¸ [DATA-GOV] OHLC invÃ¡lido (valores <= 0) | open={open_} high={high} low={low} close={close}. "
                "Abortando anÃ¡lise para evitar decisÃ£o com dados sem significado econÃ´mico."
            )
            return

        if high < low or close < low or close > high:
            logging.error(
                f"âš ï¸ [DATA-GOV] OHLC inconsistente | open={open_} high={high} low={low} close={close}. "
                "Abortando anÃ¡lise para evitar decisÃ£o com dados corrompidos."
            )
            return
    except Exception as e:
        logging.warning(f"âš ï¸ [DATA-GOV] Falha ao validar invariantes do df de mercado: {e}")

    if verificar_ordens_ativas(session, TRADING_SYMBOL):
        logging.info("ðŸ“Œ JÃ¡ existe uma operaÃ§Ã£o ativa. RobÃ´ nÃ£o entrarÃ¡ novamente.")
        return

    # ðŸ”¹ 4. Preparar dados para IA
    try:
        # Garante que o DataFrame nÃ£o estÃ¡ vazio e as colunas existem
        if df is None or df.empty or "RSI" not in df.columns or "close" not in df.columns:
            logging.error("âŒ DataFrame vazio ou colunas obrigatÃ³rias ausentes (RSI, close). Abortando envio para IA.")
            market_data = None
        else:
            # Verifica se hÃ¡ valores nÃ£o nulos suficientes no RSI e close
            if df["RSI"].dropna().empty or df["close"].dropna().empty:
                logging.error("âŒ Colunas RSI ou close estÃ£o vazias no Ãºltimo candle. Abortando envio para IA.")
                market_data = None
            else:
                # Pega os valores do Ãºltimo candle vÃ¡lido
                rsi = float(df["RSI"].dropna().iloc[-1])
                preco_atual = float(df["close"].dropna().iloc[-1])

                # ðŸ”¹ Campos obrigatÃ³rios para o endpoint da IA
                #symbol = get_ativos_env()[0]   # LÃª o ativo do .env para esta instÃ¢ncia
                side = "buy"        # ou "sell" conforme sua lÃ³gica anterior
                preco_entrada = preco_atual  # ou outro valor, se calculado

                market_data = {
                    "symbol": symbol,
                    "side": side,
                    "preco_entrada": preco_entrada,
                    "RSI": rsi,
                    "preco_atual": preco_atual
                }

                logging.info(f"ðŸ“¦ Dados preparados para IA: {market_data}")

    except Exception as e:
        logging.error(f"âŒ Erro ao extrair RSI/preÃ§o para IA: {e}")
        market_data = None






    # ðŸ”¹ 5. DecisÃ£o da IA via API externa
    decisao_ia = tomar_decisao_com_ia(market_data)
    if not decisao_ia:
        logging.warning("âš ï¸ A IA nÃ£o retornou decisÃ£o vÃ¡lida.")
        return

    logging.info(f"ðŸ¤– DecisÃ£o da IA: {decisao_ia}")
    if not decisao_ia.get("enter_market"):
        logging.info("ðŸŸ¡ IA recomendou NÃƒO entrar no mercado.")
        return

    melhor_estrategia = decisao_ia.get("melhor_estrategia", "BREAKOUT").replace(" ", "").upper()

    # âœ… Impede execuÃ§Ã£o de estratÃ©gias nÃ£o ativadas no sistema
    estrategias_disponiveis = list(estrategias_execucao.keys())
    if melhor_estrategia not in estrategias_disponiveis:
        logging.warning(f"âš ï¸ EstratÃ©gia sugerida pela IA ({melhor_estrategia}) nÃ£o estÃ¡ habilitada.")
        melhor_estrategia = "BREAKOUT"
        logging.info(f"ðŸ” EstratÃ©gia substituÃ­da por: {melhor_estrategia}")
    else:
        logging.info(f"âœ… EstratÃ©gia validada e aceita: {melhor_estrategia}")


    # ðŸ”¹ 6. Consulta ao modelo OpenAI (reforÃ§o contextual)
    prompt = f"""
    RSI atual: {rsi}
    PreÃ§o atual: {preco_atual}
    EstratÃ©gia sugerida pela IA: {melhor_estrategia}
    """
    logging.info("ðŸ“¨ Enviando consulta para OpenAI com contexto do mercado...")

    resposta_openai = consultar_openai_contexto_com_cooldown(prompt)
    logging.info(f"ðŸ§  OpenAI respondeu: {resposta_openai}")

    # ðŸ”¹ 7. Backtest completo com escolha automÃ¡tica da melhor estratÃ©gia
    logging.info("ðŸ§ª Iniciando backtest completo com saldo inicial de $10.000...")
    melhor_resultado, relatorio_completo, relatorios_por_estrategia = executar_backtest_completo(df, sentimento=None, capital_inicial=10000)

    lucro_chave = (melhor_resultado or {}).get("lucro_total", (melhor_resultado or {}).get("lucro", 0))

    if not melhor_resultado or lucro_chave is None or lucro_chave <= 0:
        logging.warning("ðŸš« Nenhuma estratÃ©gia passou no backtest. Entrada serÃ¡ evitada.")
        return

    melhor_estrategia = (melhor_resultado.get("estrategia", "BREAKOUT") or "BREAKOUT").replace(" ", "").upper()
    logging.info(f"ðŸ“ˆ Melhor estratÃ©gia selecionada: {melhor_estrategia} | Lucro: {lucro_chave:.2f} | Retorno: {melhor_resultado.get('retorno_pct', 0)}%")

    
    # ðŸ”¹ 8. Treinamento e sugestÃ£o do LSTM
    logging.info("ðŸ§  Treinando e prevendo com modelo LSTM...")
    previsao = "N/A"
    try:
        from estrategias.lstm_rnn import treinar_e_prever_lstm

        modelo_lstm, previsao = treinar_e_prever_lstm(df)

        if previsao in ["buy", "sell", "hold"]:
            logging.info(f"ðŸ”® LSTM sugere: {previsao.upper()}")
        else:
            logging.warning(f"âš ï¸ PrevisÃ£o LSTM invÃ¡lida: {previsao}. Continuando com cautela...")

    except Exception as e:
        logging.error(f"âŒ Erro ao executar previsÃ£o com LSTM: {e}")
        previsao = "N/A"

    # ðŸ”¹ 9. Todos os filtros aprovados â€” iniciar robÃ´
    logging.info("âœ… Todos os filtros aprovados. Sistema pronto para iniciar operaÃ§Ãµes reais.")



if __name__ == "__main__":
    import atexit
    import argparse
    import sys
    import os
    from dotenv import load_dotenv
    from utils.worker_singleton import WorkerAlreadyRunning, WorkerInstanceLock

    # ------------------------------
    # 1ï¸âƒ£ CARREGAR .ENV (o que manda)
    # ------------------------------
    # Se recebeu .env-eth / .env-btc como argumento â†’ carrega ele
    if len(sys.argv) > 1 and os.path.isfile(os.path.join(os.getcwd(), sys.argv[1])):
        env_path = os.path.join(os.getcwd(), sys.argv[1])
        if os.path.exists(env_path):
            load_dotenv(env_path)
            logging.info(f"ðŸ”— Arquivo .env carregado: {env_path}")
        else:
            logging.warning(f"âš ï¸ Arquivo {env_path} nÃ£o encontrado. Carregando .env padrÃ£o...")
            load_dotenv()
    else:
        # Sem argumento â†’ usa .env padrÃ£o
        load_dotenv()
        logging.info("ðŸ”— .env padrÃ£o carregado.")

    # ------------------------------
    # 2ï¸âƒ£ LER ATIVO DIRETO DO .ENV
    # ------------------------------
    ativo = os.getenv("ATIVOS", "").strip().upper()
    if not ativo:
        logging.warning("âš ï¸ ATIVOS nÃ£o definido no .env. Usando valor padrÃ£o BTCUSDT.")
        ativo = "BTCUSDT"

    # One long-lived worker per symbol. Different symbols may still run in
    # parallel, but a duplicate instance cannot reach the exchange/database.
    worker_lock = WorkerInstanceLock(ativo)
    try:
        worker_lock.acquire()
    except WorkerAlreadyRunning as exc:
        logging.error("[WORKER_LOCK] %s", exc)
        raise SystemExit(2)
    atexit.register(worker_lock.release)
    logging.info("[WORKER_LOCK] lock adquirido para %s em %s", ativo, worker_lock.path)

    logging.info(f"ðŸ§© Ativo definido pelo .env: {ativo}")

    # ------------------------------
    # 3ï¸âƒ£ LER MODO DE OPERAÃ‡ÃƒO
    # ------------------------------
    modo = os.getenv("MODO_VALIDACAO", "moderado").lower()
    logging.info(f"ðŸ” Modo de operaÃ§Ã£o: {modo.upper()}")

    # ------------------------------
    # 4ï¸âƒ£ Multiprocessamento (Windows)
    # ------------------------------
    try:
        set_start_method("spawn")
    except RuntimeError:
        pass

    logging.info("ðŸš€ Iniciando o sistema RobÃ´ Fluxo...")

    # ------------------------------
    # 5ï¸âƒ£ InicializaÃ§Ã£o geral
    # ------------------------------
    inicializar_banco()
    verificar_e_corrigir_flag_operacao(session, ativo)

    # ------------------------------
    # 6ï¸âƒ£ Carregar dados histÃ³ricos e modelo
    #    âš ï¸ Corrigido: usar ativo do .env e nÃ£o ATIVOS (variÃ¡vel inexistente)
    # ------------------------------
    INTERVAL = os.getenv("INTERVAL", "15")

    # O worker de produção usa candles da exchange. O carregamento legado de
    # CSV/ML não pode derrubar um ativo novo que ainda não possui arquivo
    # local, como AVAX. Mantemos o modelo opcional e deixamos o fluxo live
    # carregar os dados diretamente da exchange.
    try:
        df = carregar_dados(ativo, INTERVAL)
    except FileNotFoundError:
        df = None
        logging.warning(
            "[DATA-LEGACY] CSV local ausente para %s/%sm; seguindo com dados da exchange.",
            ativo,
            INTERVAL,
        )
    except Exception as exc:
        df = None
        logging.warning(
            "[DATA-LEGACY] carregamento ML ignorado para %s: %s; seguindo com dados da exchange.",
            ativo,
            type(exc).__name__,
        )
    modelo = carregar_modelo_com_fallback(verbose=True)

    if modelo is None and df is not None:
        logging.warning("âš ï¸ Modelo nÃ£o encontrado. Treinando novo modelo...")
        try:
            X = df.drop(columns=["target"])
            y = df["target"]
            modelo, _, _, _ = treinar_modelo(X, y, algorithm="RandomForest")
            joblib.dump(modelo, "modelo_treinado.pkl")
            logging.info("âœ… Modelo treinado e salvo com sucesso.")
        except Exception as e:
            logging.error(f"âŒ Erro ao treinar modelo: {e}")

    logging.info(f"â° Timeframe operacional definido pelo .env: {INTERVAL} minutos.")

    # ------------------------------
    # 7ï¸âƒ£ Fluxo principal (IA + backtest)
    # ------------------------------
    main()

    # ------------------------------
    # 8ï¸âƒ£ ExecuÃ§Ã£o REAL do robÃ´ (produÃ§Ã£o)
    # ------------------------------
    try:
        iniciar_robo(
            session=session,
            symbol=ativo,
            max_trades_per_day=MAX_TRADES_PER_DAY,
            loss_limit_percent=LOSS_LIMIT_PERCENT,
            interval=INTERVAL,
        )
    except KeyboardInterrupt:
        logging.info("ðŸ›‘ ExecuÃ§Ã£o interrompida pelo usuÃ¡rio.")
