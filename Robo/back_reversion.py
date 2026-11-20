import pandas as pd
from estrategias.estrategia_mean_reversion_pro import executar_mean_reversion_pro
from utils.indicadores import calculate_indicators
from utils.data_loader import carregar_dados_mercado  # se tiver util similar no projeto

# 1. Carrega dados históricos (candles)
df = pd.read_csv("dados/BTCUSDT_1h.csv")  # ou usa get_historical_data da Bybit
df = calculate_indicators(df)

# 2. Executa o backtest
resultado = executar_mean_reversion_pro(df, modo="backtest", symbol="BTCUSDT", saldo=1000)

print(resultado)
