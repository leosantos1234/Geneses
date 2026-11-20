"""
trade_transition_manager.py
-------------------------------------------------
Gerencia a transição de trades do tipo BREAKOUT
para outros estados:

- Mantém breakout válido
- Encerra breakout fracassado
- Migra breakout expirado para tendência
- Aplica time-stop para evitar sangria
-------------------------------------------------

Timeframe base: M5 (5 minutos)
"""

import time
from typing import Dict


# ==================================================
# CONFIGURAÇÕES OFICIAIS (M5)
# ==================================================

BREAKOUT_TF_SEC = 300              # 5 minutos
BREAKOUT_EARLY_CANDLES = 3         # 15 minutos → explosão inicial
BREAKOUT_MAX_CANDLES = 6           # 30 minutos → validade máxima

# Explosão mínima medida em ATR
EXPLOSAO_EARLY_ATR = 0.35          # até 15 min
EXPLOSAO_FULL_ATR = 0.60           # até 30 min

# Retorno para dentro do range = breakout morto
RETORNO_RANGE_ATR = 0.25           # tolerância aumentada de 0.15 para 0.25

# Time-stop (anti-lenga-lenga)
TIME_STOP_ATR = 0.45               # corta antes do stop original

# Configurações do estado AGUARDAR ativo
MAX_TEMPO_AGUARDAR = 3 * BREAKOUT_TF_SEC  # 3 candles M5 (15 minutos)
REACAO_MINIMA_ATR = 0.15           # Reação mínima a favor
PERDA_MAXIMA_AGUARDAR_ATR = 0.5    # Stop por ATR contra entrada


# ==================================================
# FUNÇÕES AUXILIARES
# ==================================================

