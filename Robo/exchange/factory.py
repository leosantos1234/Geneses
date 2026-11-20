"""Fábrica única de clientes de exchange.

Os módulos de estratégia recebem o cliente por esta fábrica e não devem criar
clientes de exchange diretamente. A implementação Bybit preserva a API atual;
OKX fica protegida até o adaptador compatível com o contrato do robô existir.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from exchange_config import get_active_exchange
from exchange.write_guard import GuardedExchangeProxy


def active_exchange_name() -> str:
    return get_active_exchange()


def create_exchange_client():
    exchange = active_exchange_name()

    if exchange == "bybit":
        from pybit.unified_trading import HTTP

        api_key = os.getenv("BYBIT_API_KEY")
        api_secret = os.getenv("BYBIT_API_SECRET")
        if not api_key or not api_secret:
            raise RuntimeError("Credenciais BYBIT_API_KEY/BYBIT_API_SECRET ausentes.")
        testnet = os.getenv("BYBIT_TESTNET", "false").strip().lower() in (
            "1", "true", "yes", "on", "sim"
        )
        return GuardedExchangeProxy(HTTP(testnet=testnet, api_key=api_key, api_secret=api_secret))

    if exchange == "okx":
        # As credenciais da conta empresarial ficam centralizadas em .env-okx;
        # o perfil do ativo define apenas o símbolo e a exchange ativa.
        okx_env = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env-okx"))
        load_dotenv(okx_env, override=False)
        from .okx_client import OKXClient
        return GuardedExchangeProxy(OKXClient())

    raise RuntimeError(f"Exchange nao suportada: {exchange}")
