# Reason codes

| Código | Uso |
|---|---|
| `NO_STRATEGY_SELECTED` | backtest não produziu vencedora |
| `AGUARDANDO_TRIGGER` | contexto aguarda gatilho |
| `COMMANDER_BLOCK` | comandante não autorizou |
| `RISK_BLOCKED` / `RISK_DAILY_LIMIT` | bloqueio do risk guard |
| `EXECUTION_BLOCK` | execution gate recusou |
| `ORDER_REJECTED` | exchange retornou rejeição/falha |
| `ORDER_ACCEPTED` | ordem aceita e trade persistido |
| `DECISION_ERROR` | exceção no fluxo de decisão |
| `LINEAGE_RECORDING_FAILURE` | falha da observabilidade; não bloqueia o trade |
| `DATABASE_OPERATIONAL_FAILURE` | indisponibilidade do PostgreSQL operacional |

Razões específicas retornadas por componentes podem ser preservadas como `reason_code`, desde que sanitizadas.
