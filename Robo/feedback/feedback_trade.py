import json
import logging
import os
import threading
from datetime import datetime
from typing import Any


logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_LOG_PATH = os.path.join(_BASE_DIR, "trades_log.json")


def _to_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except Exception:
        return default


def _ler_trades() -> list[dict]:
    if not os.path.exists(_LOG_PATH):
        return []

    try:
        with open(_LOG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"Falha ao ler feedback de trades: {e}")
        return []


def _escrever_trades(trades: list[dict]) -> bool:
    try:
        os.makedirs(_BASE_DIR, exist_ok=True)
        with open(_LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(trades, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.warning(f"Falha ao escrever feedback de trades: {e}")
        return False


def registrar_trade_fechado(dados_trade: dict) -> bool:
    """
    Registra um trade encerrado para análise posterior.
    Salva em feedback/trades_log.json sem impactar a lógica operacional.
    """
    dados_trade = dict(dados_trade or {})

    with _LOCK:
        trades = _ler_trades()
        trades.append(dados_trade)
        return _escrever_trades(trades)


def analisar_trades(limit_recent: int | None = None) -> dict:
    """
    Analisa o histórico salvo e retorna métricas simples de acerto/erro.
    """
    with _LOCK:
        trades = _ler_trades()

    if isinstance(limit_recent, int) and limit_recent > 0:
        trades = trades[-limit_recent:]

    wins = 0
    losses = 0
    perdas_por_regime: dict[str, int] = {}
    perdas_por_estrategia: dict[str, int] = {}
    perdas_score_baixo = 0
    score_baixo_threshold = _to_float(os.getenv("FEEDBACK_LOW_SCORE_THRESHOLD", 5.0), 5.0) or 5.0

    for trade in trades:
        resultado = str(trade.get("resultado") or "").strip().lower()
        regime = str(trade.get("regime") or "desconhecido").strip().lower() or "desconhecido"
        estrategia = str(trade.get("estrategia") or "desconhecida").strip().lower() or "desconhecida"
        score = _to_float(trade.get("score"))

        if resultado == "win":
            wins += 1
            continue

        losses += 1
        perdas_por_regime[regime] = perdas_por_regime.get(regime, 0) + 1
        perdas_por_estrategia[estrategia] = perdas_por_estrategia.get(estrategia, 0) + 1
        if score is not None and score < score_baixo_threshold:
            perdas_score_baixo += 1

    return {
        "total_trades": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / len(trades)) if trades else 0.0,
        "loss_rate": (losses / len(trades)) if trades else 0.0,
        "perdas_por_regime": perdas_por_regime,
        "perdas_por_estrategia": perdas_por_estrategia,
        "perdas_com_score_baixo": perdas_score_baixo,
        "gerado_em": datetime.utcnow().isoformat(),
    }
