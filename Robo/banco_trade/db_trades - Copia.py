import sqlite3
from datetime import datetime

DB_PATH = "trades.db"

def inicializar_banco():
    """
    Cria a tabela 'trades' se não existir.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            symbol TEXT,
            side TEXT,
            entry_price REAL,
            exit_price REAL,
            profit_loss REAL,
            strategy TEXT,
            decision_source TEXT,
            entry_score REAL,
            sentiment_score REAL,
            stop_loss REAL,
            take_profit REAL,
            trailing_stop REAL,
            volatilidade_histórica REAL,
            liquidez_mercado REAL,
            rsi_entrada REAL,
            macd_entrada REAL,
            sma_entrada REAL,
            atr_entrada REAL,
            volume_entrada REAL,
            sentimento_entrada REAL,
            ajuste_manual_stop_loss REAL,
            ajuste_manual_take_profit REAL,
            trailing_stop_inicial REAL,
            trailing_stop_final REAL,
            backtest_result REAL,
            spread REAL,
            profundidade_livro REAL,
            estrategia_utilizada TEXT,
            risco_por_operacao REAL,
            timestamp_decisao_ia TEXT
        )
    """)
    conn.commit()
    conn.close()

def salvar_trade_sqlite(trade_data):
    """
    Salva os dados de um trade na tabela 'trades'.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO trades (
            timestamp, symbol, side, entry_price, exit_price,
            profit_loss, strategy, decision_source,
            entry_score, sentiment_score, stop_loss, take_profit, trailing_stop,
            volatilidade_histórica, liquidez_mercado, rsi_entrada, macd_entrada,
            sma_entrada, atr_entrada, volume_entrada, sentimento_entrada,
            ajuste_manual_stop_loss, ajuste_manual_take_profit, trailing_stop_inicial,
            trailing_stop_final, backtest_result, spread, profundidade_livro,
            estrategia_utilizada, risco_por_operacao, timestamp_decisao_ia
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        trade_data.get("timestamp"),
        trade_data.get("symbol"),
        trade_data.get("side"),
        trade_data.get("entry_price"),
        trade_data.get("exit_price"),
        trade_data.get("profit_loss"),
        trade_data.get("strategy"),
        trade_data.get("decision_source"),
        trade_data.get("entry_score"),
        trade_data.get("sentiment_score"),
        trade_data.get("stop_loss"),
        trade_data.get("take_profit"),
        trade_data.get("trailing_stop"),
        trade_data.get("volatilidade_histórica"),
        trade_data.get("liquidez_mercado"),
        trade_data.get("rsi_entrada"),
        trade_data.get("macd_entrada"),
        trade_data.get("sma_entrada"),
        trade_data.get("atr_entrada"),
        trade_data.get("volume_entrada"),
        trade_data.get("sentimento_entrada"),
        trade_data.get("ajuste_manual_stop_loss"),
        trade_data.get("ajuste_manual_take_profit"),
        trade_data.get("trailing_stop_inicial"),
        trade_data.get("trailing_stop_final"),
        trade_data.get("backtest_result"),
        trade_data.get("spread"),
        trade_data.get("profundidade_livro"),
        trade_data.get("estrategia_utilizada"),
        trade_data.get("risco_por_operacao"),
        trade_data.get("timestamp_decisao_ia")
    ))

    conn.commit()
    conn.close()
