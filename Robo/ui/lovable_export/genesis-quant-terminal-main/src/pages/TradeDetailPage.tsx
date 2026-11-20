import { useParams, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { SideBadge, StatusBadge, PnlDisplay } from "@/components/Badges";
import { LoadingState, ErrorState } from "@/components/StateDisplays";
import { ArrowLeft } from "lucide-react";
import { cn } from "@/lib/utils";

function DetailBlock({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <h3 className="text-xs font-medium uppercase tracking-wider text-primary mb-3">{title}</h3>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        {children}
      </div>
    </div>
  );
}

function Field({ label, value, className }: { label: string; value?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("space-y-0.5", className)}>
      <span className="text-xs text-muted-foreground">{label}</span>
      <div className="text-sm font-mono-nums">{value ?? <span className="text-muted-foreground">—</span>}</div>
    </div>
  );
}

export default function TradeDetailPage() {
  const { tradeId } = useParams<{ tradeId: string }>();
  const navigate = useNavigate();

  const query = useQuery({
    queryKey: ["trade", tradeId],
    queryFn: () => api.tradeDetail(tradeId!),
    enabled: !!tradeId,
    retry: 1,
  });

  if (query.isLoading) return <LoadingState message="Carregando detalhes do trade..." />;
  if (query.isError) return <ErrorState message="Erro ao carregar trade." onRetry={() => query.refetch()} />;

  const t = query.data!;

  return (
    <div className="space-y-4 animate-slide-in">
      <button onClick={() => navigate(-1)} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors">
        <ArrowLeft className="h-4 w-4" />
        Voltar
      </button>

      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold font-mono-nums">{t.trade_id}</h2>
        <SideBadge side={t.side} />
        <StatusBadge status={t.status} />
      </div>

      <div className="grid gap-4">
        <DetailBlock title="Identificação">
          <Field label="Trade ID" value={t.trade_id} />
          <Field label="Símbolo" value={t.symbol} />
          <Field label="Estratégia" value={t.strategy} />
          <Field label="Order ID" value={t.order_id} />
          <Field label="Exchange" value={t.exchange} />
        </DetailBlock>

        <DetailBlock title="Entrada">
          <Field label="Preço Entrada" value={t.entry_price?.toFixed(6)} />
          <Field label="Data Entrada" value={t.entry_timestamp ? new Date(t.entry_timestamp).toLocaleString("pt-BR") : t.timestamp ? new Date(t.timestamp).toLocaleString("pt-BR") : undefined} />
          <Field label="Quantidade" value={t.quantity?.toFixed(6)} />
          <Field label="Taxa Entrada" value={t.entry_fee?.toFixed(6)} />
        </DetailBlock>

        <DetailBlock title="Saída">
          <Field label="Preço Saída" value={t.exit_price?.toFixed(6)} />
          <Field label="Data Saída" value={t.exit_timestamp ? new Date(t.exit_timestamp).toLocaleString("pt-BR") : undefined} />
          <Field label="P&L" value={<PnlDisplay value={t.profit_loss} />} />
          <Field label="Taxa Saída" value={t.exit_fee?.toFixed(6)} />
          <Field label="Razão Saída" value={t.exit_reason} />
          <Field label="Duração" value={t.duration} />
        </DetailBlock>

        <DetailBlock title="Risco">
          <Field label="Stop Loss" value={t.stop_loss?.toFixed(6)} />
          <Field label="Take Profit" value={t.take_profit?.toFixed(6)} />
          <Field label="R:R" value={t.risk_reward_ratio?.toFixed(2)} />
          <Field label="Max Drawdown" value={t.max_drawdown?.toFixed(4)} />
          <Field label="Trailing Stop" value={t.trailing_stop?.toFixed(6)} />
          <Field label="BE Ativado" value={t.be_activated !== undefined ? (t.be_activated ? "Sim" : "Não") : undefined} />
        </DetailBlock>

        <DetailBlock title="Contexto">
          <Field label="Regime" value={t.regime} />
          <Field label="Tendência" value={t.trend} />
          <Field label="Volatilidade" value={t.volatility?.toFixed(4)} />
          <Field label="Força do Sinal" value={t.signal_strength?.toFixed(2)} />
        </DetailBlock>

        <DetailBlock title="Auditoria">
          <Field label="Criado em" value={t.created_at ? new Date(t.created_at).toLocaleString("pt-BR") : undefined} />
          <Field label="Atualizado em" value={t.updated_at ? new Date(t.updated_at).toLocaleString("pt-BR") : undefined} />
          <Field label="Notas" value={t.notes} className="col-span-2 sm:col-span-3" />
        </DetailBlock>
      </div>
    </div>
  );
}
