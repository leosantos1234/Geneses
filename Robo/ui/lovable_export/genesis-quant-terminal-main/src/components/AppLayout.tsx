import { NavLink } from "@/components/NavLink";
import { useLocation, useNavigate } from "react-router-dom";
import {
  LayoutDashboard,
  TrendingUp,
  History,
  Shield,
  Activity,
  Menu,
  X,
  LogOut,
} from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/contexts/AuthContext";

const navItems = [
  { title: "Dashboard", url: "/dashboard", icon: LayoutDashboard },
  { title: "Posicoes", url: "/positions", icon: TrendingUp },
  { title: "Trades", url: "/trades", icon: History },
  { title: "Risco", url: "/settings/risk", icon: Shield },
];

export function AppLayout({ children }: { children: React.ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();
  const { signOut, user } = useAuth();

  const handleLogout = async () => {
    await signOut();
    navigate("/auth", { replace: true });
  };

  return (
    <div className="flex min-h-screen w-full bg-background">
      {mobileOpen && (
        <div className="fixed inset-0 z-40 bg-background/80 backdrop-blur-sm lg:hidden" onClick={() => setMobileOpen(false)} />
      )}

      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex w-56 flex-col border-r border-sidebar-border bg-sidebar transition-[transform,width] lg:w-14 lg:translate-x-0 lg:static lg:z-auto",
          mobileOpen ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex h-14 items-center gap-2 border-b border-sidebar-border px-4 lg:justify-center lg:px-0">
          <Activity className="h-5 w-5 text-primary" />
          <span className="text-sm font-semibold tracking-tight text-foreground lg:hidden">Genesis Quant</span>
          <button className="ml-auto lg:hidden" onClick={() => setMobileOpen(false)}>
            <X className="h-4 w-4 text-muted-foreground" />
          </button>
        </div>

        <nav className="flex-1 space-y-0.5 px-2 py-3">
          {navItems.map((item) => (
            <NavLink
              key={item.url}
              to={item.url}
              end={item.url === "/dashboard"}
              title={item.title}
              className="group relative flex items-center gap-2.5 rounded-md px-3 py-2 text-sm text-sidebar-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground lg:justify-center lg:px-2"
              activeClassName="bg-sidebar-accent text-primary font-medium"
              onClick={() => setMobileOpen(false)}
            >
              <item.icon className="h-4 w-4 shrink-0" />
              <span className="lg:hidden">{item.title}</span>
              <span className="pointer-events-none absolute left-full z-50 ml-2 hidden whitespace-nowrap rounded-md border border-border bg-popover px-2.5 py-1.5 text-xs text-popover-foreground shadow-lg group-hover:block group-focus-visible:block">{item.title}</span>
            </NavLink>
          ))}
        </nav>

        <div className="space-y-2 border-t border-sidebar-border px-4 py-3 lg:px-2">
          <div className="flex items-center gap-2">
            <div className="h-2 w-2 rounded-full bg-profit animate-pulse-glow" />
            <span className="text-xs text-muted-foreground lg:hidden">Sistema ativo</span>
          </div>
          <div className="truncate text-xs text-muted-foreground lg:hidden" title={user?.email || ""}>{user?.email}</div>
          <button
            onClick={handleLogout}
            title="Sair"
            className="inline-flex w-full items-center justify-center gap-2 rounded-md border border-sidebar-border px-3 py-1.5 text-xs text-sidebar-foreground transition-colors hover:bg-sidebar-accent lg:border-0 lg:px-2"
          >
            <LogOut className="h-3.5 w-3.5" />
            <span className="lg:hidden">Sair</span>
          </button>
        </div>
      </aside>

      <div className="flex flex-1 flex-col min-w-0">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-border bg-background/95 backdrop-blur-sm px-4">
          <button className="lg:hidden" onClick={() => setMobileOpen(true)}>
            <Menu className="h-5 w-5 text-muted-foreground" />
          </button>
          <h1 className="text-sm font-medium text-foreground truncate">
            {navItems.find((n) => location.pathname.startsWith(n.url))?.title || "Genesis Quant"}
          </h1>
        </header>

        <main className="flex-1 p-4 lg:p-6">{children}</main>
      </div>
    </div>
  );
}
