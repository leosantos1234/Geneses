BEGIN;

-- Additive runtime provenance only.  Existing rows remain untouched and are
-- intentionally not backfilled because their execution context is ambiguous.
ALTER TABLE breakout_evaluations
    ADD COLUMN IF NOT EXISTS candle_open_time TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS candle_close_time TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS candle_closed BOOLEAN,
    ADD COLUMN IF NOT EXISTS analysis_key TEXT,
    ADD COLUMN IF NOT EXISTS evaluation_context TEXT,
    ADD COLUMN IF NOT EXISTS run_id TEXT,
    ADD COLUMN IF NOT EXISTS commander_invoked BOOLEAN;

ALTER TABLE breakout_evaluations
    DROP CONSTRAINT IF EXISTS breakout_evaluations_record_provenance_check;

ALTER TABLE breakout_evaluations
    ADD CONSTRAINT breakout_evaluations_record_provenance_check
    CHECK (record_provenance IN (
        'PRODUCTION_RUNTIME', 'BACKTEST_RUNTIME', 'TEST', 'ACCEPTANCE',
        'SYNTHETIC', 'UNKNOWN'
    ));

CREATE INDEX IF NOT EXISTS breakout_evaluations_analysis_key_idx
    ON breakout_evaluations(analysis_key);
CREATE INDEX IF NOT EXISTS breakout_evaluations_context_run_idx
    ON breakout_evaluations(evaluation_context, run_id);

INSERT INTO genesis_migration_runs(migration_key, source_path, status, details)
VALUES (
    'breakout-runtime-provenance-v1',
    'Robo/ops/migrations/010_breakout_runtime_provenance.sql',
    'completed',
    '{"additive":true,"historical_backfill":false,"historical_delete":false}'::jsonb
)
ON CONFLICT (migration_key) DO UPDATE
SET completed_at=NOW(), status=EXCLUDED.status, details=EXCLUDED.details;

COMMIT;
