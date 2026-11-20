

## Add Mock Data Fallback

When the API is unreachable, the app will use realistic sample data so all screens render properly in the preview.

### What will change

1. **New file `src/lib/mock-data.ts`** -- Contains sample data for all endpoints: summary, positions, trades, trade detail, and filters.

2. **Update `src/lib/api.ts`** -- Wrap each API call in a try/catch. If the fetch fails (network error), return the corresponding mock data instead. This keeps the real API integration intact -- when your backend is running, it will use real data automatically.

### Technical details

- Mock data will include ~5 sample trades, ~3 open positions, realistic PnL values, and all filter options (symbols, statuses).
- Trade IDs in mock data will be consistent so `/trades/:tradeId` detail page also works.
- No new dependencies required.
- The fallback is transparent -- a small console warning will log when mock data is being used.

