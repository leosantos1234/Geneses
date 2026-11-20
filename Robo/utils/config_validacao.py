# utils/config_validacao.py

import os
import logging
from utils.parse_env import get_ativos_env


def obter_config_validacao():
    """
    Retorna score mínimo e score percentual com base na PRIORIDADE:
    1) Regime escolhido para o ativo (get_parametros())
    2) .env (fallback)
    """
    from regime.regime_controller import get_parametros  # import seguro (lazy import)

    # Identifica ativo atual do .env
    symbol = get_ativos_env()[0]

    logging.info(f"🔍 [VALIDAÇÃO] Buscando regime para o ativo: {symbol}")

    # ===========================================================
    # 1) TENTAR PEGAR CONFIGURAÇÕES DO REGIME
    # ===========================================================
    params = get_parametros(symbol)
    logging.info(f"🧪 Regime carregado: {params}")

    if params:
        score_min = params.get("score_min")
        score_pct = params.get("score_pct")
        modo = params.get("modo", "agressivo")

        logging.info(
            f"🧩 Regime detectado → "
            f"score_min={score_min}, score_pct={score_pct}, modo={modo}"
        )

        if score_min is not None and score_pct is not None:
            logging.info("✅ Usando configurações do REGIME.")
            return score_min, score_pct, modo

        logging.warning("⚠️ Regime incompleto — faltando score_min ou score_pct. Aplicando fallback ENV.")

    # ===========================================================
    # 2) FALLBACK .ENV
    # ===========================================================
    modo = os.getenv("MODO_VALIDACAO", "agressivo").strip().lower()
    logging.info(f"🔄 Fallback ENV. MODO_VALIDACAO={modo.upper()}")

    mapa_score_minimo = {
        "agressivo": int(os.getenv("SCORE_MINIMO_AGRESSIVO", 1)),
        "moderado": int(os.getenv("SCORE_MINIMO_MODERADO", 3)),
        "conservador": int(os.getenv("SCORE_MINIMO_CONSERVADOR", 4)),
    }

    mapa_score_pct = {
        "agressivo": int(os.getenv("SCORE_PCT_AGRESSIVO", 0)),
        "moderado": int(os.getenv("SCORE_PCT_MODERADO", 50)),
        "conservador": int(os.getenv("SCORE_PCT_CONSERVADOR", 65)),
    }

    score_min_final = mapa_score_minimo.get(modo, 4)
    score_pct_final = mapa_score_pct.get(modo, 65)

    logging.info(
        f"📘 Configuração FINAL (ENV): "
        f"score_min={score_min_final}, score_pct={score_pct_final}%, modo={modo}"
    )

    return score_min_final, score_pct_final, modo
