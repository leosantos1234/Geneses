# MCP V1 + V2 API Reference

**Genesis Quant Read-Only MCP** — 10 tools for system monitoring, trading analysis, and decision lineage.

All tools are **read-only** with **zero writes** to the operational database.  
**PostgreSQL 16.15** (`genesis_trading` schema) is the canonical backend.  
**SQLite fallback is NOT allowed** — all tools reject sqlite backend.

---

## V1 Tools (Original — 5)

### 1. `get_system_health()`

Queries PostgreSQL, worker, and risk system health without side effects.

**Signature:**
```python
def get_system_health() -> HealthResponse
```

**Response:**
```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "healthy|degraded|unhealthy",
  "database": {
    "backend": "postgres",
    "status": "healthy|unavailable",
    "canonical": true,
    "target": null
  },
  "worker": {
    "status": "running|stopped|unknown"
  },
  "risk": {
    "status": "ok|blocked|unknown"
  },
  "environment": "live|sandbox|backtest",
  "timestamp": "2026-10-07T12:00:00Z",
  "error": null
}
```

**Status Logic:**
- `healthy`: PostgreSQL healthy AND canonical AND worker running AND risk ok
- `degraded`: one or more conditions not met
- `unhealthy`: PostgreSQL down or all systems unavailable

---

### 2. `list_open_positions(symbol=None, limit=100)`

Lists open positions from `trades` table where `status='open'`.

**Signature:**
```python
def list_open_positions(
    symbol: str | None = None,
    limit: int = 100  # 1-500
) -> PositionsResponse
```

**Parameters:**
- `symbol`: Optional, case-insensitive, regex validated
- `limit`: Default 100, max 500

**Response:**
```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440001",
  "source": "postgresql",
  "count": 3,
  "positions": [
    {
      "trade_id": "trade-001",
      "timestamp": "2026-10-07T10:00:00Z",
      "symbol": "BTCUSDT",
      "side": "LONG",
      "entry_price": 45000.0,
      "stop_loss": 44000.0,
      "take_profit": 46000.0,
      "trailing_stop": null,
      "strategy": "rsi_divergence",
      "status": "open"
    }
  ],
  "error": null
}
```

**Errors:**
- `INVALID_ARGUMENT`: symbol format invalid or limit out of bounds
- `DATABASE_UNAVAILABLE`: PostgreSQL down

---

### 3. `list_trades(limit=50, offset=0, symbol=None, strategy=None, status=None, start_time=None, end_time=None)`

Lists trades with pagination and multi-field filtering.

**Signature:**
```python
def list_trades(
    limit: int = 50,                    # 1-500
    offset: int = 0,                    # ≥ 0
    symbol: str | None = None,
    strategy: str | None = None,
    status: str | None = None,          # "open" | "closed"
    start_time: str | None = None,      # ISO-8601
    end_time: str | None = None         # ISO-8601
) -> TradesResponse
```

**Response:**
```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440002",
  "source": "postgresql",
  "filters": {
    "symbol": "BTCUSDT",
    "strategy": "rsi_divergence",
    "status": "closed",
    "start_time": "2026-10-01T00:00:00Z",
    "end_time": "2026-10-07T00:00:00Z",
    "limit": 50,
    "offset": 0
  },
  "count": 5,
  "total": 47,
  "has_more": true,
  "trades": [
    {
      "trade_id": "trade-001",
      "timestamp": "2026-10-07T10:00:00Z",
      "symbol": "BTCUSDT",
      "side": "LONG",
      "entry_price": 45000.0,
      "exit_price": 45500.0,
      "profit_loss": 500.0,
      "stop_loss": 44000.0,
      "take_profit": 46000.0,
      "trailing_stop": null,
      "strategy": "rsi_divergence",
      "decision_source": "candle_close",
      "regime": "trending",
      "tendencia": "up",
      "status": "closed"
    }
  ],
  "error": null
}
```

**Pagination:**
- `has_more`: true if more results exist beyond current offset+limit
- Use `offset += limit` for next page

