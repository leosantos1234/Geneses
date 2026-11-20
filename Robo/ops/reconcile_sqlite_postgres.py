import csv, os, sqlite3, subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sqlite_path = root / 'Robo' / 'trades.db'
psql = r'C:\Program Files\PostgreSQL\16\bin\psql.exe'
env = os.environ.copy(); env['PGPASSWORD'] = os.getenv('GENESIS_LOCAL_PG_PASSWORD', 'postgres')

def pg(sql):
    out = subprocess.check_output([psql, '-h', '127.0.0.1', '-U', 'postgres', '-d', 'genesis_trading', '-At', '-c', sql], env=env, text=True)
    return out.splitlines()

with sqlite3.connect(sqlite_path) as db:
    sqlite_counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ('trades','predictions_lstm','log_events','mr_fsm_state')}
    sqlite_trade_ids = {r[0] for r in db.execute("SELECT trade_id FROM trades WHERE trade_id IS NOT NULL")}
    sqlite_trade_id_rows = {r[0]: r[1] for r in db.execute("SELECT trade_id, id FROM trades WHERE trade_id IS NOT NULL")}
    sqlite_ids = {t: {str(r[0]) for r in db.execute(f'SELECT id FROM {t} WHERE id IS NOT NULL')} for t in ('trades','predictions_lstm','log_events')}

pg_counts = {line.split('|')[0]: int(line.split('|')[1]) for line in pg("SELECT 'trades',count(*) FROM trades UNION ALL SELECT 'predictions_lstm',count(*) FROM predictions_lstm UNION ALL SELECT 'log_events',count(*) FROM log_events UNION ALL SELECT 'mr_fsm_state',count(*) FROM mr_fsm_state")}
pg_trade_ids = set(pg("SELECT trade_id FROM trades WHERE trade_id IS NOT NULL"))
pg_trade_id_rows = {line.split('|')[0]: line.split('|')[1] for line in pg("SELECT trade_id,id FROM trades WHERE trade_id IS NOT NULL")}
print('COUNTS_SQLITE', sqlite_counts)
print('COUNTS_POSTGRES', pg_counts)
print('TRADE_IDS_SOURCE_MISSING_IN_PG', len(sqlite_trade_ids - pg_trade_ids))
print('TRADE_IDS_ONLY_IN_PG', len(pg_trade_ids - sqlite_trade_ids))
print('TRADE_ID_ID_COLLISIONS_DIFFERENT_ID', sum(1 for k in sqlite_trade_ids & pg_trade_ids if str(sqlite_trade_id_rows[k]) != str(pg_trade_id_rows[k])))
for table in ('trades','predictions_lstm','log_events'):
    pg_ids = set(pg(f'SELECT id FROM {table} WHERE id IS NOT NULL'))
    print(f'{table.upper()}_SOURCE_IDS_MISSING_IN_PG', len(sqlite_ids[table] - pg_ids))
print('PG_MIN_MAX_TRADE_TS', pg("SELECT COALESCE(MIN(timestamp::text),'NULL'),COALESCE(MAX(timestamp::text),'NULL') FROM trades"))
print('PG_SUM_PNL', pg("SELECT COALESCE(SUM(profit_loss),0) FROM trades"))
print('PG_STATUS', pg("SELECT COALESCE(status,'NULL'),count(*) FROM trades GROUP BY 1 ORDER BY 1"))
