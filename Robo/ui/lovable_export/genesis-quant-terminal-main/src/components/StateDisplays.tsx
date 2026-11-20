import { ReactNode } from "react";
import { Loader2, AlertTriangle, Inbox } from "lucide-react";

export function LoadingState({ message = "Carregando..." }: { message?: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-3 animate-slide-in">
      <Loader2 className="h-8 w-8 animate-spin text-primary" />
      <p className="text-sm text-muted-foreground">{message}</p>
    </div>
  );
}

export function ErrorState({ message = "Erro ao carregar dados.", onRetry }: { message?: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-3 animate-slide-in">
      <AlertTriangle className="h-8 w-8 text-loss" />
      <p className="text-sm text-muted-foreground">{message}</p>
      {onRetry && (
        <button onClick={onRetry} className="text-sm text-primary hover:underline">
          Tentar novamente
        </button>
      )}
    </div>
  );
}

export function EmptyState({ message = "Nenhum dado encontrado.", icon }: { message?: string; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-3 animate-slide-in">
      {icon || <Inbox className="h-8 w-8 text-muted-foreground" />}
      <p className="text-sm text-muted-foreground">{message}</p>
    </div>
  );
}
