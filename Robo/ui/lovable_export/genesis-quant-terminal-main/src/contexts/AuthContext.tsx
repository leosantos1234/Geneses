import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

interface AuthContextType {
  user: { id: string; email: string; role?: string } | null;
  session: { access_token: string } | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);
const LOCAL_AUTH_KEY = "genesis_quant_auth";
const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8010";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<{ id: string; email: string; role?: string } | null>(null);
  const [session, setSession] = useState<{ access_token: string } | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    const restoreSession = async () => {
      try {
        const raw = localStorage.getItem(LOCAL_AUTH_KEY);
        const parsed = raw ? JSON.parse(raw) as {
          user?: { id: string; email: string; role?: string };
          session?: { access_token: string };
        } : null;
        const accessToken = parsed?.session?.access_token;

        if (!parsed?.user || !accessToken) {
          localStorage.removeItem(LOCAL_AUTH_KEY);
          return;
        }

        const response = await fetch(`${API_BASE}/api/auth/me`, {
          headers: { Authorization: `Bearer ${accessToken}` },
        });
        if (!response.ok) throw new Error("invalid_session");

        const data = await response.json();
        if (!cancelled) {
          setUser(data.user || parsed.user);
          setSession(parsed.session);
        }
      } catch {
        localStorage.removeItem(LOCAL_AUTH_KEY);
        if (!cancelled) {
          setUser(null);
          setSession(null);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void restoreSession();
    return () => { cancelled = true; };
  }, []);

  const signIn = async (email: string, password: string) => {
    if (!email || !password) {
      throw new Error("Email e senha sao obrigatorios.");
    }
    const response = await fetch(`${API_BASE}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data?.detail || "Credenciais inválidas.");
    const nextUser = data.user;
    const nextSession = { access_token: data.access_token };
    setUser(nextUser);
    setSession(nextSession);
    localStorage.setItem(LOCAL_AUTH_KEY, JSON.stringify({ user: nextUser, session: nextSession }));
  };

  const signOut = async () => {
    setUser(null);
    setSession(null);
    localStorage.removeItem(LOCAL_AUTH_KEY);
  };

  return (
    <AuthContext.Provider value={{ user, session, loading, signIn, signOut }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AuthProvider");
  return context;
}
