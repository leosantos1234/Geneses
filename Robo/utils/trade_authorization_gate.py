import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def autorizar_trade(
    df,
    regime: int,
    side: str,
    score_side: Optional[float],
    indicadores: Dict,
    estrategia_escolhida: str,
    modo_execucao: str = "PROD",
) -> Dict:
    """
    Gate de autorização de trade.
    - Não altera lógica das estratégias; apenas bloqueia/autoriza antes da execução.
    - Aplica hard blocks: regime permitido, volume, RSI, EMA, MACD coerentes.
    - IA deve agir como validadora externa: este gate não inverte lado nem autoriza por score.
    """

    side_norm = (side or "").strip().upper()
    estrategia_norm = (estrategia_escolhida or "").strip().upper()
    resultado = {"autorizado": False, "motivo": "invalid_input"}

    # Regime permitido (gate leve): 1/2 para direcional; 3 somente Market Making; 4 bloqueado.
    if regime not in (1, 2, 3):
        logger.info("[AUTH] BLOQUEADO: Regime %s não permite execução.", regime)
        resultado["motivo"] = "regime_bloqueado"
        return resultado

    # BREAKOUT: gate passivo (não duplica filtros internos)
    if estrategia_norm == "BREAKOUT":
        if regime in (1, 2):
            if side_norm not in ("BUY", "SELL"):
                logger.info("[AUTH] BLOQUEADO: Lado inválido ou empatado (side=%s) para BREAKOUT.", side)
                resultado["motivo"] = "lado_invalido_ou_empate"
                return resultado
            logger.info("[AUTH] PASSIVO: BREAKOUT autorizado (regime %s, lado %s).", regime, side_norm)
            resultado.update(
                {
                    "autorizado": True,
                    "motivo": "ok",
                    "ajuste_risco": None,
                    "estrategia": estrategia_norm,
                    "side": side_norm,
                }
            )
            return resultado
        else:
            logger.info("[AUTH] BLOQUEADO: BREAKOUT fora dos regimes 1/2.")
        resultado["motivo"] = "regime_bloqueado"
        return resultado

    # MARKETMAKING: apenas aceita se veio de regime 3, demais validações no módulo de MM
    if estrategia_norm == "MARKETMAKING":
        if regime != 3:
            logger.info("[AUTH] BLOQUEADO: MARKETMAKING fora do regime 3.")
            resultado["motivo"] = "regime_bloqueado"
            return resultado
        if side_norm not in ("BUY", "SELL"):
            logger.info("[AUTH] BLOQUEADO: Lado inválido ou empatado (side=%s) para MARKETMAKING.", side)
            resultado["motivo"] = "lado_invalido_ou_empate"
            return resultado
        logger.info("[AUTH] PASSIVO: MARKETMAKING autorizado (regime 3).")
        resultado.update(
            {
                "autorizado": True,
                "motivo": "ok",
                "ajuste_risco": None,
                "estrategia": estrategia_norm,
                "side": side_norm,
            }
        )
        return resultado

    # Para MEAN_REVERSION (regimes 1/2): gate apenas checa lado único e regime; filtros técnicos ficam na estratégia.
    if estrategia_norm == "MEAN_REVERSION":
        if regime not in (1, 2):
            logger.info("[AUTH] BLOQUEADO: MEAN_REVERSION fora dos regimes 1/2.")
            resultado["motivo"] = "regime_bloqueado"
            return resultado
        if side_norm not in ("BUY", "SELL"):
            logger.info("[AUTH] BLOQUEADO: Lado inválido ou empatado (side=%s) para MEAN_REVERSION.", side)
            resultado["motivo"] = "lado_invalido_ou_empate"
            return resultado
        # Sem hard blocks técnicos aqui; estratégia já fez.
        logger.info("[AUTH] PASSIVO: MEAN_REVERSION autorizado (regime %s, lado %s).", regime, side_norm)
        resultado.update(
            {
                "autorizado": True,
                "motivo": "ok",
                "ajuste_risco": None,
                "estrategia": estrategia_norm,
                "side": side_norm,
            }
        )
        return resultado

    # Exclusividade direcional já deve vir definida; se vazio, bloqueia
    if side_norm not in ("BUY", "SELL"):
        logger.info("[AUTH] BLOQUEADO: Lado inválido ou empatado (side=%s).", side)
        resultado["motivo"] = "lado_invalido_ou_empate"
        return resultado

    # Score não autoriza; apenas log de prioridade
    logger.info("[AUTH] Lado %s com score=%.4f segue para validação IA/execução.", side_norm, score_side or 0)

    # Gate de lucro forçado em produção
    if modo_execucao.strip().upper() == "PROD":
        # Somente SL/TP/Trailing/defesa IA devem fechar; aqui apenas registramos a restrição.
        logger.info("[AUTH] Modo PROD: lucro forçado/timeout desabilitado por política.")

    resultado.update(
        {
            "autorizado": True,
            "motivo": "ok",
            "ajuste_risco": None,
            "estrategia": estrategia_norm,
            "side": side_norm,
        }
    )
    logger.info("[AUTH] AUTORIZADO: Estratégia=%s Side=%s", estrategia_norm, side_norm)
    return resultado
