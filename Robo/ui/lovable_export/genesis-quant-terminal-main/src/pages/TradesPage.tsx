import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { DataTable } from "@/components/DataTable";
import { SideBadge, StatusBadge, PnlDisplay } from "@/components/Badges";
import { FilterBar, FilterSelect } from "@/components/FilterBar";
import { LoadingState, ErrorState, EmptyState } from "@/components/StateDisplays";
import type { TradeRecord } from "@/types/api";

const PAGE_SIZE = 20;

export default function TradesPage() {
  const [symbol, setSymbol] = useState("SOLUSDT");
  const [status, setStatus] = useState("closed");
  const [offset, setOffset] = useState(0);
  const navigate = useNavigate();

  const filtersQuery = useQuery({ queryKey: ["filters"], queryFn: api.filters, retry: 1 });
  const tradesQuery = useQuery({
    queryKey: ["trades", symbol, status, offset],
    queryFn: () => api.trades({ symbol, status, limit: PAGE_SIZE, offset }),
    retry: 1,
  });

  const symbols = filtersQuery.data?.symbols || ["SOLUSDT"];
  const statuses = filtersQuery.data?.statuses || ["open", "closed", "cancelled"];

  const columns = [
    {
      key: "timestamp",
      header: "Data",
      render: (r: TradeRecord) => <span className="font-mono-nums text-xs">{new Date(r.timestamp).toLocaleString("pt-BR")}</span>,
    },
    { key: "symbol", header: "Símbolo", render: (r: TradeRecord) => <span className="font-mono-nums text-xs">{r.symbol}</span> },
    { key: "side", header: "Lado", render: (r: TradeRecord) => <SideBadge side={r.side} /> },
    { key: "entry_price", header: "Entrada", render: (r: TradeRecord) => <span className="font-mono-nums">{r.entry_price.toFixed(4)}</span> },
    { key: "exit_price", header: "Saída", render: (r: TradeRecord) => <span className="font-mono-nums">{r.exit_price?.toFixed(4) ?? "—"}</span> },
    { key: "profit_loss", header: "P&L", render: (r: TradeRecord) => <PnlDisplay value={r.profit_loss} /> },
    { key: "strategy", header: "Estratégia", render: (r: TradeRecord) => <span className="text-xs">{r.strategy}</span> },
    { key: "status", header: "Status", render: (r: TradeRecord) => <StatusBadge status={r.status} /> },
  ];

  const totalPages = tradesQuery.data ? Math.ceil(tradesQuery.data.total / PAGE_SIZE) : 0;
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  return (
    <div className="space-y-4 animate-slide-in">
      <FilterBar>
        <FilterSelect
          label="Símbolo"
          value={symbol}
          onChange={(v) => { setSymbol(v); setOffset(0); }}
          options={symbols.map((s) => ({ value: s, label: s }))}
        />
        <FilterSelect
          label="Status"
          value={status}
          onChange={(v) => { setStatus(v); setOffset(0); }}
          options={[{ value: "", label: "Todos" }, ...statuses.map((s) => ({ value: s, label: s }))]}
        />
      </FilterBar>

      {tradesQuery.isLoading && <LoadingState message="Carregando trades..." />}
      {tradesQuery.isError && <ErrorState message="Erro ao carregar trades." onRetry={() => tradesQuery.refetch()} />}
      {tradesQuery.isSuccess && (
        tradesQuery.data.data.length > 0 ? (
          <>
            <DataTable
              columns={columns}
              data={tradesQuery.data.data}
              onRowClick={(r) => navigate(`/trades/${r.trade_id}`)}
            />
            {totalPages > 1 && (
              <div className="flex items-center justify-between pt-2">
                <span className="text-xs text-muted-foreground">
                  Página {currentPage} de {totalPages} ({tradesQuery.data.total} trades)
                </span>
                <div className="flex gap-2">
                  <button
                    onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                    disabled={offset === 0}
                    className="rounded border border-border px-3 py-1 text-xs text-foreground disabled:opacity-30 hover:bg-accent transition-colors"
                  >
                    Anterior
                  </button>
                  <button
                    onClick={() => setOffset(offset + PAGE_SIZE)}
                    disabled={currentPage >= totalPages}
                    className="rounded border border-border px-3 py-1 text-xs text-foreground disabled:opacity-30 hover:bg-accent transition-colors"
                  >
                    Próxima
                  </button>
                </div>
              </div>
            )}
          </>
        ) : (
          <EmptyState message="Nenhum trade encontrado." />
        )
      )}
    </div>
  );
}
