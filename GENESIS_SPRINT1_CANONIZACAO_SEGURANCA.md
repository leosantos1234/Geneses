# Gênesis Quant — Sprint 1: Canonização e Segurança

Data: 2026-10-07  
Escopo: baseline, fronteiras, configuração, segurança e rastreabilidade.  
Fora do escopo: Sprint 2, MCP, RAG, Skills, Comandante 2.0 e qualquer melhoria quantitativa.

## Resultado executivo

Foi estabelecida uma baseline documentada e uma autoridade arquitetural provisória, com uma limitação importante:

- Gênesis oficial: raiz `C:\Users\NPO\Desenvolvimento\genesis`.
- Worker oficial: `Robo/trading_bot_geneses.py`, confirmado por Dockerfile, Render, scripts, documentação e imports; processo ativo não estava presente na verificação.
- Comandante operacional oficial: `Robo/comandante/hierarquia.py`.
- Banco operacional local oficial: `Robo/trades.db`, resolvido por `Robo/genesis_runtime.py`.
- PostgreSQL: declarado em Compose/Render, mas não promovido a fonte operacional neste sprint porque o risk/execution gate ainda é SQLite-específico.
- Caminho autorizado até a exchange: worker canônico → comandante local → risk guard/execution gate → exchange factory/client. A API de agentes é paralela e fica fail-closed para mutações.
- Leitura e escrita foram classificadas no [GENESIS_CAPABILITY_REGISTRY.yaml](C:\Users\NPO\Desenvolvimento\genesis\GENESIS_CAPABILITY_REGISTRY.yaml).

## Decisões

1. Não criar Git automaticamente. A raiz não possui `.git`; não houve branch, commit ou diff Git.
2. Não migrar, apagar ou alterar bancos.
3. Não ativar PostgreSQL sem adaptação do gate.
4. Não alterar estratégia, score, regime, indicadores, sizing, risco, SL, TP, trailing, alavancagem, ranking ou backtest.
5. Bloquear por padrão as mutações da API de agentes.
6. Manter backups e componentes paralelos, classificando-os em vez de apagá-los.

## Matriz de autoridade

| Capacidade | Componente autorizado | Read | Write | Exchange access | Observação |
|---|---|---:|---:|---:|---|
| Market data | worker/exchange client | SIM | NÃO | leitura | fluxo operacional |
| Trades | API UI/repository | SIM | NÃO | NÃO | fonte local canônica |
| Positions | API UI/exchange client | SIM | NÃO | leitura | autenticação UI |
| Risk | risk guard/execution gate | SIM | estado interno | não cria ordem | gate obrigatório |
| Backtest | Backtest/estratégia | SIM | relatórios | NÃO | isolado |
| Decision | comandante local | SIM | autorização interna | NÃO direto | canônico |
| Order creation | worker + execution gate | SIM | SIM | SIM | único caminho operacional |
| Order cancellation | worker/monitoramento | SIM | SIM | SIM | não expor ao MCP |
| Position close | worker/saída protegida | SIM | SIM | SIM | não expor ao MCP |
| Stop modification | worker/gestão protegida | SIM | SIM | SIM | API agente bloqueada por default |
| Strategy modification | governança/configuração | SIM | controlado | NÃO direto | fora do Sprint 1 |
| Agent API mutation | nenhum por default | SIM | NÃO | NÃO | exige duas flags + Bearer |

## Matriz de bancos

| Banco | Tipo | Finalidade | Leitura | Escrita | Canônico? | Histórico? | MCP | RAG |
|---|---|---|---:|---:|---:|---:|---:|---:|
| `Robo/trades.db` | SQLite | trades, LSTM, FSM, logs | SIM | SIM | SIM local | não | SIM read-only | SIM com curadoria |
| `Agente_IA/trades.db` | SQLite | histórico/agente | SIM | legado | NÃO | SIM | não inicialmente | somente após classificação |
| `Agente_IA/logs/decisoes_ia.db` | SQLite | decisões agente | SIM | SIM pelo paralelo | NÃO | SIM | SIM read-only futuro | SIM com cautela |
| `Robo/trade_quality/trade_quality.db` | SQLite | dataset qualidade | SIM | pipeline ML | NÃO | SIM/ML | P1 read-only | SIM |
| `Robo/banco_trade/trades.db` | SQLite | schema legado vazio | SIM | não recomendado | NÃO | SIM | NÃO | NÃO |
| `Robo/research/trades.db` | SQLite | pesquisa/diagnóstico | SIM | diagnóstico | NÃO | SIM | NÃO | NÃO |
| `Robo/trades1.db` | SQLite | legado | SIM | não | NÃO | SIM | NÃO | NÃO |
| `trades.db` | SQLite relativo | indeterminado | SIM | indeterminado | NÃO | UNKNOWN | NÃO | NÃO |
| PostgreSQL declarado | PostgreSQL | implantação pretendida | PENDENTE | PENDENTE | NÃO neste sprint | não | BLOCKED | BLOCKED |

