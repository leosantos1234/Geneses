# Revisão de segurança MCP V1

## Controles aprovados

- allowlist exata de 5 tools P0;
- servidor STDIO, sem porta pública;
- nenhum import de worker, exchange, `execution_gate`, commander ou módulos de ordem;
- nenhum `sqlite3`, `trades.db`, SQL arbitrário, file tool, shell ou subprocesso no servidor;
- apenas repositories e consultas read-only; as conexões MCP do repository usam `default_transaction_read_only=on`, `statement_timeout=5000ms` e `lock_timeout=1000ms`;
- filtros e limites validados; `list_trades` limitado a 500 e paginado;
- erros não expõem stack trace ao cliente;
- logs sanitizados com request_id, tool, duração, status, contagem e error_code, enviados para stderr;
- connection string é sanitizada no healthcheck;
- se o backend não for PostgreSQL canônico, as tools de dados/risco recusam a consulta antes de abrir repository; SQLite retorna `DATABASE_UNAVAILABLE`;
- registry marca tools P0 como `mcp_exposed: true`, `read_only: true`; ações de escrita permanecem `false`.

## Ações explicitamente ausentes

`create_order`, `cancel_order`, `close_position`, `set_trading_stop`, `modify_stop`, `modify_take_profit`, `modify_strategy`, `start_robot`, `run_command`, `read_file` e `execute_sql` não são tools e não podem ser alcançadas pelo servidor.

## Limitações

O worker local não estava ativo durante a auditoria, portanto `worker.status` pode ser `unknown`/`stopped`; isso degrada a saúde, sem inferir que o worker está saudável. O cliente assíncrono oficial não conseguiu criar pipes no sandbox Windows (`WinError 5`); o protocolo real foi validado por harness STDIO síncrono.
