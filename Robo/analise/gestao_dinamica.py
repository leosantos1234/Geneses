import logging
import math
import os

def calcular_exposicao_dinamica(decisao, df, saldo_disponivel, confianca=0.7):
    """
    Camada 3 – Gestão Dinâmica de Risco e Exposição
    Ajusta o tamanho da posição, SL, TP e trailing de acordo com volatilidade e confiança.
    """
    try:
        atr = float(df["ATR"].iloc[-1])
        close = float(df["close"].iloc[-1])
        regime = "alta" if close > df["EMA50"].iloc[-1] else "baixa"

        # 🧠 Ajusta o risco conforme o regime e a confiança da IA
        base_risco = float(os.getenv("RISCO_PERCENTUAL", "0.004"))
        risco_final = base_risco * (0.5 + confianca)       # modulado pela confiança (0.5–1.2x)

        # 💰 Define o tamanho da posição
        position_size = saldo_disponivel * risco_final
        logging.info(f"📊 Exposição ajustada dinamicamente: {position_size:.2f} USDT ({risco_final*100:.2f}%)")

        # 🎯 Calcula SL/TP dinâmico via ATR
        stop_loss = close - (atr * 2.2) if decisao == "buy" else close + (atr * 2.2)
        take_profit = close + (atr * 4.0) if decisao == "buy" else close - (atr * 4.0)
        trailing_offset = atr * 1.5

        # 🔒 Ajuste defensivo
        stop_loss = round(stop_loss, 2)
        take_profit = round(take_profit, 2)
        trailing_offset = round(trailing_offset, 2)

        logging.info(f"🧮 SL={stop_loss} | TP={take_profit} | Trailing={trailing_offset}")
        return {
            "risk_percent": risco_final,
            "position_size": position_size,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "trailing_offset": trailing_offset,
            "regime": regime,
        }

    except Exception as e:
        logging.error(f"❌ Erro na gestão dinâmica de risco: {e}")
        return None
