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
from regime.regime_detector import RegimeDetector
from utils.trade_authorization_gate import autorizar_trade



# === FLAGS DE CONTROLE DAS ESTRATÉGIAS (True = desativada no backtest) ===
DESATIVAR_REVERSAO = False
DESATIVAR_MARKETMAKING = False # True

# === DICIONÁRIO DE ESTRATÉGIAS COM CONTROLE DE ATIVAÇÃO ===
ESTRATEGIAS = {}

# Estratégia de reversão
if not DESATIVAR_REVERSAO:
    ESTRATEGIAS["MEAN_REVERSION"] = lambda df, direcao, capital_inicial: criar_backtest_reversao(
        session, BYBIT_API_KEY, BYBIT_API_SECRET)(df, direcao, capital_inicial)
else:
    ESTRATEGIAS["MEAN_REVERSION"] = None # lambda df, direcao, capital_inicial: {
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
  #  "MEAN_REVERSION": lambda df, direcao, capital_inicial: criar_backtest_reversao(session, BYBIT_API_KEY, BYBIT_API_SECRET)(df, direcao, capital_inicial),
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
        "MEAN_REVERSION": None
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
    _ = df, sentimento, capital_inicial
    logging.info("[FUNIL] Backtest completo (multi-estrat?gia) iniciado.")

    if df is None or len(df) < 50:
        logging.warning(
            f"[FUNIL] DF muito curto ({len(df) if df is not None else 0} candles) minimo recomendado: 50 candles. Cancelando backtest."
        )
        return None, [], {}

    direcao_global_raw = os.getenv("DIRECAO_GLOBAL", "").strip().lower()
    direcao_contexto = direcao_global_raw if direcao_global_raw in ["buy", "sell"] else None

    contexto_decisao = {
        "direcao": direcao_contexto,
        "confianca": 1.0,
        "placar": {"buy": 0, "sell": 0},
        "autorizado": True,
    }

    lado_permitido = contexto_decisao["direcao"]
    logging.info(
        f"[BACKTEST-GERAL] Avaliando contexto para lado aprovado: "
        f"{lado_permitido.upper() if lado_permitido else 'AUTO (BUY/SELL)'}"
    )

    try:
        regime = RegimeDetector().detectar(df)
    except Exception as e:
        logging.warning(f"[BACKTEST-GERAL] Falha ao detectar regime, usando fallback 2: {e}")
        regime = 2
    logging.info(f"[SIM][REGIME] Regime detectado para simula??o/diagn?stico: {regime}")

    candidatas = {}
    if regime in (1, 2):
        candidatas["BREAKOUT"] = lambda: executar_backtest_breakout(df.copy(), lado_permitido)
        candidatas["MEAN_REVERSION"] = lambda: criar_backtest_reversao(
            session, BYBIT_API_KEY, BYBIT_API_SECRET
        )(df.copy(), lado_permitido, capital_inicial)
    elif regime == 3 and not DESATIVAR_MARKETMAKING:
        candidatas["MARKETMAKING"] = lambda: backtest_marketmaking(
            df.copy(),
            {
                "capital_inicial": capital_inicial,
                "order_qty": 0.001,
                "spread_min": 0.05,
                "spread_multiplier": 1.2,
                "inventory_limit": 0.005,
            },
        )
    else:
        logging.info(f"[BACKTEST-GERAL] Regime {regime} sem candidatas eleg?veis (prote??o).")
        return None, {}, {}

    logging.info(f"[FUNIL] Regime {regime} -> Candidatas: {', '.join(candidatas.keys())}")
    logging.info(f"[SIM][CANDIDATAS] {', '.join(candidatas.keys())}")

    avaliacoes = {}
    sem_trade = set()

    def _extrair_metrica(res):
        if not res or not isinstance(res, dict):
            return None
        if res.get("sem_trade"):
            return None
        for chave in ["lucro_total", "lucro"]:
            if chave in res:
                return res[chave]
        return None

    for nome, func in candidatas.items():
        try:
            logging.info(f"[SIM][BACKTEST][{nome}] Iniciando avalia??o.")
            resultado = func()
        except Exception as e:
            logging.error(f"[FUNIL] Erro ao avaliar {nome}: {e}")
            continue

        metrica = _extrair_metrica(resultado)
        trades = resultado.get("trade_log") if isinstance(resultado, dict) else None
        sem_trade_flag = metrica is None or (trades is not None and len(trades) == 0)
        if sem_trade_flag:
            sem_trade.add(nome)
            logging.info(f"[FUNIL] {nome} retornou sem_trade ou m?trica ausente.")
            logging.info(f"[SIM][BACKTEST][{nome}] sem_trade ou sem m?tricas.")
            continue

        avaliacoes[nome] = {
            "resultado": resultado,
            "metrica": metrica,
            "winrate": resultado.get("winrate", resultado.get("win_rate (%)", 0)) if isinstance(resultado, dict) else 0,
        }
        logging.info(f"[FUNIL] {nome} avaliada | m?trica={metrica} | winrate={avaliacoes[nome]['winrate']}")
        logging.info(f"[SIM][BACKTEST][{nome}] m?tricas={metrica} winrate={avaliacoes[nome]['winrate']}")

    if not avaliacoes:
        logging.info("[FUNIL] Nenhuma estrat?gia v?lida; sem opera??o.")
        logging.info("[SIM][FUNIL] Nenhuma vencedora (todas sem_trade ou erro).")
        return None, {}, {}

    vencedor = max(
        avaliacoes.items(), key=lambda kv: (kv[1]["metrica"], kv[1]["winrate"])
    )[0]

    resultado_vencedor = avaliacoes[vencedor]["resultado"]
    direcao_resultado = resultado_vencedor.get("direcao", lado_permitido) if isinstance(resultado_vencedor, dict) else lado_permitido
    retorno_pct = resultado_vencedor.get("retorno_pct", resultado_vencedor.get("win_rate (%)", 0)) if isinstance(resultado_vencedor, dict) else 0

    melhor = {
        "estrategia": vencedor,
        "direcao": direcao_resultado.upper() if direcao_resultado else "AUTO",
        "autorizado": contexto_decisao["autorizado"],
        "confianca": contexto_decisao["confianca"],
        "lucro_total": avaliacoes[vencedor]["metrica"],
        "retorno_pct": retorno_pct,
        "saldo_inicial": resultado_vencedor.get("saldo_inicial", capital_inicial) if isinstance(resultado_vencedor, dict) else capital_inicial,
        "saldo_final": resultado_vencedor.get("saldo_final", resultado_vencedor.get("saldo_inicial", capital_inicial)) if isinstance(resultado_vencedor, dict) else capital_inicial,
        "trade_log": resultado_vencedor.get("trade_log", []) if isinstance(resultado_vencedor, dict) else [],
        "winrate": avaliacoes[vencedor]["winrate"],
    }

    resultados = {
        "candidatas": list(candidatas.keys()),
        "sem_trade": list(sem_trade),
        "regime": regime,
        "contexto_decisao": contexto_decisao,
    }

    relatorios_por_estrategia = {
        vencedor: resultado_vencedor.get("relatorio_completo", "") if isinstance(resultado_vencedor, dict) else ""
    }

    logging.info(f"[FUNIL] Estrat?gia vencedora: {vencedor}")
    logging.info(f"[SIM][FUNIL] Vencedora={vencedor} | lucro={avaliacoes[vencedor]['metrica']} | winrate={avaliacoes[vencedor]['winrate']}")

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
    from estrategia_entrada.estrategia_mean_reversion import executar_mean_reversion_realtime
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
    logging.info(f" Lucro:     {melhor.get('lucro_total', 0):.2f}")
    logging.info(f" Retorno:   {melhor.get('retorno_pct', 0)}%")

    # ================= TRADE AUTHORIZATION GATE =================

    estrategias_execucao = {
        "BREAKOUT": lambda: _breakout(symbol, session, session),
        "MARKETMAKING": lambda: executar_marketmaking_realtime(),
        "MEAN_REVERSION": lambda: executar_mean_reversion_realtime(
            session=session, symbol=symbol
        ),
    }

    estrategia_escolhida = melhor["estrategia"].upper()

    try:
        regime_exec = RegimeDetector().detectar(df)
    except Exception:
        regime_exec = 2

    indicadores_gate = df.iloc[-1].to_dict()
    side_gate = (melhor.get("direcao") or "").upper()
    score_gate = melhor.get("confianca", 0)

    autorizacao = autorizar_trade(
        df=df,
        regime=regime_exec,
        side=side_gate,
        score_side=score_gate,
        indicadores=indicadores_gate,
        estrategia_escolhida=estrategia_escolhida,
        modo_execucao=os.getenv("MODO_EXECUCAO", "PROD"),
    )

    if not autorizacao.get("autorizado"):
        logging.warning(f"[AUTH] Execução bloqueada: {autorizacao.get('motivo')}")
        exit()  # encerra execução com segurança

    logging.info(
        f"[AUTH] AUTORIZADO | Estratégia={estrategia_escolhida} | Side={side_gate}"
    )

    if estrategia_escolhida in estrategias_execucao:
        estrategias_execucao[estrategia_escolhida]()
    else:
        logging.warning(
            f"[AUTH] Estratégia '{estrategia_escolhida}' não encontrada."
        )
