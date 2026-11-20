# ============================================================
#   MÓDULO 1 — VALIDAÇÃO DE MERCADO PARA MARKET MAKING PRO
# ============================================================

import numpy as np

def validar_marketmaking(df, cfg):
    """
    Valida se o mercado está em condições adequadas
    para executar Market Making PRO.
    """

    try:
        # Microvolatilidade (quanto menor melhor p/ MM)
        micro_vol = df["close"].pct_change().rolling(10).std().iloc[-1] * 100

        # ATR (evitar volatilidade agressiva)
        atr = df["ATR"].iloc[-1]

        # Liquidez mínima
        volume = df["volume"].iloc[-1]
        vol_media = df["volume"].rolling(20).mean().iloc[-1]

        # Tendência micro (MM gosta de lateralização)
        closes = df["close"].tail(20).values
        x = np.arange(len(closes))
        inclinacao, _ = np.polyfit(x, closes, 1)

        # ================================
        #      REGRAS DE VALIDAÇÃO
        # ================================
        if micro_vol > cfg["max_microvol"]:
            return {"apto": False, "motivo": f"Microvol alta ({micro_vol:.3f}%)"}

        if atr > cfg["max_atr"]:
            return {"apto": False, "motivo": f"ATR alto ({atr:.3f})"}

        if volume < vol_media * cfg["min_liquidez"]:
            return {"apto": False, "motivo": "Liquidez insuficiente"}

        if abs(inclinacao) > cfg["tendencia_limite"]:
            return {"apto": False, "motivo": "Mercado com tendência forte"}

        return {"apto": True}

    except Exception as e:
        return {"apto": False, "motivo": f"Erro validação: {e}"}
