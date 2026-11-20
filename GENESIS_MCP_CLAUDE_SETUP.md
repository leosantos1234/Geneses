# Genesis Quant MCP — Claude Code Setup Guide

**Date:** 2026-10-07  
**MCP Server:** Genesis MCP V1+V2  
**Client:** Claude Code (desktop/web/CLI)  
**Transport:** STDIO  

---

## Quick Start

### 1. Prerequisites

- ✅ Python 3.12+
- ✅ PostgreSQL 16 (local or remote)
- ✅ psycopg3 (driver installed)
- ✅ mcp SDK (FastMCP)

### 2. Environment Setup

```bash
# Set database backend to PostgreSQL
export GENESIS_DATABASE_BACKEND=postgres
export GENESIS_DATABASE_URL=postgresql://user:password@localhost:5432/genesis_trading

# Optional: override timeouts (default 5s)
export GENESIS_DB_CONNECT_TIMEOUT=10
```

### 3. Start MCP Server

```bash
# From project root
cd C:\Users\NPO\Desenvolvimento\genesis
python -m genesis_mcp.server

# Expected output:
# (MCP server listening on stdio)
# Ready to receive JSON-RPC initialize
```

### 4. Configure Claude Code

#### Option A: VS Code Extension

1. **Open Settings:**
   - File → Preferences → Settings
   - Search: "claude code"

2. **Add MCP Server Configuration:**
   ```json
   {
     "Claude Code": {
       "mcp": {
         "servers": {
           "genesis": {
             "command": "python",
             "args": ["-m", "genesis_mcp.server"],
             "cwd": "C:\\Users\\NPO\\Desenvolvimento\\genesis\\Robo",
             "env": {
               "GENESIS_DATABASE_BACKEND": "postgres",
               "GENESIS_DATABASE_URL": "postgresql://localhost/genesis_trading"
             }
           }
         }
       }
     }
   }
   ```

3. **Restart VS Code**

#### Option B: Claude Code CLI

```bash
# Add to ~/.claude/config.json
{
  "mcp": {
    "servers": {
      "genesis": {
        "command": "python",
        "args": ["-m", "genesis_mcp.server"],
        "cwd": "C:/Users/NPO/Desenvolvimento/genesis/Robo",
        "env": {
          "GENESIS_DATABASE_BACKEND": "postgres",
          "GENESIS_DATABASE_URL": "postgresql://localhost/genesis_trading"
        }
      }
    }
  }
}
```

#### Option C: Claude.ai Web

If Claude Code is integrated with claude.ai:

