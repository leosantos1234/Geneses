# Achados de Segurança — Sprint 1

## Correções aplicadas

| ID | Achado | Correção | Estado |
|---|---|---|---|
| SEC-001 | API de agentes tinha CORS `*` e rotas de ação sem barreira uniforme | origins explícitas; middleware fail-closed; `ENABLE_AGENT_API_TRADING=false` por padrão; `ALLOW_EXCHANGE_WRITE=false`; Bearer token exigido quando habilitado | CORRIGIDO no código |
| SEC-002 | Bancos eram escolhidos por defaults conflitantes/relativos | `Robo/genesis_runtime.py` centraliza backend e caminho; SQLite canônico local explícito; PostgreSQL exige backend e URL | CORRIGIDO parcialmente; PG pendente |
| SEC-003 | Gate usava `trades.db` relativo | gate usa caminho resolvido pela camada central | CORRIGIDO no caminho SQLite |
| SEC-004 | Caminhos de outra máquina em scripts e ops | scripts ativos e catálogo principal apontam à raiz atual | CORRIGIDO nos arquivos ativos tocados; legado permanece inventariado |
| SEC-005 | Startup não registrava baseline operacional | worker/API registram ambiente, worker, DB sem senha, exchange, flags e versão | CORRIGIDO |
| SEC-006 | Possíveis segredos em arquivos/Compose | `.gitignore` e exemplos seguros criados; nenhum segredo foi rotacionado/removido | MITIGADO; ROTATION_REQUIRED |

## Credenciais e segredos

Foram encontrados arquivos `.env*`, arquivos de chave no root e configurações de Compose/UI contendo campos de credencial. Os valores não foram reproduzidos nem gravados neste relatório.

Classificação:

- arquivos `.env` ativos: **segredo real potencial**; não rotacionado;
- `docker-compose.genesis.yml`, `.env.ui` e `RENDER_SECRETS.md`: valores locais/exemplos com aparência de credencial; devem ser substituídos por secret manager;
- `CHAVE API_BINANCE.txt`, `Chave bybit.txt`, `Senha de entrada.txt`: **ROTATION_REQUIRED** até prova de que são inválidos;
- `.env.example`/`.env.auth.example`: exemplos, sem valores operacionais.

Não houve exposição de valores no terminal ou relatório. Não houve rotação automática.

## Rotas de agentes

| Endpoint/categoria | Método | Categoria | Auth atual | Estado de mutação | Exchange | Ação Sprint 1 |
|---|---:|---|---|---:|---:|---|
| `/test_session`, `/ordens_abertas`, `/status_sistema`, `/performance_sistema`, `/decisao_comandante_sistema`, `/dashboard/*`, `/ativos` | GET | READ_ONLY/diagnóstico | sem auth uniforme | não | pode consultar | manter fora da escrita; autenticação futura |
| `/analyze_market`, `/analyze_trade`, `/comandante_tipo_stop`, `/sugerir_spread_e_trailing`, `/trade_ia`, `/validar_com_modelo` | POST | ANALYSIS | sem auth dedicada | não por contrato | não deveria | allowlist de análise; CORS restrito |
| `/create_order`, `/create_order_adaptativa` | POST | TRADING_ACTION | antes: não uniforme; agora gate | sim | sim | bloqueado por default; flag + Bearer |
| `/close_trade`, `/manage_trade`, `/adjust_trailing`, `/ajustar_sl_tp` | POST | TRADING_ACTION/STATE_CHANGE | antes: não uniforme; agora gate | sim/pode ajustar | sim ou gestão | bloqueado por default; flag + Bearer |
| `/cancelar_ordem/{order_id}` | DELETE | TRADING_ACTION | antes: não uniforme; agora gate | sim | sim | bloqueado por default; flag + Bearer |
| `/tomar_decisao_comandante`, `/decidir_saida`, `/tomar_decisao`, `/iniciar_robo`, `/monitorar_ordem` | POST | DANGEROUS/STATE_CHANGE | antes: não uniforme; agora gate | sim/indireto | sim | bloqueado por default; flag + Bearer |

A proteção está no nível da aplicação e cobre métodos não-GET fora da allowlist analítica. Quando habilitado explicitamente, exige `ENABLE_AGENT_API_TRADING=true`, `ALLOW_EXCHANGE_WRITE=true` e `Authorization: Bearer` igual a `GENESIS_AGENT_API_TOKEN`.

## CORS

`Agente_IA/api_agente_ia.py` não usa mais `allow_origins=["*"]`. O default é localhost explícito e pode ser alterado por `GENESIS_AGENT_ALLOWED_ORIGINS`. A API UI já usava `UI_CORS_ORIGINS` com lista explícita.

## Blocker de persistência

O adaptador de risco/gate permanece SQLite-específico. Por isso, PostgreSQL não é tratado como ativo canônico automaticamente. Para uma implantação PostgreSQL, ainda é obrigatório criar/adaptar o contrato de consultas e validar o gate antes de habilitar `GENESIS_DATABASE_BACKEND=postgres`.

