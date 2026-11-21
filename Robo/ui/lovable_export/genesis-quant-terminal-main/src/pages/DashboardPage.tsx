import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { StatCard } from "@/components/StatCard";
import { LoadingState, ErrorState } from "@/components/StateDisplays";
import { Activity, AlertTriangle, BrainCircuit, Database, Download, Gauge, Radio, ScrollText, ShieldCheck, Sparkles } from "lucide-react";
import type { ControlCenterAsset, ExchangeBalanceSnapshot } from "@/types/api";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useAuth } from "@/contexts/AuthContext";

const regimeTone: Record<string, string> = { "Tendência forte": "text-profit bg-profit/10", "Tendência moderada": "text-primary bg-primary/10", Lateral: "text-warning bg-warning/10", Caótico: "text-loss bg-loss/10", "Sem leitura": "text-muted-foreground bg-muted" };
const shortSymbol = (symbol: string) => symbol.replace("USDT", "");
const strategyLabels: Record<string, string> = {
  BREAKOUT: "Breakout",
  SWING_TREND_FOLLOWING: "Swing Trend Following",
  PULLBACK_TREND_FOLLOWING: "Pullback Trend Following",
  EARLY_TREND: "Early Trend",
  MEAN_REVERSION: "Mean Reversion",
  MARKET_MAKING_LATERAL: "Market Making Lateral",
};
const formatTime = (value?: string | null) => value ? new Date(value).toLocaleString("pt-BR", { day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "sem leitura";

function AssetRow({ asset }: { asset: ControlCenterAsset }) {
  const observed = asset.blocked + asset.candidates;
  const conversion = observed ? Math.round((asset.candidates / observed) * 100) : 0;
  return <div className="grid grid-cols-[1.1fr_1.5fr_0.8fr_0.8fr_0.8fr] items-center gap-3 border-b border-border/60 py-3 last:border-0">
    <div className="flex items-center gap-2"><span className="flex h-7 w-7 items-center justify-center rounded-md bg-primary/10 text-xs font-semibold text-primary">{shortSymbol(asset.symbol).slice(0, 1)}</span><div><div className="font-mono-nums text-sm font-semibold">{shortSymbol(asset.symbol)}</div><div className="text-[10px] text-muted-foreground">{formatTime(asset.last_seen)}</div></div></div>
    <div><span className={`inline-flex rounded-full px-2 py-1 text-[11px] font-medium ${regimeTone[asset.regime_label] || regimeTone["Sem leitura"]}`}>{asset.regime_label}</span></div>
    <div className="font-mono-nums text-xs text-muted-foreground">{asset.raw_breakouts} <span className="text-[10px]">romp.</span></div>
    <div className="font-mono-nums text-xs"><span className="text-warning">{asset.blocked}</span> <span className="text-[10px] text-muted-foreground">bloq.</span></div>
    <div className="text-right"><span className={conversion > 0 ? "text-profit" : "text-muted-foreground"}>{conversion}%</span><div className="text-[10px] text-muted-foreground">candidatos</div></div>
  </div>;
}

function WorkerControl() {
  const { user } = useAuth();
  const canControl = user?.role === "admin";
  const client = useQueryClient();
  const options = useQuery({ queryKey: ["runtime-options"], queryFn: api.runtimeOptions, refetchInterval: 15_000, retry: 1 });
  const [selectedAssets, setSelectedAssets] = useState<string[]>([]);
  const [strategy, setStrategy] = useState("SWING_TREND_FOLLOWING");
  const start = useMutation({ mutationFn: api.startWorkers, onSuccess: () => { client.invalidateQueries({ queryKey: ["runtime-options"] }); client.invalidateQueries({ queryKey: ["workers"] }); }, onError: (error: Error) => window.alert(error.message) });
  const refreshWorkers = async () => {
    await Promise.all([
      client.refetchQueries({ queryKey: ["runtime-options"], type: "active" }),
      client.refetchQueries({ queryKey: ["workers"], type: "active" }),
    ]);
  };
  const stop = useMutation({ mutationFn: api.stopWorkers, onSuccess: refreshWorkers, onError: (error: Error) => window.alert(error.message) });
  const running = options.data?.workers.filter((worker) => worker.running) || [];
  const toggle = (asset: string, checked: boolean | "indeterminate") => setSelectedAssets((current) => checked === true ? [...new Set([...current, asset])] : current.filter((item) => item !== asset));
  const stopSelected = () => {
    if (!selectedAssets.length) return;
    if (window.confirm(`Parar o processo de ${selectedAssets.join(", ")}?\n\nIsso encerra o worker selecionado e impede novas avaliações.`)) {
      stop.mutate({ assets: selectedAssets });
      setSelectedAssets([]);
    }
  };
  const stopAll = () => {
    const assets = running.map((worker) => worker.asset);
    if (!assets.length) return;
    if (window.confirm(`Parar todos os workers ativos (${assets.join(", ")})?\n\nIsso impede novas avaliações e novas entradas.`)) {
      stop.mutate({ assets });
      setSelectedAssets([]);
    }
  };
  return <section className="rounded-lg border border-primary/30 bg-card p-4">
    <div className="mb-3 flex flex-col justify-between gap-2 sm:flex-row sm:items-start"><div><h2 className="text-sm font-semibold">Controle de execução</h2><p className="mt-1 text-xs text-muted-foreground">{canControl ? "Marque o ativo e escolha a estratégia. Somente os marcados serão iniciados." : "Acesso somente para visualização. O controle dos ativos é reservado ao administrador."}</p></div><span className={`rounded-full px-2 py-1 text-[11px] ${canControl ? "bg-warning/10 text-warning" : "bg-muted text-muted-foreground"}`}>{canControl ? "seleção manual" : "somente leitura"}</span></div>
    {options.isLoading ? <p className="text-xs text-muted-foreground">Carregando ativos disponíveis...</p> : <>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">{options.data?.assets.map((asset) => { const worker = running.find((item) => item.asset === asset.id); const active = Boolean(worker); return <label key={asset.id} className={`flex items-center gap-2 rounded-md border border-border px-3 py-2 text-xs ${canControl ? "cursor-pointer hover:border-primary/50" : "cursor-default"}`}><Checkbox disabled={!canControl} checked={selectedAssets.includes(asset.id)} onCheckedChange={(checked) => toggle(asset.id, checked)} /><span className="font-mono-nums">{asset.label}</span><span className={`ml-auto text-right text-[10px] ${active ? "text-profit" : "text-muted-foreground"}`}><span className="block">{active ? "ativo" : "parado"}</span><span className="block max-w-36 truncate">{active ? (strategyLabels[worker?.strategy || ""] || worker?.strategy || "estratégia") : "—"}</span></span></label>; })}</div>
      <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center"><Select disabled={!canControl} value={strategy} onValueChange={setStrategy}><SelectTrigger className="sm:w-64"><SelectValue placeholder="Estratégia" /></SelectTrigger><SelectContent>{options.data?.strategies.map((item) => <SelectItem key={item.id} value={item.id}>{item.label}</SelectItem>)}</SelectContent></Select><Button size="sm" disabled={!canControl || !selectedAssets.length || start.isPending} onClick={() => start.mutate({ assets: selectedAssets, strategy })}>{start.isPending ? "Iniciando..." : "Iniciar selecionados"}</Button><Button size="sm" variant="outline" disabled={!canControl || !selectedAssets.length || stop.isPending} onClick={() => stop.mutate({ assets: selectedAssets })}>{stop.isPending ? "Parando..." : "Parar selecionados"}</Button><span className="text-[11px] text-muted-foreground">{canControl && selectedAssets.length ? `${selectedAssets.length} selecionado(s)` : "Nenhum ativo selecionado"}</span></div>
      {canControl && <div className="mt-2 flex flex-wrap justify-end gap-2"><Button size="sm" variant="destructive" disabled={!selectedAssets.length || stop.isPending} onClick={stopSelected} title="Encerra os processos dos ativos selecionados">{stop.isPending ? "Parando..." : "Parar processo selecionado"}</Button><Button size="sm" variant="destructive" disabled={!running.length || stop.isPending} onClick={stopAll} title="Encerra todos os workers ativos">{stop.isPending ? "Parando..." : "Parar todos os workers"}</Button></div>}
    </>}
  </section>;
}

function ExchangeBalanceCard() {
  const { user } = useAuth();
  const [periodDays, setPeriodDays] = useState(7);
  const balance = useQuery<ExchangeBalanceSnapshot>({ queryKey: ["exchange-balance", periodDays], queryFn: () => api.exchangeBalance(periodDays), refetchInterval: 30_000, retry: 1, enabled: user?.role === "admin" });
  if (user?.role !== "admin") return null;
  const money = (value: number) => `${value.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USD`;
  const periodLabel = periodDays === 1 ? "últimas 24 horas" : periodDays === 3650 ? "todo o histórico" : `últimos ${periodDays} dias`;
  return <section className="rounded-lg border border-primary/30 bg-card p-4">
    <div className="mb-3 flex flex-col justify-between gap-2 sm:flex-row sm:items-start"><div><h2 className="text-sm font-semibold">Portfólio da corretora</h2><p className="mt-1 text-xs text-muted-foreground">Valor estimado em USD · consulta somente leitura · atualização a cada 30 segundos</p></div><div className="flex items-center gap-2"><Select value={String(periodDays)} onValueChange={(value) => setPeriodDays(Number(value))}><SelectTrigger className="h-8 w-40 text-xs"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="1">Últimas 24 horas</SelectItem><SelectItem value="7">Últimos 7 dias</SelectItem><SelectItem value="30">Últimos 30 dias</SelectItem><SelectItem value="90">Últimos 90 dias</SelectItem><SelectItem value="365">Último ano</SelectItem><SelectItem value="3650">Todo o histórico</SelectItem></SelectContent></Select><span className="rounded-full bg-profit/10 px-2 py-1 text-[11px] text-profit">{balance.data?.exchange || "OKX"}</span></div></div>
    {balance.isLoading ? <p className="text-xs text-muted-foreground">Consultando saldo...</p> : balance.isError ? <p className="text-xs text-warning">Saldo indisponível. Verifique as credenciais OKX no serviço da API.</p> : balance.data ? <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-6"><div className="rounded-md border border-border p-3"><div className="text-[10px] uppercase text-muted-foreground">Valor total estimado</div><div className="mt-1 font-mono-nums text-lg text-primary">{money(balance.data.total_equity)}</div></div><div className="rounded-md border border-border p-3"><div className="text-[10px] uppercase text-muted-foreground">Portfólio / Funding</div><div className="mt-1 font-mono-nums text-lg text-profit">{money(balance.data.funding_balance)}</div></div><div className="rounded-md border border-border p-3"><div className="text-[10px] uppercase text-muted-foreground">Em negociação</div><div className="mt-1 font-mono-nums text-lg text-primary">{money(balance.data.trading_equity)}</div></div><div className="rounded-md border border-border p-3"><div className="text-[10px] uppercase text-muted-foreground">Ganho/perda ({periodLabel})</div><div className={`mt-1 font-mono-nums text-lg ${balance.data.realized_pnl >= 0 ? "text-profit" : "text-loss"}`}>{balance.data.realized_pnl >= 0 ? "+" : ""}{money(balance.data.realized_pnl)}</div><div className="text-[10px] text-muted-foreground">{balance.data.period_trades} trade(s) fechado(s)</div></div><div className="rounded-md border border-border p-3"><div className="text-[10px] uppercase text-muted-foreground">Usado em margem</div><div className="mt-1 font-mono-nums text-lg text-warning">{money(balance.data.used_in_positions)}</div></div><div className="rounded-md border border-border p-3"><div className="text-[10px] uppercase text-muted-foreground">PnL aberto</div><div className={`mt-1 font-mono-nums text-lg ${balance.data.unrealized_pnl >= 0 ? "text-profit" : "text-loss"}`}>{money(balance.data.unrealized_pnl)}</div><div className="text-[10px] text-muted-foreground">Posições: {balance.data.open_positions.length}</div></div></div> : null}
  </section>;
}

function LiveWorkerLogs() {
  const options = useQuery({ queryKey: ["runtime-options"], queryFn: api.runtimeOptions, refetchInterval: 5_000, retry: 1 });
  const [selectedAsset, setSelectedAsset] = useState("ALL");
  const [selectedRange, setSelectedRange] = useState("1");
  const [customSince, setCustomSince] = useState("");
  const [customUntil, setCustomUntil] = useState("");
  const [downloading, setDownloading] = useState(false);
  const rangeHours = selectedRange === "custom" ? undefined : Number(selectedRange);
  const logs = useQuery({ queryKey: ["live-logs", selectedAsset, selectedRange, customSince, customUntil], queryFn: () => api.logs(selectedAsset, rangeHours, selectedRange === "custom" ? customSince : undefined, selectedRange === "custom" ? customUntil : undefined), refetchInterval: 3_000, retry: 1, enabled: selectedRange !== "custom" || Boolean(customSince && customUntil) });
  const activeAssets = (options.data?.workers || []).filter((worker) => worker.running).map((worker) => worker.asset.replace(/USDT$/, ""));
  const assets = [...new Set(activeAssets)];
  return <section className="rounded-lg border border-border bg-card p-4">
    <div className="mb-3 flex flex-col justify-between gap-2 sm:flex-row sm:items-start">
      <div><div className="flex items-center gap-2"><ScrollText className="h-4 w-4 text-primary" /><h2 className="text-sm font-semibold">Logs dos ativos</h2><span className="h-2 w-2 animate-pulse rounded-full bg-profit" /></div><p className="mt-1 text-xs text-muted-foreground">Atualização automática a cada 3 segundos · {activeAssets.length} worker(s) ativo(s)</p></div>
      <div className="flex flex-wrap items-center gap-2"><Select value={selectedRange} onValueChange={setSelectedRange}><SelectTrigger className="h-8 w-44 text-xs"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="0.25">Ao vivo</SelectItem><SelectItem value="1">Última hora</SelectItem><SelectItem value="4">Últimas 4 horas</SelectItem><SelectItem value="24">Últimas 24 horas</SelectItem><SelectItem value="48">Últimos 2 dias</SelectItem><SelectItem value="168">Últimos 7 dias</SelectItem><SelectItem value="720">Últimos 30 dias</SelectItem><SelectItem value="custom">Personalizado</SelectItem></SelectContent></Select><Button size="sm" variant="outline" disabled={downloading || (selectedRange === "custom" && !(customSince && customUntil))} onClick={async () => { setDownloading(true); try { await api.downloadLogs(selectedAsset, rangeHours, selectedRange === "custom" ? customSince : undefined, selectedRange === "custom" ? customUntil : undefined); } catch (error) { window.alert(error instanceof Error ? error.message : "Não foi possível baixar os logs"); } finally { setDownloading(false); } }}><Download className="mr-1.5 h-3.5 w-3.5" />{downloading ? "Baixando..." : "Baixar log"}</Button></div>
    {selectedRange === "custom" && <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground"><label>De <input type="datetime-local" value={customSince} onChange={(event) => setCustomSince(event.target.value)} className="ml-1 rounded border border-border bg-background px-2 py-1 text-foreground" /></label><label>Até <input type="datetime-local" value={customUntil} onChange={(event) => setCustomUntil(event.target.value)} className="ml-1 rounded border border-border bg-background px-2 py-1 text-foreground" /></label></div>}
    </div>
    <div className="mb-3 flex flex-wrap gap-2">
      {["ALL", ...assets].map((asset) => <button key={asset} type="button" onClick={() => setSelectedAsset(asset)} className={`rounded-md border px-3 py-1.5 text-xs transition-colors ${selectedAsset === asset ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:border-primary/50"}`}>{asset === "ALL" ? "Todos" : asset}</button>)}
    </div>
    <div className="max-h-72 overflow-auto rounded-md border border-border/60 bg-background/40 p-2 font-mono-nums text-[11px]">
      {logs.isLoading ? <p className="p-3 text-muted-foreground">Carregando eventos...</p> : logs.isError ? <p className="p-3 text-warning">Não foi possível carregar os logs deste ativo.</p> : logs.data?.length ? logs.data.map((item) => <div key={item.id} className="flex gap-2 border-b border-border/40 py-1.5 last:border-0"><span className="shrink-0 text-muted-foreground">{formatTime(item.ts)}</span><span className={`shrink-0 ${item.level === "ERROR" ? "text-loss" : item.level === "WARNING" ? "text-warning" : "text-primary"}`}>{item.level}</span><span className="shrink-0 font-semibold text-primary">{shortSymbol(item.symbol || selectedAsset)}</span><span className="min-w-0 truncate text-foreground/80">{item.message}</span></div>) : <p className="p-3 text-muted-foreground">Nenhum evento registrado para este filtro ainda.</p>}
    </div>
  </section>;
}

function LiveWorkerLogsPaged() {
  const options = useQuery({ queryKey: ["runtime-options"], queryFn: api.runtimeOptions, refetchInterval: 5_000, retry: 1 });
  const [selectedAsset, setSelectedAsset] = useState("ALL");
  const [selectedRange, setSelectedRange] = useState("1");
  const [selectedLevel, setSelectedLevel] = useState("ALL");
  const [selectedCategory, setSelectedCategory] = useState("ALL");
  const [customSince, setCustomSince] = useState("");
  const [customUntil, setCustomUntil] = useState("");
  const [olderLogs, setOlderLogs] = useState<import("@/types/api").LiveLog[]>([]);
  const [nextBeforeId, setNextBeforeId] = useState<number | null>(null);
  const [downloading, setDownloading] = useState(false);
  const rangeHours = selectedRange === "custom" ? undefined : Number(selectedRange);
  const enabled = selectedRange !== "custom" || Boolean(customSince && customUntil);
  const filters = { asset: selectedAsset, hours: rangeHours, since: selectedRange === "custom" ? customSince : undefined, until: selectedRange === "custom" ? customUntil : undefined, level: selectedLevel, category: selectedCategory };
  const logs = useQuery({ queryKey: ["live-logs-paged", selectedAsset, selectedRange, customSince, customUntil, selectedLevel, selectedCategory], queryFn: () => api.logsPage(filters), refetchInterval: 3_000, retry: 1, enabled });
  useEffect(() => { setOlderLogs([]); setNextBeforeId(null); }, [selectedAsset, selectedRange, customSince, customUntil, selectedLevel, selectedCategory]);
  useEffect(() => { setNextBeforeId(logs.data?.next_before_id ?? null); }, [logs.data]);
  const activeAssets = (options.data?.workers || []).filter((worker) => worker.running).map((worker) => worker.asset.replace(/USDT$/, ""));
  const assets = [...new Set(activeAssets)];
  const allLogs = [...(logs.data?.items || []), ...olderLogs];
  const loadMore = async () => {
    if (!nextBeforeId) return;
    const page = await api.logsPage({ ...filters, before_id: nextBeforeId });
    setOlderLogs((current) => [...current, ...page.items]);
    setNextBeforeId(page.next_before_id);
  };
  const downloadLogs = async () => {
    setDownloading(true);
    try {
      await api.downloadLogsPaged(selectedAsset, rangeHours, selectedRange === "custom" ? customSince : undefined, selectedRange === "custom" ? customUntil : undefined, selectedLevel, selectedCategory);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "Não foi possível baixar os logs");
    } finally {
      setDownloading(false);
    }
  };
  return <section className="rounded-lg border border-border bg-card p-4">
    <div className="mb-3 flex flex-col justify-between gap-2 sm:flex-row sm:items-start">
      <div><div className="flex items-center gap-2"><ScrollText className="h-4 w-4 text-primary" /><h2 className="text-sm font-semibold">Logs dos ativos</h2><span className="h-2 w-2 animate-pulse rounded-full bg-profit" /></div><p className="mt-1 text-xs text-muted-foreground">Atualização automática a cada 3 segundos · {activeAssets.length} worker(s) ativo(s)</p></div>
      <div className="flex flex-wrap items-center gap-2"><Select value={selectedRange} onValueChange={setSelectedRange}><SelectTrigger className="h-8 w-44 text-xs"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="0.25">Ao vivo</SelectItem><SelectItem value="1">Última hora</SelectItem><SelectItem value="4">Últimas 4 horas</SelectItem><SelectItem value="24">Últimas 24 horas</SelectItem><SelectItem value="48">Últimos 2 dias</SelectItem><SelectItem value="168">Últimos 7 dias</SelectItem><SelectItem value="720">Últimos 30 dias</SelectItem><SelectItem value="custom">Personalizado</SelectItem></SelectContent></Select><Select value={selectedLevel} onValueChange={setSelectedLevel}><SelectTrigger className="h-8 w-32 text-xs"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">Todos os níveis</SelectItem><SelectItem value="INFO">Informações</SelectItem><SelectItem value="WARNING">Avisos</SelectItem><SelectItem value="ERROR">Erros</SelectItem></SelectContent></Select><Select value={selectedCategory} onValueChange={setSelectedCategory}><SelectTrigger className="h-8 w-32 text-xs"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">Todos os eventos</SelectItem><SelectItem value="errors">Erros</SelectItem><SelectItem value="orders">Ordens</SelectItem><SelectItem value="restarts">Reinícios</SelectItem></SelectContent></Select><Button size="sm" variant="outline" disabled={downloading || (selectedRange === "custom" && !(customSince && customUntil))} onClick={downloadLogs}><Download className="mr-1.5 h-3.5 w-3.5" />{downloading ? "Baixando..." : "Baixar log"}</Button></div>
    </div>
    {selectedRange === "custom" && <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground"><label>De <input type="datetime-local" value={customSince} onChange={(event) => setCustomSince(event.target.value)} className="ml-1 rounded border border-border bg-background px-2 py-1 text-foreground" /></label><label>Até <input type="datetime-local" value={customUntil} onChange={(event) => setCustomUntil(event.target.value)} className="ml-1 rounded border border-border bg-background px-2 py-1 text-foreground" /></label></div>}
    <div className="mb-3 flex flex-wrap gap-2">{["ALL", ...assets].map((asset) => <button key={asset} type="button" onClick={() => setSelectedAsset(asset)} className={`rounded-md border px-3 py-1.5 text-xs transition-colors ${selectedAsset === asset ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:border-primary/50"}`}>{asset === "ALL" ? "Todos" : asset}</button>)}</div>
    <div className="max-h-72 overflow-auto rounded-md border border-border/60 bg-background/40 p-2 font-mono-nums text-[11px]">{logs.isLoading ? <p className="p-3 text-muted-foreground">Carregando eventos...</p> : logs.isError ? <p className="p-3 text-warning">Não foi possível carregar os logs.</p> : allLogs.length ? allLogs.map((item) => <div key={item.id} className="flex gap-2 border-b border-border/40 py-1.5 last:border-0"><span className="shrink-0 text-muted-foreground">{formatTime(item.ts)}</span><span className={`shrink-0 ${item.level === "ERROR" ? "text-loss" : item.level === "WARNING" ? "text-warning" : "text-primary"}`}>{item.level}</span><span className="shrink-0 font-semibold text-primary">{shortSymbol(item.symbol || selectedAsset)}</span><span className="min-w-0 truncate text-foreground/80">{item.message}</span></div>) : <p className="p-3 text-muted-foreground">Nenhum evento para este filtro.</p>}</div>
    <div className="mt-2 flex items-center justify-between"><span className="text-[11px] text-muted-foreground">Exibindo {allLogs.length} registros carregados. O banco mantém até 7 dias.</span>{nextBeforeId && <Button size="sm" variant="outline" onClick={loadMore}>Carregar logs anteriores</Button>}</div>
  </section>;
}

export default function DashboardPage() {
  const query = useQuery({ queryKey: ["control-center", 30], queryFn: () => api.controlCenter(30), refetchInterval: 30_000, retry: 1 });
  if (query.isLoading) return <LoadingState message="Conectando ao Centro de Controle..." />;
  if (query.isError) return <ErrorState message="Não foi possível carregar a telemetria do Centro de Controle." onRetry={() => query.refetch()} />;
  const data = query.data!;
  const runtime = data.runtime;
  const realOn = Boolean(runtime.live_trading_enabled) && Boolean(runtime.exchange_write_gate);
  const databaseOk = runtime.database_backend === "postgres";
  const totalBlocked = data.assets.reduce((sum, item) => sum + item.blocked, 0);
  const totalCandidates = data.assets.reduce((sum, item) => sum + item.candidates, 0);
  return <div className="space-y-5 animate-slide-in">
    <ExchangeBalanceCard />
    <WorkerControl />
    <LiveWorkerLogsPaged />
    <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-end"><div><div className="mb-1 flex items-center gap-2 text-xs uppercase tracking-[0.18em] text-primary"><Activity className="h-3.5 w-3.5" /> centro de controle</div><h1 className="text-2xl font-semibold tracking-tight">Genesis <span className="text-muted-foreground">/ Visão geral</span></h1><p className="mt-1 text-sm text-muted-foreground">Operação real, sombra e evidências no mesmo painel.</p></div><div className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-xs"><span className={`h-2 w-2 rounded-full ${realOn ? "bg-profit animate-pulse" : "bg-warning"}`} /><span className={realOn ? "text-profit" : "text-warning"}>{realOn ? "REAL ATIVO" : "REAL PROTEGIDO"}</span><span className="text-muted-foreground">· {data.assets.length} ativos · atualização {formatTime(data.generated_at)}</span></div></div>
    <div className="grid grid-cols-2 gap-3 xl:grid-cols-4"><StatCard title="Banco operacional" value={databaseOk ? "PostgreSQL" : "indisponível"} subtitle={String(runtime.environment || "local")} icon={<Database className="h-4 w-4" />} trend={databaseOk ? "up" : "down"} /><StatCard title="Breakouts observados" value={data.assets.reduce((sum, item) => sum + item.raw_breakouts, 0)} subtitle={`janela móvel de ${data.window_days} dias`} icon={<Gauge className="h-4 w-4" />} /><StatCard title="Candidatos reais" value={totalCandidates} subtitle={`${totalBlocked} avaliações bloqueadas`} icon={<ShieldCheck className="h-4 w-4" />} trend={totalCandidates ? "up" : "neutral"} /><StatCard title="Sombra" value={data.shadow.events} subtitle={`${data.shadow.divergences} divergências · ${data.shadow.paper_trades} trades papel`} icon={<BrainCircuit className="h-4 w-4" />} /></div>
    <div className="grid gap-4 xl:grid-cols-[1.45fr_0.9fr]"><section className="rounded-lg border border-border bg-card p-4"><div className="mb-2 flex items-start justify-between"><div><h2 className="text-sm font-semibold">Mapa de regimes por ativo</h2><p className="mt-1 text-xs text-muted-foreground">Leitura mais recente registrada pelo runtime.</p></div><Radio className="h-4 w-4 text-primary" /></div><div className="grid grid-cols-[1.1fr_1.5fr_0.8fr_0.8fr_0.8fr] gap-3 border-b border-border py-2 text-[10px] uppercase tracking-wider text-muted-foreground"><span>Ativo</span><span>Regime</span><span>Romp.</span><span>Bloq.</span><span className="text-right">Conversão</span></div><div>{data.assets.map((asset) => <AssetRow key={asset.symbol} asset={asset} />)}</div></section><section className="rounded-lg border border-border bg-card p-4"><div className="mb-3 flex items-start justify-between"><div><h2 className="text-sm font-semibold">Guardião de promoção</h2><p className="mt-1 text-xs text-muted-foreground">O modo sombra não altera a operação real.</p></div><ShieldCheck className="h-4 w-4 text-profit" /></div><div className="space-y-2 text-xs"><div className="flex items-center justify-between rounded-md bg-profit/10 p-3"><span className="flex items-center gap-2"><span className="h-2 w-2 rounded-full bg-profit" />Campeão real</span><span className="font-mono-nums text-profit">protegido</span></div><div className="flex items-center justify-between rounded-md bg-primary/10 p-3"><span className="flex items-center gap-2"><Sparkles className="h-3.5 w-3.5 text-primary" />Variantes sombra</span><span className="font-mono-nums text-primary">{data.shadow.policies.length || "—"} ativas</span></div><div className="flex items-center justify-between rounded-md border border-border p-3"><span className="text-muted-foreground">Resultado sombra</span><span className={data.shadow.pnl >= 0 ? "font-mono-nums text-profit" : "font-mono-nums text-loss"}>{data.shadow.pnl >= 0 ? "+" : ""}{data.shadow.pnl.toFixed(2)} R</span></div><div className="flex items-center justify-between rounded-md border border-border p-3"><span className="text-muted-foreground">Critério</span><span className="text-right font-mono-nums text-[11px]">amostra + OOS + drawdown</span></div></div></section></div>
    <div className="grid gap-4 xl:grid-cols-2"><section className="rounded-lg border border-border bg-card p-4"><div className="mb-3 flex items-center justify-between"><div><h2 className="text-sm font-semibold">Principais bloqueios</h2><p className="mt-1 text-xs text-muted-foreground">O que está impedindo a entrada.</p></div><AlertTriangle className="h-4 w-4 text-warning" /></div>{data.blockers.length ? <div className="space-y-2">{data.blockers.slice(0, 6).map((item, index) => <div key={`${item.symbol}-${item.blocker}-${index}`} className="flex items-center justify-between border-b border-border/60 py-2 text-xs last:border-0"><span><span className="mr-2 font-mono-nums text-primary">{shortSymbol(item.symbol)}</span>{item.blocker}</span><span className="font-mono-nums text-warning">{item.total}</span></div>)}</div> : <p className="text-xs text-muted-foreground">Nenhuma avaliação de breakout disponível ainda.</p>}</section><section className="rounded-lg border border-border bg-card p-4"><div className="mb-3 flex items-center justify-between"><div><h2 className="text-sm font-semibold">Linha de decisão</h2><p className="mt-1 text-xs text-muted-foreground">Últimos eventos registrados pelo sistema.</p></div><Activity className="h-4 w-4 text-primary" /></div>{data.recent_events.length ? <div className="space-y-2">{data.recent_events.slice(0, 6).map((item, index) => <div key={`${item.timestamp_utc}-${index}`} className="flex items-center gap-3 border-b border-border/60 py-2 text-xs last:border-0"><span className={`h-2 w-2 rounded-full ${item.raw_breakout ? "bg-primary" : "bg-muted-foreground"}`} /><span className="font-mono-nums text-muted-foreground">{formatTime(item.timestamp_utc)}</span><span className="font-mono-nums text-primary">{shortSymbol(item.symbol)}</span><span className="truncate text-muted-foreground">{item.final_state || item.primary_blocker || "avaliação"}</span></div>)}</div> : <p className="text-xs text-muted-foreground">Nenhum evento recente disponível.</p>}</section></div>
  </div>;
}
