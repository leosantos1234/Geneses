# Sprint 5 Corrections Status

## Status Atual

**Fase:** Corrigindo 7 pontos factuais antes de Phases 31-45

### ✅ CORREÇÃO 1: LSTM PostgreSQL Confirmada
- **POSTGRES_LSTM_TABLE_EXISTS:** True
- **POSTGRES_LSTM_ROW_COUNT:** 116,167
- **POSTGRES_LSTM_SCHEMA:** 13 colunas (id, trade_id, symbol, model_name, timestamp, predicted_price, real_price, direction_predicted, direction_real, error_percent, confidence_score, window_size, sequence_input)
- **POSTGRES_LSTM_SOURCE_CONFIRMED:** YES
- **Ação:** Removido arquivo 005_predictions_lstm.sql (redundante)

### 🔄 CORREÇÃO 2: Namespace MCP
- **Ação Completada:** Renomeado `Robo/mcp` → `Robo/genesis_mcp`
- **Próxima Ação:** Atualizar todos os imports em:
  - Robo/genesis_mcp/server.py (linha 31, 43, 45)
  - Robo/genesis_mcp/v2/lineage.py (linha 18-22)
  - Robo/genesis_mcp/v2/lstm.py (linha 9-11)
  - Robo/genesis_mcp/v2/inventory.py (linha 8-10)
  - Robo/genesis_mcp/tests/*.py (linha 1)

### ⏳ CORREÇÃO 3: Paginação list_decisions
**Problema Atual:**
```python
total = len(rows)
has_more = False
```

**Solução:** Implementar contagem real com LIMIT + OFFSET usando lineage_list_decisions canônico

### ⏳ CORREÇÃO 4: PostgreSQL read-only
**Problema Atual:**
- `get_lstm_predictions` abre conexão sem read-only flag
- Falta verificação em outras V2 tools

**Solução:** Usar `connect_operational_database(read_only=True)` onde disponível, ou `SET default_transaction_read_only = on` em psycopg

### ⏳ CORREÇÃO 5: Model Inventory Status
**Problema Atual:**
- Apenas AVAILABLE retornado
- Sem diferenciação entre ACTIVE_IN_LIVE, INACTIVE, UNKNOWN

**Solução:** Implementar verificação de evidência operacional (ex: arquivo mencionado em config, ou timestamp recente)

### ⏳ CORREÇÃO 6: Rollback sem Git
**Problema Atual:**
- Documentação menciona `git revert`, `git checkout`, `git history`

**Solução:** 
- Remover todas referências a Git em SPRINT5_* e MCP_*.md
- Documentar rollback via backup de arquivos

### ⏳ CORREÇÃO 7: Integração MCP Real
**Problema Atual:**
- Teste original falhou com `ModuleNotFoundError: No module named 'mcp.server.fastmcp'`
- Substituído por test_integration_simplified.py que não valida STDIO/JSON-RPC real

**Solução:**
- Depois de corrigir namespace, restaurar teste real que:
  - Importa FastMCP
  - Registra 10 tools
  - Valida tools/list via JSON-RPC
  - Testa chamadas reais

---

## Próximos Passos

1. Atualizar todos imports `from mcp.` para `from genesis_mcp.`
2. Reescrever get_lstm_predictions com schema real
3. Implementar paginação real em list_decisions
4. Adicionar read-only=True em todas conexões
5. Atualizar Model Inventory com status real
6. Remover referências a Git na documentação
7. Restaurar teste MCP real

**Esperado:** Todas as 7 correções concluídas, testes passando, pronto para Phases 31-45.

---

**Atualizado:** 2026-10-07  
**By:** Claude Haiku 4.5
