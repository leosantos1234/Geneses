# Sprint 4 — Decision Lineage / Observabilidade

## Resultado

Foi implementada uma trilha de decisão append-only no PostgreSQL operacional `genesis_trading`, sem alterar critérios, scores, indicadores, sizing, SL/TP, estratégia ou envio de ordens. O `decision_id` nasce no início da oportunidade que entra no funil canônico e é propagado por contexto até os gates de comandante, risco, execução, ordem e trade.

## Escopo entregue

- `Robo/lineage/decision_lineage.py`: serviço pequeno, tipado, assíncrono e best-effort.
- `Robo/ops/migrations/004_decision_lineage.sql`: migração aditiva e idempotente.
- `decision_trace` para o resumo da decisão e `decision_events` para eventos append-only.
- `trades.decision_id` para vínculo sem backfill artificial.
- `get_decision_trace()` e `list_decisions()` internos, read-only.
- Redação de segredos e fingerprint sem persistir credenciais.
- Falha de gravação gera `LINEAGE_RECORDING_FAILURE` e não muda a decisão.
- Testes de caminhos autorizado, commander block, risk block, execution block, order rejected, legado e contrato de schema.

## Limites preservados

O Sprint 4 não adicionou MCP tools, não usou SQLite como fonte operacional, não fabricou lineage para os 1.449 trades existentes, não enviou ordem real e não alterou o comportamento quantitativo.

## Baseline e limitações

O workspace não contém repositório Git; portanto, não foi possível criar branch ou registrar diff via Git. A validação foi feita por compilação, testes Python e inspeção read-only do PostgreSQL.
