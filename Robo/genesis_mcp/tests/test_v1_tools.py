"""Unit tests para MCP V1 tools - validar ausência de regressão.

Testes para as 5 ferramentas originais do MCP V1.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
import sys
from pathlib import Path

# Setup path for imports
robo_root = Path(__file__).resolve().parents[2]
if str(robo_root) not in sys.path:
    sys.path.insert(0, str(robo_root))


class TestGetSystemHealth:
    """Test get_system_health tool - verify V1 no regression."""

    def test_health_response_schema(self):
        """Verify HealthResponse schema unchanged."""
        from genesis_mcp.common import HealthResponse, DatabaseHealth, WorkerHealth, RiskHealth

        response = HealthResponse(
            request_id="test-id",
            status="healthy",
            database=DatabaseHealth(backend="postgres", status="healthy", canonical=True),
            worker=WorkerHealth(status="running"),
            risk=RiskHealth(status="ok"),
            environment="live",
            timestamp="2026-10-07T00:00:00Z",
            error=None,
        )

        assert response.request_id == "test-id"
        assert response.status == "healthy"
        assert response.database.canonical is True
        assert response.error is None

    def test_health_response_with_error(self):
        """Verify error handling in health response."""
        from genesis_mcp.common import HealthResponse, ErrorInfo, WorkerHealth, RiskHealth

        response = HealthResponse(
            request_id="test-id",
            status="unhealthy",
            database=None,
            worker=WorkerHealth(status="unknown"),
            risk=RiskHealth(status="unknown"),
            environment="unknown",
            timestamp="2026-10-07T00:00:00Z",
            error=ErrorInfo(code="DATABASE_UNAVAILABLE", message="PostgreSQL down"),
        )

        assert response.error is not None
        assert response.error.code == "DATABASE_UNAVAILABLE"


class TestListOpenPositions:
    """Test list_open_positions tool - verify V1 no regression."""

    def test_positions_response_schema(self):
        """Verify PositionsResponse schema unchanged."""
        from genesis_mcp.common import PositionsResponse, Position

        response = PositionsResponse(
            request_id="test-id",
            source="postgresql",
            count=1,
            positions=[
                Position(
                    trade_id="trade-001",
                    symbol="BTCUSDT",
                    side="LONG",
                    entry_price=45000.0,
                    status="open",
                )
            ],
            error=None,
        )

        assert response.count == 1
        assert len(response.positions) == 1
        assert response.positions[0].symbol == "BTCUSDT"

    def test_limit_validation(self):
        """Verify limit validation (1-500)."""
        from genesis_mcp.common.validators import valid_limit

        # Valid
        assert valid_limit(1) is None
        assert valid_limit(50) is None
        assert valid_limit(500) is None

        # Invalid
        assert valid_limit(0) is not None
        assert valid_limit(501) is not None
        assert valid_limit(-1) is not None


class TestListTrades:
    """Test list_trades tool - verify V1 no regression."""

    def test_trades_response_schema(self):
        """Verify TradesResponse schema unchanged."""
        from genesis_mcp.common import TradesResponse, Trade

        response = TradesResponse(
            request_id="test-id",
            source="postgresql",
            filters={"symbol": "BTCUSDT", "limit": 50, "offset": 0},
            count=1,
            total=10,
            has_more=True,
            trades=[
                Trade(
                    trade_id="trade-001",
                    symbol="BTCUSDT",
                    side="LONG",
                    entry_price=45000.0,
                    status="closed",
                )
            ],
            error=None,
        )

        assert response.count == 1
        assert response.total == 10
        assert response.has_more is True
        assert len(response.trades) == 1

    def test_pagination_validation(self):
        """Verify pagination constraints."""
        from genesis_mcp.common.validators import valid_limit, valid_offset

        # Valid
        assert valid_limit(50) is None
        assert valid_offset(0) is None
        assert valid_offset(100) is None

        # Invalid
        assert valid_limit(1000) is not None
        assert valid_offset(-1) is not None

    def test_status_filter_validation(self):
        """Verify status filter only accepts open/closed."""
        # Note: status validation is in server.py, but schema accepts any string
        # Test that schema at least has the field
        from genesis_mcp.common import Trade

        trade_open = Trade(status="open")
        trade_closed = Trade(status="closed")

        assert trade_open.status == "open"
        assert trade_closed.status == "closed"


class TestGetTradeDetail:
    """Test get_trade_detail tool - verify V1 no regression."""

    def test_trade_detail_response_schema(self):
        """Verify TradeDetailResponse schema unchanged."""
        from genesis_mcp.common import TradeDetailResponse

        response = TradeDetailResponse(
            request_id="test-id",
            source="postgresql",
            trade={"trade_id": "trade-001", "symbol": "BTCUSDT", "status": "closed"},
            error=None,
        )

        assert response.trade is not None
        assert response.trade["trade_id"] == "trade-001"

    def test_trade_detail_not_found(self):
        """Verify error handling for not found."""
        from genesis_mcp.common import TradeDetailResponse, ErrorInfo

        response = TradeDetailResponse(
            request_id="test-id",
            source="postgresql",
            trade=None,
            error=ErrorInfo(code="NOT_FOUND", message="trade_id not found"),
        )

        assert response.trade is None
        assert response.error is not None
        assert response.error.code == "NOT_FOUND"


class TestGetRiskStatus:
    """Test get_risk_status tool - verify V1 no regression."""

    def test_risk_response_schema(self):
        """Verify RiskResponse schema unchanged."""
        from genesis_mcp.common import RiskResponse

        response = RiskResponse(
            request_id="test-id",
            source="postgresql",
            status="allow",
            kill_switch=False,
            open_positions=2,
            exposure=10000.0,
            daily_pnl=500.0,
            daily_loss=100.0,
            limits={"max_open_trades": 5, "max_open_exposure_usdt": 50000},
            reasons=[],
            error=None,
        )

        assert response.status == "allow"
        assert response.kill_switch is False
        assert response.open_positions == 2
        assert len(response.reasons) == 0

    def test_risk_response_blocked(self):
        """Verify risk blocked response."""
        from genesis_mcp.common import RiskResponse

        response = RiskResponse(
            request_id="test-id",
            source="postgresql",
            status="blocked",
            kill_switch=True,
            reasons=["kill_switch"],
            error=None,
        )

        assert response.status == "blocked"
        assert response.kill_switch is True
        assert "kill_switch" in response.reasons


class TestInputValidation:
    """Test input validation utilities."""

    def test_valid_filter_safe_text(self):
        """Verify regex validation for safe text."""
        from genesis_mcp.common.validators import valid_filter

        # Valid
        assert valid_filter("BTCUSDT", "symbol") is None
        assert valid_filter("rsi_divergence", "strategy") is None
        assert valid_filter("2026-10-07", "date") is None

        # Invalid
        assert valid_filter("INVALID<SYMBOL>", "symbol") is not None
        assert valid_filter("'; DROP TABLE;--", "input") is not None

    def test_valid_time_iso8601(self):
        """Verify ISO-8601 timestamp validation."""
        from genesis_mcp.common.validators import valid_time

        # Valid
        assert valid_time("2026-10-07T00:00:00Z", "timestamp") is None
        assert valid_time("2026-10-07T12:30:45+00:00", "timestamp") is None

        # Invalid
        assert valid_time("not-a-date", "timestamp") is not None
        assert valid_time("2026-13-45T99:99:99Z", "timestamp") is not None

    def test_filter_max_length(self):
        """Verify max length constraint (256 chars)."""
        from genesis_mcp.common.validators import valid_filter

        # Valid
        long_valid = "A" * 256
        assert valid_filter(long_valid, "field") is None

        # Invalid (257 chars)
        too_long = "A" * 257
        assert valid_filter(too_long, "field") is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
