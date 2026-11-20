import logging
logger = logging.getLogger(__name__)
import pandas as pd
import matplotlib.pyplot as plt

def _log_temporal_order(df: pd.DataFrame, origem: str) -> None:
    """Log temporario para auditar ordem temporal dos candles."""
    try:
        if df is None or df.empty or "time" not in df.columns:
            logging.info(f"[TEMP-CANDLE-ORDER] origem={origem} sem_dados")
            return
        head_ts = [str(x) for x in df["time"].head(3).tolist()]
        tail_ts = [str(x) for x in df["time"].tail(3).tolist()]
        inc = bool(df["time"].is_monotonic_increasing)
        dec = bool(df["time"].is_monotonic_decreasing)
        logging.info(
            "[TEMP-CANDLE-ORDER] origem=%s head3=%s tail3=%s monotonic_inc=%s monotonic_dec=%s",
            origem,
            head_ts,
            tail_ts,
            inc,
            dec,
        )
    except Exception as e:
        logging.warning(f"[TEMP-CANDLE-ORDER] falha_origem={origem}: {e}")


# backtesting.py
def carregar_dados_historicos(session, symbol, interval, limite=200):
    """
    Carrega dados históricos da API da Bybit.
    """
    try:
        response = session.get_kline(
            category="spot",  # Define se é Spot, Futures, etc.
            symbol=symbol,
            interval=interval,
            limit=limite
        )

        if response.get("retCode") != 0:
            logging.error(f"Erro ao carregar dados históricos: {response.get('retMsg')} "
                          f"(ErrCode: {response.get('retCode')})")
            return None

        # Criar DataFrame com os dados retornados
        df = pd.DataFrame(response["result"]["list"], columns=["time", "open", "high", "low", "close", "volume", "turnover"])

        # Converter timestamp para datetime
        df["time"] = pd.to_datetime(pd.to_numeric(df["time"]), unit="ms")
        df = df.sort_values("time").reset_index(drop=True)
        if not df["time"].is_monotonic_increasing:
            logging.warning("Candles fora de ordem detectados. Corrigindo automaticamente.")
            df = df.sort_values("time").reset_index(drop=True)
        _log_temporal_order(df, "backtest_module.carregar_dados_historicos")

        return df

    except Exception as e:
        logging.error(f"Erro ao carregar dados históricos: {e}")
        return None


def executar_backtesting(dados, estrategia_func, saldo_inicial=10000):
    """
    Executa o backtesting em dados históricos com base em uma estratégia definida.

    Args:
        dados (pd.DataFrame): Dados históricos processados.
        estrategia_func (func): Função que define a estratégia de entrada/saída.
        saldo_inicial (float): Saldo inicial para o backtesting.

    Returns:
        dict: Resultados do backtesting incluindo saldo final, lucro, drawdown, etc.
    """
    logging.info(f"📊 Iniciando Backtest para a estratégia: {estrategia_func.__name__} com saldo inicial de {saldo_inicial}...")

    saldo = saldo_inicial
    posicao = 0
    historico_operacoes = []

    for index, linha in dados.iterrows():
        try:
            sinal = estrategia_func(linha)  # **Chama a estratégia dinâmica**
        except Exception as e:
            logging.error(f"⚠️ Erro ao executar estratégia {estrategia_func.__name__}: {e}")
            continue

        if sinal == "buy" and saldo > 0:
            posicao += saldo / linha["close"]
            historico_operacoes.append({"time": linha["time"], "tipo": "buy", "preco": linha["close"], "saldo": saldo})
            saldo = 0
            logging.info(f"✅ COMPRA em {linha['time']} | Preço: {linha['close']:.2f}")

        elif sinal == "sell" and posicao > 0:
            saldo += posicao * linha["close"]
            historico_operacoes.append({"time": linha["time"], "tipo": "sell", "preco": linha["close"], "saldo": saldo})
            posicao = 0
            logging.info(f"✅ VENDA em {linha['time']} | Preço: {linha['close']:.2f}")

    saldo_final = saldo + (posicao * dados.iloc[-1]["close"])
    lucro = saldo_final - saldo_inicial
    retorno = (lucro / saldo_inicial) * 100

    saldos = [op["saldo"] for op in historico_operacoes] + [saldo_final]
    drawdown_maximo = calcular_drawdown(saldos)

    logging.info(f"📈 Backtest Finalizado! Saldo final: R${saldo_final:.2f} | Lucro: R${lucro:.2f} | Retorno: {retorno:.2f}% | Drawdown Máximo: {drawdown_maximo:.2f}%")
    
    return {
        "saldo_inicial": saldo_inicial,
        "saldo_final": saldo_final,
        "lucro": lucro,
        "retorno": retorno,
        "drawdown_maximo": drawdown_maximo,
        "historico_operacoes": historico_operacoes
    }



def calcular_drawdown(saldos):
    """
    Calcula o drawdown máximo.

    Args:
        saldos (list): Lista de saldos ao longo do tempo.

    Returns:
        float: Drawdown máximo.
    """
    pico = saldos[0]
    drawdown_maximo = 0

    for saldo in saldos:
        if saldo > pico:
            pico = saldo
        drawdown = (pico - saldo) / pico
        drawdown_maximo = max(drawdown_maximo, drawdown)

    return drawdown_maximo * 100


def gerar_relatorio(resultados, dados):
    """
    Gera relatórios e visualizações dos resultados do backtesting.

    Args:
        resultados (dict): Resultados do backtesting.
        dados (pd.DataFrame): Dados históricos.
    """
    print("=== RELATÓRIO DE BACKTESTING ===")
    print(f"Saldo Inicial: ${resultados['saldo_inicial']:.2f}")
    print(f"Saldo Final: ${resultados['saldo_final']:.2f}")
    print(f"Lucro: ${resultados['lucro']:.2f}")
    print(f"Retorno: {resultados['retorno']:.2f}%")
    print(f"Drawdown Máximo: {resultados['drawdown_maximo']:.2f}%")

    plt.figure(figsize=(10, 6))
    plt.plot(dados["time"], dados["close"], label="Preço de Fechamento")
    plt.title("Histórico de Preços")
    plt.xlabel("Tempo")
    plt.ylabel("Preço")
    plt.legend()
    plt.grid(True)
    plt.savefig("relatorio_backtest.png")
    plt.close()


def estrategia_cruzamento_medias(linha):
    """
    Estratégia de cruzamento de médias móveis:
    - Compra quando a SMA de 10 períodos cruza acima da SMA de 50 períodos.
    - Venda quando a SMA de 10 períodos cruza abaixo da SMA de 50 períodos.

    Args:
        linha (pd.Series): Linha atual de dados.

    Returns:
        str: "buy", "sell" ou None.
    """
    if linha["SMA_10"] > linha["SMA_50"]:
        return "buy"
    elif linha["SMA_10"] < linha["SMA_50"]:
        return "sell"
    return None
