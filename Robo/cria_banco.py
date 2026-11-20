import sqlite3

# Caminho para o banco de dados
db_path = "C:/Users/Leonardo/Documents/robo/banco_trade/trades.db"

def criar_nova_tabela():
    try:
        # Conectar ao banco de dados
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Criar a nova tabela 'trades'
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades_novo (
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
        print("Nova tabela 'trades_novo' criada com sucesso!")

    except Exception as e:
        print(f"Erro ao criar nova tabela: {e}")

# Chama a função para criar a nova tabela
criar_nova_tabela()
