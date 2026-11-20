from config import BYBIT_API_KEY, BYBIT_API_SECRET, TESTNET
from pybit.unified_trading import HTTP

print(f"🔐 API KEY: {BYBIT_API_KEY[:6]}... (oculto)")
print(f"🌐 Ambiente: {'TESTNET' if TESTNET else 'MAINNET'}")

session = HTTP(
    api_key=BYBIT_API_KEY,
    api_secret=BYBIT_API_SECRET,
    testnet=TESTNET
)

try:
    resposta = session.get_wallet_balance(accountType="UNIFIED")
    saldo = resposta["result"]["list"][0]["totalWalletBalance"]
    print(f"✅ Conexão OK. Saldo: {saldo} USDT")
except Exception as e:
    print(f"❌ Erro na conexão: {e}")
