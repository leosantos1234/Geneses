# Reconciliação SQLite × PostgreSQL — Sprint 2

- Todos os IDs de `trades`, `predictions_lstm` e `log_events` do SQLite estão presentes no PostgreSQL.
- Todos os `trade_id` não nulos do SQLite estão presentes no PostgreSQL.
- Colisões de `trade_id` com IDs diferentes: 0.
- `mr_fsm_state`: 5 linhas no SQLite e 5 no PostgreSQL.
- Trades adicionais apenas no PostgreSQL: 6; preservados e não sobrescritos.
- Soma de `profit_loss` no PostgreSQL após reconciliação: aproximadamente `-133,72940173`.

O PostgreSQL é superset reconciliado da fonte operacional anterior. `trade_quality_samples` não foi misturado: é dataset ML separado.
