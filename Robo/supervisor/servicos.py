import json
import logging
import os
from pathlib import Path
from typing import Any

from feedback.feedback_trade import analisar_trades
from gestor_de_trade import encerrar_ordem

try:
    from compra1 import session as bybit_session
except Exception:
    bybit_session = None


logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[1]
TRADES_DB = ROOT_DIR / "trades.db"
OPERACAO_FLAG = ROOT_DIR / "operacao_ativa.flag"
FEEDBACK_LOG = ROOT_DIR / "feedback" / "trades_log.json"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _get_api():
    if bybit_session is None:
        raise RuntimeError("sessao_bybit_indisponivel")
    return bybit_session


def _ler_feedback_log() -> list[dict]:
    if not FEEDBACK_LOG.exists():
        return []
    try:
        with FEEDBACK_LOG.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning("Falha ao ler feedback log: %s", e)
        return []


def obter_regime_atual() -> str | None:
    trades = _ler_feedback_log()
    if not trades:
        return None
    for trade in reversed(trades):
        regime = str(trade.get("regime") or "").strip().lower()
        if regime:
            return regime
    return None


def listar_posicoes_ativas() -> list[dict]:
    api = _get_api()
    try:
        resp = api.get_positions(category="linear", settleCoin="USDT")
        if resp.get("retCode") != 0:
            raise RuntimeError(resp.get("retMsg") or "get_positions_falhou")
        lista = resp.get("result", {}).get("list", []) or []
        return [p for p in lista if abs(_safe_float(p.get("size"))) > 0]
    except Exception as e:
        logger.warning("Falha ao listar posicoes com settleCoin: %s", e)
        symbol = os.getenv("ATIVOS", "").strip()
        if not symbol:
            return []
        try:
            resp = api.get_positions(category="linear", symbol=symbol)
            lista = resp.get("result", {}).get("list", []) or []
            return [p for p in lista if abs(_safe_float(p.get("size"))) > 0]
        except Exception as inner:
            logger.warning("Falha ao listar posicoes por symbol fallback: %s", inner)
            return []


def obter_status_robo() -> dict:
    ativo_flag = OPERACAO_FLAG.exists()
    posicoes = listar_posicoes_ativas()
    pnl_total = 0.0
    for pos in posicoes:
        pnl_total += _safe_float(
            pos.get("unrealisedPnl", pos.get("unrealizedPnl", 0.0)),
            0.0,
        )
    return {
        "robo_ativo": ativo_flag or bool(posicoes),
        "flag_operacao": ativo_flag,
        "trades_ativos": len(posicoes),
        "pnl_total": round(pnl_total, 4),
        "regime_atual": obter_regime_atual(),
        "posicoes": posicoes,
    }


def obter_resumo_feedback(limit_recent: int = 5) -> dict:
    analise = analisar_trades()
    trades = _ler_feedback_log()
    ultimas = list(reversed(trades[-limit_recent:])) if trades else []
    return {
        "analise": analise,
        "ultimas_operacoes": ultimas,
    }


def obter_analise_feedback(limit_recent: int = 50) -> dict:
    analise = analisar_trades(limit_recent=limit_recent)
    insights = []

    perdas_por_regime = analise.get("perdas_por_regime") or {}
    perdas_por_estrategia = analise.get("perdas_por_estrategia") or {}
    losses = int(analise.get("losses") or 0)
    total = int(analise.get("total_trades") or 0)
    loss_rate = float(analise.get("loss_rate") or 0.0)

    if total == 0:
        insights.append("Sem trades suficientes para análise.")
    if loss_rate >= 0.6 and total >= 5:
        insights.append("Taxa recente de loss está alta.")
    if perdas_por_regime:
        pior_regime = max(perdas_por_regime, key=perdas_por_regime.get)
        insights.append(f"Maior concentração de perdas em regime {pior_regime}.")
    if perdas_por_estrategia:
        pior_estrategia = max(perdas_por_estrategia, key=perdas_por_estrategia.get)
        insights.append(f"Estratégia com mais perdas recentes: {pior_estrategia}.")
    if int(analise.get("perdas_com_score_baixo") or 0) >= 3:
        insights.append("Scores baixos têm histórico recente ruim.")
    if not insights and losses > 0:
        insights.append("Há perdas distribuídas, sem concentração forte no recorte recente.")

    analise["insights"] = insights
    return analise


def fechar_symbol(symbol: str) -> dict:
    symbol = str(symbol or "").strip().upper()
    if not symbol:
        return {"ok": False, "mensagem": "Símbolo inválido."}

    try:
        api = _get_api()
        posicoes = api.get_positions(category="linear", symbol=symbol)
        lista = posicoes.get("result", {}).get("list", []) or []
        ativa = next((p for p in lista if abs(_safe_float(p.get("size"))) > 0), None)
        if not ativa:
            return {"ok": False, "mensagem": f"Sem posição ativa em {symbol}."}

        encerrar_ordem(symbol=symbol, api=api)
        return {"ok": True, "mensagem": f"Solicitação de fechamento enviada para {symbol} com reduceOnly."}
    except Exception as e:
        logger.exception("Erro ao fechar symbol=%s", symbol)
        return {"ok": False, "mensagem": f"Falha ao fechar {symbol}: {e}"}


def fechar_todas() -> dict:
    try:
        posicoes = listar_posicoes_ativas()
        if not posicoes:
            return {"ok": False, "mensagem": "Não há posições abertas para fechar.", "fechados": []}

        api = _get_api()
        fechados = []
        falhas = []
        for pos in posicoes:
            symbol = str(pos.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            resultado = fechar_symbol(symbol)
            if resultado.get("ok"):
                fechados.append(symbol)
            else:
                falhas.append({"symbol": symbol, "erro": resultado.get("mensagem")})

        ok = len(fechados) > 0 and len(falhas) == 0
        mensagem = f"Fechamento solicitado para: {', '.join(fechados)}" if fechados else "Nenhuma posição foi fechada."
        if falhas:
            mensagem += f" | Falhas: {len(falhas)}"
        return {"ok": ok, "mensagem": mensagem, "fechados": fechados, "falhas": falhas}
    except Exception as e:
        logger.exception("Erro ao fechar todas as posicoes")
        return {"ok": False, "mensagem": f"Falha ao fechar posições: {e}", "fechados": []}


def consultar_total_trades_db() -> int | None:
    try:
        from genesis_runtime import connect_operational_database
        conn = connect_operational_database(read_only=True)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM trades")
        total = int(cur.fetchone()[0])
        conn.close()
        return total
    except Exception as e:
        logger.warning("Falha ao consultar total de trades no DB: %s", e)
        return None
