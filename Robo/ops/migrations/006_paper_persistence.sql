BEGIN;

CREATE TABLE IF NOT EXISTS paper_portfolios (
    paper_portfolio_id TEXT PRIMARY KEY,
    initial_cash NUMERIC(38, 18) NOT NULL,
    available_cash NUMERIC(38, 18) NOT NULL,
    reserved_cash NUMERIC(38, 18) NOT NULL,
    equity NUMERIC(38, 18) NOT NULL,
    realized_pnl NUMERIC(38, 18) NOT NULL,
    unrealized_pnl NUMERIC(38, 18) NOT NULL,
    gross_exposure NUMERIC(38, 18) NOT NULL,
    net_exposure NUMERIC(38, 18) NOT NULL,
    paper_only BOOLEAN NOT NULL CHECK (paper_only IS TRUE),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS paper_orders (
    paper_order_id TEXT PRIMARY KEY,
    paper_portfolio_id TEXT NOT NULL REFERENCES paper_portfolios(paper_portfolio_id),
    commander_decision_id TEXT NOT NULL UNIQUE,
    timestamp_utc TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    requested_quantity NUMERIC(38, 18) NOT NULL,
    requested_price NUMERIC(38, 18),
    status TEXT NOT NULL,
    paper_only BOOLEAN NOT NULL CHECK (paper_only IS TRUE),
    execution_authority TEXT NOT NULL CHECK (execution_authority = 'NONE'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS paper_fills (
    paper_fill_id TEXT PRIMARY KEY,
    paper_order_id TEXT NOT NULL REFERENCES paper_orders(paper_order_id),
    timestamp_utc TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity NUMERIC(38, 18) NOT NULL,
    fill_price NUMERIC(38, 18) NOT NULL,
    fee NUMERIC(38, 18) NOT NULL,
    slippage_cost NUMERIC(38, 18) NOT NULL,
    paper_only BOOLEAN NOT NULL CHECK (paper_only IS TRUE)
);

CREATE TABLE IF NOT EXISTS paper_positions (
    paper_portfolio_id TEXT NOT NULL REFERENCES paper_portfolios(paper_portfolio_id),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity NUMERIC(38, 18) NOT NULL,
    average_entry_price NUMERIC(38, 18) NOT NULL,
    mark_price NUMERIC(38, 18) NOT NULL,
    unrealized_pnl NUMERIC(38, 18) NOT NULL,
    realized_pnl NUMERIC(38, 18) NOT NULL,
    opened_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    paper_only BOOLEAN NOT NULL CHECK (paper_only IS TRUE),
    PRIMARY KEY (paper_portfolio_id, symbol)
);

CREATE TABLE IF NOT EXISTS paper_portfolio_effects (
    paper_effect_id TEXT PRIMARY KEY,
    paper_portfolio_id TEXT NOT NULL REFERENCES paper_portfolios(paper_portfolio_id),
    paper_fill_id TEXT NOT NULL REFERENCES paper_fills(paper_fill_id),
    paper_order_id TEXT NOT NULL REFERENCES paper_orders(paper_order_id),
    commander_decision_id TEXT NOT NULL,
    timestamp_utc TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    effect_type TEXT NOT NULL,
    quantity_delta NUMERIC(38, 18) NOT NULL,
    cash_delta NUMERIC(38, 18) NOT NULL,
    realized_pnl_delta NUMERIC(38, 18) NOT NULL,
    paper_only BOOLEAN NOT NULL CHECK (paper_only IS TRUE)
);

CREATE TABLE IF NOT EXISTS paper_execution_events (
    paper_event_id TEXT PRIMARY KEY,
    timestamp_utc TIMESTAMPTZ NOT NULL,
    event_type TEXT NOT NULL,
    commander_decision_id TEXT NOT NULL,
    paper_portfolio_id TEXT NOT NULL REFERENCES paper_portfolios(paper_portfolio_id),
    paper_order_id TEXT REFERENCES paper_orders(paper_order_id),
    paper_fill_id TEXT REFERENCES paper_fills(paper_fill_id),
    paper_effect_id TEXT REFERENCES paper_portfolio_effects(paper_effect_id),
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION prevent_paper_event_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'paper_execution_events is append-only';
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'paper_execution_events_immutable') THEN
        CREATE TRIGGER paper_execution_events_immutable
        BEFORE UPDATE OR DELETE ON paper_execution_events
        FOR EACH ROW EXECUTE FUNCTION prevent_paper_event_mutation();
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS paper_orders_commander_idx ON paper_orders(commander_decision_id);
CREATE INDEX IF NOT EXISTS paper_fills_order_idx ON paper_fills(paper_order_id);
CREATE INDEX IF NOT EXISTS paper_effects_commander_idx ON paper_portfolio_effects(commander_decision_id);
CREATE INDEX IF NOT EXISTS paper_events_commander_idx ON paper_execution_events(commander_decision_id, timestamp_utc);

INSERT INTO genesis_migration_runs(migration_key, source_path, status, details)
VALUES ('paper-persistence-v1', 'Robo/ops/migrations/006_paper_persistence.sql', 'completed', '{"additive":true,"append_only_events":true}'::jsonb)
ON CONFLICT (migration_key) DO UPDATE SET completed_at=NOW(), status=EXCLUDED.status, details=EXCLUDED.details;

COMMIT;
