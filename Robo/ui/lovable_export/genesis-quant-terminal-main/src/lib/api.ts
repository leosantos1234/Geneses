import type {
  HealthResponse,
  TradeSummary,
  TradeRecord,
  TradeDetail,
  Position,
  FiltersResponse,
  PaginatedResponse,
  ControlCenterResponse,
  RuntimeOptionsResponse,
  WorkerStatus,
  LiveLog,
} from "@/types/api";

const API_BASE = import.meta.env.VITE_API_BASE || "https://genesis-panel-api.onrender.com";

class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, params?: Record<string, string | number | undefined>): Promise<T> {
  const url = new URL(`${API_BASE}${path}`);
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== "") {
        url.searchParams.set(key, String(value));
      }
    });
  }

  const raw = localStorage.getItem("genesis_quant_auth");
  let accessToken: string | undefined;
  try {
    accessToken = raw ? JSON.parse(raw)?.session?.access_token : undefined;
  } catch {
    localStorage.removeItem("genesis_quant_auth");
  }
  const res = await fetch(url.toString(), {
    headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
  });
  if (res.status === 401) {
    localStorage.removeItem("genesis_quant_auth");
    if (window.location.pathname !== "/auth") {
      window.location.assign("/auth");
    }
    throw new ApiError("Sessão expirada ou inválida", res.status);
  }
  if (!res.ok) {
    throw new ApiError(`API error: ${res.statusText}`, res.status);
  }
  return res.json();
}

async function requestJson<T>(path: string, body: unknown): Promise<T> {
  const raw = localStorage.getItem("genesis_quant_auth");
  let accessToken: string | undefined;
  try { accessToken = raw ? JSON.parse(raw)?.session?.access_token : undefined; } catch { localStorage.removeItem("genesis_quant_auth"); }
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}) },
    body: JSON.stringify(body),
  });
  if (res.status === 401) { localStorage.removeItem("genesis_quant_auth"); window.location.assign("/auth"); throw new ApiError("Sessão expirada ou inválida", res.status); }
  if (!res.ok) { const detail = await res.json().catch(() => ({})); throw new ApiError(detail.detail || `API error: ${res.statusText}`, res.status); }
  return res.json();
}


export const api = {
  users: () => request<Array<{ id: string; email: string; role: string; is_active: boolean }>>("/api/auth/users"),
  createUser: (payload: { email: string; password: string; role: "admin" | "viewer" }) =>
    requestJson<{ user: { id: string; email: string; role: string } }>("/api/auth/users", payload),
  health: () => request<HealthResponse>("/api/ui/health"),

  summary: (params: { days?: number; symbol?: string }) => request<TradeSummary>("/api/ui/summary", params as any),

  openPositions: (params: { symbol?: string; limit?: number }) => request<Position[]>("/api/ui/open-positions", params as any),

  trades: (params: { symbol?: string; status?: string; limit?: number; offset?: number }) =>
    request<PaginatedResponse<TradeRecord>>("/api/ui/trades", params as any),

  tradeDetail: (tradeId: string) => request<TradeDetail>(`/api/ui/trades/${tradeId}`),

  filters: () => request<FiltersResponse>("/api/ui/filters"),

  controlCenter: (days = 30) => request<ControlCenterResponse>("/api/ui/control-center", { days }),
  runtimeOptions: () => request<RuntimeOptionsResponse>("/api/ui/runtime-options"),
  exchangeBalance: (days = 7) => request<import("@/types/api").ExchangeBalanceSnapshot>("/api/ui/exchange-balance", { days }),
  workers: () => request<WorkerStatus[]>("/api/ui/workers"),
  logs: (asset?: string, hours?: number, since?: string, until?: string) => request<LiveLog[]>("/api/ui/logs", { asset, hours, since, until, limit: 250 }),
  logsPage: (params: { asset?: string; hours?: number; since?: string; until?: string; before_id?: number; level?: string; category?: string }) => request<import("@/types/api").LiveLogPage>("/api/ui/logs/page", { ...params, limit: 250 }),
  downloadLogs: async (asset?: string, hours?: number, since?: string, until?: string, level?: string, category?: string) => {
    const url = new URL(`${API_BASE}/api/ui/logs/download`);
    url.searchParams.set("limit", "100000");
    if (asset && asset !== "ALL") url.searchParams.set("asset", asset);
    if (hours) url.searchParams.set("hours", String(hours));
    if (since) url.searchParams.set("since", since);
    if (until) url.searchParams.set("until", until);
    if (level && level !== "ALL") url.searchParams.set("level", level);
    if (category && category !== "ALL") url.searchParams.set("category", category);
    const raw = localStorage.getItem("genesis_quant_auth");
    let accessToken: string | undefined;
    try { accessToken = raw ? JSON.parse(raw)?.session?.access_token : undefined; } catch { localStorage.removeItem("genesis_quant_auth"); }
    const res = await fetch(url.toString(), { headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {} });
    if (!res.ok) throw new ApiError(`Não foi possível baixar os logs: ${res.statusText}`, res.status);
    const blob = await res.blob();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `genesis-logs-${asset && asset !== "ALL" ? asset : "todos"}.log`;
    link.click();
    URL.revokeObjectURL(link.href);
  },
  startWorkers: (payload: { assets: string[]; strategy: string }) => requestJson<{ workers: WorkerStatus[] }>("/api/ui/workers/start", payload),
  stopWorkers: (payload: { assets: string[]; strategy?: string }) => requestJson<{ workers: WorkerStatus[] }>("/api/ui/workers/stop", { ...payload, strategy: payload.strategy || "" }),
};

export { ApiError };
