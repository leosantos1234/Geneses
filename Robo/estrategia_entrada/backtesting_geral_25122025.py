import os
print("BACKTESTING_GERAL - LOADED FROM:", __file__)
import sys

from utils.logger_config import configurar_logger_global
configurar_logger_global("backtesting_completo.log", modo="debug")

import logging
import pandas as pd

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ["MODO_RELAXADO"] = "true"

# Agora seus outros imports, funções e lógica.


# Importações dos módulos internos
from estrategia_entrada.estrategia_breakout_filtros import executar_backtest_breakout, confirmar_entrada_com_criterios
from estrategia_entrada.estrategia_mean_reversion import criar_backtest_reversao
from estrategia_entrada.Market_Making._marketmaking_backtest import backtest_marketmaking
from estrategia_entrada.Market_Making._marketmaking_validacao import validar_marketmaking
from estrategias.analise_sentimento import analisar_sentimento
from utils.indicadores import calculate_indicators, detectar_tendencia, calcular_volatilidade
from sessao_bybit import session
from config import BYBIT_API_KEY, BYBIT_API_SECRET
import requests



# === FLAGS DE CONTROLE DAS ESTRATÉGIAS (True = desativada no backtest) ===
DESATIVAR_REVERSAO = True
DESATIVAR_MARKETMAKING = True # False

# === DICIONÁRIO DE ESTRATÉGIAS COM CONTROLE DE ATIVAÇÃO ===
ESTRATEGIAS = {}

# Estratégia de reversão
if not DESATIVAR_REVERSAO:
    ESTRATEGIAS["reversao"] = lambda df, direcao, capital_inicial: criar_backtest_reversao(
        session, BYBIT_API_KEY, BYBIT_API_SECRET)(df, direcao, capital_inicial)
else:
    ESTRATEGIAS["reversao"] = None # lambda df, direcao, capital_inicial: {
        #"lucro": -9999,
        #"mensagem": "❌ Estratégia REVERSÃO desativada via flag"
    #}

# Estratégia de market making
if not DESATIVAR_MARKETMAKING:
    ESTRATEGIAS["marketmaking"] = lambda df, direcao, capital_inicial: backtest_marketmaking(
        df.copy(),
        {
            "capital_inicial": capital_inicial,
            "order_qty": 0.001,
            "spread_min": 0.05,
            "spread_multiplier": 1.2,
            "inventory_limit": 0.005
        }
    )
else:
    ESTRATEGIAS["marketmaking"] = None #lambda df, direcao, capital_inicial: {
        #"lucro": -9999,
        #"mensagem": "❌ Estratégia MARKETMAKING desativada via flag"
    #}

# === LIMPA ESTRATÉGIAS DESATIVADAS ===
ESTRATEGIAS = {k: v for k, v in ESTRATEGIAS.items() if v is not None}

logging.info(f"📊 Estratégias disponíveis para backtest: {list(ESTRATEGIAS.keys())}")

# Estratégia de breakout (sempre ativa)
ESTRATEGIAS["breakout"] = lambda df, direcao, capital_inicial: executar_backtest_breakout(
    df.copy(),
    lado_permitido=direcao
)


#ESTRATEGIAS = {
 #   "breakout": lambda df, direcao, capital_inicial: executar_backtest_breakout(df),
  #  "reversao": lambda df, direcao, capital_inicial: criar_backtest_reversao(session, BYBIT_API_KEY, BYBIT_API_SECRET)(df, direcao, capital_inicial),
   # "marketmaking": lambda df, direcao, capital_inicial: criar_backtest_marketmaking(df, saldo_inicial=capital_inicial)

#}




# Pesos atribuídos às fontes de decisão para score final
PESOS = {
    "lstm": 30,
    "openai": 30,
    "indicadores": 20,
    "volatilidade": 10,
    "tendencia": 10
}



