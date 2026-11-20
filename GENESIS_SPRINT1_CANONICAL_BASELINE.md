# Gênesis Quant — Baseline do Sprint 1

Data da fotografia: 2026-10-07  
Raiz auditada: `C:\Users\NPO\Desenvolvimento\genesis`

## Controle da baseline

- Git: não detectado na raiz nem em subdiretório conhecido; branch, commit-base e diff não puderam ser registrados.
- Processos Genesis/Python/Uvicorn detectáveis: nenhum na consulta realizada.
- Portas Genesis/PostgreSQL consultadas: nenhuma em estado `Listen` na consulta realizada.
- Bases, arquivos `.env`, backups e configurações foram somente lidos.
- Não foram enviados, cancelados ou modificados pedidos reais nesta fotografia.
- Alterações anteriores a este Sprint 1: o ajuste da entrada Breakout/OKX realizado antes deste sprint permanece preexistente e foi preservado; o Sprint 1 não o reescreve.

## Entrypoints e infraestrutura observados

| Item | Evidência | Estado de baseline |
|---|---|---|
| Worker candidato | `Robo/trading_bot_geneses.py`, `Robo/Dockerfile.worker`, `render.yaml` | candidato canônico; confirmação operacional limitada porque não havia processo ativo |
| API UI | `Robo/api_ui.py`, `Robo/Dockerfile.api` | leitura protegida por bearer; CORS configurável |
| API Agente IA | `Agente_IA/api_agente_ia.py`, scripts `Robo/tela/iniciar_cinco_ativos.bat` | paralelo; rotas de ação e CORS amplo |
| Exchange | `Robo/exchange/factory.py`, perfis `.env-*` | Bybit/OKX disponíveis; perfil efetivo depende do ambiente |
| Banco declarado no deploy | `GENESIS_DATABASE_URL` em Compose/Render | PostgreSQL pretendido |
| Banco populado principal | `Robo/trades.db` | SQLite com 1.443 trades, 115.632 previsões LSTM e 670.612 eventos |

## Bases encontradas

| Caminho | Tabelas e contagens observadas | Classificação preliminar |
|---|---|---|
| `Robo/trades.db` | `trades` 1.443; `predictions_lstm` 115.632; `mr_fsm_state` 5; `log_events` 670.612 | operacional SQLite candidato |
| `Agente_IA/trades.db` | `trades` 1.675; `trade_lifecycle` 1; `log_events` 1.036 | histórico/agente legado |
| `Agente_IA/logs/decisoes_ia.db` | `decisoes_ia` 410 | decisões do agente IA |
| `Robo/trade_quality/trade_quality.db` | `trade_quality_samples` 131 | dataset/qualidade ML |
| `Robo/banco_trade/trades.db` | tabelas vazias | legado/auxiliar |
| `Robo/research/trades.db` | `log_events` 2 | pesquisa/diagnóstico |
| `Robo/trades1.db` | `trades` 2 | legado/diagnóstico |
| `trades.db` | `log_events` 14 | banco relativo/indeterminado |
| `Agente_IA/Backup/*` | cópias de decisões/trades | backup; não canônico |

Nenhum banco foi migrado, apagado, compactado ou alterado.

## Configuração e segurança observadas

- `docker-compose.genesis.yml` contém valores locais de PostgreSQL, senha administrativa e segredo de autenticação diretamente no arquivo; são tratados como configuração insegura e exigem substituição por ambiente de implantação.
- Há múltiplos perfis `.env-*` em `Robo`, `Agente_IA`, `automl` e backups. Os valores não são reproduzidos neste documento.
- Foram encontrados arquivos de chaves/credenciais no root e no projeto. Não houve rotação nem remoção nesta baseline.
- `Agente_IA/api_agente_ia.py` usa `allow_origins=["*"]` e inclui rotas de criação/cancelamento/fechamento/ajuste/inicialização.
- `Robo/api_ui.py` usa `UI_CORS_ORIGINS` com lista explícita e autenticação bearer nas consultas sensíveis.
- `Robo/ops/services.json` e comandos de restart ainda contêm caminhos `C:\Users\emgepron\Documents\robo`, classificados como obsoletos/broken para esta raiz.

## Limitações e stop conditions

- Como não havia processo ativo, a autoridade operacional foi inferida por entrypoints, Docker, Render, scripts, imports e logs; não foi possível confirmar o comando efetivamente em execução.
- Como não há Git, qualquer alteração será rastreada pelos relatórios e hashes/arquivos, sem commit ou branch dedicada.
- A existência de PostgreSQL configurado não prova que ele esteja disponível ou que seja a fonte efetiva em runtime.
- Nenhuma alteração que possa enviar ordem real será usada como teste; validações de escrita serão mocks/estáticas.
## Atualização Sprint 2

O PostgreSQL existente foi localizado e promovido a fonte operacional canônica. O SQLite `Robo/trades.db` permanece somente como snapshot legado verificável; a migração aditiva e a reconciliação estão documentadas em `GENESIS_SPRINT2_POSTGRESQL.md`.
