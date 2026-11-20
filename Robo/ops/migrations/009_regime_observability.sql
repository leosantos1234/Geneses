BEGIN;

CREATE TABLE IF NOT EXISTS regime_evaluations (
    regime_evaluation_id TEXT PRIMARY KEY,
    timestamp_utc TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    candle_open_time TIMESTAMPTZ NOT NULL,
    candle_close_time TIMESTAMPTZ NOT NULL,
    candle_closed BOOLEAN NOT NULL,
    close NUMERIC,
    atr NUMERIC,
    atr_percent NUMERIC,
    ema12 NUMERIC,
    ema26 NUMERIC,
    ema50 NUMERIC,
    ema200 NUMERIC,
    ema12_ema26_distance NUMERIC,
    ema50_ema200_distance NUMERIC,
    ema12_slope NUMERIC,
    ema50_slope NUMERIC,
    ema200_slope NUMERIC,
    ema200_distance_percent NUMERIC,
    volume NUMERIC,
    volume_average NUMERIC,
    volume_ratio NUMERIC,
    body_fraction NUMERIC,
    tendencia_forte BOOLEAN,
    tendencia_moderada BOOLEAN,
    regime4_rule BOOLEAN,
    regime1_rule BOOLEAN,
    regime3_rule_path_1 BOOLEAN,
    regime3_rule_path_2 BOOLEAN,
    regime2_explicit_rule BOOLEAN,
    fallback_to_regime2 BOOLEAN,
    winning_rule TEXT NOT NULL,
    regime INTEGER NOT NULL CHECK (regime IN (1,2,3,4)),
    record_provenance TEXT NOT NULL
        CHECK (record_provenance IN ('PRODUCTION_RUNTIME','TEST','ACCEPTANCE','SYNTHETIC','UNKNOWN')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS regime_evaluations_time_idx
    ON regime_evaluations(timestamp_utc, symbol, timeframe);
CREATE INDEX IF NOT EXISTS regime_evaluations_symbol_idx
    ON regime_evaluations(symbol, candle_close_time);

INSERT INTO genesis_migration_runs(migration_key, source_path, status, details)
VALUES ('regime-observability-v1', 'Robo/ops/migrations/009_regime_observability.sql', 'completed', '{"additive":true,"historical_backfill":false}'::jsonb)
ON CONFLICT (migration_key) DO UPDATE SET completed_at=NOW(), status=EXCLUDED.status, details=EXCLUDED.details;

COMMIT;