**Errors:**
- `INVALID_ARGUMENT`: filter format invalid, limit/offset out of bounds, start_time > end_time
- `DATABASE_UNAVAILABLE`: PostgreSQL down

---

### 4. `get_trade_detail(trade_id)`

Retrieves full details for a single trade.

**Signature:**
```python
def get_trade_detail(trade_id: str) -> TradeDetailResponse
```

**Parameters:**
- `trade_id`: Required, regex validated

**Response:**
```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440003",
  "source": "postgresql",
  "trade": {
    "trade_id": "trade-001",
    "timestamp": "2026-10-07T10:00:00Z",
    "symbol": "BTCUSDT",
    "side": "LONG",
    "entry_price": 45000.0,
    "exit_price": 45500.0,
    "profit_loss": 500.0,
    "stop_loss": 44000.0,
    "take_profit": 46000.0,
    "strategy": "rsi_divergence",
    "status": "closed",
    "decision_id": "550e8400-e29b-41d4-a716-446655440050"
  },
  "error": null
}
```

**Errors:**
- `INVALID_ARGUMENT`: trade_id format invalid
- `NOT_FOUND`: trade_id doesn't exist
- `DATABASE_UNAVAILABLE`: PostgreSQL down

---

### 5. `get_risk_status()`

Returns current risk state, kill switch, and position stats.

**Signature:**
```python
def get_risk_status() -> RiskResponse
```

**Response:**
```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440004",
  "source": "postgresql",
  "status": "allow|blocked",
  "kill_switch": false,
  "open_positions": 2,
  "exposure": 10000.0,
  "daily_pnl": 500.0,
  "daily_loss": 100.0,
  "limits": {
    "reference_balance": 100000.0,
    "rolling_window_hours": 24,
    "max_open_trades": 5,
    "max_open_exposure_usdt": 50000
  },
  "reasons": [],
  "error": null
}
```

**Status:**
- `allow`: All limits OK, kill_switch off
- `blocked`: Risk limits breached or kill_switch active

**Reasons:**
- `["kill_switch"]` if manual stop is active
- `["MAX_OPEN_TRADES_REACHED"]` if position limit exceeded
- etc. (multiple reasons possible)

**Errors:**
- `DATABASE_UNAVAILABLE`: PostgreSQL down

---

## V2 Tools (New — 5)

### 6. `get_decision_trace(decision_id)`

Retrieves complete append-only decision trace with all events.

**Signature:**
```python
def get_decision_trace(decision_id: str) -> DecisionTraceResponse
```

**Parameters:**
- `decision_id`: Required, must be valid UUID

**Response:**
```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440005",
  "source": "postgresql",
  "trace": {
    "decision_id": "550e8400-e29b-41d4-a716-446655440050",
    "created_at": "2026-10-07T10:00:00Z",
    "updated_at": "2026-10-07T10:00:05Z",
    "symbol": "BTCUSDT",
    "timeframe": "1h",
    "strategy": "rsi_divergence",
    "environment": "live",
    "mode": "paper",
    "final_status": "SUCCESS",
    "trade_id": "trade-001",
    "order_id": "order-001",
    "position_id": null,
    "schema_version": "1.0",
    "decision_context": {
      "rsi": 35,
      "price": 45000,
      "volume": 1000
    },
    "config_fingerprint": "abc123def456",
    "code_version": "v2.0",
    "strategy_version": "v1.5",
    "model_version": "v2_btc_protecao_antecipada",
    "reason_code": null,
    "reason_message": null,
    "legacy": false,
    "lineage_quality": "complete",
    "events": [
      {
        "event_id": 1,
        "event_ts": "2026-10-07T10:00:00Z",
        "stage": "CANDIDATE",
        "event_type": "DECISION_CREATED",
        "component": "decision_lineage",
        "status": "INFO",
        "reason_code": null,
        "duration_ms": 0.5,
        "payload": {},
        "schema_version": "1.0"
      },
      {
        "event_id": 2,
        "event_ts": "2026-10-07T10:00:01Z",
        "stage": "STRATEGY",
        "event_type": "STRATEGY_SELECTED",
        "component": "strategy",
        "status": "SUCCESS",
        "reason_code": null,
        "duration_ms": 100,
        "payload": {"score": 0.95},
        "schema_version": "1.0"
      },
      {
        "event_id": 3,
        "event_ts": "2026-10-07T10:00:02Z",
        "stage": "COMMANDER",
        "event_type": "COMMANDER_ALLOWED",
        "component": "commander",
        "status": "SUCCESS",
        "reason_code": null,
        "duration_ms": 50,
        "payload": {},
        "schema_version": "1.0"
      }
    ]
  },
  "error": null
}
```

