import sqlite3
import logging
import os
from genesis_runtime import database_path

# Caminho do banco de dados
DB_PATH = str(database_path())

# Padrão correto: só crie o logger!
logger = logging.getLogger(__name__)

def inicializar_banco():
    """
    Função que inicializa o banco de dados e cria a tabela de trades se não existir.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Criação da tabela 'trades' se não existir
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER,
                symbol TEXT,
                side TEXT,
                entry_price REAL,
                exit_price REAL,
                profit_loss REAL,
                strategy TEXT,
                decision_source TEXT,
                entry_score INTEGER,
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
        logging.info("✅ Banco de dados e tabela 'trades' inicializados com sucesso!")

    except Exception as e:
        logging.error(f"❌ Erro ao inicializar o banco de dados: {e}")

def salvar_trade(trade_data):
    """
    Função que salva os dados do trade no banco de dados.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Executar inserção no banco de dados com todos os dados
        cursor.execute("""
            INSERT INTO trades (timestamp, symbol, side, entry_price, exit_price, profit_loss, strategy, 
            decision_source, entry_score, sentiment_score, stop_loss, take_profit, trailing_stop,
            volatilidade_histórica, liquidez_mercado, rsi_entrada, macd_entrada, sma_entrada, atr_entrada, 
            volume_entrada, sentimento_entrada, ajuste_manual_stop_loss, ajuste_manual_take_profit, 
            trailing_stop_inicial, trailing_stop_final, backtest_result, spread, profundidade_livro, 
            estrategia_utilizada, risco_por_operacao, timestamp_decisao_ia)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade_data["timestamp"],
            trade_data["symbol"],
            trade_data["side"],
            trade_data["entry_price"],
            trade_data["exit_price"],
            trade_data["profit_loss"],
            trade_data["strategy"],
            trade_data["decision_source"],
            trade_data["entry_score"],
            trade_data["sentiment_score"],
            trade_data["stop_loss"],
            trade_data["take_profit"],
            trade_data["trailing_stop"],
            trade_data["volatilidade_histórica"],
            trade_data["liquidez_mercado"],
            trade_data["rsi_entrada"],
            trade_data["macd_entrada"],
            trade_data["sma_entrada"],
            trade_data["atr_entrada"],
            trade_data["volume_entrada"],
            trade_data["sentimento_entrada"],
            trade_data["ajuste_manual_stop_loss"],
            trade_data["ajuste_manual_take_profit"],
            trade_data["trailing_stop_inicial"],
            trade_data["trailing_stop_final"],
            trade_data["backtest_result"],
            trade_data["spread"],
            trade_data["profundidade_livro"],
            trade_data["estrategia_utilizada"],
            trade_data["risco_por_operacao"],
            trade_data["timestamp_decisao_ia"]
        ))

        conn.commit()
        conn.close()
        logging.info("✅ Dados do trade salvos no banco de dados com sucesso!")

    except Exception as e:
        logging.error(f"❌ Erro ao salvar trade no banco: {e}")

def consultar_trades():
    """
    Função que consulta todos os trades registrados no banco de dados.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM trades")
        trades = cursor.fetchall()

        conn.close()
        logging.info("✅ Dados dos trades consultados com sucesso!")
        return trades

    except Exception as e:
        logging.error(f"❌ Erro ao consultar trades no banco: {e}")
        return None
