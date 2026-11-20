BEGIN;

-- Prospective-only shadow funnel observability.  Existing V1 rows are left
-- untouched and remain excluded from V2 funnel analysis.
ALTER TABLE shadow_decision_telemetry
    ADD COLUMN IF NOT EXISTS telemetry_version TEXT,
    ADD COLUMN IF NOT EXISTS current_stage_results JSONB,
    ADD COLUMN IF NOT EXISTS shadow_stage_results JSONB,
    ADD COLUMN IF NOT EXISTS current_stage_reached JSONB,
    ADD COLUMN IF NOT EXISTS shadow_stage_reached JSONB,
    ADD COLUMN IF NOT EXISTS current_stage_passed JSONB,
    ADD COLUMN IF NOT EXISTS shadow_stage_passed JSONB,
    ADD COLUMN IF NOT EXISTS current_first_failed_stage TEXT,
    ADD COLUMN IF NOT EXISTS shadow_first_failed_stage TEXT,
    ADD COLUMN IF NOT EXISTS current_final_blocker TEXT,
    ADD COLUMN IF NOT EXISTS shadow_final_blocker TEXT,
    ADD COLUMN IF NOT EXISTS shadow_raw_candidate BOOLEAN,
    ADD COLUMN IF NOT EXISTS shadow_final_candidate BOOLEAN;

CREATE INDEX IF NOT EXISTS shadow_decision_telemetry_v2_idx
    ON shadow_decision_telemetry(symbol, timeframe, candle_open_time)
    WHERE telemetry_version = 'V2';

INSERT INTO genesis_migration_runs(migration_key, source_path, status, details)
VALUES (
    'shadow-funnel-telemetry-v2',
    'Robo/ops/migrations/014_shadow_funnel_telemetry_v2.sql',
    'completed',
    '{"additive":true,"prospective_only":true,"historical_backfill":false,"execution_authority":"NONE"}'::jsonb
)
ON CONFLICT (migration_key) DO UPDATE
SET completed_at=NOW(), status=EXCLUDED.status, details=EXCLUDED.details;

COMMIT;
