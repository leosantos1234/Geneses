# Sprint 6 — Checkpoint 2.5C — Blockers Identificados

**Data:** 2026-10-07  
**Status:** ⚠️ BLOCKERS ENCONTRADOS — Requer ação antes de Phase 3

---

## Blockers Reais Identificados

### 1. MCP CLIENT DISPATCH ⚠️ BLOCKER

**Problema:**
- MCPReadOnlyClientAdapter atual usa `call_tool()` do módulo interno
- Isso é internal function dispatch, NÃO cliente MCP via STDIO
- Skills devem ser clientes STDOUT do MCP, não consumidoras de funções internas

**Contrato Requerido:**
```python
MCP_TRANSPORT = STDIO (via FastMCP)
MCP_OFFICIAL_SDK_CLIENT = YES (Python MCP SDK)
INTERNAL_TOOL_DISPATCH_FROM_SKILLS = NO (absolute zero)
```

**Solução Requerida (Fase 3):**
- Criar verdadeiro cliente STDIO usando `mcp.ClientSession`
- Skills conectam via STDIO ao MCP server
- Não chamar funções internas do servidor
- Teste real: executar get_system_health via STDIO sem escriba

**Severity:** CRÍTICA — Violação arquitetural

---

### 2. ERROR NORMALIZATION NÃO INTEGRADO ⚠️ BLOCKER

**Problema:**
- `normalize_mcp_error()` existe como função isolada
- BaseSkill não usa
- Adapter não usa
- Executor não usa

**Resultado Atual:**
- Erros MCP passam sem normalização
- Comportamento inconsistente entre skills
- except Exception: pass ainda possível

**Solução Requerida (Fase 3):**
- Integrar `normalize_mcp_error()` em BaseSkill.call_mcp_tool()
- Nunca mascarar DATABASE_UNAVAILABLE, INTERNAL_ERROR, READ_ONLY_VIOLATION
- NOT_FOUND esperado → data_gap
- Teste: cada tipo de erro resulta em SkillResult.status correto

**Severity:** CRÍTICA — Falta integração

---

### 3. EXECUTION AUDIT NÃO IMPLEMENTADO ⚠️ BLOCKER

**Problema:**
- Auditoria definida no contrato mas não implementada
- Nenhuma Skill registra skill_run_id, tool_call_count, evidence_count

**Requerido:**
```python
skill_run_id (UUID único)
skill_id
skill_version
started_at
finished_at
status
tools_called (array)
tool_call_count
evidence_count
error_code
```

**Solução Requerida (Fase 3):**
- Implementar em BaseSkill.__init__() / execute()
- Logging estruturado local (não DB)
- Teste: skill_run_id varia entre execuções

**Severity:** ALTA — Falta observabilidade

---

### 4. MANIFEST CONTENT NÃO VALIDADO ⚠️ BLOCKER

**Problema:**
- Testes apenas verificam field existence
- `procedure = ""` (vazio) é aceito
- `preconditions = []` (vazio) é aceito
- Nenhuma validação de conteúdo significativo

**Requerido:**
```
procedure != ""
preconditions não vazio
success_criteria não vazio
failure_conditions não vazio
abort_conditions não vazio
evidence_requirements não vazio
limitations não vazio
```

**Solução Requerida (Fase 3):**
- Expandir `SkillManifest.validate()` para conteúdo
- Preencher fields de verdade nas 4 skills
- Teste: manifest.validate() falha se procedure = ""

**Severity:** ALTA — Falta validação semântica

---

### 5. AUTOPSY HISTORICAL RISK ⚠️ BLOCKER

**Problema:**
- Skill chama get_risk_status() (estado atual)
- Usa como "risk_at_trade_time" (histórico)
- Afirma "Risk Guard was blocking" sem evidência histórica

**Requerido:**
```python
# Nunca
"Risk Guard was blocking at trade time"

# Sempre usar
current_risk_snapshot
HISTORICAL_RISK_STATE_NOT_AVAILABLE (se ausente)
```

**Solução Requerida (Fase 3):**
- Remover afirmação histórica
- Usar decision_trace para estado histórico se disponível
- Usar named tuple / enum para distinguir snapshots

**Severity:** MÉDIA — Semântica incorreta

---

### 6. RISK THRESHOLD 30% ⚠️ BLOCKER

**Problema:**
- Risk Review cria regra inventada: concentration > 30% = high risk
- Sem base canônica no Risk Guard

