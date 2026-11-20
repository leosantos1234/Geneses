"""Políticas de risco para estados degradados do modelo."""

from __future__ import annotations

import json
import os
from typing import Any


def fallback_poucos_dados_ativo(payload: Any) -> bool:
    """Detecta o estado explícito de fallback por insuficiência de dados."""
    try:
        serialized = json.dumps(payload, ensure_ascii=False, default=str).lower()
    except Exception:
        serialized = str(payload).lower()
    normalized = serialized.replace("-", "_").replace(" ", "_")
    return "fallback_poucos_dados" in normalized or "poucos_dados" in normalized


def model_fallback_policy(payload: Any) -> dict[str, Any]:
    """Retorna a ação fail-safe configurada para o fallback do modelo."""
    active = fallback_poucos_dados_ativo(payload)
    # A redução de lote só deve ser habilitada quando todas as estratégias
    # consumirem este multiplicador. Até lá, qualquer configuração diferente
    # de block permanece fail-safe e bloqueia a entrada.
    action = os.getenv("MODEL_FALLBACK_POUCOS_DADOS_ACTION", "block").strip().lower()
    if action != "block":
        action = "block"
    multiplier = max(0.0, min(1.0, float(os.getenv("MODEL_FALLBACK_RISK_MULTIPLIER", "0.25"))))
    return {
        "active": active,
        "action": action if active else "none",
        "risk_multiplier": multiplier if active else 1.0,
        "reason_code": "MODEL_FALLBACK_POUCOS_DADOS" if active else None,
    }
