# Inventário de Componentes do Gênesis Quant — Sprint 1

## Matriz canônica

| Componente | Papel | Canônico? | Ativo? | Pode executar? | Banco usado | Risco | Status |
|---|---|---:|---:|---:|---|---|---|
| `Robo/trading_bot_geneses.py` | worker operacional | SIM, candidato confirmado por Docker/Render/scripts | não confirmado nesta sessão | SIM | `Robo/trades.db` por contrato local | crítico | CANONICAL |
| `Robo/comandante/hierarquia.py` | autorização determinística no worker | SIM | chamado pelo fluxo documentado | NÃO diretamente | operacional | alto | CANONICAL |
| `Agente_IA/cerebro_ia/*` + `tomar_decisao_comandante.py` | decisão paralela/API | NÃO | API separada | SIM, código legado exposto | `Agente_IA/logs/decisoes_ia.db` | crítico | DANGEROUS_PARALLEL_PATH; bloqueado por default |
| `Robo/utils/risk_guard.py` | limites e kill switch | SIM no worker | chamado pelo gate | NÃO | banco operacional SQLite no modo local | crítico | CANONICAL |
| `Robo/execution/execution_gate.py` | idempotência, TTL e autorização de execução | SIM | chamado antes de ordem | SIM, como gate | estado JSON + banco operacional | crítico | CANONICAL |
| `Robo/api_ui.py` | API do painel | SIM para leitura | não confirmado nesta sessão | NÃO | resolver central | médio | READ_ONLY |
| `Agente_IA/api_agente_ia.py` | análise e ações de agente | NÃO | processo separado não detectado | SIM no código, agora bloqueado por default | decisões IA + exchange | crítico | EXPERIMENTAL / DANGEROUS_PARALLEL_PATH |
| `Robo/estrategia_entrada/*` | Breakout/MR/MM/early trend | SIM como módulos do worker | não confirmado | SIM via worker | operacional | crítico | CANONICAL/EXPERIMENTAL por estratégia |
| `Robo/estrategias/lstm_rnn.py` | previsão LSTM | experimental | telemetria observada; serviço não confirmado | NÃO diretamente | `predictions_lstm` | alto | EXPERIMENTAL |
| `Robo/utils/ml_core.py` | RF/XGB | experimental | RF parcialmente observado; XGB não comprovado | NÃO diretamente | artefatos/DB | alto | EXPERIMENTAL |
| `Robo/estrategias/analise_sentimento.py` | sentimento | auxiliar | chamado em caminhos do worker | NÃO diretamente | caches/telemetria | médio | EXPERIMENTAL |
| `Robo/estrategia_entrada/backtesting_geral.py` | ranking/filtro de setups | CANÔNICO como backtest do worker | chamado como filtro | NÃO em exchange | arquivos/relatórios | médio | DIAGNOSTIC/ISOLATED |
| `Robo/Backtest/*` | walk-forward/multiativos | NÃO para live | ferramentas | NÃO | relatórios | baixo/médio | DIAGNOSTIC |
| Learning/feedback | qualidade e relatórios | NÃO comprovado no live | parcial | NÃO | JSON/quality DB | médio | EXPERIMENTAL |
| Bybit client | exchange histórica/compatibilidade | não é ativo confirmado nesta sessão | desconhecido | SIM no código | n/a | crítico | LEGACY/EXCHANGE |
| OKX client | exchange atual integrada | SIM como integração disponível | não confirmado nesta sessão | SIM no código | n/a | crítico | CANONICAL EXCHANGE ADAPTER |
| PostgreSQL | persistência declarada | NÃO confirmado como ativo | não confirmado | sim se habilitado | `GENESIS_DATABASE_URL` | crítico | BLOCKED/PENDING ADAPTER |
| `Robo/trades.db` | trades, LSTM, FSM e logs | SIM local | populado | sim | SQLite | alto | CANONICAL_OPERATIONAL_DATABASE local |
| `Agente_IA/trades.db` | histórico/agente | NÃO | populado | não deveria receber live | SQLite | alto | HISTORICAL |
| `Agente_IA/logs/decisoes_ia.db` | decisões do agente paralelo | NÃO | populado | não deveria comandar exchange | SQLite | crítico | AGENT_DECISIONS |

## Worker e fluxo canônicos

Evidências convergentes: `Robo/Dockerfile.worker`, `render.yaml`, `Robo/iniciar_robo.bat`, `Robo/SERVICOS.md`, imports do worker e logs. O worker canônico é `Robo/trading_bot_geneses.py`, com a ressalva operacional de que nenhum processo estava ativo na inspeção final.

Fluxo canônico reconhecido:

```text
market data
  -> indicadores/regime/backtest/setup
  -> estratégia selecionada
  -> compra1.executar_ordem_completa
  -> comandante.hierarquia.avaliar_decisao_comandante
  -> risk_guard / execution_gate
  -> exchange factory / cliente ativo
  -> persistência em Robo/trades.db (modo SQLite local)
```

`Agente_IA/api_agente_ia.py` e seus routers são classificados como caminho paralelo perigoso. Eles não foram apagados nem fundidos, mas métodos de mutação agora exigem flag de trading e token dedicados.

## Serviços

| Serviço | Comando/configuração | Porta | Dependências | Enabled declarativo | Classificação |
|---|---|---:|---|---:|---|
| Worker | `python trading_bot_geneses.py .env-*` / `Dockerfile.worker` | — | exchange, DB, modelos | `false` no catálogo ops | CANONICAL |
| API UI | `uvicorn api_ui:app --port 8010` | 8010 | repository, auth | `false` no catálogo ops | CANONICAL READ_ONLY |
| Frontend | Vite/React Lovable | 8080/10000 no Compose | API UI | Compose/Render | CANONICAL UI |
| API Agente IA | `uvicorn api_agente_ia:app` | 8000/8002/8003/8004/8011 em scripts | exchange, OpenAI, routers | scripts | PARALLEL/DANGEROUS |
| LSTM | `estrategias/lstm_service.py` | 8505/8506 no catálogo | TensorFlow/modelos | `false` | EXPERIMENTAL |
| PostgreSQL | Compose/Render | 5435 local no Compose | API/worker se habilitado | declarado | PENDING |
| Supervisor | `ops/supervisor_runner.ps1` | — | services.json | manual/task scheduler | OPS |

O catálogo de ops foi corrigido para apontar à raiz atual, mas os serviços continuam `enabled:false`; isso não é uma ativação automática.

