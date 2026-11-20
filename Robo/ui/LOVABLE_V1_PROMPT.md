Crie um frontend web responsivo para o sistema de trading "Genesis Quant" com React + TypeScript.

Requisitos visuais:
- Estética profissional de mesa de operação (sem visual genérico).
- Tema escuro com alto contraste.
- Sidebar fixa + header.
- Estados de loading, vazio e erro em todas as telas.
- Design mobile-first e bom uso em desktop.

Rotas (V1):
1. `/dashboard`
2. `/positions`
3. `/trades`
4. `/trades/:tradeId`
5. `/settings/risk`

API base:
- `http://localhost:8010`

Endpoints:
- `GET /api/ui/health`
- `GET /api/ui/summary?days=7&symbol=SOLUSDT`
- `GET /api/ui/open-positions?symbol=SOLUSDT&limit=100`
- `GET /api/ui/trades?symbol=SOLUSDT&status=closed&limit=100&offset=0`
- `GET /api/ui/trades/{trade_id}`
- `GET /api/ui/filters`

Tela `/dashboard`:
- Cards: `pnl_total`, `win_rate_pct`, `total_trades`, `open_positions`.
- Filtros: `symbol`, `days`.
- Tabela resumida dos últimos trades.

Tela `/positions`:
- Tabela de posições abertas com colunas:
  - `timestamp`, `symbol`, `side`, `entry_price`, `stop_loss`, `take_profit`, `strategy`, `status`.
- Filtro por `symbol`.

Tela `/trades`:
- Tabela paginada com colunas:
  - `timestamp`, `symbol`, `side`, `entry_price`, `exit_price`, `profit_loss`, `strategy`, `status`.
- Filtros por `symbol` e `status`.
- Clique na linha abre `/trades/:tradeId`.

Tela `/trades/:tradeId`:
- Painel completo com todos os campos retornados do endpoint de detalhe.
- Blocos: Identificação, Entrada, Saída, Risco, Contexto (regime/tendencia), Auditoria.

Tela `/settings/risk` (somente UI na V1):
- Formulário para parâmetros de risco (não precisa persistir no backend nesta V1).
- Campos:
  - `TP_DECISION_ZONE_PCT`
  - `TP_FORCE_EXIT_ZONE_PCT`
  - `TP_EXTENSION_STEP_PCT`
  - `TRAILING_ACTIVATION_PCT`
  - `BE_ACTIVATION_PCT`
- Mostrar JSON preview do payload.

Requisitos técnicos:
- Criar camada de API client central.
- Usar React Query (ou equivalente) para cache/refetch.
- Tipagem forte dos objetos de resposta.
- Componentes reutilizáveis de tabela, card, badge de side (`buy/sell`) e status (`aberto/fechado`).

Entrega:
- Projeto pronto para rodar.
- Comandos de execução.

