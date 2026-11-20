# Sprint 2 — PostgreSQL existente como fonte única de verdade

POSTGRES EXISTENTE LOCALIZADO: SIM — cluster local PostgreSQL 16.15, `genesis_trading`, schema `public`.

NOVO POSTGRES CRIADO: NÃO.

POSTGRES CANÔNICO: SIM — banco existente `genesis_trading`.

SQLite alterado: NÃO. Snapshot em `Robo/ops/snapshots/trades_sqlite_pre_sprint2_20261007.db`.

PG alterado: SIM, somente aditivo/idempotente: carga faltante, metadados e índices de idempotência.

Linhas migradas: 47 trades, 1.600 previsões, 29.517 eventos, 0 estados FSM.

Reconciliação: todos os IDs da fonte estão no PG; 6 trades adicionais pré-existentes no PG foram preservados; zero colisões de `trade_id` com IDs diferentes.

Risk parity: SIM. Execution parity: SIM. API parity: SIM. SQLite fallback operacional: NÃO.

Comportamento quantitativo alterado: NÃO. Ordens reais/cancelamentos/alterações: NÃO. Deploy remoto: NÃO executado.

Arquivos principais: `Robo/genesis_runtime.py`, `Robo/banco_trade/db_trades.py`, `Robo/utils/risk_guard.py`, `Robo/execution/execution_gate.py`, `Robo/ui_backend/repository.py`, `Robo/api_ui.py`, `Robo/utils/logger_config.py`, `Robo/estrategias/mean_reversion/mr_state_store.py`, `Robo/ops/migrate_sqlite_to_postgres.py`.

Compilação sintática dos componentes alterados: OK. Health read-only via `psql`: OK. Driver `psycopg` não estava instalado no Python local; foi incluído nos requisitos e os Dockerfiles já o instalam. Nenhum worker/API foi iniciado e nenhuma escrita foi feita na exchange.

Riscos residuais: deploy remoto ainda não executado; credenciais locais exigem rotação fora deste sprint; dataset de qualidade segue separado; teste end-to-end requer ambiente com `psycopg`. MCP readiness permanece bloqueado conforme Sprint 1.
