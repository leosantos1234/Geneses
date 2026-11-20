import os
from typing import Any, Dict


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _normalize_strategy(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalize_regime(value: Any) -> str:
    if isinstance(value, (int, float)):
        try:
            ivalue = int(value)
            if ivalue == 3:
                return "lateral"
            if ivalue == 4:
                return "caos"
            if ivalue in (1, 2):
                return "tendencia"
        except Exception:
            pass
    return str(value or "").strip().lower()


def avaliar_decisao_comandante(contexto_trade: dict) -> Dict[str, Any]:
    """
    Camada hierárquica do Comandante.

    Responsabilidade:
    - validar a decisão da lógica base
    - bloquear quando o contexto for incompatível
    - devolver ajustes leves de risco, sem executar ordens
    """
    contexto_trade = contexto_trade or {}

    decisao_base = bool(contexto_trade.get("decisao_base", True))
    if not decisao_base:
        return {
            "autorizado": False,
            "motivo": "logica_base_nao_autorizou",
            "ajustes": {},
        }

    score = _to_float(
        contexto_trade.get("score_estrategia", contexto_trade.get("entry_score", 0.0)),
        0.0,
    )
    threshold_min = _to_float(os.getenv("COMMANDER_MIN_SCORE", 4.0), 4.0)
    threshold_high = _to_float(os.getenv("COMMANDER_HIGH_SCORE", 7.0), 7.0)

    if score < threshold_min:
        return {
            "autorizado": False,
            "motivo": f"score_baixo:{score:.2f}<{threshold_min:.2f}",
            "ajustes": {},
        }

    strategy = _normalize_strategy(contexto_trade.get("strategy"))
    regime = _normalize_regime(contexto_trade.get("regime"))
    contexto_mercado = _normalize_regime(contexto_trade.get("contexto_mercado"))

    regime_lateral = regime == "lateral" or contexto_mercado == "lateral"
    regime_caos = regime == "caos" or contexto_mercado == "caos"

    if "breakout" in strategy and (regime_lateral or regime_caos):
        return {
            "autorizado": False,
            "motivo": f"conflito_regime_breakout:{regime or contexto_mercado or 'desconhecido'}",
            "ajustes": {},
        }

    ajustes: Dict[str, Any] = {}

    # Regra inicial controlada: score alto permite risco um pouco mais apertado.
    if score >= threshold_high:
        ajustes["risco"] = _to_float(os.getenv("COMMANDER_HIGH_SCORE_RISK_MULT", 0.9), 0.9)
    else:
        ajustes["risco"] = 1.0

    trailing_base = contexto_trade.get("trailing_base")
    if trailing_base not in (None, "") and score >= threshold_high:
        trailing_mult = _to_float(os.getenv("COMMANDER_HIGH_SCORE_TRAILING_MULT", 0.9), 0.9)
        ajustes["trailing"] = _to_float(trailing_base, 0.0) * trailing_mult

    return {
        "autorizado": True,
        "motivo": "comandante_autorizou",
        "ajustes": ajustes,
    }