**Events (20+ types):**
- DECISION_CREATED, MARKET_CONTEXT_CAPTURED, STRATEGY_EVALUATED, STRATEGY_SELECTED
- COMMANDER_EVALUATED, COMMANDER_ALLOWED, COMMANDER_BLOCKED
- RISK_EVALUATED, RISK_ALLOWED, RISK_BLOCKED
- EXECUTION_GATE_EVALUATED, EXECUTION_BLOCKED
- ORDER_INTENT_CREATED, ORDER_SUBMITTED, ORDER_ACCEPTED, ORDER_REJECTED
- FILL_RECORDED, POSITION_OPENED, DECISION_FINALIZED, DECISION_ERROR

**Errors:**
- `INVALID_ARGUMENT`: decision_id is not a valid UUID
- `NOT_FOUND`: decision_id doesn't exist
- `DATABASE_UNAVAILABLE`: PostgreSQL down

---

### 7. `list_decisions(symbol=None, strategy=None, status=None, reason_code=None, start_time=None, end_time=None, trade_id=None, limit=50, offset=0)`

Lists decision traces with multi-field filtering and pagination.

**Signature:**
```python
def list_decisions(
    symbol: str | None = None,
    strategy: str | None = None,
    status: str | None = None,               # final_status
    reason_code: str | None = None,
    start_time: str | None = None,           # ISO-8601
    end_time: str | None = None,             # ISO-8601
    trade_id: str | None = None,
    limit: int = 50,                         # 1-500
    offset: int = 0                          # ≥ 0
) -> ListDecisionsResponse
```

**Response:**
```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440006",
  "source": "postgresql",
  "filters": {
    "symbol": "BTCUSDT",
    "strategy": "rsi_divergence",
    "status": "SUCCESS",
    "reason_code": null,
    "start_time": "2026-10-01T00:00:00Z",
    "end_time": "2026-10-07T00:00:00Z",
    "trade_id": null,
    "limit": 50,
    "offset": 0
  },
  "count": 10,
  "total": 42,
  "has_more": true,
  "decisions": [
    {
      "decision_id": "550e8400-e29b-41d4-a716-446655440050",
      "created_at": "2026-10-07T10:00:00Z",
      "symbol": "BTCUSDT",
      "timeframe": "1h",
      "strategy": "rsi_divergence",
      "final_status": "SUCCESS",
      "trade_id": "trade-001",
      "order_id": "order-001",
      "reason_code": null,
      "lineage_quality": "complete"
    }
  ],
  "error": null
}
```

**Ordering:** `created_at DESC, decision_id DESC`

**Errors:**
- `INVALID_ARGUMENT`: filter format invalid, limit/offset out of bounds, start_time > end_time
- `DATABASE_UNAVAILABLE`: PostgreSQL down

---

### 8. `get_lstm_predictions(symbol, limit=50, offset=0)`

Queries LSTM model predictions for a symbol.

**Signature:**
```python
def get_lstm_predictions(
    symbol: str,                             # Required
    limit: int = 50,                         # 1-500
    offset: int = 0                          # ≥ 0
) -> LSTMPredictionsResponse
```

