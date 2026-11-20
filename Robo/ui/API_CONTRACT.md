# UI API Contract (V1)

Base URL (local): `http://localhost:8010`

## Health
- `GET /api/ui/health`
- Response:
```json
{ "status": "ok", "service": "genesis-ui-api" }
```

## Summary (cards do dashboard)
- `GET /api/ui/summary?days=7&symbol=SOLUSDT`
- Query params:
  - `days` (`1..365`, default `7`)
  - `symbol` (optional)
- Response:
```json
{
  "window_days": 7,
  "symbol": "SOLUSDT",
  "total_trades": 42,
  "wins": 20,
  "losses": 22,
  "win_rate_pct": 47.62,
  "pnl_total": 123.45,
  "open_positions": 1
}
```

## Open Positions
- `GET /api/ui/open-positions?symbol=SOLUSDT&limit=100`
- Query params:
  - `symbol` (optional)
  - `limit` (`1..500`, default `100`)
- Response: lista de posições abertas.

## Trades (tabela/histórico)
- `GET /api/ui/trades?symbol=SOLUSDT&status=closed&limit=100&offset=0`
- Query params:
  - `symbol` (optional)
  - `status` (`open|closed`, optional)
  - `limit` (`1..500`, default `100`)
  - `offset` (`>=0`, default `0`)
- Response: lista de trades ordenada por `timestamp DESC`.

## Trade Detail
- `GET /api/ui/trades/{trade_id}`
- Response: objeto completo do trade.

## Filters (combos)
- `GET /api/ui/filters`
- Response:
```json
{
  "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
  "strategies": ["breakout", "bybit_existing"]
}
```

---

## Run
```bash
python api_ui.py
```
ou
```bash
uvicorn api_ui:app --host 0.0.0.0 --port 8010
```

