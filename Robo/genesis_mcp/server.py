"""Servidor MCP V1 + V2 do Gênesis: dez ferramentas consultivas via STDIO.

Estrutura modular:
- V1 (5 tools): system_health, list_open_positions, list_trades, get_trade_detail, get_risk_status
- V2 (5 tools): get_decision_trace, list_decisions, get_lstm_predictions, get_model_inventory, get_commander_decision

Este módulo importa somente runtime, repository, risk_guard, lineage e decision_lineage read-only.
Não importa worker, exchange, execution gate, commander ou qualquer módulo de escrita.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

_ROBO_ROOT = Path(__file__).resolve().parents[1]
if str(_ROBO_ROOT) not in sys.path:
    sys.path.append(str(_ROBO_ROOT))

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from genesis_runtime import database_backend, database_health, runtime_snapshot
from ui_backend import repository
from utils.risk_guard import evaluate_risk_limits

from genesis_mcp.common import (
    ErrorInfo,
    HealthResponse,
    ListDecisionsResponse,
    LSTMPredictionsResponse,
    ModelInventoryResponse,
    PositionsResponse,
    RiskResponse,
    TradeDetailResponse,
    TradesResponse,
    call_tool,
)
from genesis_mcp.common import LOGGER as audit_logger
from genesis_mcp.common.validators import valid_filter, valid_limit, valid_offset, valid_time
from genesis_mcp.v2 import (
    get_commander_decision_impl,
    get_decision_trace_impl,
    get_lstm_predictions_impl,
    get_model_inventory_impl,
    list_decisions_impl,
)

mcp = FastMCP("Genesis Quant Read-Only MCP")
TOOL_NAMES = [
    "get_system_health",
    "list_open_positions",
    "list_trades",
    "get_trade_detail",
    "get_risk_status",
    "get_decision_trace",
    "list_decisions",
    "get_lstm_predictions",
    "get_model_inventory",
    "get_commander_decision",
]


def _request_id() -> str:
    return uuid.uuid4().hex


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error(code: str, message: str) -> ErrorInfo:
    return ErrorInfo(code=code, message=message)


def _canonical_database_error() -> ErrorInfo | None:
    if database_backend() != "postgres":
        return _error("DATABASE_UNAVAILABLE", "MCP exige PostgreSQL canônico; SQLite não é uma fonte permitida")
    return None


def _worker_status() -> str:
    try:
        import psutil  # type: ignore
        for proc in psutil.process_iter(["cmdline"]):
            cmd = " ".join(proc.info.get("cmdline") or []).lower()
            if "trading_bot_geneses.py" in cmd and "mcp" not in cmd:
                return "running"
        return "stopped"
    except Exception:
        return "unknown"


def _kill_switch() -> bool:
    raw = os.getenv("RISK_KILL_SWITCH", "").strip().lower()
    if raw in {"1", "true", "yes", "on", "sim"}:
        return True
    flag = Path(__file__).resolve().parents[1] / "risk_kill.flag"
    try:
        if flag.exists():
            return True
    except OSError:
        return False
    return False


# V1 Tools

@mcp.tool()
def get_system_health() -> HealthResponse:
    """Consulta a saúde do PostgreSQL canônico, worker e risco; sem efeitos colaterais."""
    request_id = _request_id()

    def run() -> HealthResponse:
        from genesis_mcp.common import DatabaseHealth, RiskHealth, WorkerHealth

        db = database_health()
        db_model = DatabaseHealth(
            backend=str(db.get("backend", "unknown")),
            status=str(db.get("status", "unavailable")),
            canonical=bool(db.get("canonical", False)),
            target=db.get("target")
        )
        worker = _worker_status()
        risk_status = "unknown"
        if db_model.status == "healthy":
            try:
                risk_status = "blocked" if evaluate_risk_limits(symbol=None).get("blocked") else "ok"
            except Exception:
                risk_status = "unknown"
        status = "healthy" if db_model.status == "healthy" and db_model.canonical and risk_status != "unknown" and worker == "running" else "degraded"
        return HealthResponse(
            request_id=request_id,
            status=status,
            database=db_model,
            worker=WorkerHealth(status=worker),
            risk=RiskHealth(status=risk_status),
            environment=str(runtime_snapshot().get("environment", "unknown")),
            timestamp=_timestamp()
        )

    result = call_tool("get_system_health", request_id, run)
    if isinstance(result, Exception):
        from genesis_mcp.common import DatabaseHealth, RiskHealth, WorkerHealth
        return HealthResponse(
            request_id=request_id,
            status="unhealthy",
            database=None,
            worker=WorkerHealth(status="unknown"),
            risk=RiskHealth(status="unknown"),
            environment="unknown",
            timestamp=_timestamp(),
            error=_error("DATABASE_UNAVAILABLE", "Não foi possível consultar a saúde operacional")
        )
    return result


@mcp.tool()
def list_open_positions(symbol: str | None = None, limit: Annotated[int, Field(ge=1, le=500)] = 100) -> PositionsResponse:
    """Lista posições abertas persistidas no PostgreSQL canônico."""
    request_id = _request_id()

    def run() -> PositionsResponse:
        from genesis_mcp.common import Position

        canonical_error = _canonical_database_error()
        if canonical_error:
            return PositionsResponse(request_id=request_id, source="postgresql", count=0, positions=[], error=canonical_error)
        if limit < 1 or limit > 500:
            return PositionsResponse(
                request_id=request_id,
                source="postgresql",
                count=0,
                positions=[],
                error=_error("INVALID_ARGUMENT", "limit deve estar entre 1 e 500")
            )
        invalid = valid_filter(symbol, "symbol")
        if invalid:
            return PositionsResponse(request_id=request_id, source="postgresql", count=0, positions=[], error=invalid)
        rows = repository.list_open_positions(symbol=symbol, limit=limit, read_only=True)
        return PositionsResponse(request_id=request_id, source="postgresql", count=len(rows), positions=[Position(**row) for row in rows])

    result = call_tool("list_open_positions", request_id, run)
    if isinstance(result, Exception):
        return PositionsResponse(request_id=request_id, source="postgresql", count=0, positions=[], error=_error("DATABASE_UNAVAILABLE", "Não foi possível consultar posições"))
    return result


@mcp.tool()
def list_trades(
    limit: Annotated[int, Field(ge=1, le=500)] = 50,
    offset: Annotated[int, Field(ge=0)] = 0,
    symbol: str | None = None,
    strategy: str | None = None,
    status: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> TradesResponse:
    """Lista trades do PostgreSQL canônico com paginação limit/offset segura."""
    request_id = _request_id()

    def run() -> TradesResponse:
        from genesis_mcp.common import Trade

        canonical_error = _canonical_database_error()
        if canonical_error:
            return TradesResponse(request_id=request_id, source="postgresql", filters={}, count=0, total=0, has_more=False, trades=[], error=canonical_error)
        if limit < 1 or limit > 500 or offset < 0:
            return TradesResponse(
                request_id=request_id,
                source="postgresql",
                filters={},
                count=0,
                total=0,
                has_more=False,
                trades=[],
                error=_error("INVALID_ARGUMENT", "limit deve estar entre 1 e 500 e offset não pode ser negativo")
            )
        for name, value in (("symbol", symbol), ("strategy", strategy), ("status", status), ("start_time", start_time), ("end_time", end_time)):
            invalid = valid_time(value, name) if name in {"start_time", "end_time"} else valid_filter(value, name)
            if invalid:
                return TradesResponse(request_id=request_id, source="postgresql", filters={}, count=0, total=0, has_more=False, trades=[], error=invalid)
        if start_time and end_time and start_time > end_time:
            return TradesResponse(
                request_id=request_id,
                source="postgresql",
                filters={},
                count=0,
                total=0,
                has_more=False,
                trades=[],
                error=_error("INVALID_ARGUMENT", "start_time deve ser anterior a end_time")
            )
        if status is not None and status not in {"open", "closed"}:
            return TradesResponse(
                request_id=request_id,
                source="postgresql",
                filters={},
                count=0,
                total=0,
                has_more=False,
                trades=[],
                error=_error("INVALID_ARGUMENT", "status deve ser open ou closed")
            )
        rows = repository.list_trades(symbol=symbol, strategy=strategy, status=status, start_time=start_time, end_time=end_time, limit=limit, offset=offset, read_only=True)
        total = repository.count_trades(symbol=symbol, strategy=strategy, status=status, start_time=start_time, end_time=end_time, read_only=True)
        filters = {"symbol": symbol, "strategy": strategy, "status": status, "start_time": start_time, "end_time": end_time, "limit": limit, "offset": offset}
        return TradesResponse(request_id=request_id, source="postgresql", filters=filters, count=len(rows), total=total, has_more=offset + len(rows) < total, trades=[Trade(**row) for row in rows])

    result = call_tool("list_trades", request_id, run)
    if isinstance(result, Exception):
        return TradesResponse(request_id=request_id, source="postgresql", filters={}, count=0, total=0, has_more=False, trades=[], error=_error("DATABASE_UNAVAILABLE", "Não foi possível consultar trades"))
    return result


@mcp.tool()
def get_trade_detail(trade_id: str) -> TradeDetailResponse:
    """Consulta um trade pelo trade_id no PostgreSQL canônico."""
    request_id = _request_id()

    def run() -> TradeDetailResponse:
        canonical_error = _canonical_database_error()
        if canonical_error:
            return TradeDetailResponse(request_id=request_id, source="postgresql", error=canonical_error)
        invalid = valid_filter(trade_id, "trade_id")
        if invalid:
            return TradeDetailResponse(request_id=request_id, source="postgresql", error=invalid)
        row = repository.get_trade(trade_id.strip(), read_only=True)
        if not row:
            return TradeDetailResponse(request_id=request_id, source="postgresql", error=_error("NOT_FOUND", "trade_id não encontrado"))
        return TradeDetailResponse(request_id=request_id, source="postgresql", trade=row)

    result = call_tool("get_trade_detail", request_id, run)
    if isinstance(result, Exception):
        return TradeDetailResponse(request_id=request_id, source="postgresql", error=_error("DATABASE_UNAVAILABLE", "Não foi possível consultar o trade"))
    return result


@mcp.tool()
def get_risk_status() -> RiskResponse:
    """Consulta o estado de risco sem chamar exchange, gate de execução ou funções de escrita."""
    request_id = _request_id()

    def run() -> RiskResponse:
        db = database_health()
        if db.get("status") != "healthy" or not db.get("canonical") or database_backend() != "postgres":
            return RiskResponse(request_id=request_id, source="postgresql", status="unknown", kill_switch=_kill_switch(), error=_error("DATABASE_UNAVAILABLE", "PostgreSQL canônico indisponível"))
        state = evaluate_risk_limits(symbol=None)
        stats = repository.get_open_position_stats(read_only=True)
        kill = _kill_switch()
        blocked = kill or bool(state.get("blocked"))
        limits = {key: value for key, value in {
            "reference_balance": state.get("reference_balance", 0.0),
            "rolling_window_hours": state.get("rolling_window_hours", 24),
            "max_open_trades": os.getenv("MAX_OPEN_TRADES", os.getenv("MAX_SIMULTANEOUS_TRADES", "5")),
            "max_open_exposure_usdt": os.getenv("MAX_OPEN_EXPOSURE_USDT", "0"),
        }.items()}
        return RiskResponse(
            request_id=request_id,
            source="postgresql",
            status="blocked" if blocked else "allow",
            kill_switch=kill,
            open_positions=int(stats["count"]),
            exposure=float(stats["exposure"]),
            daily_pnl=float(state.get("day_net_pnl_global", 0.0)),
            daily_loss=float(state.get("day_loss_usdt_global", 0.0)),
            limits=limits,
            reasons=(["kill_switch"] if kill else []) + ([str(state.get("reason"))] if state.get("blocked") else [])
        )

    result = call_tool("get_risk_status", request_id, run)
    if isinstance(result, Exception):
        return RiskResponse(request_id=request_id, source="postgresql", status="unknown", kill_switch=_kill_switch(), error=_error("DATABASE_UNAVAILABLE", "Não foi possível consultar risco"))
    return result


# V2 Tools

@mcp.tool()
def get_decision_trace(decision_id: str) -> Any:
    """Consulta trilha completa de decisão append-only com todos os eventos."""
    request_id = _request_id()
    return get_decision_trace_impl(decision_id, request_id)


@mcp.tool()
def list_decisions(
    symbol: str | None = None,
    strategy: str | None = None,
    status: str | None = None,
    reason_code: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    trade_id: str | None = None,
    limit: Annotated[int, Field(ge=1, le=500)] = 50,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> ListDecisionsResponse:
    """Lista decisões com paginação e filtros múltiplos."""
    request_id = _request_id()
    return list_decisions_impl(
        symbol=symbol,
        strategy=strategy,
        status=status,
        reason_code=reason_code,
        start_time=start_time,
        end_time=end_time,
        trade_id=trade_id,
        limit=limit,
        offset=offset,
        request_id=request_id,
    )


@mcp.tool()
def get_lstm_predictions(
    symbol: str,
    limit: Annotated[int, Field(ge=1, le=500)] = 50,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> LSTMPredictionsResponse:
    """Consulta predições LSTM do modelo para um símbolo."""
    request_id = _request_id()
    return get_lstm_predictions_impl(symbol=symbol, limit=limit, offset=offset, request_id=request_id)


@mcp.tool()
def get_model_inventory() -> ModelInventoryResponse:
    """Escaneia filesystem para artefatos de modelos (weights, checkpoints, configs)."""
    request_id = _request_id()
    return get_model_inventory_impl(request_id=request_id)


@mcp.tool()
def get_commander_decision(decision_id: str) -> Any:
    """Consulta decisão do Comandante para um decision_id."""
    request_id = _request_id()
    return get_commander_decision_impl(decision_id, request_id)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
