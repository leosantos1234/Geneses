# ============================================================
#   MÓDULO 4 — ORQUESTRAÇÃO GERAL MARKET MAKING PRO
# ============================================================

import logging
from estrategia_entrada.Market_Making._marketmaking_validacao import validar_marketmaking
from estrategia_entrada.Market_Making._marketmaking_backtest import backtest_marketmaking
from estrategia_entrada.Market_Making._marketmaking_execucao import executar_marketmaking_realtime


def market_making_controller(df, session, symbol, cfg, ia_comandante_fn):
    # ===== 1) VALIDAR MERCADO =====
    valid = validar_marketmaking(df, cfg)
    if not valid["apto"]:
        return {"status": "HOLD", "motivo": valid["motivo"]}

    # ===== 2) BACKTEST =====
    back = backtest_marketmaking(df.copy(), cfg)
    if back["lucro"] <= 0 or back["trades"] < 3:
        return {"status": "BLOCK", "motivo": "BACKTEST_NEGATIVO"}

    # ===== 3) IA COMANDANTE =====
    decisao = ia_comandante_fn(df, back)

    if decisao["action"] != "operar":
        return {"status": "HOLD", "motivo": decisao["motivo"]}

    # ===== 4) EXECUTAR REALTIME =====
    execucao = executar_marketmaking_realtime(df, session, symbol, cfg)

    return {
        "status": "EXECUTADO",
        "execucao": execucao,
        "backtest": back
    }
