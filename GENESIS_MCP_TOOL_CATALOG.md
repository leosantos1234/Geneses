# Catálogo de tools MCP V1 — Gênesis Quant

Servidor: `Robo/mcp/server.py`  
Transporte: STDIO local  
Side effects de todas as tools: `NONE`  
Fonte operacional: PostgreSQL canônico `genesis_trading.public`.

| Tool | Input | Output | Fonte | Risco |
|---|---|---|---|---|
| `get_system_health` | nenhum | status, database, worker, risk, ambiente, timestamp | `genesis_runtime.database_health` + leitura de processo + `risk_guard.evaluate_risk_limits` | leitura local/DB; sem exchange |
| `list_open_positions` | `symbol?`, `limit` 1–500 | source, count, positions, error | `ui_backend.repository.list_open_positions` | somente posições persistidas |
| `list_trades` | `limit` 1–500, `offset`, `symbol?`, `strategy?`, `status?`, `start_time?`, `end_time?` ISO-8601 | filtros, count, total, has_more, trades, error | `ui_backend.repository.list_trades/count_trades` | paginação limitada; sem SQL do cliente |
| `get_trade_detail` | `trade_id` | trade ou erro `NOT_FOUND`/`INVALID_ARGUMENT` | `ui_backend.repository.get_trade` | somente um registro |
| `get_risk_status` | nenhum | allow/blocked/unknown, kill switch, exposição, PnL, limites e razões | `risk_guard.evaluate_risk_limits` + repository DB-only | não chama exchange/gate/escrita |

Erros estruturados: `INVALID_ARGUMENT`, `NOT_FOUND`, `DATABASE_UNAVAILABLE`, `INTERNAL_ERROR`. Nenhuma tool aceita SQL, caminho, comando ou payload de exchange.
