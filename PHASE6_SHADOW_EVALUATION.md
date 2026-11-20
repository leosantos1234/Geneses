# Sprint 7 — Phase 6: Shadow Evaluation

The Phase 6 evaluator is on-demand and pure. It evaluates a persisted
Commander decision against an explicit historical price window and horizon.

- Decision source: `COMMANDER_DECISION_CREATED` lineage payloads.
- Price source: caller-supplied historical observations; no canonical candle
  table exists in the current repository.
- Persistence: none; no migration or new table is required.
- Cutoff: only `decision_timestamp <= price_timestamp <= decision_timestamp + horizon`.
- Directional scoring applies only to `CANDIDATE_LONG` and `CANDIDATE_SHORT`.
- `NO_ACTION`, `WATCH`, and `INSUFFICIENT_DATA` remain non-directional.
- Missing values serialize as the exact `DATA_NOT_AVAILABLE` marker.
- The evaluator has no exchange, LLM, network, lineage-write, or business-state
  dependencies.
