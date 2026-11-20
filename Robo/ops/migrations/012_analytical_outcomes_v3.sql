BEGIN;

-- Prospective analytics only.  This table never participates in strategy,
-- risk, Commander, paper, or exchange execution.
CREATE TABLE IF NOT EXISTS analytical_event_outcomes (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    event_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    event_candle_open_time TIMESTAMPTZ NOT NULL,
    event_candle_close_time TIMESTAMPTZ,
    direction TEXT,
    regime INTEGER,
    blocker TEXT,
    feature_vector JSONB NOT NULL DEFAULT '{}'::jsonb,
    forward_return_1 NUMERIC,
    forward_return_3 NUMERIC,
    forward_return_6 NUMERIC,
    forward_return_12 NUMERIC,
    mfe_pct_1 NUMERIC,
    mae_pct_1 NUMERIC,
    mfe_pct_3 NUMERIC,
    mae_pct_3 NUMERIC,
    mfe_pct_6 NUMERIC,
    mae_pct_6 NUMERIC,
    mfe_pct_12 NUMERIC,
    mae_pct_12 NUMERIC,
    forward_return_1_available_at TIMESTAMPTZ,
    forward_return_3_available_at TIMESTAMPTZ,
    forward_return_6_available_at TIMESTAMPTZ,
    forward_return_12_available_at TIMESTAMPTZ,
    outcome_class TEXT,
    available_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (event_type, event_id),
    CHECK (direction IS NULL OR upper(direction) IN ('BUY','SELL','LONG','SHORT','UNKNOWN')),
    CHECK (outcome_class IS NULL OR outcome_class IN
           ('PROTECTED_CAPITAL','MISSED_OPPORTUNITY','NEUTRAL','INSUFFICIENT_DATA'))
);

CREATE INDEX IF NOT EXISTS analytical_event_outcomes_symbol_idx
    ON analytical_event_outcomes(symbol, event_candle_open_time);
CREATE INDEX IF NOT EXISTS analytical_event_outcomes_blocker_idx
    ON analytical_event_outcomes(symbol, blocker, regime);
CREATE INDEX IF NOT EXISTS analytical_event_outcomes_available_idx
    ON analytical_event_outcomes(available_at);

CREATE INDEX IF NOT EXISTS breakout_evaluations_production_candle_idx
    ON breakout_evaluations(record_provenance, candle_open_time, symbol, timeframe);

INSERT INTO genesis_migration_runs(migration_key, source_path, status, details)
VALUES (
    'analytical-outcomes-v3',
    'Robo/ops/migrations/012_analytical_outcomes_v3.sql',
    'completed',
    '{"additive":true,"prospective_only":true,"historical_backfill":false,"commander_authority":"NONE"}'::jsonb
)
ON CONFLICT (migration_key) DO UPDATE
SET completed_at=NOW(), status=EXCLUDED.status, details=EXCLUDED.details;

COMMIT;
