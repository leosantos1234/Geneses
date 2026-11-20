BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE TABLE IF NOT EXISTS public.genesis_migration_runs (
  migration_key TEXT PRIMARY KEY, source_path TEXT NOT NULL,
  completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), status TEXT NOT NULL,
  details JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE UNIQUE INDEX IF NOT EXISTS genesis_trades_id_uidx ON public.trades (id);
CREATE UNIQUE INDEX IF NOT EXISTS genesis_predictions_lstm_id_uidx ON public.predictions_lstm (id);
CREATE UNIQUE INDEX IF NOT EXISTS genesis_log_events_id_uidx ON public.log_events (id);
CREATE TEMP TABLE stage_trades AS SELECT * FROM public.trades WITH NO DATA;
CREATE TEMP TABLE stage_predictions_lstm AS SELECT * FROM public.predictions_lstm WITH NO DATA;
CREATE TEMP TABLE stage_log_events AS SELECT * FROM public.log_events WITH NO DATA;
CREATE TEMP TABLE stage_mr_fsm_state AS SELECT * FROM public.mr_fsm_state WITH NO DATA;
\copy stage_trades FROM 'sprint2_csv/trades.csv' WITH (FORMAT csv, HEADER true, NULL '\N');
\copy stage_predictions_lstm FROM 'sprint2_csv/predictions_lstm.csv' WITH (FORMAT csv, HEADER true, NULL '\N');
\copy stage_log_events FROM 'sprint2_csv/log_events.csv' WITH (FORMAT csv, HEADER true, NULL '\N');
\copy stage_mr_fsm_state FROM 'sprint2_csv/mr_fsm_state.csv' WITH (FORMAT csv, HEADER true, NULL '\N');
INSERT INTO public.trades SELECT * FROM stage_trades ON CONFLICT DO NOTHING;
INSERT INTO public.predictions_lstm SELECT * FROM stage_predictions_lstm ON CONFLICT DO NOTHING;
INSERT INTO public.log_events SELECT * FROM stage_log_events ON CONFLICT DO NOTHING;
INSERT INTO public.mr_fsm_state SELECT * FROM stage_mr_fsm_state ON CONFLICT DO NOTHING;
INSERT INTO public.genesis_migration_runs (migration_key, source_path, status, details)
VALUES ('sqlite-to-existing-postgres-v1', 'Robo/trades.db', 'completed', '{"method":"psql_staging","destructive":false}'::jsonb)
ON CONFLICT (migration_key) DO UPDATE SET completed_at=NOW(), status=EXCLUDED.status, details=EXCLUDED.details;
COMMIT;
