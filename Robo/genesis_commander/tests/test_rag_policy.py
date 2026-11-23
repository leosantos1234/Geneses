"""Tests for the advisory RAG maturity policy."""

from genesis_commander.orchestrator import (
    RAG_CONFIRMATION_MIN_CONTEXT,
    RAG_VALIDATION_MIN_CONTEXT,
    rag_review_status,
)


def test_rag_starts_in_observation_without_context():
    assert rag_review_status(0) == "OBSERVATION"


def test_rag_suggests_confirmation_after_minimum_context():
    assert rag_review_status(RAG_CONFIRMATION_MIN_CONTEXT) == "CONFIRMATION_FILTER_SUGGESTED"


def test_rag_suggests_validation_after_more_context():
    assert rag_review_status(RAG_VALIDATION_MIN_CONTEXT) == "VALIDATION_SUGGESTED"


def test_rag_policy_never_grants_execution_authority():
    assert rag_review_status(RAG_VALIDATION_MIN_CONTEXT + 100) == "VALIDATION_SUGGESTED"
