from pybit.unified_trading import HTTP

# Cole a chave que deseja testar:
API_KEY = "ZDMew2QP3b3BESqUp1"
API_SECRET = "6dFxZYSDsCM1CBSRtaJ3wVwOGnUM5EWsl3gy"

# Troque para True se for chave da TESTNET. False se for da MAINNET (real).
TESTNET = True

def testar_sessao(api_key, api_secret, testnet):
    try:
        print(f"\nTestando BYBIT - {'TESTNET' if testnet else 'MAINNET'}")
        session = HTTP(api_key=api_key, api_secret=api_secret, testnet=testnet)
        response = session.get_wallet_balance(accountType="UNIFIED")
        print("Resposta do saldo:", response)
        if response.get("retCode") == 0:
            print("✅ Chave válida e autenticada para este ambiente!")
        else:
            print(f"❌ Erro Bybit: {response.get('retMsg')} (retCode {response.get('retCode')})")
    except Exception as e:
        print(f"❌ Erro de conexão ou autenticação: {e}")

if __name__ == "__main__":
    # Testa o ambiente escolhido
    testar_sessao(API_KEY, API_SECRET, TESTNET)

    # Testa o outro ambiente para garantir que está tudo certo (opcional, pode remover)
    testar_sessao(API_KEY, API_SECRET, not TESTNET)
