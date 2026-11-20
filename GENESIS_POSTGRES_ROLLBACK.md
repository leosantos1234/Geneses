# Rollback PostgreSQL — Sprint 2

O rollback é reversível: interromper worker/API, restaurar a configuração anterior somente após decisão explícita e manter `Robo/ops/snapshots/trades_sqlite_pre_sprint2_20261007.db` intacto. Não executar `DROP`, `DELETE` ou restauração sobre PostgreSQL sem backup e aprovação.

Antes de qualquer retorno, validar hash do snapshot, contagens reconciliadas e ausência de processos apontando para fontes divergentes. O rollback não deve enviar, cancelar ou modificar ordens.

A migração foi aditiva; não é necessário remover linhas migradas para reverter a aplicação. `genesis_migration_runs` e índices `genesis_*` podem permanecer até manutenção documentada.