**Parameters:**
- `symbol`: Required, case-insensitive, regex validated (e.g., "BTCUSDT")

**Response:**
```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440007",
  "source": "postgresql",
  "symbol": "BTCUSDT",
  "count": 5,
  "predictions": [
    {
      "timestamp": "2026-10-07T10:00:00Z",
      "symbol": "BTCUSDT",
      "direction": "UP",
      "confidence": 0.95,
      "prediction_ts": "2026-10-07T10:00:00Z"
    },
    {
      "timestamp": "2026-10-07T11:00:00Z",
      "symbol": "BTCUSDT",
      "direction": "DOWN",
      "confidence": 0.72,
      "prediction_ts": "2026-10-07T11:00:00Z"
    }
  ],
  "error": null
}
```

**Data Source:** `predictions_lstm` table (append-only log)

**Ordering:** `prediction_ts DESC`

**Errors:**
- `INVALID_ARGUMENT`: symbol format invalid or limit/offset out of bounds
- `DATABASE_ERROR`: query failed
- `DATABASE_UNAVAILABLE`: PostgreSQL down

---

### 9. `get_model_inventory()`

Scans filesystem for model artifacts (weights, checkpoints, configs).

**Signature:**
```python
def get_model_inventory() -> ModelInventoryResponse
```

**Response:**
```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440008",
  "source": "filesystem",
  "count": 3,
  "models": [
    {
      "name": "model_v2_lstm.pt",
      "version": "2",
      "path": "models/model_v2_lstm.pt",
      "size_bytes": 102400,
      "modified_at": "2026-10-07T09:00:00Z",
      "model_type": "pytorch"
    },
    {
      "name": "strategy_weights_v1.h5",
      "version": "1",
      "path": "weights/strategy_weights_v1.h5",
      "size_bytes": 51200,
      "modified_at": "2026-10-06T15:00:00Z",
      "model_type": "keras"
    },
    {
      "name": "decision_classifier.pkl",
      "version": null,
      "path": "artifacts/decision_classifier.pkl",
      "size_bytes": 25600,
      "modified_at": "2026-10-05T12:00:00Z",
      "model_type": "pickle"
    }
  ],
  "error": null
}
```

**Scanned Directories:**
- `models/`
- `checkpoints/`
- `weights/`
- `artifacts/`

**Recognized Types:**
- PyTorch: `.pt`, `.pth`
- Keras: `.h5`, `.keras`
- Pickle: `.pkl`
- Joblib: `.joblib`
- ONNX: `.onnx`

**Ordering:** `modified_at DESC` (newest first)

**Errors:**
- `FILESYSTEM_ERROR`: scan failed

---

### 10. `get_commander_decision(decision_id)`

Retrieves Commander's decision for a decision_id.

**Signature:**
```python
def get_commander_decision(decision_id: str) -> CommanderDecisionResponse
```

**Parameters:**
- `decision_id`: Required, must be valid UUID

**Response (Allowed):**
```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440009",
  "source": "postgresql",
  "decision": {
    "decision_id": "550e8400-e29b-41d4-a716-446655440050",
    "created_at": "2026-10-07T10:00:00Z",
    "symbol": "BTCUSDT",
    "strategy": "rsi_divergence",
    "commander_status": "allowed",
    "reason_code": null,
    "trade_id": "trade-001"
  },
  "error": null
}
```

**Response (Blocked):**
```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440009",
  "source": "postgresql",
  "decision": {
    "decision_id": "550e8400-e29b-41d4-a716-446655440051",
    "created_at": "2026-10-07T11:00:00Z",
    "symbol": "ETHUSDT",
    "strategy": "bollinger_squeeze",
    "commander_status": "blocked",
    "reason_code": "MAX_POSITIONS_REACHED",
    "trade_id": null
  },
  "error": null
}
```

**Commander Status:**
- `allowed`: COMMANDER_ALLOWED event found
- `blocked`: COMMANDER_BLOCKED event found
- `null`: No commander event yet

