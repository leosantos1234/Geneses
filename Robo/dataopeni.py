import time
import hashlib
import requests
import urllib.parse

# Função para gerar a assinatura
def gerar_assinatura(params, api_secret):
    # Ordena os parâmetros em ordem alfabética
    sorted_params = sorted(params.items())
    # Concatena os parâmetros em uma string query
    query_string = urllib.parse.urlencode(sorted_params)
    # Adiciona o api_secret ao final da string query
    query_string = query_string + f'&api_secret={api_secret}'
    # Gera a assinatura usando SHA256
    signature = hashlib.sha256(query_string.encode('utf-8')).hexdigest()
    return signature

# URL para o endpoint de saldo da carteira (testnet ou produção)
url = 'https://api-testnet.bybit.com/v2/private/wallet/balance'  # Usando o Testnet, substitua por produção se necessário

# Defina os parâmetros necessários
api_key = 'zJprgOUpJUQeZZaRqO'  # Substitua pela sua chave de API
api_secret = 'lGAHa337TPEsApB5tBPe7elhSZ1nnTjCORf8'  # Substitua pelo seu segredo da API

# Obtenha o timestamp atual em milissegundos
api_timestamp = str(int(time.time() * 1000))

# Parâmetros da requisição
params = {
    'api_key': api_key,
    'api_timestamp': api_timestamp,
}

# Gere a assinatura
api_signature = gerar_assinatura(params, api_secret)

# Adicione a assinatura aos parâmetros
params['api_signature'] = api_signature

# Enviar a requisição GET
response = requests.get(url, params=params)

# Verifique o status da resposta
if response.status_code == 200:
    print(response.json())  # Se a resposta for 200 OK, exibe o conteúdo
else:
    print(f"Erro ao conectar: {response.status_code} - {response.text}")
