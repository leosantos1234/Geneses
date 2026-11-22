import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { DataTable } from "@/components/DataTable";
import { SideBadge, PnlDisplay } from "@/components/Badges";
import { FilterBar, FilterSelect } from "@/components/FilterBar";
import { LoadingState, ErrorState, EmptyState } from "@/components/StateDisplays";
import type { ExchangeBalanceSnapshot } from "@/types/api";

const price = (value?: number) => value === undefined || value === null || value === 0
  ? "—"
  : value.toLocaleString("en-US", { minimumFractionDigits: 4, maximumFractionDigits: 8 });

function pnlPercent(position: ExchangeBalanceSnapshot["open_positions"][number]) {
  if (position.unrealized_pnl_pct !== undefined && position.unrealized_pnl_pct !== 0) return position.unrealized_pnl_pct;
  const base = (position.entry_price || 0) * Math.abs(position.size || 0);
  return base > 0 ? (position.unrealized_pnl / base) * 100 : 0;
}

export default function PositionsPage() {
  const [symbol, setSymbol] = useState("ALL");
  const positionsQuery = useQuery({
    queryKey: ["live-positions"],
    queryFn: () => api.exchangeBalance(1),
    refetchInterval: 5000,
    staleTime: 3000,
    retry: 1,
  });

  const positions = positionsQuery.data?.open_positions || [];
  const symbols = useMemo(() => ["ALL", ...Array.from(new Set(positions.map((p) => p.symbol).filter(Boolean) as string[]))], [positions]);
  const filtered = symbol === "ALL" ? positions : positions.filter((position) => position.symbol === symbol);
  const totalPnl = filtered.reduce((sum, position) => sum + (position.unrealized_pnl || 0), 0);

  const columns = [
    { key: "symbol", header: "Ativo", render: (r: ExchangeBalanceSnapshot["open_positions"][number]) => <span className="font-mono-nums font-semibold">{r.symbol || "—"}</span> },
    { key: "side", header: "Direção", render: (r: ExchangeBalanceSnapshot["open_positions"][number]) => <SideBadge side={String(r.side || "").toLowerCase().includes("sell") || String(r.side || "").toLowerCase().includes("short") ? "sell" : "buy"} /> },
    { key: "size", header: "Quantidade", render: (r: ExchangeBalanceSnapshot["open_positions"][number]) => <span className="font-mono-nums">{r.size.toLocaleString("en-US", { maximumFractionDigits: 8 })}</span> },
    { key: "entry_price", header: "Entrou em", render: (r: ExchangeBalanceSnapshot["open_positions"][number]) => <span className="font-mono-nums">{price(r.entry_price)}</span> },
    { key: "current_price", header: "Preço atual", render: (r: ExchangeBalanceSnapshot["open_positions"][number]) => <span className="font-mono-nums">{price(r.current_price)}</span> },
    { key: "unrealized_pnl", header: "Ganho / perda", render: (r: ExchangeBalanceSnapshot["open_positions"][number]) => <div><PnlDisplay value={r.unrealized_pnl} /><div className={`font-mono-nums text-[11px] ${r.unrealized_pnl >= 0 ? "text-profit" : "text-loss"}`}>{r.unrealized_pnl >= 0 ? "+" : ""}{pnlPercent(r).toFixed(2)}%</div></div> },
    { key: "leverage", header: "Alavancagem", render: (r: ExchangeBalanceSnapshot["open_positions"][number]) => <span className="font-mono-nums">{r.leverage ? `${r.leverage}x` : "—"}</span> },
  ];

  return (
    <div className="space-y-4 animate-slide-in">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-base font-semibold">Posições abertas</h1>
          <p className="mt-1 text-xs text-muted-foreground">Atualização ao vivo pela {positionsQuery.data?.exchange || "exchange"} · a cada 5 segundos</p>
        </div>
        <div className="rounded-md border border-border px-3 py-2 text-right">
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">PnL aberto{symbol !== "ALL" ? ` · ${symbol}` : ""}</div>
          <PnlDisplay value={totalPnl} />
        </div>
      </div>

      <FilterBar>
        <FilterSelect label="Ativo" value={symbol} onChange={setSymbol} options={symbols.map((item) => ({ value: item, label: item === "ALL" ? "Todos os ativos" : item }))} />
      </FilterBar>

      {positionsQuery.isLoading && <LoadingState message="Consultando posições na exchange..." />}
      {positionsQuery.isError && <ErrorState message="Não foi possível consultar as posições ao vivo." onRetry={() => positionsQuery.refetch()} />}
      {positionsQuery.isSuccess && (filtered.length > 0 ? <DataTable columns={columns} data={filtered} /> : <EmptyState message="Nenhuma posição aberta no momento." />)}
      {positionsQuery.data && <p className="text-[11px] text-muted-foreground">Última consulta: {new Date(positionsQuery.data.fetched_at).toLocaleTimeString("pt-BR")}. Valores positivos aparecem em verde; negativos, em vermelho.</p>}
    </div>
  );
}
