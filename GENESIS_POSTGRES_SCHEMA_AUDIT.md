# Auditoria do schema PostgreSQL — Sprint 2

Data: 2026-10-07. Banco existente: cluster local PostgreSQL 16.15, `genesis_trading`, schema `public`, host local/porta 5432. Credenciais não são reproduzidas.

Tabelas operacionais: `trades` (53 colunas), `predictions_lstm` (13), `log_events` (8), `mr_fsm_state` (3). `trade_quality_samples` existe no PostgreSQL com 4 linhas e é dataset separado; a fonte SQLite correspondente está em `Robo/trade_quality/trade_quality.db`.

Contagens pré-carga: `trades` 1.402; `predictions_lstm` 114.567; `log_events` 641.095; `mr_fsm_state` 5.

As quatro tabelas operacionais possuem as mesmas colunas da fonte `Robo/trades.db`, com tipos PostgreSQL equivalentes. Foram acrescentados índices únicos aditivos por `id` em `trades`, `predictions_lstm` e `log_events` após verificação de duplicidade.

Não foram executados `DROP`, `DELETE`, recriação de tabela ou criação de banco. Foi criada apenas `genesis_migration_runs` e índices de idempotência.