def carregar_dados_historicos(session, symbol, interval="60", limite=100):
    try:
        response = session.get_kline(
            category="linear",  # Corrigido para Futuros PERPÉTUOS
            symbol=symbol,
            interval=interval,
            limit=limite
        )

        if response.get("retCode") != 0:
            logging.error(f"Erro na API Bybit: {response.get('retMsg')} (retCode {response.get('retCode')})")
            return None

        df = pd.DataFrame(response["result"]["list"], columns=["time", "open", "high", "low", "close", "volume", "turnover"])
        df[["open", "high", "low", "close", "volume", "turnover"]] = df[["open", "high", "low", "close", "volume", "turnover"]].astype(float)

        #logging.info(f"{len(df)} candles carregados com sucesso do mercado PERPÉTUO!")
        return df

    except Exception as e:
        logging.error(f"Erro ao carregar dados históricos PERPÉTUO: {e}")
        return None


def obter_previsao_next_candle(df_lstm):
    url = os.getenv("LSTM_API_URL")

    payload = {"data": df_lstm.values.tolist()}

    try:
        r = requests.post(url, json=payload, timeout=3)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception as e:
        logging.error(f"[LSTM SERVICE] Erro: {e}")
        return None




def imprimir_ranking_estrategias(resultados):
    """
    resultados = {
        "marketmaking": {"lucro_pct": 9.15, "lucro_abs": 91.49},
        "breakout": {"lucro_pct": -8.90, "lucro_abs": -8908.50},
        "reversao": None
    }
    """

    print("\n\n🏆 RESULTADO DA DISPUTA ENTRE ESTRATÉGIAS")
    print("----------------------------------------")

    # Ordenar estratégias ativas por lucro percentual
    ativos = {
        k: v for k, v in resultados.items()
        if v is not None and v.get("lucro_pct") is not None
    }

    if not ativos:
        print("⚠ Nenhuma estratégia ativa disponível para ranking.")
        return None

    ranking = sorted(ativos.items(), key=lambda x: x[1]["lucro_pct"], reverse=True)

    posicao = 1
    for nome, dados in ranking:
        print(f"{posicao}º lugar: {nome.upper()}")
        print(f"- Lucro (%): {dados['lucro_pct']:+.2f}%")
        print(f"- Lucro (R$): {dados['lucro_abs']:+.2f}")
        print("")
        posicao += 1

    # Estratégias desativadas / sem resultado
    desativadas = [k for k, v in resultados.items() if v is None]
    for nome in desativadas:
        print(f"{posicao}º lugar: {nome.upper()} (desativada)")
        posicao += 1

    print("----------------------------------------")

    # Escolher vencedora
    if ranking:
        vencedora = ranking[0][0]
        print(f"🎯 Estratégia escolhida para operar: {vencedora.upper()}\n")
        return vencedora
    else:
        print("⚠ Nenhuma estratégia ativa disponível.")
        return None






def calcular_vencedora_do_ranking(resultados_ranking):
    """Retorna apenas o nome da estratégia vencedora."""
    melhor = None
    melhor_pct = -999999

    for nome, dados in resultados_ranking.items():
        if dados is None:
            continue

        pct = dados.get("lucro_pct", None)
        if pct is not None and pct > melhor_pct:
            melhor_pct = pct
            melhor = nome

    return melhor




def salvar_direcao_global(direcao):
    """Salva a direção global (buy/sell) no arquivo .env ativo."""
    env_path = os.getenv("ENV_FILE", ".env")  # você decide qual ENV está usando

    try:
        if not os.path.exists(env_path):
            print(f"⚠️ ENV '{env_path}' não encontrado. Criando...")
            with open(env_path, "w") as f:
                f.write(f"DIRECAO_GLOBAL={direcao}\n")
            return

        nova = []
        encontrado = False

        with open(env_path, "r") as f:
            for linha in f.readlines():
                if linha.startswith("DIRECAO_GLOBAL="):
                    nova.append(f"DIRECAO_GLOBAL={direcao}\n")
                    encontrado = True
                else:
                    nova.append(linha)

        if not encontrado:
            nova.append(f"DIRECAO_GLOBAL={direcao}\n")

        with open(env_path, "w") as f:
            f.writelines(nova)

        print(f"🔄 DIRECAO_GLOBAL atualizada para: {direcao.upper()}")

    except Exception as e:
        print(f"❌ Erro ao salvar DIRECAO_GLOBAL: {e}")




