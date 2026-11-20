BEGIN;

CREATE TABLE IF NOT EXISTS breakout_evaluations (
    breakout_evaluation_id TEXT PRIMARY KEY,
    timestamp_utc TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    regime TEXT,
    direction_candidate TEXT,
    stage_results JSONB NOT NULL DEFAULT '{}'::jsonb,
    primary_blocker TEXT,
    failed_stages JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw_breakout BOOLEAN NOT NULL DEFAULT FALSE,
    final_state TEXT NOT NULL,
    commander_decision_id TEXT,
    risk_status TEXT,
    risk_reason TEXT,
    risk_evaluated_at TIMESTAMPTZ,
    paper_order_id TEXT,
    paper_fill_id TEXT,
    paper_effect_id TEXT,
    record_provenance TEXT NOT NULL DEFAULT 'UNKNOWN'
        CHECK (record_provenance IN ('PRODUCTION_RUNTIME','TEST','ACCEPTANCE','SYNTHETIC','UNKNOWN')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS breakout_evaluations_time_idx
    ON breakout_evaluations(timestamp_utc, symbol, direction_candidate);
CREATE INDEX IF NOT EXISTS breakout_evaluations_blocker_idx
    ON breakout_evaluations(symbol, primary_blocker, timestamp_utc);
CREATE INDEX IF NOT EXISTS breakout_evaluations_commander_idx
    ON breakout_evaluations(commander_decision_id);

INSERT INTO genesis_migration_runs(migration_key, source_path, status, details)
VALUES ('breakout-observability-v1', 'Robo/ops/migrations/008_breakout_observability.sql', 'completed', '{"additive":true,"historical_backfill":false}'::jsonb)
ON CONFLICT (migration_key) DO UPDATE SET completed_at=NOW(), status=EXCLUDED.status, details=EXCLUDED.details;

COMMIT;
