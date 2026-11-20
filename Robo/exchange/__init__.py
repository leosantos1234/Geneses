"""Camada de acesso à exchange ativa do Robô Fluxo."""

from .factory import active_exchange_name, create_exchange_client

__all__ = ["active_exchange_name", "create_exchange_client"]
