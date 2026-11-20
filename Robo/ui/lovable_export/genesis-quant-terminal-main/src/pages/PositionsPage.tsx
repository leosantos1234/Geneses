import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { DataTable } from "@/components/DataTable";
import { SideBadge, StatusBadge } from "@/components/Badges";
import { FilterBar, FilterSelect } from "@/components/FilterBar";
import { LoadingState, ErrorState, EmptyState } from "@/components/StateDisplays";
import type { Position } from "@/types/api";

export default function PositionsPage() {
  const [symbol, setSymbol] = useState("SOLUSDT");

  const filtersQuery = useQuery({ queryKey: ["filters"], queryFn: api.filters, retry: 1 });
  const positionsQuery = useQuery({
    queryKey: ["positions", symbol],
    queryFn: () => api.openPositions({ symbol, limit: 100 }),
    retry: 1,
  });

  const symbols = filtersQuery.data?.symbols || ["SOLUSDT"];

  const columns = [
    {
      key: "timestamp",
      header: "Data",
      render: (r: Position) => <span className="font-mono-nums text-xs">{new Date(r.timestamp).toLocaleString("pt-BR")}</span>,
    },
    { key: "symbol", header: "Símbolo", render: (r: Position) => <span className="font-mono-nums text-xs">{r.symbol}</span> },
    { key: "side", header: "Lado", render: (r: Position) => <SideBadge side={r.side} /> },
    { key: "entry_price", header: "Entrada", render: (r: Position) => <span className="font-mono-nums">{r.entry_price.toFixed(4)}</span> },
    { key: "stop_loss", header: "Stop Loss", render: (r: Position) => <span className="font-mono-nums text-loss">{r.stop_loss?.toFixed(4) ?? "—"}</span> },
    { key: "take_profit", header: "Take Profit", render: (r: Position) => <span className="font-mono-nums text-profit">{r.take_profit?.toFixed(4) ?? "—"}</span> },
    { key: "strategy", header: "Estratégia", render: (r: Position) => <span className="text-xs">{r.strategy}</span> },
    { key: "status", header: "Status", render: (r: Position) => <StatusBadge status={r.status} /> },
  ];

  return (
    <div className="space-y-4 animate-slide-in">
      <FilterBar>
        <FilterSelect
          label="Símbolo"
          value={symbol}
          onChange={setSymbol}
          options={symbols.map((s) => ({ value: s, label: s }))}
        />
      </FilterBar>

      {positionsQuery.isLoading && <LoadingState message="Carregando posições..." />}
      {positionsQuery.isError && <ErrorState message="Erro ao carregar posições." onRetry={() => positionsQuery.refetch()} />}
      {positionsQuery.isSuccess && (
        positionsQuery.data.length > 0
          ? <DataTable columns={columns} data={positionsQuery.data} />
          : <EmptyState message="Nenhuma posição aberta." />
      )}
    </div>
  );
}
