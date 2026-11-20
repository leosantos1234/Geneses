# Sprint 6 — Skills Integration — Raio-X Arquitetural

**Data:** 2026-10-07  
**Status:** Fase 0 — Análise completada  

---

## Achados Principais

### MCP Existente ✅

**Localização:** `Robo/genesis_mcp/`

**10 Tools Confirmadas:**

V1 (5 ferramentas):
1. `get_system_health` — status do sistema
2. `list_open_positions` — posições abertas
3. `list_trades` — histórico de trades
4. `get_trade_detail` — detalhe de um trade
5. `get_risk_status` — estado de risco

V2 (5 ferramentas):
6. `get_decision_trace` — lineage de decisão
7. `list_decisions` — lista de decisões recentes
8. `get_lstm_predictions` — predições do modelo LSTM
9. `get_model_inventory` — modelos disponíveis
10. `get_commander_decision` — decisão do Commander

**Transporte:** STDIO (FastMCP)  
**Banco:** PostgreSQL canônico (`genesis_trading`)  
**Autoridade:** READ-ONLY (SET default_transaction_read_only = on)  

---

### Conceito de Skills

**Existente no Genesis:** NÃO (não há skills nativas)

**Conceitos similares encontrados:**
- `analise/` — análise histórica
- `Backtest/` — motor de backtest (não deve ser alterado)
- `automl/` — AutoML LSTM
- Nenhum executor de procedimentos analíticos declarativo

**Decisão:** Criar namespace novo: `Robo/genesis_skills/`

---

### Estrutura a Criar

```
Robo/genesis_skills/
├── __init__.py
├── manifest.py          # Validação de manifests
├── registry.py          # Registro de skills
├── executor.py          # Executor genérico de skills
├── errors.py            # Erros padronizados
├── skills/
│   ├── __init__.py
│   ├── trade_decision_autopsy.py
│   ├── risk_review.py
│   ├── lstm_diagnostics.py
│   ├── system_health_review.py
│   └── base.py          # Classe base Skill
├── tests/
│   ├── __init__.py
│   ├── test_manifest.py
│   ├── test_registry.py
│   ├── test_executor.py
│   ├── test_skill_autopsy.py
│   ├── test_skill_risk.py
│   ├── test_skill_lstm.py
│   ├── test_skill_health.py
│   └── test_security.py
└── catalog.yaml         # Catalog de skills instaladas
```

---

### Contrato de Skill

Cada Skill terá MANIFEST com:

```python
{
    "skill_id": "trade_decision_autopsy",
    "name": "Trade Decision Autopsy",
    "version": "1.0.0",
    "status": "AVAILABLE",
    "authority": "ANALYTICAL_READ_ONLY",
    "category": "analysis",
    
    "description": "...",
    "inputs": {...},
    "outputs": {...},
    
    "allowed_mcp_tools": [
        "get_trade_detail",
        "get_decision_trace",
        "get_commander_decision",
        "get_lstm_predictions",
        "get_risk_status",
        "list_trades"
    ],
    
    "preconditions": [...],
    "procedure": "...",
    "evidence_requirements": [...],
    "success_criteria": [...],
    "failure_conditions": [...],
    "abort_conditions": [...],
    "limitations": [...],
    "side_effects": "NONE"
}
```

---

### 4 Skills a Implementar

#### Skill 1: trade_decision_autopsy
- Objetivo: Explicar retrospectivamente um trade
- Inputs: `trade_id` ou (`symbol` + `timestamp_range`)
- Allowed tools: get_trade_detail, get_decision_trace, get_commander_decision, get_lstm_predictions, get_risk_status, (opt) list_trades

#### Skill 2: risk_review
- Objetivo: Análise de estado de risco
- Inputs: nenhum (analisa estado atual)
- Allowed tools: get_risk_status, list_open_positions, list_trades, get_system_health

#### Skill 3: lstm_diagnostics
- Objetivo: Diagnóstico de modelo LSTM
- Inputs: `model_name?`, `symbol?`, `lookback_periods?`
- Allowed tools: get_lstm_predictions, get_model_inventory, list_trades, get_trade_detail

#### Skill 4: system_health_review
- Objetivo: Diagnóstico operacional geral
- Inputs: nenhum (analisa estado)
- Allowed tools: get_system_health, get_risk_status, list_open_positions, list_decisions, get_model_inventory

---

### Executor Genérico

O executor deverá:
1. Carregar manifest
2. Validar inputs
3. Validar allowlist de tools
4. Executar procedimento (chamar tools em sequência)
5. Coletar evidência
6. Produzir output estruturado
7. Registrar auditoria

**Proteções:**
- Max tool calls: 20 (configurável)
- Detecção de recursão
- Timeout por skill
- Fail closed em qualquer erro

---

### Garantias de Segurança

- ✅ Skills não podem chamar tools fora de allowlist
- ✅ Skills não podem escrever no PostgreSQL
- ✅ Skills não podem chamar exchange APIs
- ✅ Skills não podem executar modelos
- ✅ Skills não podem enviar ordens
- ✅ Skills não podem alterar FSM/Risk Guard/Commander
- ✅ Skills não podem usar SQL direto
- ✅ Skills não podem usar shell
- ✅ Skills não podem chamar OpenAI/Claude/APIs externas

---

### Testes Obrigatórios

**Mock Tests:**
- Manifest validation
- Registry operations
- Executor
- Tool allowlist enforcement
- Unknown skill
- Disabled skill
- Invalid input
- Forbidden tool
- Tool NOT_FOUND
- Tool INTERNAL_ERROR
- MCP unavailable
- Timeout
- Max tool calls exceeded
- Security: no SQL, no shell, no exchange, no LLM calls

**Real Smoke Tests (contra PostgreSQL real):**
- trade_decision_autopsy (com trade real)
- risk_review (estado atual)
- lstm_diagnostics (predictions reais)
- system_health_review (estado atual)

**Before/After Database:**
- Capturar contagens antes
- Executar todos os testes
- Capturar contagens depois
- DELTA = 0 (zero writes)

---

### Não Alterar (Checklist)

- ❌ MCP tools (continue com 10, nenhuma nova)
- ❌ Autoridade operacional (continue ANALYTICAL_READ_ONLY)
- ❌ PostgreSQL schema
- ❌ Risk Guard
- ❌ Execution Gate
- ❌ Commander
- ❌ FSM
- ❌ Worker
- ❌ Exchange configuration
- ❌ Live trading settings
- ❌ Estratégias
- ❌ Thresholds, SL, TP, leverage
- ❌ Backtests
- ❌ Model weights

---

## Próximas Fases

1. **Fase 1:** Implementar estrutura mínima de Skills
2. **Fase 2:** Implementar 4 Skills
3. **Fase 3:** Testes unitários
4. **Fase 4:** Testes de segurança
5. **Fase 5:** Smoke tests reais
6. **Fase 6:** Documentação
7. **Fase 7:** Performance analysis

---

## Critérios de Conclusão

- ✅ 4/4 Skills implementadas
- ✅ Manifest validation = PASS
- ✅ Tool allowlist = PASS
- ✅ MCP tool count = 10
- ✅ Real smoke test = PASS
- ✅ Database writes = 0
- ✅ Exchange writes = 0
- ✅ Orders sent = 0
- ✅ Security review = PASS
- ✅ Tests failed = 0
- ✅ Documentation = COMPLETE

**Status Raio-X:** ✅ COMPLETO — Pronto para implementação
