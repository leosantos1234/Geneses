"""RAG V1 — Bounded, auditable retrieval layer for Commander 2.0."""

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from genesis_runtime import database_url

try:
    import psycopg
except ImportError:
    psycopg = None


class RAGTimeoutError(Exception):
    """RAG operation timed out."""
    pass


class RAGUnavailableError(Exception):
    """RAG infrastructure unavailable."""
    pass


# Configuration
MAX_RAG_RESULTS = 10
TIMEOUT_SECONDS = 5.0
SCHEMA_VERSION = "1.0"

# Content patterns to exclude (secrets, credentials, env vars, API keys)
SECRET_PATTERNS = [
    r'(?i)(api[_-]?key|apikey)',
    r'(?i)(secret[_-]?key|secretkey)',
    r'(?i)(password|passwd)',
    r'(?i)(token|auth)',
    r'(?i)(connection[_-]?string)',
    r'(?i)(dsn)',
    r'(?i)(\$?env\.)',
    r'(?i)(export\s+)',
    r'(?i)(aws[_-]?key)',
    r'(?i)(private[_-]?key)',
    r'(?i)(bearer\s+)',
]

ALLOWED_SOURCES = {
    "decision_trace",
    "decision_events",
    "closed_trade",
    "incident_log",
    "model_metadata",
    "risk_event",
    "risk_context",
    "strategy_context",
    "performance_context",
    "similar_setup_context",
    "impulse_reset_context",
    "volatility_context",
    "market_context",
}


def normalize_symbol(symbol: Optional[str]) -> Optional[str]:
    """Return Genesis' canonical USDT symbol without mutating stored rows."""
    if symbol is None:
        return None
    raw = str(symbol).strip().upper().replace(" ", "")
    if not raw:
        return None
    raw = raw.replace("_", "-").replace("/", "-")
    if raw.endswith("-SWAP"):
        raw = raw[:-5]
    if raw.endswith("-USD"):
        raw = raw[:-4] + "-USDT"
    if raw.endswith("-USDT"):
        return raw.replace("-", "")
    if raw.endswith("USDT"):
        return raw
    if raw.endswith("-USDT-SWAP"):
        return raw[:-5].replace("-", "")
    if raw.endswith("USD"):
        return raw[:-3] + "USDT"
    return raw.replace("-", "")


_SQL_NORMALIZED_SYMBOL = "regexp_replace(regexp_replace(regexp_replace(upper(coalesce(symbol, '')), '[-_/]', '', 'g'), 'SWAP$', ''), 'USD$', 'USDT')"
_SOURCE_PRIORITY_SQL = "CASE source_type WHEN 'similar_setup_context' THEN 0 WHEN 'strategy_context' THEN 1 WHEN 'performance_context' THEN 2 WHEN 'impulse_reset_context' THEN 3 WHEN 'volatility_context' THEN 4 WHEN 'risk_context' THEN 5 WHEN 'market_context' THEN 6 WHEN 'risk_event' THEN 7 WHEN 'decision_events' THEN 8 WHEN 'decision_trace' THEN 9 ELSE 10 END"
ANALYTICAL_CONTEXT_PRIORITY = {
    "similar_setup_context": 0, "strategy_context": 1, "performance_context": 2,
    "impulse_reset_context": 3, "volatility_context": 4, "risk_context": 5,
    "market_context": 6, "risk_event": 7, "decision_events": 8, "decision_trace": 9,
}


