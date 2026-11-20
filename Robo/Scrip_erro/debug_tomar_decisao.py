import requests
import logging

# Configuração do log
logging.basicConfig(
    filename="debug_ordem.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

def testar_ordem(symbol="BTCUSDT", side="buy", preco_entrada=104500.0):
    try:
        url = f"http://localhost:8000/tomar_decisao"
        params = {
            "symbol": symbol,
            "side": side,
            "preco_entrada": preco_entrada
        }

        resposta = requests.post(url, params=params, timeout=10)
        resposta.raise_for_status()
        dados = resposta.json()

        if dados.get("status") == "ordem_enviada":
            logging.info(f"✅ Ordem enviada com sucesso: {dados}")
        elif dados.get("status") == "erro":
            logging.warning(f"⚠️ Erro ao enviar ordem: {dados.get('detalhe')}")
        else:
            logging.warning(f"❓ Resposta inesperada: {dados}")

        return dados

    except Exception as e:
        logging.error(f"❌ Falha ao executar teste de ordem: {e}")
        return {"erro": str(e)}

if __name__ == "__main__":
    resultado = testar_ordem()
    print("Resultado:", resultado)
