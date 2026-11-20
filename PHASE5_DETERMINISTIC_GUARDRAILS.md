# Sprint 7 — Phase 5: Deterministic Guardrails

## Scope

Phase 5 adds a pure guardrail boundary for Commander 2.0 shadow outputs. It
does not call an exchange, MCP, persistence, `risk_guard`, or `execution_gate`.
Those components keep their existing ownership.

## Rules

- Invalid decision types and malformed evidence fail closed to `INSUFFICIENT_DATA`.
- Actionable decisions without evidence fail closed.
- Explicit `RISK_BLOCKED`, `RISK_GUARD_BLOCKED`, `EXECUTION_BLOCK`,
  `EXECUTION_BLOCKED`, or `DATABASE_UNAVAILABLE` signals downgrade a candidate
  to `NO_ACTION`.
- Operational verbs in the derived summary/rationale fail closed.
- The guardrail function is pure and does not mutate its input.
- `shadow_only = true` and `execution_authority = NONE` remain enforced by the
  existing Commander contract.

## Verification

`Robo/genesis_commander/tests/test_phase5_guardrails.py` covers normal-path
preservation, fail-closed behavior, explicit risk/execution blocks, malformed
inputs, operational language, and input immutability.