def rank_analytical_context(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Order context by analytical relevance before recency.

    This is an in-memory helper and does not mutate records or persist RAG data.
    """
    def key(row: Dict[str, Any]):
        raw_ts = str(row.get("timestamp_utc") or "")
        try:
            ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError, OverflowError):
            ts = float("-inf")
        return (ANALYTICAL_CONTEXT_PRIORITY.get(str(row.get("source_type") or ""), 99), -ts, str(row.get("source_id") or ""))
    return sorted(records, key=key)


class RAGRecord:
    """RAG record with metadata and provenance."""

    @staticmethod
    def _scan_for_secrets(text: str) -> bool:
        """Check if text contains secret patterns. Returns True if secret found."""
        if not text:
            return False
        for pattern in SECRET_PATTERNS:
            if re.search(pattern, str(text)):
                return True
        return False

    @staticmethod
    def _recursive_secret_scan(obj: Any) -> bool:
        """Recursively scan object tree for secrets. Returns True if secret found."""
        if isinstance(obj, str):
            return RAGRecord._scan_for_secrets(obj)
        elif isinstance(obj, (list, tuple)):
            return any(RAGRecord._recursive_secret_scan(item) for item in obj)
        elif isinstance(obj, dict):
            return any(
                RAGRecord._recursive_secret_scan(k) or RAGRecord._recursive_secret_scan(v)
                for k, v in obj.items()
            )
        return False

    def __init__(
        self,
        source_type: str,
        source_id: str,
        symbol: Optional[str],
        timestamp_utc: datetime,
        summary: str,
        content: str,
        tags: List[str] = None,
        metadata: Dict[str, Any] = None,
    ):
        """Initialize RAG record with strict validation."""
        # Type validation
        if not isinstance(source_type, str):
            raise ValueError("source_type must be string")
        if not isinstance(source_id, str):
            raise ValueError("source_id must be string")
        if symbol is not None and not isinstance(symbol, str):
            raise ValueError("symbol must be None or string")
        if not isinstance(timestamp_utc, datetime):
            raise ValueError("timestamp_utc must be datetime")
        if not isinstance(summary, str):
            raise ValueError("summary must be string")
        if not isinstance(content, str):
            raise ValueError("content must be string")
        if tags is not None and not isinstance(tags, list):
            raise ValueError("tags must be None or list")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be None or dict")

        # Field format validation
        if source_type not in ALLOWED_SOURCES:
            raise ValueError(f"Invalid source_type: {source_type}")

        if not source_id or not source_id.strip():
            raise ValueError("source_id required")

        if not summary or not summary.strip():
            raise ValueError("summary required")

        if not content or not content.strip():
            raise ValueError("content required")

        # Validate tags are all strings
        if tags:
            if not all(isinstance(t, str) for t in tags):
                raise ValueError("tags must be list of strings")

        # Validate timestamp is UTC-aware
        if timestamp_utc.tzinfo is None:
            raise ValueError("timestamp_utc must be timezone-aware (UTC)")

        # Reject secrets in all persisted fields (recursive scan)
        if self._recursive_secret_scan(source_id):
            raise ValueError("Forbidden content pattern in source_id")
        if self._recursive_secret_scan(symbol):
            raise ValueError("Forbidden content pattern in symbol")
        if self._recursive_secret_scan(tags or []):
            raise ValueError("Forbidden content pattern in tags")
        if self._recursive_secret_scan(metadata or {}):
            raise ValueError("Forbidden content pattern in metadata")
        if self._recursive_secret_scan(summary):
            raise ValueError("Forbidden content pattern in summary")
        if self._recursive_secret_scan(content):
            raise ValueError("Forbidden content pattern in content")

        self.rag_record_id = str(uuid4())
        self.source_type = source_type
        self.source_id = source_id
        self.symbol = symbol
        self.timestamp_utc = timestamp_utc
        self.summary = summary
        self.content = content
        self.content_hash = self._compute_hash(content)
        self.tags = tags or []
        self.metadata = metadata or {}
        self.created_at = datetime.now(timezone.utc)

    @staticmethod
    def _compute_hash(content: str) -> str:
        """Compute deterministic content hash."""
        return hashlib.sha256(content.encode('utf-8')).hexdigest()[:24]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return {
            "rag_record_id": self.rag_record_id,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "symbol": self.symbol,
            "timestamp_utc": self.timestamp_utc.isoformat(),
            "summary": self.summary,
            "content": self.content,
            "content_hash": self.content_hash,
            "tags": self.tags,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }


def ingest_record(record: RAGRecord) -> bool:
    """Ingest a record into RAG storage.

    Returns: True if record was inserted, False if duplicate (idempotent).
    Raises: Exception on database errors.
    """
    if not record or not isinstance(record, RAGRecord):
        raise ValueError("record required")

    if psycopg is None or not database_url():
        raise RuntimeError("PostgreSQL unavailable")

    try:
        with psycopg.connect(database_url(), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                # Check if record already exists (idempotent deduplication)
                cur.execute(
                    """SELECT COUNT(*) FROM rag_records
                    WHERE source_type = %s AND source_id = %s AND content_hash = %s""",
                    (record.source_type, record.source_id, record.content_hash)
                )
                if cur.fetchone()[0] > 0:
                    # Duplicate: already ingested
                    return False

                # Insert new record
                cur.execute(
                    """INSERT INTO rag_records
                    (rag_record_id, source_type, source_id, symbol, timestamp_utc,
                     summary, content, content_hash, tags, metadata, created_at, schema_version)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)""",
                    (
                        record.rag_record_id,
                        record.source_type,
                        record.source_id,
                        record.symbol,
                        record.timestamp_utc,
                        record.summary,
                        record.content,
                        record.content_hash,
                        record.tags,
                        json.dumps(record.metadata),
                        record.created_at,
                        SCHEMA_VERSION,
                    )
                )
            conn.commit()
    except Exception as e:
        logging.error("RAG_INGEST_FAILURE error=%s", type(e).__name__)
        raise

    return True


def retrieve(
    symbol: Optional[str] = None,
    source_type: Optional[str] = None,
    tags: Optional[List[str]] = None,
    as_of: Optional[datetime] = None,
    max_results: int = MAX_RAG_RESULTS,
    strategy: Optional[str] = None,
    regime: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Retrieve records from RAG storage.

    Args:
        symbol: Filter by symbol
        source_type: Filter by source type
        tags: Filter by tags (any match)
        as_of: Only return records with timestamp_utc <= as_of
        max_results: Maximum results to return (bounded)

    Returns:
        List of RAG records with provenance, or [{"marker": "DATA_NOT_AVAILABLE"}] if empty.
        Raises RAGTimeoutError on timeout, RAGUnavailableError on connection failure.
    """
    if max_results <= 0 or max_results > MAX_RAG_RESULTS:
        max_results = MAX_RAG_RESULTS

    # Validate parameters
    if tags is not None and not isinstance(tags, list):
        raise ValueError("tags must be None or list")
    if tags is not None and not all(isinstance(t, str) for t in tags):
        raise ValueError("tags must be list of strings")

    if psycopg is None or not database_url():
        raise RuntimeError("PostgreSQL unavailable")

    try:
        with psycopg.connect(database_url(), connect_timeout=5) as conn:
            conn.execute("SET default_transaction_read_only = on")
            conn.execute("SET statement_timeout = '5s'")

            with conn.cursor() as cur:
                clauses = []
                params = []

                normalized_query_symbol = normalize_symbol(symbol)
                exact_symbol = str(symbol).strip().upper() if symbol else None
                if normalized_query_symbol:
                    clauses.append(
                        f"(upper(coalesce(symbol, '')) = %s OR {_SQL_NORMALIZED_SYMBOL} = %s)"
                    )
                    params.extend([exact_symbol, normalized_query_symbol])

                if source_type:
                    if source_type not in ALLOWED_SOURCES:
                        raise ValueError(f"Invalid source_type: {source_type}")
                    clauses.append("source_type = %s")
                    params.append(source_type)

                if as_of:
                    clauses.append("timestamp_utc <= %s")
                    params.append(as_of)

                if tags:
                    clauses.append("tags && %s")
                    params.append(tags)

                if strategy:
                    clauses.append("metadata->>'strategy' = %s")
                    params.append(str(strategy))

                if regime:
                    clauses.append("metadata->>'regime' = %s")
                    params.append(str(regime))

                where = " WHERE " + " AND ".join(clauses) if clauses else ""

                order_params = []
                exact_priority = ""
                if exact_symbol:
                    exact_priority = "CASE WHEN upper(coalesce(symbol, '')) = %s THEN 0 ELSE 1 END, "
                    order_params.append(exact_symbol)

                cur.execute(
                    f"""SELECT rag_record_id, source_type, source_id, symbol, timestamp_utc,
                       summary, content, content_hash, tags, metadata, created_at
                       FROM rag_records{where}
                       ORDER BY {exact_priority}{_SOURCE_PRIORITY_SQL}, timestamp_utc DESC,
                                source_type ASC, source_id ASC, content_hash ASC
                       LIMIT %s""",
                    params + order_params + [max_results]
                )

                raw_rows = cur.fetchall()
                # Preserve the legacy exact-symbol contract when exact rows
                # exist; aliases are fallback-compatible, not duplicates in
                # the returned context.
                if exact_symbol:
                    exact_rows = [
                        row for row in raw_rows
                        if str(row[3] or "").strip().upper() == exact_symbol
                    ]
                    rows = exact_rows or raw_rows
                else:
                    rows = raw_rows

                results = []
                for row in rows:
                    record = {
                        "rag_record_id": str(row[0]),
                        "source_type": row[1],
                        "source_id": row[2],
                        "symbol": row[3],
                        "timestamp_utc": row[4].isoformat() if row[4] else None,
                        "summary": row[5],
                        "content": row[6],
                        "content_hash": row[7],
                        "tags": row[8] or [],
                        "metadata": row[9] or {},
                        "created_at": row[10].isoformat() if row[10] else None,
                        "content_role": "UNTRUSTED_RETRIEVED_DATA",
                        "normalized_symbol": normalize_symbol(row[3]),
                    }
                    results.append(record)

                # No results => DATA_NOT_AVAILABLE marker
                if not results:
                    return [{"marker": "DATA_NOT_AVAILABLE"}]

                quality_sources = {str(record.get("source_type") or "") for record in results}
                context_quality = "LOW" if quality_sources <= {"decision_trace", "decision_events"} else "STANDARD"
                for record in results:
                    record["context_quality"] = context_quality
                return results

    except psycopg.errors.QueryCanceled as e:
        logging.error("RAG_RETRIEVE_TIMEOUT error=%s", type(e).__name__)
        raise RAGTimeoutError("RAG retrieve operation timed out") from e
    except psycopg.OperationalError as e:
        logging.error("RAG_RETRIEVE_UNAVAILABLE error=%s", type(e).__name__)
        raise RAGUnavailableError("RAG infrastructure unavailable") from e
    except Exception as e:
        logging.error("RAG_RETRIEVE_FAILURE error=%s", type(e).__name__)
        raise