## Matriz de endpoints relevantes

| Endpoint | Método | Categoria | Auth | Altera estado? | Exchange? | Manter/bloquear | MCP |
|---|---:|---|---|---:|---:|---|---|
| `/api/ui/health` | GET | READ_ONLY | pública | NÃO | NÃO | manter | SIM |
| `/api/ui/summary` | GET | READ_ONLY | bearer | NÃO | NÃO | manter | SIM |
| `/api/ui/open-positions` | GET | READ_ONLY | bearer | NÃO | leitura | manter | SIM |
| `/api/ui/trades` | GET | READ_ONLY | bearer | NÃO | NÃO | manter | SIM |
| `/api/ui/trades/{trade_id}` | GET | READ_ONLY | bearer | NÃO | NÃO | manter | SIM |
| `/api/auth/login` | POST | AUTH | credencial | sessão | NÃO | manter | não |
| `/create_order`, `/create_order_adaptativa` | POST | TRADING_ACTION | flag + Bearer agente | SIM | SIM | bloqueado por default | NÃO |
| `/close_trade`, `/manage_trade` | POST | TRADING_ACTION | flag + Bearer agente | SIM/indireto | SIM | bloqueado por default | NÃO |
| `/cancelar_ordem/{order_id}` | DELETE | TRADING_ACTION | flag + Bearer agente | SIM | SIM | bloqueado por default | NÃO |
| `/ajustar_sl_tp`, `/adjust_trailing` | POST | STATE_CHANGE | flag + Bearer agente | SIM | SIM/indireto | bloqueado por default | NÃO |
| `/iniciar_robo` | POST | ADMIN/DANGEROUS | flag + Bearer agente | SIM | indireto | bloqueado por default | NÃO |
| `/analyze_market`, `/analyze_trade` | POST | ANALYSIS | allowlist; auth futura | NÃO | NÃO deveria | manter análise | P1 |
| `/status_sistema`, `/dashboard/*`, `/ativos` | GET | READ_ONLY/DIAGNOSTIC | auth futura uniforme | NÃO | possível leitura | manter | P1 |

## Alterações realizadas

- Criado `Robo/genesis_runtime.py` para resolver banco, flags de escrita, origins e startup snapshot.
- `Robo/banco_trade/db_trades.py`, UI repository, execution gate e risk guard usam resolução central local.
- API de agentes recebeu middleware fail-closed para mutações, token Bearer opcional apenas quando explicitamente habilitada e CORS restrito.
- Worker e API registram startup sem senha.
- Caminhos ativos de `db_manager`, `analyze_trade`, UI launchers e supervisor foram convertidos para a raiz atual.
- Catálogo `Robo/ops/services.json` foi corrigido para a raiz atual sem ativar serviços.
- Criados `.gitignore`, `Robo/.env.example` e complementado `.env.auth.example`.
- Criados inventários e registry deste sprint.

## Testes executados

- `python -m py_compile` nos módulos centrais alterados e nos módulos Breakout/OKX: aprovado.
- Importação isolada de `genesis_runtime`: aprovada; snapshot mostrou `database_backend=sqlite`, `Robo/trades.db` e flags de escrita desligadas.
- Consulta read-only do repository e do risk gate: aprovada após tornar a dependência Bybit lazy no módulo de persistência; nenhuma chamada de exchange foi feita.
- Parse de `Robo/ops/services.json`: aprovado.
- Consulta somente leitura das bases e contagens: aprovada; nenhum banco foi alterado.
- Busca de CORS wildcard no `api_agente_ia.py` ativo: não encontrado após correção.
- Busca de caminhos antigos nas superfícies ativas tocadas: não encontrado; referências restantes estão em legado/documentação histórica e foram inventariadas.
- Nenhum teste abriu cliente de exchange para enviar ordem.

## Regressão quantitativa

Não foram alterados thresholds, scores, estratégias, regras de entrada/saída, risk limits, SL, TP, trailing, sizing, indicadores, pesos, modelos ou lógica de backtest.

