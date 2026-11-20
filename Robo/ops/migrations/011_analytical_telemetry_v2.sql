BEGIN;

-- Prospective analytical telemetry only.  No historical rows are backfilled.
CREATE TABLE IF NOT EXISTS analytical_candle_telemetry (
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    candle_open_time TIMESTAMPTZ NOT NULL,
    candle_close_time TIMESTAMPTZ NOT NULL,
    open NUMERIC,
    high NUMERIC,
    low NUMERIC,
    close NUMERIC,
    volume NUMERIC,
    atr NUMERIC,
    atr_percent NUMERIC,
    volume_ratio NUMERIC,
    body_fraction NUMERIC,
    regime INTEGER CHECK (regime IN (1,2,3,4)),
    winning_rule TEXT,
    candle_closed BOOLEAN NOT NULL,
    record_provenance TEXT NOT NULL
        CHECK (record_provenance = 'PRODUCTION_RUNTIME'),
    feature_vector JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, timeframe, candle_open_time)
);

CREATE INDEX IF NOT EXISTS analytical_candle_telemetry_close_idx
    ON analytical_candle_telemetry(symbol, timeframe, candle_close_time);

ALTER TABLE breakout_evaluations
    ADD COLUMN IF NOT EXISTS feature_vector JSONB;

INSERT INTO genesis_migration_runs(migration_key, source_path, status, details)
VALUES (
    'analytical-telemetry-v2',
    'Robo/ops/migrations/011_analytical_telemetry_v2.sql',
    'completed',
    '{"additive":true,"prospective_only":true,"historical_backfill":false}'::jsonb
)
ON CONFLICT (migration_key) DO UPDATE
SET completed_at=NOW(), status=EXCLUDED.status, details=EXCLUDED.details;

COMMIT;