**Data Source:** `decision_trace` + `decision_events` (lineage append-only)

**Errors:**
- `INVALID_ARGUMENT`: decision_id is not a valid UUID
- `NOT_FOUND`: decision_id doesn't exist
- `DATABASE_UNAVAILABLE`: PostgreSQL down

---

## Error Codes (All Tools)

| Code | HTTP | Meaning | Retry? |
|------|------|---------|--------|
| `DATABASE_UNAVAILABLE` | 503 | PostgreSQL down or unreachable | Yes |
| `DATABASE_ERROR` | 500 | Query execution failed | Possibly |
| `NOT_FOUND` | 404 | Resource doesn't exist | No |
| `INVALID_ARGUMENT` | 400 | Bad input (format, bounds, etc) | No |
| `INTERNAL_ERROR` | 500 | Unexpected exception | Possibly |
| `SERVICE_UNAVAILABLE` | 503 | Tool timed out | Yes |
| `FILESYSTEM_ERROR` | 500 | Directory scan failed | Possibly |

---

## Validation Rules

### String Fields
- **Regex:** `^[A-Za-z0-9_.:/|+\- ]{1,256}$`
- **Max length:** 256 characters
- **Examples:** `BTCUSDT`, `rsi_divergence`, `2026-10-07`
- **Rejected:** SQL injections, HTML, special chars

### Integer Fields
- **limit:** 1–500 (default varies)
- **offset:** ≥ 0 (default 0)
- Both required to be within stated bounds

### Timestamp Fields
- **Format:** ISO-8601 with timezone
- **Examples:** `2026-10-07T12:00:00Z`, `2026-10-07T12:00:00+00:00`
- **Validation:** `datetime.fromisoformat()` with `Z` → `+00:00` conversion

### UUID Fields
- **Format:** RFC 4122 (standard UUID v4)
- **Validation:** `uuid.UUID(str)` success/fail
- **Examples:** `550e8400-e29b-41d4-a716-446655440000`

---

## Common Patterns

### Pagination
```python
# First page
response = list_trades(limit=50, offset=0)
print(f"Page 1: {response.count} trades, more={response.has_more}")

# Next page
if response.has_more:
    response = list_trades(limit=50, offset=50)
    print(f"Page 2: {response.count} trades, more={response.has_more}")
```

### Filtering by Date Range
```python
response = list_decisions(
    start_time="2026-10-01T00:00:00Z",
    end_time="2026-10-07T23:59:59Z",
    limit=100
)
```

### Symbol-Specific Analysis
```python
positions = list_open_positions(symbol="BTCUSDT")
trades = list_trades(symbol="BTCUSDT", status="closed", limit=50)
predictions = get_lstm_predictions(symbol="BTCUSDT", limit=20)
```

### Error Handling
```python
response = get_decision_trace("invalid-uuid")
if response.error:
    print(f"Error: {response.error.code} — {response.error.message}")
else:
    print(f"Trace: {response.trace.decision_id}")
```

---

## Performance Notes

- **Tool call audit:** All calls logged to stderr with `mcp_audit` prefix
- **Query timeout:** 5 seconds per database operation (configurable)
- **Paginat limit:** Enforced 1–500 per query (prevents resource exhaustion)
- **Filesystem scan:** Recursive, can take seconds on large model directories

---

## Database Backend

**PostgreSQL 16.15** is the sole operational backend.

**Tables:**
- `trades` — historical trade log
- `decision_trace` — decision metadata
- `decision_events` — append-only decision events
- `predictions_lstm` — LSTM model predictions (Sprint 5 new)

**Schema:** `public` (default PostgreSQL schema)

**Connection:** Configured via `GENESIS_DATABASE_URL` environment variable

**Failsafe:** If PostgreSQL unavailable, all tools return `DATABASE_UNAVAILABLE` error (fail-closed).

---

**Version:** 1.0  
**Last Updated:** 2026-10-07  
**Spec Status:** STABLE  
**Maintained by:** Genesis Quant Team
