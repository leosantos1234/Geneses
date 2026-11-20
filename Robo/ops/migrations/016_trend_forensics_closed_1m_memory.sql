BEGIN;

CREATE TABLE IF NOT EXISTS canonical_market_candles_1m (
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    timeframe TEXT NOT NULL CHECK (timeframe = '1m'),
    candle_open_time TIMESTAMPTZ NOT NULL,
    candle_close_time TIMESTAMPTZ NOT NULL,
    open NUMERIC,
    high NUMERIC,
    low NUMERIC,
    close NUMERIC,
    volume NUMERIC,
    turnover NUMERIC,
    closed BOOLEAN NOT NULL CHECK (closed = TRUE),
    record_provenance TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, exchange, timeframe, candle_open_time),
    CHECK (candle_close_time >= candle_open_time + INTERVAL '1 minute')
);

CREATE INDEX IF NOT EXISTS canonical_market_candles_1m_close_idx
    ON canonical_market_candles_1m(symbol, candle_close_time);

CREATE TABLE IF NOT EXISTS breakout_trend_forensics (
    evaluation_id TEXT NOT NULL,
    evaluation_kind TEXT NOT NULL CHECK (evaluation_kind IN ('CURRENT','SHADOW_NO_IMPULSE_RESET')),
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    candle_open_time TIMESTAMPTZ,
    candle_close_time TIMESTAMPTZ,
    expected_direction TEXT,
    trend_ema_short_period INTEGER,
    trend_ema_medium_period INTEGER,
    trend_ema_long_period INTEGER,
    trend_ema_short_value NUMERIC,
    trend_ema_medium_value NUMERIC,
    trend_ema_long_value NUMERIC,
    trend_ema_short_slope NUMERIC,
    trend_ema_medium_slope NUMERIC,
    trend_ema_long_slope NUMERIC,
    trend_condition_short_medium BOOLEAN,
    trend_condition_medium_long BOOLEAN,
    trend_condition_final BOOLEAN,
    trend_distance_short_medium_pct NUMERIC,
    trend_distance_medium_long_pct NUMERIC,
    trend_state TEXT NOT NULL CHECK (trend_state IN ('PASS','FAIL','NOT_REACHED','NOT_APPLICABLE')),
    trend_failure_reason TEXT,
    record_provenance TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (evaluation_id, evaluation_kind)
);

CREATE TABLE IF NOT EXISTS breakout_market_outcomes_1m (
    event_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    breakout_candle_open_time TIMESTAMPTZ NOT NULL,
    breakout_candle_close_time TIMESTAMPTZ,
    policy TEXT NOT NULL,
    direction TEXT,
    first_blocker TEXT,
    forward_return_5m NUMERIC,
    forward_return_10m NUMERIC,
    forward_return_15m NUMERIC,
    forward_return_30m NUMERIC,
    forward_return_60m NUMERIC,
    mfe_5m NUMERIC, mae_5m NUMERIC,
    mfe_10m NUMERIC, mae_10m NUMERIC,
    mfe_15m NUMERIC, mae_15m NUMERIC,
    mfe_30m NUMERIC, mae_30m NUMERIC,
    mfe_60m NUMERIC, mae_60m NUMERIC,
    available_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (symbol, breakout_candle_open_time, policy)
);

INSERT INTO genesis_migration_runs(migration_key, source_path, status, details)
VALUES ('trend-forensics-closed-1m-v1',
        'Robo/ops/migrations/016_trend_forensics_closed_1m_memory.sql',
        'completed',
        '{"additive":true,"prospective_only":true,"historical_backfill":false,"execution_authority":"NONE"}'::jsonb)
ON CONFLICT (migration_key) DO UPDATE
SET completed_at=NOW(), status=EXCLUDED.status, details=EXCLUDED.details;

COMMIT;
