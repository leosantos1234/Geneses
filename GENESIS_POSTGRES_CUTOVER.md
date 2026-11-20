# Cutover PostgreSQL canônico — Sprint 2

Status: CONCLUÍDO para o runtime configurado.

`genesis_trading.public` no cluster PostgreSQL local existente é `CANONICAL_OPERATIONAL_DATABASE`. Não foi criado novo PostgreSQL. `Robo/trades.db` fica como snapshot legado/recovery, não como fallback operacional.

Adaptações: runtime central; trades/previsões; risk guard; execution gate; API UI; logger operacional; estado FSM Mean Reversion; perfis locais/deploy. Todos usam PostgreSQL quando configurados e falham fechado quando o banco canônico está indisponível.

Risk parity: SIM. Execution parity: SIM. API parity: SIM. SQLite fallback operacional: NÃO. Comportamento quantitativo alterado: NÃO. Ordens reais/testes na exchange: NÃO.
