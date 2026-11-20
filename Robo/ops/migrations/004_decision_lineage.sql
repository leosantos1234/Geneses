BEGIN;
CREATE TABLE IF NOT EXISTS decision_trace (
    decision_id UUID PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), symbol TEXT, timeframe TEXT,
    strategy TEXT, environment TEXT, mode TEXT, final_status TEXT NOT NULL DEFAULT 'IN_PROGRESS',
    trade_id TEXT, order_id TEXT, position_id TEXT, schema_version TEXT NOT NULL DEFAULT '1.0',
    decision_context JSONB NOT NULL DEFAULT '{}'::jsonb, config_fingerprint TEXT,
    code_version TEXT, strategy_version TEXT, model_version TEXT, reason_code TEXT,
    reason_message TEXT, legacy BOOLEAN NOT NULL DEFAULT FALSE, lineage_quality TEXT NOT NULL DEFAULT 'complete'
);
CREATE TABLE IF NOT EXISTS decision_events (
    event_id BIGSERIAL PRIMARY KEY, decision_id UUID NOT NULL REFERENCES decision_trace(decision_id),
    event_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(), stage TEXT NOT NULL, event_type TEXT NOT NULL,
    component TEXT NOT NULL, status TEXT NOT NULL, reason_code TEXT, duration_ms DOUBLE PRECISION,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb, schema_version TEXT NOT NULL DEFAULT '1.0'
);
CREATE INDEX IF NOT EXISTS decision_events_decision_ts_idx ON decision_events(decision_id,event_ts,event_id);
CREATE INDEX IF NOT EXISTS decision_trace_symbol_created_idx ON decision_trace(symbol,created_at DESC);
CREATE INDEX IF NOT EXISTS decision_trace_trade_idx ON decision_trace(trade_id);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS decision_id UUID;
CREATE INDEX IF NOT EXISTS trades_decision_id_idx ON trades(decision_id);
CREATE TABLE IF NOT EXISTS genesis_migration_runs (
    migration_key TEXT PRIMARY KEY, source_path TEXT NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), status TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);
INSERT INTO genesis_migration_runs(migration_key,source_path,status,details)
VALUES ('decision-lineage-v1','Robo/ops/migrations/004_decision_lineage.sql','completed','{"append_only":true}'::jsonb)
ON CONFLICT (migration_key) DO UPDATE SET completed_at=NOW(), status=EXCLUDED.status, details=EXCLUDED.details;
COMMIT;
