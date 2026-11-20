import os
from dotenv import load_dotenv

# Limpar cache de variáveis de ambiente relacionadas à Bybit
os.environ.pop("BYBIT_API_KEY", None)
os.environ.pop("BYBIT_API_SECRET", None)
os.environ.pop("BYBIT_TESTNET", None)

# Recarregar as variáveis do arquivo .env
load_dotenv()

# Exibir valores atualizados
print("BYBIT_API_KEY:", os.getenv("BYBIT_API_KEY"))
print("BYBIT_API_SECRET:", os.getenv("BYBIT_API_SECRET"))
print("BYBIT_TESTNET:", os.getenv("BYBIT_TESTNET"))
