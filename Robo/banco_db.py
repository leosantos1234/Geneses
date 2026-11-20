# arquivo: rodar_inicializacao.py

from banco_trade.db_trades import inicializar_banco, salvar_trade_sqlite
import time

# Inicializar o banco de dados
inicializar_banco()

# Exemplo de dados do trade (com todos os campos necessários)
trade_data = {
    "timestamp": int(time.time() * 1000),
    "symbol": "BTCUSDT",
    "side": "buy",  # "buy" ou "sell"
    "entry_price": 93489.80,
    "exit_price": 93904.20,
    "profit_loss": 100.50,
    "strategy": "Breakout",
    "decision_source": "AI",
    "entry_score": 3,
    "sentiment_score": 0.75,
    "stop_loss": 93000.0,
    "take_profit": 94000.0,
    "trailing_stop": 93600.0,
    "volatilidade_histórica": 0.15,
    "liquidez_mercado": 0.02,
    "rsi_entrada": 52,
    "macd_entrada": 0.5,
    "sma_entrada": 93500.0,
    "atr_entrada": 700.0,
    "volume_entrada": 1500,
    "sentimento_entrada": 0.7,
    "ajuste_manual_stop_loss": None,
    "ajuste_manual_take_profit": None,
    "trailing_stop_inicial": 93400.0,
    "trailing_stop_final": 93600.0,
    "backtest_result": 0.02,
    "spread": 0.5,
    "profundidade_livro": 0.1,
    "estrategia_utilizada": "Breakout",
    "risco_por_operacao": 1.0,
    "timestamp_decisao_ia": "2025-04-23T15:46:00",
    "regime": "tendencia",
    "tendencia": "buy",
    "consenso": "buy",
    "consenso_score": 3.0,
    "consenso_razao": "exemplo",
    "decisao_lstm": "buy",
}

# Salvar trade no banco de dados
salvar_trade_sqlite(trade_data)

print("Banco de dados inicializado e trade salvo com sucesso!")
