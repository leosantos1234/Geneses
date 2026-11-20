import logging
import numpy as np
import pandas as pd
import time
import requests
from datetime import datetime
import os

from compra1 import (
    session,
    get_saldo_disponivel,
    calcular_qty_maxima,
    get_min_qty,
    cancelar_todas_ordens,
    create_order,
    executar_ordem_completa
)

logger = logging.getLogger(__name__)

# ============================================================
# CARREGA CONFIG DO .env
# ============================================================

# ATIVO OPERACIONAL
SYMBOL_DEFAULT = os.getenv("ATIVOS", "BTCUSDT").strip()

# IA
API_IA_BASE = os.getenv("API_IA_URL", "http://localhost:8000")
API_IA_SPREAD = f"{API_IA_BASE}/sugerir_spread_e_trailing"



# ============================================================
#   CONFIGURAÇÃO DO MARKET MAKING PRO
# ============================================================

MM_CONFIG = {
    "inventory_target": 0.0,        # objetivo é ficar zerado
    "inventory_limit": 0.02,        # limite absoluto de inventário
    "base_order_qty": 0.001,        # tamanho mínimo de ordem
    "spread_multiplier": 0.6,       # multiplica volatilidade para achar spread
    "skew_strength": 0.25,          # 0 = neutro, 1 = muito agressivo
    "cancel_before_repost": True,
    "interval_seconds": 60,
    "api_ia_url": API_IA_SPREAD     # <-- dinâmico via .env
}

# ============================================================
#   CÁLCULOS DE MICROESTRUTURA
# ============================================================

def microvol(df):
    return df["close"].pct_change().rolling(10).std().iloc[-1] * 100

def tendencia_micro(df):
    closes = df["close"].tail(20).values
    x = np.arange(len(closes))
    m, _ = np.polyfit(x, closes, 1)
    return m  # inclinação

def calcular_spread_pro(df, preco_mid):
    vol = microvol(df)
    spread = max(0.05, vol * MM_CONFIG["spread_multiplier"])
    return round(spread, 2), vol

def calcular_skew_pro(df):
    m = tendencia_micro(df)
    if abs(m) < 0.00001:
        return 0.0
    direction = 1 if m > 0 else -1
    return direction * MM_CONFIG["skew_strength"]

# ============================================================
#         MARKET MAKING PRO — EXECUÇÃO
# ============================================================
def executar_marketmaking(df, symbol=None):
    if symbol is None:
        symbol = SYMBOL_DEFAULT

    preco_mid = float(df["close"].iloc[-1])
    spread, vol = calcular_spread_pro(df, preco_mid)
    skew = calcular_skew_pro(df)

    bid_price = round(preco_mid - spread * (1 - skew), 2)
    ask_price = round(preco_mid + spread * (1 + skew), 2)

    pos_atual = 0.0

    if pos_atual >= MM_CONFIG["inventory_limit"]:
        logger.warning("⚠️ INVENTÁRIO ALTO → bloqueando BID temporariamente.")
        executar_ask_only(symbol, ask_price)
        return ("marketmaking", preco_mid, MM_CONFIG["base_order_qty"])

    if pos_atual <= -MM_CONFIG["inventory_limit"]:
        logger.warning("⚠️ INVENTÁRIO NEGATIVO → bloqueando ASK temporariamente.")
        executar_bid_only(symbol, bid_price)
        return ("marketmaking", preco_mid, MM_CONFIG["base_order_qty"])

    qty = MM_CONFIG["base_order_qty"]

    cancelar_todas_ordens(symbol)

    logger.info(
        f"📌 MM PRO | {symbol} | MID={preco_mid} | "
        f"BID={bid_price} | ASK={ask_price} | "
        f"SPREAD={spread} | VOL={vol:.2f}% | SKEW={skew}"
    )

    try:
        executar_ordem_completa(
            session=session,
            symbol=symbol,
            side="buy",
            qty=qty,
            entry_price=bid_price,
            strategy="marketmaking"
        )
    except Exception as e:
        logger.error(f"Erro BID: {e}")

    try:
        executar_ordem_completa(
            session=session,
            symbol=symbol,
            side="sell",
            qty=qty,
            entry_price=ask_price,
            strategy="marketmaking"
        )
    except Exception as e:
        logger.error(f"Erro ASK: {e}")

    # >>>>> RETORNO ADICIONADO <<<<<
    return ("marketmaking", preco_mid, qty)


