import { Link, Outlet, useNavigate, useLocation } from "react-router-dom";

import { clearToken } from "../services/auth";
import { Button } from "../components/ui/button";

const DashboardLayout = () => {
  const navigate = useNavigate();
  const location = useLocation();

  const handleLogout = () => {
    clearToken();
    navigate("/login", { replace: true });
  };

  const navItem = (to: string, label: string) => {
    const active = location.pathname.startsWith(to);
    return (
      <Link
        to={to}
        className={`block rounded-md px-4 py-2 ${active ? "bg-[#1c2230] text-white" : "text-white hover:bg-[#1c2230]"}`}
      >
        {label}
      </Link>
    );
  };

  return (
    <div className="min-h-screen flex bg-[var(--bg)] text-white">
      <aside className="w-64 border-r border-[var(--border)] bg-[#0f131a] p-4 space-y-4">
        <div className="text-xl font-bold">Genesis Quant</div>
        <nav className="space-y-2">
          {navItem("/dashboard", "Dashboard")}
          {navItem("/bots", "Bots")}
        </nav>
        <div>
          <Button
            onClick={handleLogout}
            style={{
              width: "100%",
              background: "transparent",
              color: "var(--text)",
              border: "1px solid var(--border)",
            }}
          >
            Logout
          </Button>
        </div>
      </aside>
      <main className="flex-1 p-6">
        <Outlet />
      </main>
    </div>
  );
};

export default DashboardLayout;
