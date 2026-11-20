# Navigation + Wireframe (V1)

## Sidebar
- Dashboard (`/dashboard`)
- Positions (`/positions`)
- Trades (`/trades`)
- Risk Settings (`/settings/risk`)

## `/dashboard`
- Header:
  - Symbol select
  - Window days select
- KPI row:
  - PnL total
  - Win rate
  - Closed trades
  - Open positions
- Bottom:
  - Latest trades table

## `/positions`
- Header:
  - Symbol filter
  - Refresh button
- Content:
  - Open positions table

## `/trades`
- Header:
  - Symbol filter
  - Status filter
  - Pagination controls
- Content:
  - Trades table

## `/trades/:tradeId`
- Block 1: Meta (trade_id, symbol, strategy, decision_source, status)
- Block 2: Price (entry/exit/profit_loss)
- Block 3: Risk (stop_loss/take_profit/trailing)
- Block 4: Context (regime/tendencia/consenso)
- Block 5: Timestamps (open_ts/close_ts/timestamp_decisao_ia)

## `/settings/risk`
- Form section:
  - TP decision zone
  - TP extension step
  - Trailing activation
  - BE activation
- Side panel:
  - JSON preview
  - Save button (UI-only in V1)

