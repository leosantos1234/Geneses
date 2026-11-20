"""Selecao central da exchange ativa."""

import os

SUPPORTED_EXCHANGES = ("bybit", "okx")
IMPLEMENTED_EXCHANGES = ("bybit", "okx")


def get_active_exchange() -> str:
    value = os.getenv("ACTIVE_EXCHANGE", "bybit").strip().lower()
    if value not in SUPPORTED_EXCHANGES:
        allowed = ", ".join(SUPPORTED_EXCHANGES)
        raise RuntimeError(
            f"ACTIVE_EXCHANGE invalida: {value!r}. Use uma destas opcoes: {allowed}."
        )
    return value


def validate_active_exchange() -> str:
    # A politica de escrita fica no cliente OKX, que exige explicitamente
    # OKX_READ_ONLY=False para qualquer endpoint de mutacao.
    return get_active_exchange()
