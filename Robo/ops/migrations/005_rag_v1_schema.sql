BEGIN;

CREATE TABLE IF NOT EXISTS rag_records (
    rag_record_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    symbol TEXT,
    timestamp_utc TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    tags TEXT[] DEFAULT ARRAY[]::TEXT[],
    summary TEXT NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    schema_version TEXT NOT NULL DEFAULT '1.0'
);

CREATE INDEX IF NOT EXISTS rag_records_source_type_idx ON rag_records(source_type);
CREATE INDEX IF NOT EXISTS rag_records_symbol_idx ON rag_records(symbol);
CREATE INDEX IF NOT EXISTS rag_records_timestamp_idx ON rag_records(timestamp_utc DESC);
CREATE INDEX IF NOT EXISTS rag_records_content_hash_idx ON rag_records(content_hash);
CREATE INDEX IF NOT EXISTS rag_records_tags_idx ON rag_records USING GIN(tags);

INSERT INTO genesis_migration_runs(migration_key, source_path, status, details)
VALUES ('rag-v1-schema', 'Robo/ops/migrations/005_rag_v1_schema.sql', 'completed', '{"append_only":true}'::jsonb)
ON CONFLICT (migration_key) DO UPDATE SET completed_at=NOW(), status=EXCLUDED.status, details=EXCLUDED.details;

COMMIT;
