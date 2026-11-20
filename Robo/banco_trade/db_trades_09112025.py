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
    campos = [
        "timestamp", "symbol", "side", "entry_price", "exit_price",
        "profit_loss", "strategy", "decision_source",
        "entry_score", "sentiment_score", "stop_loss", "take_profit", "trailing_stop",
        "volatilidade_histórica", "liquidez_mercado", "rsi_entrada", "macd_entrada",
        "sma_entrada", "atr_entrada", "volume_entrada", "sentimento_entrada",
        "ajuste_manual_stop_loss", "ajuste_manual_take_profit", "trailing_stop_inicial",
        "trailing_stop_final", "backtest_result", "spread", "profundidade_livro",
        "estrategia_utilizada", "risco_por_operacao", "timestamp_decisao_ia"
    ]

    valores = [trade_data.get(c) for c in campos]

    if len(valores) != len(campos):
        raise ValueError(f"❌ Número incorreto de valores: {len(valores)} fornecidos, {len(campos)} esperados.")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(f"""
        INSERT INTO trades ({', '.join(campos)})
        VALUES ({', '.join(['?'] * len(campos))})
    """, valores)

    conn.commit()
    conn.close()