**Requerido:**
```python
# Remover
if concentration > 30%:
    high risk

# Manter
symbol, exposure, percentage (descritivo)
```

**Solução Requerida (Fase 3):**
- Remover classificação de risco
- Manter cálculos percentuais
- Usar somente Risk Guard officiallimits quando disponíveis

**Severity:** MÉDIA — Cria novo threshold arbitrário

---

### 7. LSTM NULL HANDLING ⚠️ BLOCKER

**Problema:**
- Ignora None em direction_predicted
- Conta None == None como acerto
- Não ordena por timestamp
- Não verifica sample size para validez

**Requerido:**
```python
ignore(direction_predicted == None)
ignore(direction_real == None)
never_count(None == None as match)
sort_by(timestamp)
sample_valid >= 10
```

**Solução Requerida (Fase 3):**
- Implementar filtros nulos
- Ordenação temporal obrigatória
- Sample size mínimo para conclusions

**Severity:** MÉDIA — Cálculos potencialmente inválidos

---

### 8. CONFIDENCE ARBITRÁRIA ⚠️ BLOCKER

**Problema:**
- Usa números: 0.95, 0.90, 0.70
- Sem regra determinística

**Requerido:**
```python
HIGH / MEDIUM / LOW (enum)
```

**Solução Requerida (Fase 3):**
- Implementar enum
- Definir regra objetiva (documentada)
- Testes que verificam regra

**Severity:** BAIXA — UX issue

---

### 9. OPERATIONAL LANGUAGE ⚠️ BLOCKER

**Problema:**
- Skills usam "Continue normal operations"
- Isso é comando operacional, não análise

**Requerido:**
```python
# Remover
"Continue normal operations"
"Address risk blocks"

# Usar
"No blocking condition observed"
```

**Solução Requerida (Fase 3):**
- Remover frases operacionais
- Usar linguagem puramente analítica

**Severity:** BAIXA — Semântica

---

## Resumo de Blockers

| Blocker | Severity | Type | Phase 3 Action |
|---------|----------|------|-----------------|
| MCP STDIO Dispatch | CRÍTICA | Arquitetura | Reimplementar adapter |
| Error Normalization Integração | CRÍTICA | Integração | Integrar em BaseSkill |
| Execution Audit | ALTA | Implementação | Implementar logging |
| Manifest Content Validation | ALTA | Validação | Expandir validate() |
| Autopsy Historical | MÉDIA | Semântica | Remover afirmação hist |
| Risk Threshold 30% | MÉDIA | Semântica | Remover classificação |
| LSTM Null Handling | MÉDIA | Implementação | Filtros nulos + sort |
| Confidence Arbitrária | BAIXA | UX | Implementar enum |
| Operational Language | BAIXA | Semântica | Remover frases operacionais |

---

## Decisão de Checkpoint 2.5C

### Status: ⏸️ **PARTIAL PASS**

**Itens PASS:**
- ✅ Files layout
- ✅ Registry bootstrap
- ✅ Manifest structure
- ✅ Authority/Side effects

**Itens BLOCKER (não iniciar Fase 3):**
- ⚠️ MCP STDIO Dispatch (critical)
- ⚠️ Error Normalization Integration (critical)
- ⚠️ Execution Audit (high)
- ⚠️ Manifest Content Validation (high)
- ⚠️ 5 correções semânticas (medium/low)

---

## Recomendação

**NÃO iniciar Phase 3 Test Suite enquanto os 2 blockers críticos não forem resolvidos.**

**Bloqueadores Críticos:**
1. **MCP STDIO Dispatch** — Skills devem ser clientes STDIO reais, não consumidoras de funções internas
2. **Error Normalization Integration** — Integrar normalization no fluxo de execução

**Bloqueadores Altos:**
3. **Execution Audit** — Implementar logging de execução
4. **Manifest Content Validation** — Validar conteúdo significativo dos manifests

**Recomendação de Sequência Phase 3:**
1. Resolver 2 blockers críticos (1-2 horas)
2. Resolver 2 blockers altos (1 hora)
3. Aplicar 5 correções semânticas (1 hora)
4. Sanity check final (30 min)
5. Mock test suite completo (2-3 horas)
6. Smoke tests reais (1-2 horas)

---

**CHECKPOINT_2_5C_STATUS = PARTIAL_PASS**

**READY_FOR_PHASE_3_FULL_TEST_SUITE = NO**

Corrigir blockers críticos antes de iniciar testes completos.
