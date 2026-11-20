BEGIN;

ALTER TABLE breakout_evaluations
    ADD COLUMN IF NOT EXISTS breakout_timeframe TEXT,
    ADD COLUMN IF NOT EXISTS breakout_candle_open_time TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS breakout_candle_close_time TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS breakout_candle_closed BOOLEAN,
    ADD COLUMN IF NOT EXISTS canonical_regime_timeframe TEXT,
    ADD COLUMN IF NOT EXISTS canonical_regime_candle_open_time TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS canonical_regime_candle_close_time TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS breakout_evaluations_1m_lineage_idx
    ON breakout_evaluations(symbol, breakout_timeframe, breakout_candle_open_time);

INSERT INTO genesis_migration_runs(migration_key, source_path, status, details)
VALUES (
    'breakout-1m-lineage-v1',
    'Robo/ops/migrations/015_breakout_1m_lineage.sql',
    'completed',
    '{"additive":true,"prospective_only":true,"historical_backfill":false,"execution_authority":"NONE"}'::jsonb
)
ON CONFLICT (migration_key) DO UPDATE
SET completed_at=NOW(), status=EXCLUDED.status, details=EXCLUDED.details;

COMMIT;