def filtro_lstm_backtest(
    lstm_result,
    side,
    atr,
    conf_min=0.002,
    atr_mult_min=0.3
):
    if not lstm_result:
        return False

    delta = lstm_result["delta"]
    confidence = lstm_result["confidence"]
    direction = lstm_result["direction"]

    if confidence < conf_min:
        return False

    if abs(delta) < atr_mult_min * atr:
        return False

    if side == "buy" and direction != "up":
        return False

    if side == "sell" and direction != "down":
        return False

    return True



def executar_backtest_completo(df, sentimento, capital_inicial=1000):
    _ = df, sentimento, capital_inicial  # contrato explícito (lint + auditoria)
    logging.info("🚀 Entrando na versão aprimorada da função de backtest completo.")


    # ============================================================
    # Proteção GLOBAL: impede backtest com DF curto
    # ============================================================
    if df is None or len(df) < 50:
        logging.warning(
            f"⚠️ DF muito curto ({len(df) if df is not None else 0} candles) "
            f"— mínimo recomendado: 50 candles. Cancelando backtest."
        )
        return None, [], {}

    # Contexto explícito de decisão: evita dependência de variáveis implícitas
    direcao_global_raw = os.getenv("DIRECAO_GLOBAL", "").strip().lower()
    if direcao_global_raw in ["buy", "sell"]:
        direcao_contexto = direcao_global_raw
    else:
        direcao_contexto = None  # modo auto: deixa a direção ser definida pelo breakout

    contexto_decisao = {
        "direcao": direcao_contexto,
        "confianca": 1.0,
        "placar": {"buy": 0, "sell": 0},
        "autorizado": True,  # preparado para modos futuros
    }

    melhor = None
    # Se direção está None (auto), não filtra; Breakout define lado. Caso contrário, filtra pelo lado definido.
    lado_permitido = contexto_decisao["direcao"]
    logging.info(
        f"[BACKTEST-GERAL] Avaliando contexto para lado aprovado: "
        f"{lado_permitido.upper() if lado_permitido else 'AUTO (BUY/SELL)'}"
    )

    # Executa backtest do Breakout apenas para m?tricas/contexto (n?o para veto)
    try:
        breakout_metrics = executar_backtest_breakout(df.copy(), lado_permitido)
    except Exception as e:
        logging.error(f"[BACKTEST-GERAL] Falha ao executar backtest do Breakout: {e}")
        breakout_metrics = {}

    lucro_estimado = breakout_metrics.get("lucro_total", breakout_metrics.get("lucro", 0))
    retorno_pct = breakout_metrics.get("retorno_pct", breakout_metrics.get("win_rate (%)", 0))

    direcao_resultado = breakout_metrics.get("direcao", lado_permitido) or lado_permitido
    melhor = {
        "estrategia": "BREAKOUT",
        "direcao": direcao_resultado.upper() if direcao_resultado else "AUTO",
        "autorizado": contexto_decisao["autorizado"],
        "confianca": contexto_decisao["confianca"],
        "lucro_total": max(lucro_estimado, 0),
        "retorno_pct": retorno_pct,
        "saldo_inicial": breakout_metrics.get("saldo_inicial", capital_inicial),
        "saldo_final": breakout_metrics.get("saldo_final", breakout_metrics.get("saldo_inicial", capital_inicial)),
        "trade_log": breakout_metrics.get("trade_log", []),
        "winrate": breakout_metrics.get("winrate", breakout_metrics.get("win_rate (%)", 0)),
    }

    resultados = {
        "buy": {"score": contexto_decisao["placar"]["buy"]},
        "sell": {"score": contexto_decisao["placar"]["sell"]},
        "confianca": contexto_decisao["confianca"],
        "autorizado": contexto_decisao["autorizado"],
        "contexto_decisao": contexto_decisao,
    }

    relatorios_por_estrategia = {
        "BREAKOUT": breakout_metrics.get("relatorio_completo", "")
    }

    return melhor, resultados, relatorios_por_estrategia







