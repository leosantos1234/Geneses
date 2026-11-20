import { cn } from "@/lib/utils";

export function SideBadge({ side }: { side: "buy" | "sell" }) {
  return (
    <span className={cn(
      "inline-flex items-center rounded px-1.5 py-0.5 text-xs font-semibold uppercase font-mono-nums",
      side === "buy" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"
    )}>
      {side === "buy" ? "LONG" : "SHORT"}
    </span>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    open: "bg-primary/15 text-primary",
    closed: "bg-muted text-muted-foreground",
    cancelled: "bg-warning/15 text-warning",
  };
  return (
    <span className={cn(
      "inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium capitalize",
      colors[status] || "bg-muted text-muted-foreground"
    )}>
      {status}
    </span>
  );
}

export function PnlDisplay({ value }: { value?: number }) {
  if (value === undefined || value === null) return <span className="text-muted-foreground">—</span>;
  const isPositive = value >= 0;
  return (
    <span className={cn("font-mono-nums font-medium", isPositive ? "text-profit" : "text-loss")}>
      {isPositive ? "+" : ""}{value.toFixed(2)}
    </span>
  );
}
