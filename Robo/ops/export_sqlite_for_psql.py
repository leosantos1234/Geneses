"""Exporta tabelas operacionais para CSV PostgreSQL, preservando NULL como \\N."""
import csv, os, sqlite3, sys
from pathlib import Path

source = Path(os.environ.get("GENESIS_SQLITE_PATH", Path(__file__).resolve().parents[1] / "trades.db"))
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
tables = ("trades", "predictions_lstm", "log_events", "mr_fsm_state")
with sqlite3.connect(source) as db:
    for table in tables:
        cols = [row[1] for row in db.execute(f'PRAGMA table_info("{table}")')]
        with (out / f"{table}.csv").open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(cols)
            for row in db.execute(f'SELECT {", ".join(chr(34)+c.replace(chr(34), chr(34)*2)+chr(34) for c in cols)} FROM "{table}"'):
                writer.writerow([r if r is not None else r"\N" for r in row])
        print(table, (out / f"{table}.csv").stat().st_size)
