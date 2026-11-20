BEGIN;

ALTER TABLE commander_decisions
    ADD COLUMN IF NOT EXISTS record_provenance TEXT NOT NULL DEFAULT 'UNKNOWN';
ALTER TABLE paper_orders
    ADD COLUMN IF NOT EXISTS record_provenance TEXT NOT NULL DEFAULT 'UNKNOWN';
ALTER TABLE paper_fills
    ADD COLUMN IF NOT EXISTS record_provenance TEXT NOT NULL DEFAULT 'UNKNOWN';
ALTER TABLE paper_portfolio_effects
    ADD COLUMN IF NOT EXISTS record_provenance TEXT NOT NULL DEFAULT 'UNKNOWN';

CREATE INDEX IF NOT EXISTS commander_decisions_provenance_idx ON commander_decisions(record_provenance);
CREATE INDEX IF NOT EXISTS paper_orders_provenance_idx ON paper_orders(record_provenance);

INSERT INTO genesis_migration_runs(migration_key, source_path, status, details)
VALUES ('record-provenance-v1', 'Robo/ops/migrations/007_record_provenance.sql', 'completed', '{"additive":true,"historical_default":"UNKNOWN"}'::jsonb)
ON CONFLICT (migration_key) DO UPDATE SET completed_at=NOW(), status=EXCLUDED.status, details=EXCLUDED.details;

COMMIT;