def candles_passados_m5(entry_ts: float, now_ts: float) -> int:
    """Converte tempo decorrido em candles M5"""
    elapsed = max(0.0, now_ts - entry_ts)
    return int(elapsed // BREAKOUT_TF_SEC)


def deslocamento_favor(entry_price: float, last_price: float, side: str, atr: float):
    """Calcula deslocamento a favor do trade"""
    if side.lower() == "buy":
        move = last_price - entry_price
    else:
        move = entry_price - last_price

    move_pct = move / entry_price if entry_price else 0.0
    move_atr = move / atr if atr else 0.0
    return move, move_pct, move_atr


def voltou_para_range(last_close: float, breakout_level: float, side: str, atr: float) -> bool:
    """Detecta retorno para dentro do range"""
    tolerancia = atr * RETORNO_RANGE_ATR if atr else 0.0

    if side.lower() == "buy":
        return last_close < (breakout_level - tolerancia)
    else:
        return last_close > (breakout_level + tolerancia)


def calcular_time_stop(entry_price: float, side: str, atr: float) -> float:
    """Calcula stop curto por tempo (anti-sangria)"""
    distancia = atr * TIME_STOP_ATR if atr else 0.0

    if side.lower() == "buy":
        return entry_price - distancia
    else:
        return entry_price + distancia


def verificar_reacao_favor(entry_price: float, last_price: float, side: str, atr: float) -> bool:
    """Verifica se houve reação mínima a favor durante AGUARDAR"""
    if side.lower() == "buy":
        return last_price >= entry_price + (REACAO_MINIMA_ATR * atr)
    else:
        return last_price <= entry_price - (REACAO_MINIMA_ATR * atr)


def verificar_perda_atr_aguardar(entry_price: float, last_price: float, side: str, atr: float) -> bool:
    """Verifica se o preço foi muito contra durante AGUARDAR"""
    if side.lower() == "buy":
        return last_price < entry_price - (PERDA_MAXIMA_AGUARDAR_ATR * atr)
    else:
        return last_price > entry_price + (PERDA_MAXIMA_AGUARDAR_ATR * atr)


# ==================================================
# CLASSIFICAÇÃO DO BREAKOUT
# ==================================================

def classificar_breakout(trade_state: Dict, now_ts: float, last_price: float, last_close_m5: float) -> Dict:
    """
    Classifica o estado atual do breakout
    """
    entry_ts = trade_state["entry_ts"]
    entry_price = trade_state["entry_price"]
    side = trade_state["side"]
    atr = trade_state["atr_entry"]
    level = trade_state["breakout_level"]

    candles = candles_passados_m5(entry_ts, now_ts)
    move, move_pct, move_atr = deslocamento_favor(entry_price, last_price, side, atr)

    # 1️⃣ Retorno para o range → falhou (exceto para testes manuais)
    strategy = trade_state.get("strategy", "")
    if strategy != "teste_manual" and voltou_para_range(last_close_m5, level, side, atr):
        return {
            "status": "BREAKOUT_FALHOU_RANGE",
            "candles": candles,
            "move_atr": move_atr,
            "move_pct": move_pct
        }

    # 2️⃣ Janela de explosão inicial
    if candles <= BREAKOUT_EARLY_CANDLES:
        if move_atr >= EXPLOSAO_EARLY_ATR:
            return {"status": "BREAKOUT_OK_EARLY", "candles": candles, "move_atr": move_atr}
        return {"status": "BREAKOUT_AGUARDANDO", "candles": candles, "move_atr": move_atr}

    # 3️⃣ Janela final de breakout
    if candles <= BREAKOUT_MAX_CANDLES:
        if move_atr >= EXPLOSAO_FULL_ATR:
            return {"status": "BREAKOUT_OK_CONFIRMADO", "candles": candles, "move_atr": move_atr}
        return {"status": "BREAKOUT_FRACO", "candles": candles, "move_atr": move_atr}

    # 4️⃣ Expirou como breakout
    return {"status": "BREAKOUT_EXPIROU", "candles": candles, "move_atr": move_atr}


# ==================================================
# DECISÃO DE TRANSIÇÃO
# ==================================================

def decidir_transicao(classif: Dict, regime: str, tendencia: str, trade_state: Dict = None, last_price: float = None) -> Dict:
    """
    Decide o que fazer com o trade após classificação
    Parâmetros opcionais (trade_state, last_price) ativam lógica inteligente do AGUARDAR
    """
    status = classif["status"]
    move_atr = classif.get("move_atr", 0)

    # Breakout saudável
    if status in ("BREAKOUT_OK_EARLY", "BREAKOUT_OK_CONFIRMADO"):
        return {"acao": "MANTER_BREAKOUT"}

    # Falhou → sai imediatamente
    if status == "BREAKOUT_FALHOU_RANGE":
        return {"acao": "SAIR_IMEDIATO", "motivo": "retorno_ao_range"}

    # Fraco e mercado ruim
    if status == "BREAKOUT_FRACO":
        if regime in ("lateral", "caos"):
            return {"acao": "TIME_STOP", "motivo": "fraco_em_regime_ruim"}
        return {"acao": "AGUARDAR"}

    # Expirou
    if status == "BREAKOUT_EXPIROU":
        if move_atr > 0 and regime == "tendencia" and tendencia in ("alta", "baixa"):
            return {"acao": "MIGRAR_TENDENCIA", "motivo": "expirou_mas_trend_ok"}

        if regime in ("lateral", "caos"):
            if move_atr >= 0.15:
                return {"acao": "REALIZAR_PARCIAL", "motivo": "lucro_curto"}
            return {"acao": "SAIR_IMEDIATO", "motivo": "expirou_sem_contexto"}

        if move_atr < 0.15:
            return {"acao": "SAIR_IMEDIATO", "motivo": "sem_deslocamento"}

        return {"acao": "MIGRAR_TENDENCIA"}

    # =============================================
    # AGUARDAR ATIVO E INTELIGENTE
    # =============================================
    if trade_state and last_price:
        # Estender trade_state defensivamente
        trade_state.setdefault("aguardar_inicio_ts", time.time())
        trade_state.setdefault("aguardar_reacoes", 0)
        
        entry_price = trade_state["entry_price"]
        side = trade_state["side"]
        atr = trade_state["atr_entry"]
        
        tempo_em_aguardar = time.time() - trade_state["aguardar_inicio_ts"]
        
        # Verificar reação a favor
        if verificar_reacao_favor(entry_price, last_price, side, atr):
            trade_state["aguardar_reacoes"] += 1
        
        # Calcular lucro atual
        if side.lower() == "buy":
            lucro_atual = last_price - entry_price
        else:
            lucro_atual = entry_price - last_price
        
        # Time-stop elegante (só se lucro <= 0)
        if tempo_em_aguardar > MAX_TEMPO_AGUARDAR and lucro_atual <= 0:
            return {"acao": "SAIR_IMEDIATO", "motivo": "time_stop_aguardar"}
        
        # Stop antecipado por ATR contra entrada
        if verificar_perda_atr_aguardar(entry_price, last_price, side, atr):
            return {"acao": "SAIR_IMEDIATO", "motivo": "perda_atr_aguardar"}
        
        # Log informativo
        import logging
        logger = logging.getLogger(__name__)
        logger.info(
            f"⏳ AGUARDAR | Tempo={tempo_em_aguardar:.1f}s | "
            f"Reações={trade_state['aguardar_reacoes']} | ATR={atr:.2f}"
        )

    return {"acao": "AGUARDAR"}


# ==================================================
# FUNÇÃO PRINCIPAL (CHAMADA PELO MONITORAMENTO)
# ==================================================

def avaliar_trade_breakout(
    trade_state: Dict,
    last_price: float,
    last_close_m5: float,
    regime: str,
    tendencia: str
) -> Dict:
    """
    Função principal a ser chamada no monitoramento
    """
    now_ts = time.time()

    classif = classificar_breakout(
        trade_state=trade_state,
        now_ts=now_ts,
        last_price=last_price,
        last_close_m5=last_close_m5
    )

    decisao = decidir_transicao(
        classif=classif,
        regime=regime,
        tendencia=tendencia,
        trade_state=trade_state,
        last_price=last_price
    )

    # Calcula stop por tempo, se necessário
    if decisao["acao"] == "TIME_STOP":
        decisao["novo_stop"] = calcular_time_stop(
            trade_state["entry_price"],
            trade_state["side"],
            trade_state["atr_entry"]
        )

    return {
        "classificacao": classif,
        "decisao": decisao
    }



def detectar_tendencia(df) -> str:
    """
    Detecta tendência predominante no M5
    Retorna: 'alta', 'baixa' ou 'neutra'
    """
    try:
        ema_fast = df["EMA_20"].iloc[-1]
        ema_slow = df["EMA_50"].iloc[-1]

        if ema_fast > ema_slow * 1.001:
            return "alta"
        elif ema_fast < ema_slow * 0.999:
            return "baixa"
        else:
            return "neutra"
    except Exception:
        return "neutra"


def detectar_regime_mercado(df) -> str:
    """
    Detecta o regime de mercado no M5
    Retorna: 'tendencia', 'lateral' ou 'caos'
    """
    try:
        close = df["close"].iloc[-1]
        atr = df["ATR"].iloc[-1]

        atr_pct = atr / close if close else 0

        ema_fast = df["EMA_20"].iloc[-1]
        ema_slow = df["EMA_50"].iloc[-1]

        inclinacao = abs(ema_fast - ema_slow) / close

        # Mercado caótico → volatilidade excessiva
        if atr_pct > 0.006:
            return "caos"

        # Mercado em tendência → direção + volatilidade saudável
        if inclinacao > 0.0015 and atr_pct > 0.0015:
            return "tendencia"

        # Caso contrário → lateral
        return "lateral"

    except Exception:
        return "lateral"
