BEGIN;

CREATE TABLE IF NOT EXISTS strategy_calibration_profiles (
    profile_id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    profile_name TEXT NOT NULL CHECK (profile_name IN ('agressivo','moderado','conservador')),
    version INTEGER NOT NULL CHECK (version > 0),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    parameters JSONB NOT NULL,
    effective_from TIMESTAMPTZ,
    effective_until TIMESTAMPTZ,
    created_by TEXT NOT NULL DEFAULT 'system',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (jsonb_typeof(parameters) = 'object'),
    UNIQUE (symbol, timeframe, profile_name, version)
);

CREATE INDEX IF NOT EXISTS strategy_calibration_profiles_active_idx
    ON strategy_calibration_profiles(symbol, timeframe, profile_name, enabled, version DESC);

-- Baseline snapshots reproduce the current per-asset environment behavior.
-- Security flags, credentials and exchange authorization are intentionally absent.
WITH assets(symbol, ema_curta, ema_media, ema_longa, ema_trend) AS (
    VALUES ('BTCUSDT',20,50,200,200), ('ETHUSDT',14,44,150,150),
           ('BNBUSDT',20,50,200,200), ('XRPUSDT',20,50,200,200),
           ('SOLUSDT',21,55,200,200)
), modes(profile_name, min_score) AS (
    VALUES ('agressivo',1), ('moderado',2), ('conservador',3)
)
INSERT INTO strategy_calibration_profiles
    (symbol, timeframe, profile_name, version, parameters, created_by)
SELECT a.symbol, '5m', m.profile_name, 1,
       jsonb_build_object(
           'modo_validacao', m.profile_name,
           'min_score_breakout_agressivo', 1,
           'min_score_breakout_moderado', 2,
           'min_score_breakout_conservador', 3,
           'filtro_breakout_min_confirmacoes', 2,
           'breakout_min_volume_ratio', 1.0,
           'ema_curta', a.ema_curta, 'ema_media', a.ema_media,
           'ema_longa', a.ema_longa, 'ema_trend', a.ema_trend,
           'sr_lookback', 14, 'breakout_level_lookback', 5,
           'breakout_impulse_reset_min_bars', 0,
           'breakout_impulse_atr_mult', 1.5,
           'breakout_impulse_body_min', 0.55),
       'migration-017'
FROM assets a CROSS JOIN modes m
ON CONFLICT (symbol, timeframe, profile_name, version) DO NOTHING;

INSERT INTO genesis_migration_runs(migration_key, source_path, status, details)
VALUES ('strategy-calibration-profiles-v1',
        'Robo/ops/migrations/017_strategy_calibration_profiles.sql',
        'completed',
        '{"additive":true,"historical_backfill":false,"security_flags_in_database":false}'::jsonb)
ON CONFLICT (migration_key) DO UPDATE
SET completed_at=NOW(), status=EXCLUDED.status, details=EXCLUDED.details;

COMMIT;
