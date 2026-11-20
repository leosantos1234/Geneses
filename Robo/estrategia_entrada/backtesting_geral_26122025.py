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
from estrategia_entrada.estrategia_breakout_filtros import (
    executar_backtest_breakout,
    confirmar_entrada_com_criterios,
    calculate_breakout_levels,
    calculate_stop_take_trailing,
    confirm_breakout,
    detect_breakout,
)
from estrategia_entrada.estrategia_mean_reversion import criar_backtest_reversao
from estrategia_entrada.Market_Making._marketmaking_backtest import backtest_marketmaking
from estrategia_entrada.Market_Making._marketmaking_validacao import validar_marketmaking
from estrategias.analise_sentimento import analisar_sentimento
from utils.indicadores import calculate_indicators, detectar_tendencia, calcular_volatilidade
from sessao_bybit import session
from config import BYBIT_API_KEY, BYBIT_API_SECRET
import requests
import math
from utils.entrada_breakout_confirmada import calcular_preco_confirmacao
from utils.risco import calcular_rrr



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


_executar_backtest_breakout_original = executar_backtest_breakout


def _candle_seconds_from_interval(intervalo: str) -> int:
    mapa = {
        "1": 60,
        "3": 180,
        "5": 300,
        "15": 900,
        "30": 1800,
        "60": 3600,
        "240": 14400,
        "720": 43200,
        "1440": 86400,
    }
    try:
        if intervalo in mapa:
            return mapa[intervalo]
        valor = float(intervalo)
        return max(1, int(valor * 60))
    except Exception:
        return 60


