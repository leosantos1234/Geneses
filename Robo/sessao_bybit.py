"""Compatibilidade legada para módulos que importam ``sessao_bybit``.

O nome do módulo é mantido para não quebrar imports antigos; a sessão real é
criada pela fábrica da exchange ativa.
"""

from exchange.factory import create_exchange_client

session = create_exchange_client()
