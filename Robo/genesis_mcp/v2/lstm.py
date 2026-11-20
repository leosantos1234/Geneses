"""MCP V2 LSTM prediction tool: get_lstm_predictions."""
from __future__ import annotations

from typing import Annotated

from pydantic import Field

from genesis_runtime import database_url, database_backend

from genesis_mcp.common import ErrorInfo, LSTMPredictionsResponse, call_tool
from genesis_mcp.common.validators import valid_filter, valid_limit, valid_offset

try:
    import psycopg
except ImportError:
    psycopg = None


def _error(code: str, message: str) -> ErrorInfo:
    return ErrorInfo(code=code, message=message)


def _canonical_database_error() -> ErrorInfo | None:
    if database_backend() != "postgres":
        return ErrorInfo(
            code="DATABASE_UNAVAILABLE",
            message="MCP V2 LSTM exige PostgreSQL canônico; SQLite não é uma fonte permitida"
        )
    return None


def get_lstm_predictions_impl(
    symbol: str,
    limit: Annotated[int, Field(ge=1, le=500)] = 50,
    offset: Annotated[int, Field(ge=0)] = 0,
    request_id: str = "",
) -> LSTMPredictionsResponse:
    """Consulta predições LSTM do modelo para um símbolo."""
    def run() -> LSTMPredictionsResponse:
        canonical_error = _canonical_database_error()
        if canonical_error:
            return LSTMPredictionsResponse(
                request_id=request_id,
                source="postgresql",
                symbol=symbol,
                count=0,
                error=canonical_error
            )

        invalid = valid_filter(symbol, "symbol")
        if invalid:
            return LSTMPredictionsResponse(
                request_id=request_id,
                source="postgresql",
                symbol=symbol,
                count=0,
                error=invalid
            )

        invalid_limit = valid_limit(limit, "limit", 1, 500)
        if invalid_limit:
            return LSTMPredictionsResponse(
                request_id=request_id,
                source="postgresql",
                symbol=symbol,
                count=0,
                error=invalid_limit
            )

        invalid_offset = valid_offset(offset, "offset")
        if invalid_offset:
            return LSTMPredictionsResponse(
                request_id=request_id,
                source="postgresql",
                symbol=symbol,
                count=0,
                error=invalid_offset
            )

        try:
            if not psycopg or not database_url():
                return LSTMPredictionsResponse(
                    request_id=request_id,
                    source="postgresql",
                    symbol=symbol,
                    count=0,
                    error=_error("DATABASE_ERROR", "PostgreSQL unavailable or psycopg not installed")
                )

            conn = psycopg.connect(database_url())
            conn.execute("SET default_transaction_read_only = on")

            with conn.cursor() as cur:
                cur.execute(
                    """SELECT timestamp, symbol, direction_predicted, direction_real, confidence_score,
                              predicted_price, real_price, error_percent, model_name
                       FROM predictions_lstm
                       WHERE symbol = %s
                       ORDER BY timestamp DESC
                       LIMIT %s OFFSET %s""",
                    (symbol.upper(), limit, offset)
                )
                rows = cur.fetchall()
                predictions = []
                for row in rows:
                    predictions.append({
                        "timestamp": str(row[0]) if row[0] else None,
                        "symbol": row[1],
                        "direction": row[2],
                        "predicted_price": float(row[5]) if row[5] is not None else None,
                        "confidence": float(row[4]) if row[4] is not None else None,
                        "prediction_ts": str(row[0]) if row[0] else None,
                    })
                conn.close()
                return LSTMPredictionsResponse(
                    request_id=request_id,
                    source="postgresql",
                    symbol=symbol,
                    count=len(predictions),
                    predictions=predictions
                )
        except Exception as exc:
            return LSTMPredictionsResponse(
                request_id=request_id,
                source="postgresql",
                symbol=symbol,
                count=0,
                error=_error("DATABASE_ERROR", f"Erro ao consultar predições: {type(exc).__name__}")
            )

    result = call_tool("get_lstm_predictions", request_id, run)
    if isinstance(result, Exception):
        return LSTMPredictionsResponse(
            request_id=request_id,
            source="postgresql",
            symbol=symbol,
            count=0,
            error=_error("DATABASE_UNAVAILABLE", "Não foi possível consultar predições LSTM")
        )
    return result
