# ============================================================
#   MÓDULO 2 — BACKTEST REALISTA MARKET MAKING PRO  (CORRIGIDO)
# ============================================================

import logging
import pandas as pd


def backtest_marketmaking(df, cfg):
    """
    Backtest da estratégia Market Making para o Robô Fluxo.
    Retorno completamente padronizado e compatível com o ranking final.
    """

    # =========================
    # VALIDAÇÃO DE ENTRADA
    # =========================
    if cfg is None or not isinstance(cfg, dict):
        raise ValueError("❌ cfg inválido no backtest_marketmaking")

    if "capital_inicial" not in cfg:
        raise ValueError("❌ cfg precisa conter 'capital_inicial'")

    if df is None or len(df) < 60:
        return {
            "estrategia": "marketmaking",
            "direcao": "NONE",
            "lucro_total": -9999,
            "retorno_pct": -9999,
            "trade_log": [],
            "relatorio_completo": "DF insuficiente para backtest.",
            "saldo_inicial": cfg["capital_inicial"],
            "saldo_final": cfg["capital_inicial"]
        }

    df = df.reset_index(drop=True)

    # =========================
    # VARIÁVEIS INICIAIS
    # =========================
    capital = float(cfg["capital_inicial"])
    saldo = capital
    pos = 0.0
    trade_log = []

    spread_min = cfg.get("spread_min", 0.05)
    spread_mult = cfg.get("spread_multiplier", 1.0)
    inv_limit = cfg.get("inventory_limit", 0.005)
    qty = cfg.get("order_qty", 0.001)

    # =========================
    # LOOP PRINCIPAL
    # =========================
    for i in range(50, len(df)):
        preco = df["close"].iloc[i]

        # cálculo do spread
        try:
            micro_vol = df["close"].pct_change().rolling(10).std().iloc[i] * 100
            spread = max(spread_min, micro_vol * spread_mult)
        except Exception:
            continue

        bid = preco - spread / 2
        ask = preco + spread / 2

        low = df["low"].iloc[i]
        high = df["high"].iloc[i]

        # EXECUÇÃO DAS ORDENS
        if low <= bid:  # BUY
            pos += qty
            saldo -= qty * bid
            trade_log.append(("BUY", float(bid), float(pos)))

        if high >= ask:  # SELL
            pos -= qty
            saldo += qty * ask
            trade_log.append(("SELL", float(ask), float(pos)))

        # ZERAGEM DE INVENTÁRIO
        if abs(pos) > inv_limit:
            saldo += pos * preco
            pos = 0

    # =========================
    # RESULTADOS FINAIS
    # =========================
    saldo_final = saldo
    lucro = saldo_final - capital

    wins = sum(1 for side, price, pos_after in trade_log if side == "SELL")
    losses = sum(1 for side, price, pos_after in trade_log if side == "BUY" and pos_after < 0)

    winrate = round((wins / len(trade_log)) * 100, 2) if len(trade_log) else 0

    # DIREÇÃO FINAL
    direcao_final = "BUY" if lucro > 0 else "SELL" if lucro < 0 else "NONE"

    # =========================
    # RELATÓRIO FORMATADO
    # =========================
    relatorio = f"""
📊 ==== RESUMO DO BACKTEST (MARKET MAKING) ====
Estratégia       : MARKETMAKING
Saldo Inicial    : {capital:.2f}
Saldo Final      : {saldo_final:.2f}
Lucro Total      : {lucro:.2f}
Retorno (%)      : {round((lucro / capital) * 100, 2)}%
Trades Executados: {len(trade_log)}
Wins             : {wins}
Losses           : {losses}
Win Rate (%)     : {winrate}%
------------------------------------------------------------
📋 ==== TRADES EXECUTADOS ====
N | Side | Preço | Posição Após
------------------------------------------------------------
"""

    for i, (side, price, pos_after) in enumerate(trade_log):
        relatorio += f"{i:>3} | {side:>4} | {price:>8.2f} | {pos_after:>10}\n"

    # =========================
    # RETORNO COMPATÍVEL COM O ROBÔ FLUXO
    # =========================
    return {
        "estrategia": "marketmaking",
        "direcao": direcao_final,
        "lucro_total": float(lucro),
        "retorno_pct": round((lucro / capital) * 100, 2),
        "num_trades": len(trade_log),
        "num_wins": wins,
        "num_losses": losses,
        "win_rate": winrate,
        "relatorio_completo": relatorio,
        "trade_log": trade_log,
        "saldo_inicial": float(capital),
        "saldo_final": float(saldo_final)
    }
