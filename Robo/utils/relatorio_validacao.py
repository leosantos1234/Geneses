import logging
from datetime import datetime

from utils.config_validacao import obter_config_validacao


def gerar_relatorio_validacao(
    estrategia: str,
    lado: str,
    score_lado: float,
    score_pct_lado: float = None,
    backtest_gain: float = None,
    decisao_final: bool = None,
    etapa: str = "GERAL",
    num_trades: int = None,
    estado_validacao: str = None,
):
    """
    Gera e registra no log um relatorio completo de validacao.
    Usa a fonte oficial: obter_config_validacao().
    """

    score_minimo, pct_minimo, modo_validacao = obter_config_validacao()

    total_criterios = max(score_minimo, 1)
    if score_pct_lado is None:
        pct_lado = round((score_lado / total_criterios) * 100, 2)
    else:
        pct_lado = float(score_pct_lado)
    pct_lado = max(0.0, min(pct_lado, 100.0))

    logging.info("[RELATORIO] ================== VALIDACAO ==================")
    logging.info(f"[RELATORIO] Etapa: {etapa.upper()} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info(f"[RELATORIO] Estrategia ativa: {estrategia.upper()}")
    logging.info(f"[RELATORIO] Modo de validacao: {modo_validacao.upper()}")
    logging.info(f"[RELATORIO] Score minimo exigido: {score_minimo}")
    logging.info(f"[RELATORIO] Percentual minimo exigido: {pct_minimo}%")
    logging.info(f"[RELATORIO] Lado: {lado.upper()} -> {score_lado} pontos ({pct_lado:.2f}%)")

    if backtest_gain is not None:
        status_backtest = "POSITIVO" if backtest_gain > 0 else "NEGATIVO"
        if (num_trades is not None and int(num_trades) == 0) or str(estado_validacao or "").upper() == "AGUARDANDO_TRIGGER":
            status_backtest = "NEUTRO"
        logging.info(f"[RELATORIO] Backtest: {status_backtest} | Retorno simulado: {backtest_gain:.2f}%")

    estado_norm = str(estado_validacao or "").upper()
    if estado_norm == "AGUARDANDO_TRIGGER":
        logging.info(f"[RELATORIO] DECISAO FINAL: AGUARDANDO_TRIGGER ({lado.upper()}) - monitorando gatilho real.")
    elif decisao_final is not None:
        if bool(decisao_final):
            logging.info(f"[RELATORIO] DECISAO FINAL: Entrada AUTORIZADA ({lado.upper()}) - criterios atendidos.")
        else:
            logging.info(f"[RELATORIO] DECISAO FINAL: Entrada NEGADA ({lado.upper()}) - falhou em algum criterio.")

    logging.info("[RELATORIO] =================================================")
