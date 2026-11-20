# analise/market_regime.py

import pandas as pd

def detectar_regime(df: pd.DataFrame) -> str:
    """
    Classifica o regime de mercado com base em volatilidade e RSI.
    Retorna: "lateral", "tendencia_alta", "tendencia_baixa" ou "indefinido".
    """
    try:
        atr = df['ATR'].iloc[-1]
        media_atr = df['ATR'].rolling(50).mean().iloc[-1]
        rsi = df['RSI'].iloc[-1]

        if atr < 0.7 * media_atr:
            return "lateral"
        elif rsi > 60:
            return "tendencia_alta"
        elif rsi < 40:
            return "tendencia_baixa"
        else:
            return "indefinido"
    except Exception:
        return "indefinido"
