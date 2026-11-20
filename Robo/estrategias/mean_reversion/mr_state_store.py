import json
import threading
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any

from genesis_runtime import connect_operational_database, database_backend


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {k: _to_jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class MRStateStore:
    """Persistência da FSM por símbolo no backend operacional configurado."""

    def __init__(self, db_path: str = "trades.db"):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._ensure_db()

    def _ensure_db(self) -> None:
        try:
            with connect_operational_database() as con:
                if database_backend() == "postgres":
                    con.execute("SELECT 1 FROM mr_fsm_state LIMIT 1")
                else:
                    con.execute("CREATE TABLE IF NOT EXISTS mr_fsm_state (symbol TEXT NOT NULL PRIMARY KEY, updated_at TEXT NOT NULL, state_json TEXT NOT NULL)")
        except Exception:
            if database_backend() == "postgres":
                raise

    def save(self, symbol: str, state: dict[str, Any]) -> None:
        payload = json.dumps(_to_jsonable(state), ensure_ascii=False)
        ph = "%s" if database_backend() == "postgres" else "?"
        with self._lock:
            try:
                with connect_operational_database() as con:
                    con.execute(f"INSERT INTO mr_fsm_state (symbol, updated_at, state_json) VALUES ({ph}, {ph}, {ph}) ON CONFLICT(symbol) DO UPDATE SET updated_at=excluded.updated_at, state_json=excluded.state_json", (symbol, _utc_now_iso(), payload))
            except Exception:
                if database_backend() == "postgres":
                    raise

    def load(self, symbol: str) -> dict[str, Any] | None:
        ph = "%s" if database_backend() == "postgres" else "?"
        try:
            with connect_operational_database(read_only=True) as con:
                row = con.execute(f"SELECT state_json FROM mr_fsm_state WHERE symbol={ph}", (symbol,)).fetchone()
            return json.loads(row[0]) if row and row[0] else None
        except Exception:
            if database_backend() == "postgres":
                raise
            return None

    def clear(self, symbol: str) -> None:
        ph = "%s" if database_backend() == "postgres" else "?"
        with self._lock:
            try:
                with connect_operational_database() as con:
                    con.execute(f"DELETE FROM mr_fsm_state WHERE symbol={ph}", (symbol,))
            except Exception:
                if database_backend() == "postgres":
                    raise
