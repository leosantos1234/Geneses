import { WorkerControl } from "@/pages/DashboardPage";

export default function ExecutionPage() {
  return <div className="space-y-4 animate-slide-in">
    <div>
      <h1 className="text-base font-semibold">Controle de execução</h1>
      <p className="mt-1 text-xs text-muted-foreground">Selecione ativos e estratégias para iniciar ou parar os workers.</p>
    </div>
    <WorkerControl />
  </div>;
}
