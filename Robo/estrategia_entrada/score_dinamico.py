# estrategias/score_dinamico.py
import logging
import os

def ajustar_score_dinamico(required_score, atr_pct, atr_pct_alto=None, aumento_score=1):
    """
    Ajusta o score mínimo exigido conforme volatilidade do mercado (ATR%).
    """
    if atr_pct_alto is None:
        atr_pct_alto = float(os.getenv("ATR_PCT_MAX", 0.0030))
    if atr_pct is not None and atr_pct > atr_pct_alto:
        novo_score = required_score + aumento_score
        logging.info(f"🔺 ATR%={atr_pct:.2%} > {atr_pct_alto:.2%} → Score mínimo: {novo_score}")
        return novo_score
    return required_score
