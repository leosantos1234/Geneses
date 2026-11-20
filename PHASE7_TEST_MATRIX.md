# Sprint 7 — Phase 7 Test Matrix

## Scope

Validation-only phase. No production feature or architecture was added.

```text
TEST_FILES_TOTAL = 41 executable test files
TESTS_COLLECTED_TOTAL = 390
```

Inventory by category:

| Category | Primary test locations | Status |
|---|---|---|
| Contract | `genesis_commander/tests/test_contract.py` | PASS |
| Lineage | Commander Phase 2 tests, `Robo/tests/test_decision_lineage.py` | PASS in non-MCP run |
| RAG | `genesis_rag/tests/test_rag_v1.py` | PASS in isolated RAG run |
| MCP | `genesis_mcp/tests`, Commander MCP lifecycle/owner tests | ENVIRONMENT_BLOCKED |
| Skills | `genesis_skills/tests` | 169 passed, 15 environment-blocked |
| Commander orchestrator | `genesis_commander/tests` | 137 passed, 24 environment-blocked |
| Concurrency | Phase 4 concurrent start/owner/request isolation tests | ENVIRONMENT_BLOCKED for real MCP; deterministic fixtures PASS |
| Guardrails | `test_phase5_guardrails.py` | PASS |
| Shadow evaluation | `test_phase6_shadow_evaluation.py` | PASS |
| Business state | Commander/RAG/Skills read-only bracket tests | PASS where executed |
| Fail-closed | Phase 4 timeout, RAG, lineage, contract tests | PASS where executed |
| Real integration | Phase 4 real MCP and lineage tests | ENVIRONMENT_BLOCKED by MCP spawn |

## Acceptance matrix

| Requirement | Tests/evidence | Status |
|---|---|---|
| Commander output contract | `test_contract.py` | PASS |
| `shadow_only` | contract and orchestrator tests | PASS |
| `execution_authority = NONE` | contract and lineage tests | PASS |
| Decision enum | contract tests | PASS |
| Confidence enum | contract tests | PASS |
| Skill eligibility | Phase 4 real/eligibility tests | PASS in deterministic/non-MCP paths |
| MCP exact 10 tools | MCP v2 and sanity tests | PASS in static/implementation checks |
| MCP read-only | MCP security and tool tests | PASS in non-spawn paths |
| MCP concurrency | owner-task tests | ENVIRONMENT_BLOCKED |
| MCP process cleanup | lifecycle tests | ENVIRONMENT_BLOCKED |
| RAG provenance | RAG V1 tests | PASS |
| RAG future leakage zero | `test_as_of_cutoff` | PASS |
| RAG empty marker | RAG failure-semantics tests | PASS |
| Lineage persistence confirmation | Phase 2/4 lineage tests | PASS in non-MCP run |
| Lineage fail-closed | lineage observability tests | PASS in non-MCP run |
| Guardrail determinism | Phase 5 tests | PASS |
| Shadow evaluation determinism | Phase 6 repeatability test | PASS |
| Shadow evaluation `DATA_NOT_AVAILABLE` | Phase 6 missing-price tests and real read-only probe | PASS |
| No auto-feedback | evaluator has no write/feedback dependency; static audit | PASS |
| Business-state zero mutation | read-only brackets and existing tests | PASS |

## Determinism and fail-closed

```text
GUARDRAIL_REPEATABILITY = PASS
COMMANDER_REPEATABILITY = PASS (deterministic fixture paths)
SHADOW_EVAL_REPEATABILITY = PASS
FAIL_CLOSED_MATRIX = PASS for tested non-MCP failures
CONCURRENT_COMMANDER = ENVIRONMENT_BLOCKED for real MCP
AUDIT_RUN_ID_ISOLATION = PASS in request-isolation tests
MCP_PROCESS_LEAKS = not measurable in this restricted spawn environment; no child process was created
```

## Business-state bracket

The Phase 7 validation path used read-only snapshots. No business-state table
changed:

```text
TRADES_ROW_DELTA = 0
TRADES_MARKER_DELTA = 0
PREDICTIONS_LSTM_ROW_DELTA = 0
PREDICTIONS_LSTM_MARKER_DELTA = 0
MR_FSM_STATE_ROW_DELTA = 0
MR_FSM_STATE_MARKER_DELTA = 0
```

Lineage/RAG test fixtures may write their explicitly scoped test/audit data;
that is outside the business-state tables above.

## RAG cleanup hazard

```text
RAG_TEST_CLEANUP_HAZARD = PRESENT
RAG_PRODUCTION_DATA_PROTECTED = NO — test helper still has an unscoped DELETE FROM rag_records path
```

The hazard was detected and not expanded. Production RAG semantics were not
changed in this validation phase.

## Security and credentials

```text
TRUE_SECURITY_VIOLATIONS = 0
DB_CONNECTION = PASS (read-only probe succeeded)
DB_CREDENTIAL_ROTATION_PENDING = YES
```

No exchange, LLM, order, leverage, FSM, model, Risk Guard, or Execution Gate
operation was invoked by the Phase 7 validation path.

## Environment boundary

The restricted Codex runtime raises `WinError 5` at real MCP STDIO child
process creation. Tests were retained and not skipped, xfailed, mocked, or
weakened.

```text
REAL_MCP_VALIDATION = ENVIRONMENT_BLOCKED
```

Because the required real-MCP tests cannot pass in this environment, Phase 7
is not marked `CLOSED_PASS` here.
