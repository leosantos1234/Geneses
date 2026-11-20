from dotenv import load_dotenv
from web3 import Web3
import os

# Carregar variáveis de ambiente do arquivo .env
load_dotenv()

# Configurações seguras
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
INFURA_URL = os.getenv("INFURA_URL")
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS")

# Conectar à rede Ethereum (Sepolia)
web3 = Web3(Web3.HTTPProvider(INFURA_URL))

if web3.is_connected():
    print("Conectado à rede de testes Ethereum!")
else:
    print("Falha na conexão com a rede.")
    exit()

# Função para assinar e validar uma transação
def assinar_transacao(destinatario, valor):
    try:
        # Preparar a transação
        transacao = {
            'to': destinatario,
            'value': web3.to_wei(valor, 'ether'),
            'gas': 21000,
            'gasPrice': web3.to_wei('20', 'gwei'),
            'nonce': web3.eth.get_transaction_count(WALLET_ADDRESS),
            'chainId': 11155111  # Certifique-se de que está usando o ID correto para Sepolia
        }

        # Assinar a transação
        transacao_assinada = web3.eth.account.sign_transaction(transacao, PRIVATE_KEY)
        print("Transação assinada com sucesso!")

        # Enviar a transação assinada
        tx_hash = web3.eth.send_raw_transaction(transacao_assinada.raw_transaction)
        print(f"Transação enviada com sucesso! Hash: {web3.to_hex(tx_hash)}")
    except Exception as e:
        print(f"Erro ao assinar a transação: {str(e)}")

# Exemplo de chamada
destinatario = "0x3068357b654aa87750420dD9a95469713a133e93"
valor = 0.01
assinar_transacao(destinatario, valor)
