# Sprint 5 — Checkpoint 1: Tool Readiness Analysis

**Data:** 2026-10-07  
**Status:** PRÉ-IMPLEMENTAÇÃO

## Baseline Confirmado

- PostgreSQL canonical: SIM
- SQLite operational: NÃO
- MCP V1 tools: 5 (operacionais)
- Fail-closed: Risk e Execution confirmados
- Git: NÃO disponível
- Workspace: C:\Users\NPO\Desenvolvimento\genesis

## Tool 6: get_decision_trace

**Fonte:** Decision Lineage Sprint 4  
**Função canônica:** `get_decision_trace(decision_id)` em `Robo/lineage/decision_lineage.py`  
**Entrada:** UUID decision_id  
**Saída esperada:**  
- decision_id, created_at, symbol, timeframe, strategy  
- final_status, trade_id, order_id  
- events ordenados (decision_trace + decision_events)  
- reason codes, componentes (Commander, Risk, Execution, Order, Fill)  

**Readiness:** ✅ PRONTO  
- Schema existente (decision_trace, decision_events)  
- Função interna existente  
- Read-only garantido  
- Sem side effects  

---

## Tool 7: list_decisions

**Fonte:** Decision Lineage Sprint 4  
**Função canônica:** `list_decisions()` em `Robo/lineage/decision_lineage.py`  
**Entrada:** limit, offset, symbol, strategy, status, reason_code, start_time, end_time, trade_id  
**Saída esperada:**  
- filters, count, has_more  
- list of decisions (sem eventos completos)  

**Readiness:** ✅ PRONTO  
- Schema existente  
- Função interna existente  
- Paginação necessária (implementar)  
- Read-only garantido  

---

## Tool 8: get_lstm_predictions

**Fonte:** PostgreSQL tabela predictions_lstm  
**Entrada:** symbol, limit, start_time, end_time, direction, min_confidence  
**Saída esperada:**  
- timestamp, symbol, predicted_direction, confidence  
- actual_direction, model_version, error_percent  

**Readiness:** ✅ PRONTO  
- Tabela existente em PostgreSQL  
- Sem inferência nova  
- Sem serviço LSTM a iniciar  
- Read-only garantido  

**Observação:** error_percent está NULL em todos os registros; retornar NULL sem inventar.

---

## Tool 9: get_model_inventory

**Fonte:** Descoberta de artefatos + metadados PostgreSQL  
**Entrada:** nenhuma  
**Saída esperada:**  
- model_name, model_type, version, artifact  
- status (available/active/inactive/unknown)  
- last_updated, features, source, operational_evidence  

**Readiness:** ⚠️ PARCIAL  
**Problema:** Não existe tabela model_metadata no PostgreSQL.  

**Opções:**
1. Escanear filesystem (Robo/modelos, Robo/estrategias/*.keras/.h5)  
2. Consultar logs operacionais  
3. Criar migração para model_metadata  

**Recomendação:** Opção 1 (sem criar nova tabela neste Sprint).  
Escanear filesystem com segurança (sem executar imports).

---

## Tool 10: get_sentiment_snapshot

**Fonte:** Historicamente OpenAI/TextBlob; cache e persistência?  
**Entrada:** nenhuma  
**Saída esperada:**  
- symbol, timestamp, score, classification  
- source, provider, age_seconds, status  

**Readiness:** ❌ BLOQUEADO  
**Problema:** Nenhuma persistência de sentiment detectada.  

**Observações:**
- `analise_sentimento.py` consulta OpenAI em tempo real  
- Sem cache persistido  
- Sem histórico armazenado  
- Nenhuma tabela PostgreSQL dedicada  

**Ação recomendada:** Marcar como NOT_READY.  
Documento futuro pode propor tabela sentiment_snapshot se necessário.

---

## Tool 11: get_commander_decision

**Fonte:** Decision Lineage Sprint 4 + banco paralelo legado  
**Entrada:** decision_id ou trade_id ou filtros (symbol, limit, start_time, end_time)  
**Saída esperada:**  
- commander_type (canonical_operational vs legado_ia)  
- timestamp, input_score, strategy, regime, threshold  
- allowed/blocked, reason_code, adjustments, decision_id  

**Readiness:** ✅ PRONTO  
- Decision Lineage contém decisões do Comandante  
- Schema pronto  
- Função `get_decision_trace()` retorna informações  
- Distinção entre canonical vs legado necessária  

---

## Tool 12: get_strategy_candidates

**Fonte:** Backtesting_geral.py + relatórios + cache?  
**Entrada:** symbol, limit, start_time  
**Saída esperada:**  
- symbol, strategy, regime, setup, score/metrics  
- rank, selected, timestamp, source  

**Readiness:** ❌ NÃO PRONTO  
**Problema crítico:** Nenhuma persistência canonical de candidatos.  

**Observações:**
- `backtesting_geral.py` calcula candidatos em runtime  
- Resultado pode estar em memória ou relatório JSON  
- Sem tabela PostgreSQL  
- Sem cache persistido entre execuções  

**Ação recomendada:** Marcar como NOT_READY.  
Exigiria nova migration + persistência de candidatos/ranking.

---

## Tool 13: get_backtest_report

**Fonte:** Relatórios em filesystem + PostgreSQL?  
**Entrada:** report_id ou strategy/symbol/período  
**Saída esperada:**  
- report, run_date, dataset/período  
- strategy, symbol, trades, return, win_rate  
- profit_factor, drawdown, costs, status, source  

**Readiness:** ⚠️ PARCIAL  
**Problema:** Não há índice/tabela de relatórios em PostgreSQL.  

**Observações:**
- Backtests produzem relatórios em `Robo/reports/`  
- Artefatos em arquivos `.md`, `.json`  
- Sem centralização em banco  

**Ação recomendada:** Marcar como NOT_READY.  
Seria necessário:
1. Escanear filesystem  
2. Parsear relatórios  
3. Ou criar tabela backtest_reports  

Não implementar neste Sprint.

---

## Resumo de Readiness

| Tool | Nome | Readiness | Ação |
|---|---|---|---|
| 6 | get_decision_trace | ✅ PRONTO | Implementar |
| 7 | list_decisions | ✅ PRONTO | Implementar |
| 8 | get_lstm_predictions | ✅ PRONTO | Implementar |
| 9 | get_model_inventory | ⚠️ PARCIAL | Implementar (filesystem scan) |
| 10 | get_sentiment_snapshot | ❌ BLOQUEADO | NOT_READY |
| 11 | get_commander_decision | ✅ PRONTO | Implementar |
| 12 | get_strategy_candidates | ❌ NÃO PRONTO | NOT_READY |
| 13 | get_backtest_report | ❌ NÃO PRONTO | NOT_READY |

## Total Tools Final Esperado

**V1:** 5  
**V2 READY:** 5 (ferramentas 6, 7, 8, 9, 11)  
**V2 NOT_READY:** 3 (ferramentas 10, 12, 13)  

**Total: 10 tools**

**Justificativa NOT_READY:**
- Sentiment: sem persistência canonical  
- Strategy Candidates: sem índice/cache persistido  
- Backtest Report: sem centralização em banco  

Nenhuma dessas impõe escrita operacional; todas são limitações de dados disponíveis.

---

## Próximas Fases

- Fase 5-20: Implementação das 5 tools V2 ready
- Fase 21-35: Testes
- Fase 36-42: Integração, regressão, segurança
- Fase 43-45: Documentação final