def executar_backtest_breakout(df_original, lado_permitido=None):
    """
    Backtest do breakout ajustado para simular STOP LIMIT com offset e timeout de confirmação,
    alinhado ao fluxo de produção.
    """
    from copy import deepcopy

    df = deepcopy(df_original).reset_index(drop=True)
    df = calculate_breakout_levels(df)
    df = calculate_indicators(df)

    for col in ["open", "high", "low", "close", "ATR", "TrueRange"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    saldo = 10000.0
    saldo_inicial = saldo
    trades = []
    wins = 0
    losses = 0
    sinais_detectados = 0
    trades_confirmados = 0
    trades_cancelados = 0

    timeout_segundos = int(os.getenv("TIMEOUT_ENTRADA_SEGUNDOS", 180))
    intervalo_env = os.getenv("INTERVAL", "1")
    candle_seconds = _candle_seconds_from_interval(intervalo_env)
    timeout_candles = max(1, math.ceil(timeout_segundos / candle_seconds))

    fator_confirmacao = float(os.getenv("FATOR_CONFIRMACAO_TR", os.getenv("OFFSET_CONFIRMACAO_ATR", 0.12)))

    for i in range(20, len(df) - 1):
        df_slice = df.iloc[: i + 1].copy()

        breakout = detect_breakout(df_slice)
        if not breakout:
            continue

        if not confirm_breakout(df_slice):
            continue

        sinais_detectados += 1
        side = "buy" if breakout == "bullish" else "sell"
        if lado_permitido and side != lado_permitido:
            continue

        if not confirmar_entrada_com_criterios(df_slice, side, is_backtest=True):
            continue

        atr = float(df_slice["ATR"].iloc[-1]) if "ATR" in df_slice.columns else 0.0
        tr = float(df_slice["TrueRange"].iloc[-1]) if "TrueRange" in df_slice.columns else atr
        if atr <= 0 or tr <= 0:
            continue

        preco_sinal = float(df_slice["close"].iloc[-1])
        preco_confirmacao = calcular_preco_confirmacao(preco_sinal, side, tr, fator_confirmacao)
        if not preco_confirmacao or preco_confirmacao <= 0:
            logging.info(f"[BACKTEST-BREAKOUT] Cancelado por preço inválido | side={side.upper()} | trigger={preco_confirmacao}")
            continue

        janela_fim = min(len(df) - 1, i + timeout_candles)
        trigger_idx = None
        for j in range(i + 1, janela_fim + 1):
            high = float(df["high"].iloc[j])
            low = float(df["low"].iloc[j])
            if side == "buy" and high >= preco_confirmacao:
                trigger_idx = j
                break
            if side == "sell" and low <= preco_confirmacao:
                trigger_idx = j
                break

        if trigger_idx is None:
            trades_cancelados += 1
            logging.info(
                f"⏳ BREAKOUT não confirmado — trigger não tocado dentro do timeout | side={side.upper()} | trigger={preco_confirmacao:.4f}"
            )
            continue

        trades_confirmados += 1
        entry_price = preco_confirmacao
        stop_loss, take_profit, trailing_stop = calculate_stop_take_trailing(entry_price, atr, side)
        rrr = calcular_rrr(entry_price, stop_loss, take_profit, min_rrr=1.0)
        if rrr is None or rrr < 1.0:
            continue

        duracao_timeout = max(40, min(400, int((atr / entry_price) * 1_500_000)))  # aproximação do original
        limite_k = min(len(df) - 1, trigger_idx + duracao_timeout)

        trade_encerrado = False
        for k in range(trigger_idx, limite_k + 1):
            high_k = float(df["high"].iloc[k])
            low_k = float(df["low"].iloc[k])
            close_k = float(df["close"].iloc[k])

            if side == "buy":
                if low_k <= stop_loss:
                    perda = entry_price - stop_loss
                    saldo -= perda
                    losses += 1
                    trades.append(
                        {"data": str(df["time"].iloc[k]), "side": side, "preco_entrada": entry_price, "preco_saida": stop_loss, "lucro": -perda, "motivo_saida": "stop"}
                    )
                    trade_encerrado = True
                    break
                if high_k >= take_profit:
                    ganho = take_profit - entry_price
                    saldo += ganho
                    wins += 1
                    trades.append(
                        {"data": str(df["time"].iloc[k]), "side": side, "preco_entrada": entry_price, "preco_saida": take_profit, "lucro": ganho, "motivo_saida": "take"}
                    )
                    trade_encerrado = True
                    break
            else:
                if high_k >= stop_loss:
                    perda = stop_loss - entry_price
                    saldo -= perda
                    losses += 1
                    trades.append(
                        {"data": str(df["time"].iloc[k]), "side": side, "preco_entrada": entry_price, "preco_saida": stop_loss, "lucro": -perda, "motivo_saida": "stop"}
                    )
                    trade_encerrado = True
                    break
                if low_k <= take_profit:
                    ganho = entry_price - take_profit
                    saldo += ganho
                    wins += 1
                    trades.append(
                        {"data": str(df["time"].iloc[k]), "side": side, "preco_entrada": entry_price, "preco_saida": take_profit, "lucro": ganho, "motivo_saida": "take"}
                    )
                    trade_encerrado = True
                    break

            trailing_stop = trailing_stop  # placeholder to manter assinatura; trailing não aplicado no backtest simplificado

        if not trade_encerrado:
            close_final = float(df["close"].iloc[limite_k])
            if side == "buy":
                resultado = close_final - entry_price
            else:
                resultado = entry_price - close_final
            saldo += resultado
            if resultado >= 0:
                wins += 1
            else:
                losses += 1
            trades.append(
                {
                    "data": str(df["time"].iloc[limite_k]),
                    "side": side,
                    "preco_entrada": entry_price,
                    "preco_saida": close_final,
                    "lucro": resultado,
                    "motivo_saida": "timeout_trade",
                }
            )

    total_trades = wins + losses
    winrate = (wins / total_trades * 100) if total_trades > 0 else 0
    lucro_total = saldo - saldo_inicial
    retorno_pct = (lucro_total / saldo_inicial) * 100 if saldo_inicial else 0

    relatorio = (
        f"Trades: {total_trades} | Winrate: {winrate:.2f}% | Lucro: {lucro_total:.2f} | "
        f"Saldo final: {saldo:.2f} | Timeout_candles: {timeout_candles}"
    )

    executou_trade = trades_confirmados > 0
    motivo_nao_execucao = None
    if not executou_trade:
        if trades_cancelados > 0:
            motivo_nao_execucao = "timeout"
        elif sinais_detectados > 0:
            motivo_nao_execucao = "trigger_nao_tocado"

    status = "NEUTRO"
    if executou_trade:
        if lucro_total > 0:
            status = "POSITIVO"
        elif lucro_total < 0:
            status = "NEGATIVO"

    logging.info(
        f"📊 BACKTEST BREAKOUT | Sinais detectados: {sinais_detectados} | "
        f"Triggers tocados: {trades_confirmados} | Trades executados: {trades_confirmados} | "
        f"Trades cancelados (timeout): {trades_cancelados}"
    )

    return {
        "status": status,
        "executou_trade": executou_trade,
        "motivo_nao_execucao": motivo_nao_execucao,
        "sinais_detectados": sinais_detectados,
        "trades_confirmados": trades_confirmados,
        "trades_cancelados": trades_cancelados,
        "trades": trades,
        "lucro_total": lucro_total,
        "winrate": winrate,
        "retorno_pct": retorno_pct,
        "saldo_inicial": saldo_inicial,
        "saldo_final": saldo,
        "trade_log": trades,
        "relatorio_completo": relatorio,
        "direcao": lado_permitido,
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
    if lado_permitido is None:
        logging.info("🧭 CONTEXTO AUTORIZADO PARA OPERAR (modo AUTO) — direção será definida pelo Breakout.")

    # ============================================================
    # Decisão e trava de lado (BUY/SELL) baseada em delta mínimo
    # ============================================================
    buy_score = float(contexto_decisao["placar"].get("buy", 0) or 0)
    sell_score = float(contexto_decisao["placar"].get("sell", 0) or 0)
    lado_aprovado = direcao_contexto
    delta_pct = None
    delta_min_decisao = float(os.getenv("DELTA_MINIMO_DECISAO", 0.25))

    if buy_score > 0 or sell_score > 0:
        maior = max(buy_score, sell_score)
        if maior > 0:
            delta_pct = abs(buy_score - sell_score) / maior
            if delta_pct < delta_min_decisao:
                logging.info(
                    "⚖️ DECISÃO BLOQUEADA | BUY=%.2f SELL=%.2f Δ=%.0f%% < %.0f%%",
                    buy_score,
                    sell_score,
                    delta_pct * 100,
                    delta_min_decisao * 100,
                )
                lado_aprovado = None
            else:
                lado_aprovado = "buy" if buy_score > sell_score else "sell"
                logging.info(
                    "🧠 DECISÃO FINAL TRAVADA → LADO=%s | BUY=%.2f SELL=%.2f Δ=%.0f%%",
                    lado_aprovado.upper(),
                    buy_score,
                    sell_score,
                    delta_pct * 100,
                )

    # Executa backtest do Breakout apenas para métricas/contexto (não para veto)
    try:
        breakout_metrics = executar_backtest_breakout(df.copy(), lado_aprovado)
    except Exception as e:
        logging.error(f"[BACKTEST-GERAL] Falha ao executar backtest do Breakout: {e}")
        breakout_metrics = {}

    lucro_estimado = breakout_metrics.get("lucro_total", breakout_metrics.get("lucro", 0))
    retorno_pct = breakout_metrics.get("retorno_pct", breakout_metrics.get("win_rate (%)", 0))

    direcao_resultado = breakout_metrics.get("direcao", lado_aprovado) or lado_aprovado
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
        "BREAKOUT": {
            **breakout_metrics,
            "buy_score": buy_score,
            "sell_score": sell_score,
            "delta_pct": delta_pct,
            "lado_aprovado": direcao_resultado,
        }
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
    direcao_log = (
        f"{melhor['direcao']} (definida pelo Breakout, modo AUTO)"
        if melhor.get("direcao") == "AUTO"
        else melhor["direcao"]
    )
    logging.info(f" Direção:   {direcao_log}")
    logging.info(f" Lucro:     {melhor.get('lucro', melhor.get('lucro_total', 0)):.2f}")
    logging.info(f" Retorno:   {melhor['retorno_pct']}%")

    # 📄 Relatório detalhado da estratégia vencedora (Breakout)
    relatorio_breakout = relatorios.get("BREAKOUT")
    if isinstance(relatorio_breakout, dict):
        logging.info(
            "📊 BACKTEST BREAKOUT — Sinais: %s | Confirmados: %s | Cancelados: %s | Trades: %s | Lucro: %.2f | Winrate: %.2f%%",
            relatorio_breakout.get("sinais_detectados", 0),
            relatorio_breakout.get("trades_confirmados", 0),
            relatorio_breakout.get("trades_cancelados", 0),
            len(relatorio_breakout.get("trades", []) or []),
            relatorio_breakout.get("lucro_total", 0.0),
            relatorio_breakout.get("winrate", 0.0),
        )

        trades_exec = relatorio_breakout.get("trades") or []
        if trades_exec:
            for idx, t in enumerate(trades_exec, 1):
                logging.info(
                    "   • Trade #%s | side=%s | entrada=%.4f | saida=%.4f | resultado=%.4f | motivo=%s",
                    idx,
                    t.get("side", "").upper(),
                    float(t.get("preco_entrada", 0)),
                    float(t.get("preco_saida", 0)),
                    float(t.get("lucro", 0)),
                    t.get("motivo_saida", "desconhecido"),
                )
        else:
            logging.info("   Nenhum trade executado no backtest.")
    else:
        logging.info("Relatorio de breakout indisponivel ou sem trades executados.")

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


