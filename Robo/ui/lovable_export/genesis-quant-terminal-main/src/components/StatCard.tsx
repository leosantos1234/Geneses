import { cn } from "@/lib/utils";

interface StatCardProps {
  title: string;
  value: string | number;
  subtitle?: string;
  icon?: React.ReactNode;
  trend?: "up" | "down" | "neutral";
  className?: string;
}

export function StatCard({ title, value, subtitle, icon, trend, className }: StatCardProps) {
  return (
    <div className={cn(
      "rounded-lg border border-border bg-card p-4 flex flex-col gap-1 glow-primary transition-all hover:border-primary/30",
      className
    )}>
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{title}</span>
        {icon && <span className="text-muted-foreground">{icon}</span>}
      </div>
      <span className={cn(
        "text-2xl font-semibold font-mono-nums tracking-tight",
        trend === "up" && "text-profit",
        trend === "down" && "text-loss",
      )}>
        {value}
      </span>
      {subtitle && <span className="text-xs text-muted-foreground">{subtitle}</span>}
    </div>
  );
}
