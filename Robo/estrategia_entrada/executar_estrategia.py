import logging
from estrategia_entrada.estrategia_market_making import market_making

# 📌 Dicionário com todas as estratégias
estrategias = {
    "Market Making": market_making,
}

def executar_estrategia(nome_estrategia, session, df):
    """
    Executa a estratégia escolhida pelo backtesting.
    """
    logging.info(f"🚀 Executando estratégia: {nome_estrategia}")

    estrategia_func = estrategias.get(nome_estrategia)
    print(f"🔍 Tipo da estratégia selecionada: {type(estrategia_func)}")  # Deve ser <class 'function'>

    if estrategia_func and callable(estrategia_func):  # Verifica se é função
        saldo_inicial = 10000  # Defina um saldo inicial
        posicao_inicial = 0  # Defina uma posição inicial

        return estrategia_func(df, saldo_inicial, posicao_inicial)  # ✅ Agora passando os 3 argumentos corretamente
    else:
        logging.error(f"❌ Erro: Estratégia '{nome_estrategia}' não encontrada ou inválida.")
        return None  # Retorna None se a estratégia não for encontrada


if __name__ == "__main__":
    import pandas as pd
    from pybit.unified_trading import HTTP

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    # 🔌 Inicializar conexão com a Bybit
    session = HTTP(
        testnet=True,
        api_key="zJprgOUpJUQeZZaRqO",
        api_secret="lGAHa337TPEsApB5tBPe7elhSZ1nnTjCORf8",
    )

    # 🗂️ Simular dados históricos
    df = pd.DataFrame({
        "time": pd.date_range(start="2025-03-01", periods=10, freq="H"),
        "open": [91000.0] * 10,
        "high": [92000.0] * 10,
        "low": [90000.0] * 10,
        "close": [91500.0] * 10,
        "volume": [1.5] * 10,
        "turnover": [1000.0] * 10,
    })

    # 🎯 Nome da estratégia
    nome_estrategia = "Market Making"

    # 🚀 Executar estratégia
    logging.info("🔹 Iniciando execução da estratégia...")
    resultado = executar_estrategia(nome_estrategia, session, df)

    if resultado:
        logging.info(f"✅ Estratégia {nome_estrategia} executada com sucesso!")
    else:
        logging.error(f"⚠️ A estratégia {nome_estrategia} não foi executada corretamente.")
