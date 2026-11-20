BEGIN;

CREATE TABLE IF NOT EXISTS commander_decisions (
    commander_decision_id TEXT PRIMARY KEY,
    timestamp_utc TIMESTAMPTZ NOT NULL,
    symbol TEXT,
    decision_type TEXT NOT NULL CHECK (decision_type IN ('NO_ACTION','WATCH','CANDIDATE_LONG','CANDIDATE_SHORT','INSUFFICIENT_DATA')),
    strategy_id TEXT,
    strategy_name TEXT,
    confidence TEXT,
    reference_price NUMERIC(38,18),
    market_regime TEXT,
    commander_version TEXT NOT NULL,
    source_context_id TEXT,
    decision_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    rationale_metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS commander_decisions_timestamp_idx ON commander_decisions(timestamp_utc, commander_decision_id);
CREATE INDEX IF NOT EXISTS commander_decisions_type_idx ON commander_decisions(decision_type, timestamp_utc);

INSERT INTO genesis_migration_runs(migration_key, source_path, status, details)
VALUES ('commander-decisions-v1', 'Robo/ops/migrations/007_commander_decisions.sql', 'completed', '{"additive":true,"historical_backfill":false}'::jsonb)
ON CONFLICT (migration_key) DO UPDATE SET completed_at=NOW(), status=EXCLUDED.status, details=EXCLUDED.details;

COMMIT;
