import logging
import os
from datetime import datetime

def gerar_relatorio_validacao(
    estrategia: str,
    lado: str,
    score_lado: float,
    backtest_gain: float = None,
    decisao_final: bool = None,
    etapa: str = "GERAL"
):
    """
    Gera e registra no log um relatório completo de validação.
    Pode ser usado em diferentes etapas (pré-backtest, pós-backtest, etc).
    """

    modo_validacao = os.getenv("MODO_VALIDACAO", "agressivo").lower()

    score_min_agg = int(os.getenv("SCORE_MINIMO_AGRESSIVO", 4))
    score_min_mod = int(os.getenv("SCORE_MINIMO_MODERADO", 8))
    score_min_cons = int(os.getenv("SCORE_MINIMO_CONSERVADOR", 10))

    pct_min_agg = int(os.getenv("SCORE_PCT_AGRESSIVO", 50))
    pct_min_mod = int(os.getenv("SCORE_PCT_MODERADO", 80))
    pct_min_cons = int(os.getenv("SCORE_PCT_CONSERVADOR", 90))

    # Define conforme o modo
    if modo_validacao == "agressivo":
        score_minimo = score_min_agg
        pct_minimo = pct_min_agg
    elif modo_validacao == "moderado":
        score_minimo = score_min_mod
        pct_minimo = pct_min_mod
    else:
        score_minimo = score_min_cons
        pct_minimo = pct_min_cons

    total_criterios = max(score_minimo, 1)
    pct_lado = round((score_lado / total_criterios) * 100, 2)

    logging.info("📘 =================== RELATÓRIO DE VALIDAÇÃO ===================")
    logging.info(f"🕒 Etapa: {etapa.upper()} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info(f"🧩 Estratégia ativa: {estrategia.upper()}")
    logging.info(f"🎚️  Modo de Validação: {modo_validacao.upper()}")
    logging.info(f"🎯  Score mínimo exigido: {score_minimo}")
    logging.info(f"📈  Percentual mínimo exigido: {pct_minimo}%")
    logging.info(f"📊  Lado: {lado.upper()} → {score_lado} pontos ({pct_lado}%)")

    if backtest_gain is not None:
        status_backtest = "POSITIVO" if backtest_gain > 0 else "NEGATIVO"
        logging.info(f"🧠  Backtest: {status_backtest} | Retorno Simulado: {backtest_gain:.2f}%")

    if decisao_final is not None:
        if decisao_final:
            logging.info(f"✅ DECISÃO FINAL: Entrada AUTORIZADA ({lado.upper()}) — critérios atendidos.")
        else:
            logging.info(f"❌ DECISÃO FINAL: Entrada NEGADA ({lado.upper()}) — falhou em algum critério.")

    logging.info("📘 ===============================================================")