if __name__ == "__main__":
    import time
    import logging
    from datetime import datetime
    from utils.indicadores import calculate_indicators
    from backtest_module import carregar_dados_historicos

     # ✅ Estratégias para execução real com nomes corretos
    from estrategia_entrada.estrategia_breakout_filtros import _breakout
    from estrategia_entrada.Market_Making._marketmaking_execucao import executar_marketmaking_realtime
    from estrategia_entrada.estrategia_mean_reversion import monitorar_ordens
    from utils.parse_env import get_ativos_env
    import os
    symbol = get_ativos_env()[0]  # Sempre pega o primeiro da lista de ativos
    interval = os.getenv("INTERVAL", "1")

    #symbol = "BTCUSDT"
    #interval = "1"

    df = carregar_dados_historicos(session, symbol, interval)

    if df is None or df.empty:
        logging.error("❌ Erro ao carregar dados históricos.")
        exit()

    # ✅ Conversão segura de timestamp
    def parse_timestamp_safe(valor):
        if isinstance(valor, datetime):
            return valor
        if isinstance(valor, str) and "T" in valor:
            return datetime.fromisoformat(valor)
        if isinstance(valor, (int, float)) or (isinstance(valor, str) and valor.isdigit()):
            return datetime.utcfromtimestamp(int(valor) / 1000)
        raise ValueError(f"Formato inválido para timestamp: {valor}")

    df["time"] = df["time"].apply(parse_timestamp_safe)

    df_ind = calculate_indicators(df.copy())
    if df_ind is None or not isinstance(df_ind, pd.DataFrame):
        logging.error("❌ Indicadores inválidos.")
        exit()
    df = df_ind

    vol = calcular_volatilidade(df.copy())
    if vol is None or not isinstance(vol, (int, float)) or pd.isna(vol) or vol <= 0:
        logging.error(f"❌ Volatilidade inválida: {vol}")
        exit()
    CAPITAL_INICIAL = 10000  # ou qualquer valor desejado
    sentimento = "neutro"  # fallback seguro

    melhor, resultados, relatorios = executar_backtest_completo(
        df,
        sentimento=sentimento,
        capital_inicial=CAPITAL_INICIAL
    )




    if not melhor or not isinstance(melhor, dict):
        logging.warning("⚠️ Nenhuma estratégia foi aprovada ou houve empate no backtest.")
        #logging.warning(f"📊 Relatório completo do backtest: {relatorio}")
        logging.warning(f"⚠️ Resultado do backtest inválido ou inesperado: {melhor}")
        time.sleep(5)
        exit()

    # ✅ Exibir resultado da melhor estratégia
    logging.info("\n📊 Resultado Final:")
    logging.info(f" Estratégia: {melhor['estrategia']}")
    logging.info(f" Direção:   {melhor['direcao']}")
    logging.info(f" Lucro:     {melhor['lucro']:.2f}")
    logging.info(f" Retorno:   {melhor['retorno_pct']}%")

    # ✅ Executar a estratégia vencedora
    estrategias_execucao = {
        "BREAKOUT": lambda: _breakout(symbol, session, session),
        "marketmaking": lambda: executar_marketmaking_realtime(),
        "REVERSAO": lambda: monitorar_ordens()
    }

    estrategia_escolhida = melhor["estrategia"].upper()

    if estrategia_escolhida in estrategias_execucao:
        logging.info(f"🚀 Executando a estratégia vencedora: {estrategia_escolhida}")
        estrategias_execucao[estrategia_escolhida]()
    else:
        logging.warning(f"⚠️ Estratégia vencedora '{estrategia_escolhida}' não encontrada no dicionário de execução.")


