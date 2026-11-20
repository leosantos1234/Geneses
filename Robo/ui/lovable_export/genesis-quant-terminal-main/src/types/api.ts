// API Response Types for Genesis Quant

export interface HealthResponse {
  status: string;
  timestamp: string;
}

export interface TradeSummary {
  pnl_total: number;
  win_rate_pct: number;
  total_trades: number;
  open_positions: number;
  avg_profit: number;
  avg_loss: number;
  best_trade: number;
  worst_trade: number;
  recent_trades: TradeRecord[];
}

export interface TradeRecord {
  trade_id: string;
  timestamp: string;
  symbol: string;
  side: "buy" | "sell";
  entry_price: number;
  exit_price?: number;
  stop_loss?: number;
  take_profit?: number;
  profit_loss?: number;
  strategy: string;
  status: "open" | "closed" | "cancelled";
  duration?: string;
}

export interface TradeDetail extends TradeRecord {
  // Identification
  order_id?: string;
  exchange?: string;

  // Entry
  entry_timestamp?: string;
  entry_fee?: number;
  quantity?: number;

  // Exit
  exit_timestamp?: string;
  exit_fee?: number;
  exit_reason?: string;

  // Risk
  risk_reward_ratio?: number;
  max_drawdown?: number;
  trailing_stop?: number;
  be_activated?: boolean;

  // Context
  regime?: string;
  trend?: string;
  volatility?: number;
  signal_strength?: number;

  // Audit
  created_at?: string;
  updated_at?: string;
  notes?: string;
}

export interface Position {
  position_id?: string;
  timestamp: string;
  symbol: string;
  side: "buy" | "sell";
  entry_price: number;
  current_price?: number;
  stop_loss?: number;
  take_profit?: number;
  quantity?: number;
  unrealized_pnl?: number;
  strategy: string;
  status: string;
}

export interface FiltersResponse {
  symbols: string[];
  statuses: string[];
  strategies: string[];
}

export interface PaginatedResponse<T> {
  data: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ControlCenterAsset {
  symbol: string;
  regime: number | null;
  regime_label: string;
  confidence: number | null;
  last_seen: string | null;
  raw_breakouts: number;
  blocked: number;
  candidates: number;
}

export interface ControlCenterResponse {
  window_days: number;
  runtime: Record<string, unknown>;
  assets: ControlCenterAsset[];
  blockers: { symbol: string; blocker: string; total: number }[];
  recent_events: { timestamp_utc: string; symbol: string; direction_candidate?: string; final_state?: string; primary_blocker?: string; raw_breakout?: boolean }[];
  shadow: { events: number; divergences: number; paper_trades: number; pnl: number; policies: { policy: string; events: number; trades: number; pnl: number }[] };
  generated_at: string;
}

export interface RuntimeOption {
  id: string;
  label: string;
  env_file?: string;
}

export interface WorkerStatus {
  asset: string;
  pid?: number;
  env_file?: string;
  strategy?: string;
  status: string;
  running: boolean;
  started_at?: number;
  stopped_at?: number;
}

export interface RuntimeOptionsResponse {
  assets: RuntimeOption[];
  strategies: RuntimeOption[];
  workers: WorkerStatus[];
}