def executar_bid_only(symbol, bid_price):
    create_order(symbol, "buy", MM_CONFIG["base_order_qty"], order_type="Limit", price=bid_price)

def executar_ask_only(symbol, ask_price):
    create_order(symbol, "sell", MM_CONFIG["base_order_qty"], order_type="Limit", price=ask_price)



SYMBOL_DEFAULT = os.getenv("ATIVOS", "BTCUSDT").strip()

# ============================================================
# FUNÇÃO CORRETA PARA PEGAR DADOS DO MERCADO
# ============================================================

def obter_dados_mercado(symbol, interval="1", limit=200):
    try:
        candles = session.get_kline(
            category="linear",
            symbol=symbol,
            interval=str(interval),
            limit=limit
        )

        if candles.get("retCode") != 0:
            logger.error(f"Erro API Bybit: {candles.get('retMsg')}")
            return None

        df = pd.DataFrame(candles["result"]["list"])
        df.columns = ["timestamp", "open", "high", "low", "close", "volume", "turnover"]
        df[["open","high","low","close","volume","turnover"]] = \
            df[["open","high","low","close","volume","turnover"]].astype(float)

        return df

    except Exception as e:
        logger.error(f"Erro ao obter dados de mercado: {e}")
        return None


# ============================================================
# LOOP PRINCIPAL — MARKET MAKING PRO
# ============================================================

def monitorar_market_making(symbol=None, interval=None):
    if symbol is None:
        symbol = SYMBOL_DEFAULT

    if interval is None:
        interval = os.getenv("INTERVAL", "1")

    logger.info(f"🚀 Iniciando Market Making PRO para {symbol} | Timeframe {interval}")

    while True:
        try:
            df = obter_dados_mercado(symbol, interval, limit=200)

            if df is None or len(df) < 50:
                logger.warning("⚠️ Sem dados suficientes, tentando novamente...")
                time.sleep(5)
                continue

            executar_marketmaking(df, symbol=symbol)

            time.sleep(MM_CONFIG["interval_seconds"])

        except Exception as e:
            logger.error(f"❌ Erro no loop MM PRO: {e}", exc_info=True)
            time.sleep(5)



# ============================================================
#                   BACKTEST REALISTA
# ============================================================
def criar_backtest_marketmaking(df, direcao=None, saldo_inicial=1000):
    if not isinstance(df, pd.DataFrame):
        raise ValueError("df precisa ser um DataFrame real!")

    df = df.reset_index(drop=True)

    capital = saldo_inicial
    pos = 0.0
    saldo = capital

    trade_log = []

    for i in range(50, len(df)):
        preco = df["close"].iloc[i]

        # Protege cálculo do spread
        try:
            spread_info = calcular_spread_pro(df.iloc[i-50:i], preco)
            spread = float(spread_info[0])
        except Exception as e:
            logger.error(f"Erro no cálculo do spread no candle {i}: {e}")
            continue

        # Preços Bid/Ask simulados
        bid = preco - spread / 2
        ask = preco + spread / 2

        low = df["low"].iloc[i]
        high = df["high"].iloc[i]

        qty = MM_CONFIG["base_order_qty"]

        # Execução simulada
        if low <= bid:
            pos += qty
            saldo -= qty * bid
            trade_log.append(("BUY", bid, pos))

        if high >= ask:
            pos -= qty
            saldo += qty * ask
            trade_log.append(("SELL", ask, pos))

        # Zera inventário excedente (controle de risco)
        if pos > MM_CONFIG["inventory_limit"]:
            saldo += pos * preco
            pos = 0

    saldo_final = saldo

    resultado = {
        "saldo_final": saldo_final,
        "lucro": saldo_final - saldo_inicial,
        "trade_log": trade_log,
        "trades": len(trade_log)   # adiciona quantidade de trades
    }

    if resultado["trades"] == 0:
        resultado["retorno_pct"] = 0.0
        resultado["lucro"] = 0.0
        logger.warning("ℹ️ MARKETMAKING: Nenhum trade executado no período. Resultado neutro.")
        return resultado


# ==========================================================
# 🚀 DISPARO DA ESTRATÉGIA (EXECUÇÃO REAL)
# ==========================================================
if __name__ == "__main__":
    logging.info("🚀 Iniciando Market Making Bot...")
    monitorar_market_making()
