# test_carregar_dados.py
import pandas as pd
from pybit.unified_trading import HTTP
from backtest_module import carregar_dados_historicos  # Certifique-se de que está importando do módulo correto

# Configuração da sessão da Bybit
session = HTTP(testnet=True)  # Defina como False se for ambiente de produção

# Teste a função carregar_dados_historicos()
dados = carregar_dados_historicos(session=session, symbol="BTCUSDT", interval="60", limite=200)

# Verificando os dados retornados
if dados is None:
    print("❌ ERRO: Nenhum dado foi carregado. A função retornou None.")
elif isinstance(dados, pd.DataFrame) and dados.empty:
    print("❌ ERRO: O DataFrame retornado está vazio!")
else:
    print("✅ Dados carregados com sucesso!")
    print(dados.head())  # Exibe as primeiras linhas do DataFrame