1. Go to [claude.ai/code/settings](https://claude.ai/code/settings)
2. Add MCP server:
   - Name: `genesis`
   - Command: `python -m genesis_mcp.server`
   - CWD: `C:\Users\NPO\Desenvolvimento\genesis\Robo`
   - Environment: (as above)

---

## Server Startup Verification

### Test 1: Can the server start?

```bash
cd C:\Users\NPO\Desenvolvimento\genesis\Robo
python -m genesis_mcp.server

# Should NOT error; should accept stdin
# (Will be waiting for JSON-RPC messages)
```

### Test 2: Can Claude Code connect?

In Claude Code, try:

```
/mcp list
# Should show: genesis (or similar)
```

### Test 3: Can Claude Code list tools?

```
/mcp genesis tools/list
# Should return: 10 tools (get_system_health, list_open_positions, ...)
```

---

## Available Tools

### V1 Tools (Original)

#### 1. `get_system_health`
**Purpose:** Check database, worker, and risk status  
**Usage:** `@genesis get_system_health()`  
**Returns:** Database status, worker status, risk status, environment

#### 2. `list_open_positions`
**Purpose:** List currently open trading positions  
**Parameters:**
- `symbol` (optional): Filter by symbol (e.g., "BTCUSDT")
- `limit` (1-500, default 100): Number of positions

**Usage:** `@genesis list_open_positions(symbol="BTCUSDT", limit=50)`

#### 3. `list_trades`
**Purpose:** Query historical trades with filters and pagination  
**Parameters:**
- `limit` (1-500, default 50)
- `offset` (>=0, default 0): Pagination offset
- `symbol` (optional): Filter by symbol
- `strategy` (optional): Filter by strategy name
- `status` (optional): "open" or "closed"
- `start_time` (optional): ISO-8601 datetime
- `end_time` (optional): ISO-8601 datetime

**Usage:** `@genesis list_trades(symbol="BTCUSDT", status="closed", limit=100)`

#### 4. `get_trade_detail`
**Purpose:** Get details of a specific trade  
**Parameters:**
- `trade_id` (required): Trade ID to look up

**Usage:** `@genesis get_trade_detail(trade_id="trade_12345")`

#### 5. `get_risk_status`
**Purpose:** Check current risk limits and kill switch  
**Usage:** `@genesis get_risk_status()`  
**Returns:** Risk status, open positions, exposure, daily P&L, limits

### V2 Tools (New)

#### 6. `get_decision_trace`
**Purpose:** Get complete decision lineage for a trade  
**Parameters:**
- `decision_id` (required): UUID of decision

**Usage:** `@genesis get_decision_trace(decision_id="550e8400-e29b-41d4-a716-446655440000")`  
**Returns:** Decision record with all events (STRATEGY_EVALUATED, COMMANDER_ALLOWED, etc.)

#### 7. `list_decisions`
**Purpose:** Query decisions with filters and pagination  
**Parameters:**
- `symbol` (optional): Filter by symbol
- `strategy` (optional): Filter by strategy
- `status` (optional): Filter by final status
- `reason_code` (optional): Filter by reason code
- `start_time` (optional): ISO-8601 start
- `end_time` (optional): ISO-8601 end
- `trade_id` (optional): Filter by linked trade
- `limit` (1-500, default 50)
- `offset` (>=0, default 0)

**Usage:** `@genesis list_decisions(symbol="BTCUSDT", limit=50, offset=0)`  
**Returns:** Paginated decisions with has_more flag

#### 8. `get_lstm_predictions`
**Purpose:** Get LSTM model predictions for a symbol  
**Parameters:**
- `symbol` (required): Symbol to query (e.g., "BTCUSDT")
- `limit` (1-500, default 50)
- `offset` (>=0, default 0)

**Usage:** `@genesis get_lstm_predictions(symbol="BTCUSDT", limit=100)`  
**Returns:** Time-series predictions with confidence scores

#### 9. `get_model_inventory`
**Purpose:** Scan filesystem for model artifacts  
**Usage:** `@genesis get_model_inventory()`  
**Returns:** List of model files (weights, checkpoints, configs) with status

#### 10. `get_commander_decision`
**Purpose:** Get Commander's decision (ALLOWED or BLOCKED) for a trade  
**Parameters:**
- `decision_id` (required): UUID of decision

**Usage:** `@genesis get_commander_decision(decision_id="550e8400-e29b-41d4-a716-446655440000")`  
**Returns:** Commander decision status and reason code

---

## Example Workflows

### Workflow 1: Check System Health

```
User: "Is the trading bot healthy?"

Claude: I'll check the system health for you.

@genesis get_system_health()

Response:
{
  "status": "healthy",
  "database": {"backend": "postgres", "status": "healthy", "canonical": true},
  "worker": {"status": "running"},
  "risk": {"status": "ok"},
  "environment": "live"
}

Claude: Yes! The system is healthy. PostgreSQL is up, the worker is running, and risk is OK.
```

### Workflow 2: Query Recent Trades

```
User: "Show me the last 10 BTC trades"

Claude: I'll fetch your recent BTC trades.

@genesis list_trades(symbol="BTCUSDT", limit=10)

Response:
{
  "count": 10,
  "total": 247,
  "has_more": true,
  "trades": [...]
}

Claude: Found 10 recent BTC trades (247 total on file). Here's what happened...
```

### Workflow 3: Investigate Decision Path

```
User: "Why was trade XYZ rejected?"

Claude: Let me check the decision lineage.

@genesis get_decision_trace(decision_id="550e8400...")
@genesis get_commander_decision(decision_id="550e8400...")

Response:
{
  "trace": {
    "decision_id": "550e8400...",
    "symbol": "BTCUSDT",
    "events": [
      {"event_type": "STRATEGY_EVALUATED", "status": "passed"},
      {"event_type": "COMMANDER_BLOCKED", "reason_code": "MAX_POSITIONS"},
      ...
    ]
  },
  "decision": {"commander_status": "blocked", "reason_code": "MAX_POSITIONS"}
}

Claude: The strategy passed, but the Commander blocked it because you already have the maximum number of open positions.
```

---

## Troubleshooting

### Server Won't Start

**Error:** `ModuleNotFoundError: No module named 'mcp'`

**Solution:** Install MCP SDK:
```bash
pip install mcp
```

### PostgreSQL Connection Failed

**Error:** `connection failed: connection to server at ... failed`

**Solution:**
1. Verify PostgreSQL is running:
   ```bash
   psql -U postgres -c "SELECT 1"
   ```

2. Check environment variables:
   ```bash
   echo $GENESIS_DATABASE_BACKEND  # Should be 'postgres'
   echo $GENESIS_DATABASE_URL      # Should be set
   ```

3. Test connection manually:
   ```python
   import psycopg
   conn = psycopg.connect(os.getenv("GENESIS_DATABASE_URL"))
   ```

### Claude Code Can't Connect

**Error:** MCP server not appearing in Claude Code

**Solution:**
1. Restart Claude Code (IDE extension or web)
2. Check server logs for errors
3. Verify CWD is correct (should be `Robo` directory)
4. Ensure PYTHONPATH includes `Robo` directory

### Slow Queries

**Error:** Timeout or slow response

**Solution:**
1. Check PostgreSQL connection: `psql -c "SELECT 1"`
2. Increase timeout:
   ```bash
   export GENESIS_DB_CONNECT_TIMEOUT=30
   ```
3. Run: `EXPLAIN ANALYZE` on slow query to find missing indexes

---

## Security Configuration

### Production Settings

```bash
# Use strong PostgreSQL password
export GENESIS_DATABASE_URL="postgresql://genesis:SecurePassword123@db.example.com:5432/genesis_trading"

# Enable SSL/TLS for remote connections
export GENESIS_DATABASE_URL="postgresql://genesis:pass@db.example.com:5432/genesis_trading?sslmode=require"

# Increase timeouts for slow networks
export GENESIS_DB_CONNECT_TIMEOUT=15

# Restrict logs to stderr only (no stdout)
export MCP_LOG_LEVEL=WARNING
```

### Local Development Settings

```bash
# Use local PostgreSQL (default)
export GENESIS_DATABASE_URL="postgresql://postgres@localhost:5432/genesis_trading"

# Enable verbose logging for debugging
export MCP_LOG_LEVEL=DEBUG
```

---

## Performance Tuning

### For High-Throughput Usage

1. **Connection Pooling:** MCP server creates new connection per request
   - Recommended: Use PgBouncer for connection pooling
   ```
   [databases]
   genesis_trading = host=localhost port=5432 dbname=genesis_trading user=genesis

   [pgbouncer]
   pool_mode = transaction
   max_client_conn = 100
   max_db_connections = 10
   ```

2. **Query Optimization:** Add indexes if missing
   ```sql
   CREATE INDEX idx_decision_trace_symbol ON decision_trace(symbol, created_at DESC);
   CREATE INDEX idx_predictions_lstm_symbol_ts ON predictions_lstm(symbol, timestamp DESC);
   ```

3. **Caching:** Consider caching frequently-requested tools at MCP client level
   - Decisions rarely change
   - LSTM predictions update daily
   - Positions update continuously

---

## Advanced Usage

### Custom Tool Parameters

All tools accept additional filters:

```
@genesis list_trades(
  symbol="BTCUSDT",
  strategy="mean_reversion",
  status="closed",
  start_time="2026-10-01T00:00:00Z",
  end_time="2026-10-07T23:59:59Z",
  limit=50,
  offset=0
)
```

### Error Handling

All tools return structured errors:

```json
{
  "error": {
    "code": "INVALID_ARGUMENT",
    "message": "symbol must be alphanumeric"
  }
}
```

Possible error codes:
- `INVALID_ARGUMENT` — Invalid input parameter
- `NOT_FOUND` — Record not found
- `DATABASE_UNAVAILABLE` — PostgreSQL unavailable
- `DATABASE_ERROR` — Query error

---

## Monitoring & Logging

### MCP Audit Logs

All tool calls are logged to stderr:

```
[2026-10-07 14:05:00] mcp_audit: tool=list_trades, request_id=abc123, status=success, duration_ms=42
[2026-10-07 14:05:05] mcp_audit: tool=get_decision_trace, request_id=def456, status=not_found, duration_ms=18
```

### Metrics to Track

- `tool_call_count` — Total calls per tool
- `tool_error_rate` — % of calls that error
- `tool_latency_p50/p95/p99` — Response time percentiles
- `database_unavailable_count` — Failures due to DB

---

## Support & Debugging

### Enable Verbose Logging

```bash
export DEBUG=1
export MCP_LOG_LEVEL=DEBUG

python -m genesis_mcp.server
```

### Test Specific Tool

```bash
# Use pytest to test individual tool
python -m pytest Robo/genesis_mcp/tests/test_v2_tools.py::TestLSTMPredictionsTool::test_lstm_predictions_valid_symbol -v
```

### Report Issues

When filing a bug report, include:
1. Tool name
2. Parameters used
3. Error message
4. PostgreSQL version
5. MCP server startup logs

---

## Sign-Off

**Setup Verified:** ✅ 2026-10-07  
**MCP Server:** genesis_mcp.server (10 tools)  
**Transport:** STDIO (JSON-RPC 2024-11-05)  
**Clients:** Claude Code (desktop/web/CLI)

**Status:** Ready for integration with Claude Code

---

*This guide enables Claude Code to use Genesis Quant MCP as a tool source for decision support, position monitoring, and trade analysis.*
