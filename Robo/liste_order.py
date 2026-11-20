
import logging
import time
import hmac
import hashlib
import requests

# Configuração de logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# Configuração da API
API_KEY = "zJprgOUpJUQeZZaRqO"
API_SECRET = "lGAHa337TPEsApB5tBPe7elhSZ1nnTjCORf8"
URL_BASE = "https://api-testnet.bybit.com"  # Testnet (ou use "https://api.bybit.com" para produção)

# Função para gerar assinatura
def generate_signature(api_secret, params):
    param_str = "&".join([f"{key}={value}" for key, value in sorted(params.items())])
    return hmac.new(api_secret.encode(), param_str.encode(), hashlib.sha256).hexdigest()

# Função para listar ordens abertas
def list_open_orders():
    """
    Lista todas as ordens abertas na API da Bybit.
    """
    params = {
        "api_key": API_KEY,
        "timestamp": str(int(time.time() * 1000)),
        "category": "linear"
    }

    params["sign"] = generate_signature(API_SECRET, params)
    
    logging.info("🚀 Enviando requisição para listar ordens abertas...")
    
    try:
        response = requests.get(URL_BASE + "/v5/order/realtime", json=params)
        logging.info(f"🔍 Resposta da API ao listar ordens abertas: {response.text}")
        
        if response.status_code == 200:
            data = response.json()
            if "result" in data and "list" in data["result"]:
                if len(data["result"]["list"]) > 0:
                    logging.info(f"✅ Existem {len(data['result']['list'])} ordens abertas.")
                    return data["result"]["list"]
                else:
                    logging.warning("⚠️ Nenhuma ordem aberta foi encontrada.")
                    return None
            else:
                logging.error(f"❌ Resposta inesperada da API: {data}")
                return None
        else:
            logging.error(f"❌ Erro ao listar ordens abertas. Código HTTP: {response.status_code}, Resposta: {response.text}")
            return None
    except Exception as e:
        logging.error(f"❌ Erro ao fazer requisição para listar ordens abertas: {e}")
        return None

# Testando a listagem de ordens abertas
if __name__ == "__main__":
    logging.info("🚀 Testando listagem de ordens abertas...")

    open_orders = list_open_orders()

    if open_orders:
        print(f"✅ Foram encontradas {len(open_orders)} ordens abertas.")
        for order in open_orders:
            print(f"🛒 Ordem: {order}")
    else:
        print("⚠️ Nenhuma ordem aberta encontrada.")
