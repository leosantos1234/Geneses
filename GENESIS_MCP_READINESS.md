# Prontidão para MCP — Sprint 1

Nenhum servidor MCP, cliente MCP, RAG ou Skill foi criado neste sprint.

## P0 candidato

| Ferramenta | Fonte canônica atual | Schema previsível | Side effect | Auth | Timeout | Estado |
|---|---|---:|---:|---:|---:|---|
| `get_system_health` | `Robo/ops/healthcheck.py` + startup snapshot | parcial | não | pendente de contrato MCP | parcial | CANDIDATE |
| `list_open_positions` | API UI/repository + exchange read | parcial | não | bearer UI existente | parcial | CANDIDATE |
| `list_trades` | `Robo/trades.db` local / repository | sim, com lacunas históricas | não | bearer UI existente | parcial | CANDIDATE |
| `get_trade_detail` | API UI `/api/ui/trades/{trade_id}` | sim | não | bearer UI existente | parcial | CANDIDATE |
| `get_risk_status` | `risk_guard.evaluate_risk_limits` | parcial | não | ainda acoplado a runtime | parcial | CANDIDATE/BLOCKED PG |

## P1/P2

`get_strategy_candidates`, `get_lstm_predictions`, `get_model_inventory`, `get_sentiment_snapshot`, `get_commander_decision`, `get_backtest_report` e `compare_backtests` podem ser somente leitura. `run_backtest` deve permanecer isolado/sandbox. `create_order`, `cancel_order`, `close_position` e `modify_strategy` não são candidatos iniciais.

## Pré-requisitos antes do Sprint 2

- finalizar a decisão SQLite versus PostgreSQL e adaptar o risk gate ao backend escolhido;
- autenticar e autorizar a superfície read-only que será exposta;
- definir schemas versionados, timeout, paginação e tratamento de erro;
- propagar `decision_id`/`order_id`/`trade_id` de forma auditável;
- separar live, paper, backtest, importado e recovered;
- confirmar que nenhuma ferramenta MCP tem caminho de escrita por import indireto.

## Atualização Sprint 3

O MCP V1 read-only foi implementado em `Robo/mcp/server.py` com transporte STDIO e exatamente cinco tools P0. As fontes são o repository canônico, o runtime PostgreSQL e a leitura segura do Risk Guard. O teste JSON-RPC ponta a ponta foi aprovado; o cliente assíncrono oficial teve limitação de criação de pipes no ambiente Windows da auditoria, documentada no relatório do Sprint 3.
