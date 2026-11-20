# Relatório de migração de banco — Sprint 2

Fonte: snapshot `Robo/ops/snapshots/trades_sqlite_pre_sprint2_20261007.db`, SHA-256 `389BAC4C4829F1B814CFCB1B48E9BCB689CD15E1871AB45526A5FBF53AC6B701`, igual ao `Robo/trades.db`. Alvo: PostgreSQL existente `genesis_trading`.

Carga em staging temporário, transação única, `INSERT ... ON CONFLICT DO NOTHING`. O migrador destrutivo anterior foi substituído por `Robo/ops/migrate_sqlite_to_postgres.py`, que exige PostgreSQL/URL explícitos e não cria banco nem apaga dados.

Registros novos: 47 `trades`, 1.600 `predictions_lstm`, 29.517 `log_events`, 0 `mr_fsm_state`. Contagem final: 1.449, 116.167, 670.612 e 5. Os 6 trades extras já existentes apenas no PostgreSQL foram preservados.

Nenhum arquivo de estratégia, score, limiar, indicador, regime, modelo, sizing, SL/TP, trailing ou alavancagem foi alterado.
