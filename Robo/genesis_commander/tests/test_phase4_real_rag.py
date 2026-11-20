"""Phase 4 real RAG integration — canonical PostgreSQL RAG, no mocks, no fake retrieval.

Explicit ingestion: decision_trace rows are ingested through the canonical Phase 3 ingestion
path so rag_records is non-empty. That is the only RAG write in this module; it happens in the
fixture and is reported separately from retrieval writes.
"""

from datetime import datetime, timezone

import pytest

from genesis_commander.tests._business_state import take_business_state
from genesis_rag.rag_v1 import (
    RAGUnavailableError,
    ingest_decision_traces,
    retrieve,
)
from genesis_runtime import connect_operational_database, database_backend

UNTRUSTED_MARKER = "UNTRUSTED_RETRIEVED_DATA"
EMPTY_MARKER = "DATA_NOT_AVAILABLE"
MAX_RESULTS = 5


@pytest.fixture(scope="module")
def ingested_rag_records():
    ingested = ingest_decision_traces(limit=20)
    conn = connect_operational_database(read_only=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM rag_records")
            total = cur.fetchone()[0]
    finally:
        conn.close()
    assert total > 0, "canonical rag_records must be non-empty for the retrieval acceptance"
    return {"ingest_processed": ingested, "rag_total": total}


def _parse_ts(value):
    ts = datetime.fromisoformat(value)
    assert ts.tzinfo is not None, f"timestamp must be timezone-aware: {value}"
    return ts


def _db_ids_present(ids):
    conn = connect_operational_database(read_only=True)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT rag_record_id::text FROM rag_records WHERE rag_record_id::text = ANY(%s)",
                (list(ids),),
            )
            return {row[0] for row in cur.fetchall()}
    finally:
        conn.close()


def _count_records_at_or_before(cutoff):
    conn = connect_operational_database(read_only=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM rag_records WHERE timestamp_utc <= %s", (cutoff,))
            return cur.fetchone()[0]
    finally:
        conn.close()


def _median_timestamp():
    conn = connect_operational_database(read_only=True)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT timestamp_utc FROM rag_records ORDER BY timestamp_utc "
                "OFFSET (SELECT COUNT(*) / 2 FROM rag_records) LIMIT 1"
            )
            row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None
    return row[0]


def test_rag_backend_is_canonical_postgresql(ingested_rag_records):
    assert database_backend() == "postgres"


def test_rag_retrieval_bounded_provenance_untrusted_and_no_future_leakage(ingested_rag_records):
    cutoff = _median_timestamp()
    assert cutoff.tzinfo is not None
    records_before_cutoff = _count_records_at_or_before(cutoff)
    conn = connect_operational_database(read_only=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM rag_records WHERE timestamp_utc > %s", (cutoff,))
            future_records_in_store = cur.fetchone()[0]
    finally:
        conn.close()
    assert future_records_in_store > 0, "cutoff must exclude real records so leakage is testable"

    results = retrieve(as_of=cutoff, max_results=MAX_RESULTS)

    # B: bounded, and exactly the records available before the cutoff up to the limit
    assert len(results) <= MAX_RESULTS
    assert len(results) == min(MAX_RESULTS, records_before_cutoff)

    leakage = 0
    for item in results:
        # A/C: every item is a canonical row with provenance
        assert item.get("marker") != EMPTY_MARKER
        for field in ("rag_record_id", "source_type", "source_id", "timestamp_utc", "content_hash"):
            assert item.get(field), f"provenance field missing: {field}"
        # D: explicit untrusted marking
        assert item["content_role"] == UNTRUSTED_MARKER
        # E: historical cutoff
        if _parse_ts(item["timestamp_utc"]) > cutoff:
            leakage += 1

    assert leakage == 0, "RAG_FUTURE_LEAKAGE_COUNT must be 0"
    assert _db_ids_present([r["rag_record_id"] for r in results]) == {r["rag_record_id"] for r in results}


def test_rag_empty_retrieval_returns_exact_marker_distinct_from_errors(ingested_rag_records):
    empty_by_symbol = retrieve(symbol="PHASE4_NO_SUCH_SYMBOL", max_results=MAX_RESULTS)
    assert empty_by_symbol == [{"marker": EMPTY_MARKER}]

    empty_by_time = retrieve(
        as_of=datetime(1970, 1, 1, tzinfo=timezone.utc), max_results=MAX_RESULTS
    )
    assert empty_by_time == [{"marker": EMPTY_MARKER}]


def test_rag_unavailable_is_raised_not_reported_as_empty(ingested_rag_records, monkeypatch):
    # Real connection to a closed local port: infrastructure failure, not a fake repository.
    monkeypatch.setenv("GENESIS_DATABASE_URL", "postgresql://postgres@127.0.0.1:1/genesis_trading")
    with pytest.raises(RAGUnavailableError):
        retrieve(max_results=MAX_RESULTS)


def test_rag_retrieval_writes_nothing_to_business_or_rag_state(ingested_rag_records):
    before = take_business_state()
    retrieve(as_of=_median_timestamp(), max_results=MAX_RESULTS)
    retrieve(symbol="PHASE4_NO_SUCH_SYMBOL", max_results=MAX_RESULTS)
    after = take_business_state()
    for key in before:
        assert before[key] == after[key], f"retrieval changed {key}: {before[key]} -> {after[key]}"