O ajuste da Breakout/OKX registrado antes do início deste Sprint 1 foi preservado e não faz parte das alterações de canonização.

## Blockers e decisão humana necessária

**BLOCKER-PG:** PostgreSQL está declarado em Compose/Render, mas o risk/execution gate usa consultas SQLite. Não habilitar `GENESIS_DATABASE_BACKEND=postgres` em produção até criar e testar o adaptador completo. Evidência: `Robo/execution/execution_gate.py` e `Robo/utils/risk_guard.py` abrem `sqlite3` diretamente. Impacto: risco de ler uma fonte enquanto gravar em outra. Ação recomendada: próximo trabalho isolado de persistência, com migração controlada e teste sem exchange.

**BLOCKER-RUNTIME:** nenhum processo Genesis foi encontrado na inspeção final; não foi possível confirmar o comando realmente em execução. Impacto: worker canônico foi confirmado por configuração, não por processo vivo. Ação: healthcheck controlado antes de qualquer restart.

**ROTATION_REQUIRED:** arquivos de chaves e `.env` podem conter credenciais reais. Não foram removidos nem rotacionados. Ação humana: rotacionar na exchange/serviços e retirar os arquivos do armazenamento compartilhado.

**AGENT-AUTH:** as rotas GET da API de agentes ainda não têm autenticação uniforme, embora sejam read-only. Ação recomendada: autenticar a futura superfície de leitura antes do MCP.

## Rollback

Como não há Git, o rollback é por restauração dos arquivos listados abaixo a partir de backup/versão anterior e reversão manual dos flags/configuração. Não apagar bancos.

1. preservar cópia dos arquivos modificados;
2. restaurar `genesis_runtime.py`, middleware, resolvers e scripts de ops para a versão anterior;
3. remover as novas variáveis apenas do ambiente de teste, sem tocar nos `.env` ativos;
4. executar `py_compile` e healthcheck em modo sem exchange;
5. somente depois avaliar reinício controlado.

## Controle final obrigatório

- Arquivos modificados: `Robo/banco_trade/db_trades.py`, `Robo/banco_trade/db_manager.py`, `Robo/ui_backend/repository.py`, `Robo/execution/execution_gate.py`, `Robo/utils/risk_guard.py`, `Agente_IA/api_agente_ia.py`, `Robo/trading_bot_geneses.py`, `Robo/db_manager.py`, `Agente_IA/analyze_trade.py`, `docker-compose.genesis.yml`, `Robo/.env.ui`, launchers/supervisor/ops e `.env.auth.example`.
- Arquivos criados: `GENESIS_SPRINT1_CANONICAL_BASELINE.md`, este relatório, `GENESIS_COMPONENT_INVENTORY.md`, `GENESIS_SECURITY_FINDINGS.md`, `GENESIS_MCP_READINESS.md`, `GENESIS_CAPABILITY_REGISTRY.yaml`, `Robo/genesis_runtime.py`, `.gitignore`, `Robo/.env.example`.
- Arquivos removidos: nenhum.
- Bancos alterados: nenhum.
- Schemas alterados: nenhum.
- Dependências adicionadas: nenhuma.
- Variáveis novas: `GENESIS_DATABASE_BACKEND`, `GENESIS_SQLITE_PATH`, `ENABLE_AGENT_API_TRADING`, `ALLOW_EXCHANGE_WRITE`, `GENESIS_AGENT_API_TOKEN`, `GENESIS_AGENT_ALLOWED_ORIGINS`, `GENESIS_ENVIRONMENT`, `GENESIS_VERSION`.
- Endpoints alterados: proteção de mutações na API Agente IA; CORS restrito. API UI de leitura não recebeu mutação.
- Comportamento quantitativo alterado: **NÃO**.
- Ordens reais enviadas: **NÃO**.
- Deploy realizado: **NÃO**.
- Testes executados: compilação, import isolado de runtime, validação JSON e inspeções read-only.
- Testes aprovados: os testes acima; não houve teste de exchange real.
- Riscos restantes: blocker PostgreSQL, credenciais a rotacionar, processo ativo não confirmado, autenticação futura de GET da API de agentes, duplicações/legados.

## Critério de conclusão

O Sprint 1 está concluído no escopo seguro de canonização local e isolamento da API paralela. A promoção para PostgreSQL ou qualquer reativação de escrita da API de agentes não faz parte desta conclusão e exige tratamento dos blockers acima.
