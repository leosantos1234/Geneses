import sqlite3

DB_PATH = "C:/Users/Leonardo/Documents/robo/banco_trade/trades.db"

def corrigir_banco():
    """
    Corrige a tabela 'trades' para adicionar as colunas faltantes.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Renomeia a tabela antiga
        cursor.execute("ALTER TABLE trades RENAME TO trades_old")
        
        # Cria a nova tabela com a estrutura corrigida
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

        # Copiar os dados da tabela antiga para a nova tabela
        cursor.execute("""
            INSERT INTO trades (
                timestamp, symbol, side, entry_price, exit_price, profit_loss, strategy,
                decision_source, entry_score, sentiment_score, stop_loss, take_profit, trailing_stop,
                volatilidade_histórica, liquidez_mercado, rsi_entrada, macd_entrada, sma_entrada, atr_entrada,
                volume_entrada, sentimento_entrada, ajuste_manual_stop_loss, ajuste_manual_take_profit,
                trailing_stop_inicial, trailing_stop_final, backtest_result, spread, profundidade_livro,
                estrategia_utilizada, risco_por_operacao, timestamp_decisao_ia
            )
            SELECT 
                timestamp, symbol, side, entry_price, exit_price, profit_loss, strategy,
                decision_source, entry_score, sentiment_score, stop_loss, take_profit, trailing_stop,
                volatilidade_histórica, liquidez_mercado, rsi_entrada, macd_entrada, sma_entrada, atr_entrada,
                volume_entrada, sentimento_entrada, ajuste_manual_stop_loss, ajuste_manual_take_profit,
                trailing_stop_inicial, trailing_stop_final, backtest_result, spread, profundidade_livro,
                estrategia_utilizada, risco_por_operacao, timestamp_decisao_ia
            FROM trades_old
        """)

        # Excluir a tabela antiga
        cursor.execute("DROP TABLE trades_old")

        # Confirma as alterações e fecha a conexão
        conn.commit()
        conn.close()
        print("Banco de dados corrigido com sucesso!")
    
    except sqlite3.Error as e:
        print(f"Erro ao corrigir o banco de dados: {e}")

# Rodar a função para corrigir a tabela
corrigir_banco()
