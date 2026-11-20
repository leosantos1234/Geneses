# 7 Correções — Pronto para Execução Imediata

## ✅ Concluído

### 1. LSTM PostgreSQL Confirmada
- Verificado: 116,167 linhas em `predictions_lstm` (PostgreSQL)
- Schema: id, trade_id, symbol, model_name, timestamp, predicted_price, real_price, direction_predicted, direction_real, error_percent, confidence_score, window_size, sequence_input
- Ação: Migração 005_predictions_lstm.sql removida ✅

### 2. Namespace MCP Renomeado
- Ação: Robo/mcp → Robo/genesis_mcp ✅
- Imports em server.py atualizados de `from mcp.common` → `from genesis_mcp.common` ✅

---

## ⏳ Remanescente (7 Fixes)

### FIX 2.1: Atualizar imports em v2 modules
**Arquivos:** Robo/genesis_mcp/v2/lineage.py, lstm.py, inventory.py
**Mudança:** `from ..common import` → `from genesis_mcp.common import`

### FIX 2.2: Atualizar imports em tests
**Arquivos:** Robo/genesis_mcp/tests/*.py
**Mudança:** `from mcp.v2 import` → `from genesis_mcp.v2 import` (onde houver)

### FIX 3: Paginação Real em list_decisions
**Arquivo:** Robo/genesis_mcp/v2/lineage.py, função `list_decisions_impl`
**Linha atual (~150):**
```python
return ListDecisionsResponse(
    ...
    count=len(rows),
    total=len(rows),      # ERRADO: deve ser COUNT(*) real
    has_more=False,       # ERRADO: deve verificar se há mais registros
    decisions=rows
)
```
**Solução:**
```python
# Executar COUNT(*) separadamente ou usar LIMIT+1 strategy
cur.execute("SELECT COUNT(*) FROM decision_trace WHERE " + where_clause, params)
total = cur.fetchone()[0]
has_more = (offset + len(rows)) < total
```

### FIX 4: PostgreSQL read-only
**Arquivo:** Robo/genesis_mcp/v2/lstm.py, função `get_lstm_predictions_impl`
**Linha atual (~75):**
```python
conn = connect_operational_database()  # Sem read-only
```
**Solução:**
```python
conn = connect_operational_database(read_only=True)
# OU manualmente via psycopg:
conn = psycopg.connect(database_url())
conn.execute("SET default_transaction_read_only = on")
```

### FIX 5: Model Inventory Status
**Arquivo:** Robo/genesis_mcp/v2/inventory.py, função `get_model_inventory_impl`
**Mudança:** Adicionar campo `status` com valores:
- "AVAILABLE" — arquivo existe
- "ACTIVE_IN_LIVE" — evidência de uso (ex: mencionado em config, recentemente modificado)
- "INACTIVE" — arquivo antigo (>30 dias sem modificação)
- "UNKNOWN" — sem evidência suficiente

### FIX 6: Remover Git References
**Arquivos:** SPRINT5_FINAL_CHECKPOINT.md, MCP_API_REFERENCE.md, MCP_SECURITY.md
**Remover todas:**
- `git revert`
- `git checkout`
- `git history`
- "Revert via git history"

**Substituir por:**
- "Restaurar via backup de arquivos"
- "Reverter manualmente os arquivos modificados"

### FIX 7: Restaurar Teste MCP Real
**Arquivo:** Criar Robo/genesis_mcp/tests/test_mcp_server_real.py
**Conteúdo:**
```python
def test_mcp_server_import():
    """Test that MCP SDK can import without namespace collision."""
    from mcp.server.fastmcp import FastMCP
    assert FastMCP is not None

def test_genesis_mcp_server_starts():
    """Test that Genesis MCP server can be imported."""
    sys.path.insert(0, "Robo")
    from genesis_mcp import server
    assert server.mcp is not None
    assert len(server.TOOL_NAMES) == 10

def test_tools_registered():
    """Test that all 10 tools are registered."""
    from genesis_mcp import server
    expected = {
        "get_system_health", "list_open_positions", "list_trades",
        "get_trade_detail", "get_risk_status", "get_decision_trace",
        "list_decisions", "get_lstm_predictions", "get_model_inventory",
        "get_commander_decision"
    }
    assert set(server.TOOL_NAMES) == expected
```

---

## Prioridade

1. **CRÍTICO:** FIX 2.1, 2.2 — Sem isto, nada importa
2. **IMPORTANTE:** FIX 3, 4 — Funcionalidade correta
3. **IMPORTANTE:** FIX 7 — Validação real
4. **SECUNDÁRIO:** FIX 5, 6 — Enriquecimento

---

## Próximo Passo

Execute as 7 fixes acima, então:

```bash
pytest Robo/genesis_mcp/tests/ -v
# Esperado: 40+ tests passing (novo +4 para MCP real)
```

Depois prossiga para **Phases 31-45:**
- Regressão completa
- Integração MCP STDIO real
- Sign-off production

---

**Estimado:** 30-45 minutos para executar todas as 7 fixes
**Resultado:** Sprint 5 pronto para Phases 31-45