# Source adapters for real PostgreSQL data
def ingest_decision_traces(limit: int = None) -> int:
    """Ingest real decision_trace rows into RAG.

    Returns: number of records ingested.
    """
    if psycopg is None or not database_url():
        raise RuntimeError("PostgreSQL unavailable")

    count = 0
    try:
        with psycopg.connect(database_url(), connect_timeout=5) as conn:
            conn.execute("SET default_transaction_read_only = on")

            with conn.cursor() as cur:
                query = """
                    SELECT decision_id, symbol, strategy, created_at, decision_context
                    FROM decision_trace
                    ORDER BY created_at DESC
                """
                if limit:
                    query += f" LIMIT {limit}"

                cur.execute(query)

                for row in cur.fetchall():
                    decision_id, symbol, strategy, created_at, context = row

                    summary = f"Decision: {strategy} on {symbol}"
                    content = f"Strategy: {strategy}, Symbol: {symbol}, Status: decision trace"

                    try:
                        record = RAGRecord(
                            source_type="decision_trace",
                            source_id=str(decision_id),
                            symbol=symbol,
                            timestamp_utc=created_at,
                            summary=summary,
                            content=content,
                            tags=["lineage", "decision"],
                            metadata={"strategy": strategy},
                        )
                        ingest_record(record)
                        count += 1
                    except ValueError:
                        # Skip records that fail validation (e.g., contain secrets)
                        logging.warning("Skipping decision_trace row %s: validation failed", decision_id)

    except Exception as e:
        logging.error("DECISION_TRACE_INGESTION_FAILURE error=%s", type(e).__name__)
        raise

    return count


