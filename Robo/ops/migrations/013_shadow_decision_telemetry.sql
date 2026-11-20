BEGIN;

-- Prospective, append-only observability.  This table is deliberately outside
-- Commander, risk, paper execution and exchange write paths.
CREATE TABLE IF NOT EXISTS shadow_decision_telemetry (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    candle_open_time TIMESTAMPTZ NOT NULL,
    candle_close_time TIMESTAMPTZ,
    regime INTEGER,
    feature_vector JSONB NOT NULL DEFAULT '{}'::jsonb,
    current_blocker TEXT,
    current_raw_breakout BOOLEAN,
    current_direction TEXT,
    current_candidate BOOLEAN,
    shadow_blocker TEXT,
    shadow_raw_breakout BOOLEAN,
    shadow_direction TEXT,
    shadow_candidate BOOLEAN,
    impulse_reset_divergence BOOLEAN NOT NULL DEFAULT FALSE,
    shadow_trade_created BOOLEAN NOT NULL DEFAULT FALSE,
    shadow_entry_time TIMESTAMPTZ,
    shadow_entry_price NUMERIC,
    shadow_exit_time TIMESTAMPTZ,
    shadow_exit_price NUMERIC,
    shadow_pnl NUMERIC,
    shadow_r_multiple NUMERIC,
    current_policy TEXT NOT NULL DEFAULT 'CURRENT',
    shadow_policy TEXT NOT NULL DEFAULT 'SHADOW_NO_IMPULSE_RESET',
    record_provenance TEXT NOT NULL CHECK (record_provenance = 'SHADOW_PAPER'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (symbol, timeframe, candle_open_time, shadow_policy)
);

CREATE INDEX IF NOT EXISTS shadow_decision_telemetry_symbol_time_idx
    ON shadow_decision_telemetry(symbol, timeframe, candle_open_time);
CREATE INDEX IF NOT EXISTS shadow_decision_telemetry_blocker_idx
    ON shadow_decision_telemetry(symbol, shadow_policy, impulse_reset_divergence);

INSERT INTO genesis_migration_runs(migration_key, source_path, status, details)
VALUES (
    'shadow-decision-telemetry-v1',
    'Robo/ops/migrations/013_shadow_decision_telemetry.sql',
    'completed',
    '{"additive":true,"append_only":true,"prospective_only":true,"historical_backfill":false,"execution_authority":"NONE"}'::jsonb
)
ON CONFLICT (migration_key) DO UPDATE
SET completed_at=NOW(), status=EXCLUDED.status, details=EXCLUDED.details;

COMMIT;
