import pytest

from genesis_mcp.common import ErrorInfo, TradesResponse
from genesis_mcp.v2 import inventory


def test_inventory_sees_canonical_runtime_models(monkeypatch):
    monkeypatch.setattr(inventory, "database_backend", lambda: "postgres")
    result = inventory.get_model_inventory_impl(request_id="test")
    names = {item.name for item in result.models}
    assert result.count > 0
    for symbol in ("btc", "eth", "bnb", "sol", "xrp"):
        assert f"lstm_{symbol}_next.keras" in names
    assert all("password" not in item.path.lower() for item in result.models)


def test_backend_error_is_not_valid_empty_result():
    result = TradesResponse(
        request_id="test",
        source="postgresql",
        filters={},
        count=0,
        total=0,
        has_more=False,
        trades=[],
        error=ErrorInfo(code="DATABASE_UNAVAILABLE", message="sanitized"),
    )
    assert result.status == "error"
    assert result.backend_ok is False
    assert result.query_ok is False


def test_true_empty_result_remains_ok():
    result = TradesResponse(
        request_id="test",
        source="postgresql",
        filters={"symbol": "NO_SUCH_SYMBOL"},
        count=0,
        total=0,
        has_more=False,
        trades=[],
    )
    assert result.status == "ok"
    assert result.backend_ok is True
    assert result.query_ok is True