def ingest_decision_events(limit: int = None) -> int:
    """Ingest real decision_events rows into RAG.

    Returns: number of records ingested.
    """
    if psycopg is None or not database_url():
        raise RuntimeError("PostgreSQL unavailable")

    count = 0
    try:
        with psycopg.connect(database_url(), connect_timeout=5) as conn:
            conn.execute("SET default_transaction_read_only = on")

            with conn.cursor() as cur:
                query = """
                    SELECT event_id, decision_id, event_type, stage, event_ts, component, status
                    FROM decision_events
                    ORDER BY event_ts DESC
                """
                if limit:
                    query += f" LIMIT {limit}"

                cur.execute(query)

                for row in cur.fetchall():
                    event_id, decision_id, event_type, stage, event_ts, component, status = row

                    summary = f"Event: {event_type} ({stage})"
                    content = f"Type: {event_type}, Stage: {stage}, Component: {component}, Status: {status}"

                    try:
                        record = RAGRecord(
                            source_type="decision_events",
                            source_id=f"event-{event_id}",
                            symbol=None,  # Events may not have symbol
                            timestamp_utc=event_ts,
                            summary=summary,
                            content=content,
                            tags=[stage.lower(), event_type.lower()],
                            metadata={"decision_id": str(decision_id), "component": component},
                        )
                        ingest_record(record)
                        count += 1
                    except ValueError:
                        logging.warning("Skipping decision_event row %s: validation failed", event_id)

    except Exception as e:
        logging.error("DECISION_EVENTS_INGESTION_FAILURE error=%s", type(e).__name__)
        raise

    return count
