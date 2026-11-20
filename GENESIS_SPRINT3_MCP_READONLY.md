# Sprint 3 — MCP V1 read-only do Gênesis Quant

## Resumo executivo

Servidor MCP criado em `Robo/mcp/server.py`. Ele expõe exatamente cinco tools P0, usa STDIO local e consulta somente o PostgreSQL canônico por repositories/runtime/risk guard. Não possui tools de escrita, SQL arbitrário, file access genérico ou imports de execução.

## Arquitetura e dependência

Cliente MCP/Codex → `Robo/mcp/server.py` → `Robo/ui_backend/repository.py` / `Robo/genesis_runtime.py` / leitura DB-only do `Robo/utils/risk_guard.py` → `genesis_trading.public`. SDK oficial Python `mcp` v1.30.0, requisito `mcp>=1.28,<2`; transporte STDIO. A linha v1 foi fixada abaixo de 2 por compatibilidade com `FastMCP`.

## Tools

Exatamente: `get_system_health`, `list_open_positions`, `list_trades`, `get_trade_detail`, `get_risk_status`. Catálogo detalhado em `GENESIS_MCP_TOOL_CATALOG.md`.

Todas têm schemas Pydantic, validação de limites/strings/datas ISO-8601, paginação segura, respostas estruturadas, erros previsíveis, request_id e auditoria sanitizada. Nenhuma tem side effect.

## Read-only enforcement

O servidor não importa worker, exchange, execution gate ou commander. PostgreSQL é aberto com transação read-only, timeout de conexão de 5s, `statement_timeout=5000ms` e `lock_timeout=1000ms`. Risk status não consulta saldo da exchange, não escreve kill switch e não registra transição; usa apenas avaliação DB-only e leitura de exposição. O registry mantém ações perigosas como `mcp_exposed: false`.

## Testes

O harness JSON-RPC STDIO inicializou o servidor, descobriu exatamente cinco tools, invocou as cinco, validou filtro/paginação, `NOT_FOUND` e `INVALID_ARGUMENT`. Contagens PostgreSQL antes/depois permaneceram `(1449, 116167, 670612, 5)`. A varredura de segurança não encontrou SQLite, SQL arbitrário, imports perigosos ou caminhos de escrita. O cliente assíncrono oficial foi tentado e bloqueado por `WinError 5` ao criar pipes no Windows; o harness síncrono validou o protocolo real.

## Integração Codex

Setup em `GENESIS_MCP_CODEX_SETUP.md`: comando, diretório, STDIO, variáveis sem segredo, teste e remoção. O registro automático no host Codex não foi executado nesta sessão.

## Controle final

MCP SERVER CREATED: SIM  
MCP TRANSPORT: STDIO  
MCP CLIENT TESTED: harness JSON-RPC STDIO real; cliente assíncrono oficial limitado pelo Windows  
CODEX INTEGRATION TESTED: configuração pronta; registro no host não executado  
TOTAL MCP TOOLS: 5  
POSTGRESQL CANÔNICO UTILIZADO: `genesis_trading.public`  
DEPENDÊNCIA OPERACIONAL DE SQLITE: NÃO  
WRITE TO DATABASE: NÃO  
WRITE TO EXCHANGE: NÃO  
RISK STATE ALTERED: NÃO  
TRADING CONFIG ALTERED: NÃO  
COMPORTAMENTO QUANTITATIVO ALTERADO: NÃO  
ORDENS REAIS ENVIADAS: NÃO  
DEPLOY REALIZADO: NÃO  

## Riscos restantes

O worker pode aparecer como `unknown` quando não estiver ativo. O host Codex precisa registrar manualmente o comando STDIO descrito no setup. A dependência `mcp` deve ser instalada no ambiente final. O MCP permanece exclusivamente P0; não foram implementados Skills, RAG, memória, backtest, LSTM, sentimento, commander ou execution tools.
