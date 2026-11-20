import { FormEvent, useEffect, useState } from "react";
import { api } from "@/lib/api";

type User = { id: string; email: string; role: string; is_active: boolean };

export default function UsersPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<"viewer" | "admin">("viewer");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const loadUsers = async () => setUsers(await api.users());
  useEffect(() => { void loadUsers(); }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      await api.createUser({ email: email.trim(), password, role });
      setEmail("");
      setPassword("");
      setMessage("Usuário cadastrado com sucesso.");
      await loadUsers();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Não foi possível cadastrar o usuário.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="max-w-2xl space-y-6 animate-slide-in">
      <div>
        <h2 className="text-sm font-medium text-muted-foreground uppercase tracking-wider mb-1">Usuários</h2>
        <p className="text-xs text-muted-foreground">Cadastre acessos adicionais ao painel Genesis Quant.</p>
      </div>

      <form onSubmit={submit} className="space-y-4 rounded-lg border border-border p-4">
        <div className="space-y-1.5">
          <label htmlFor="new-user-email" className="text-xs font-medium text-muted-foreground uppercase tracking-wider">E-mail</label>
          <input id="new-user-email" type="email" required value={email} onChange={(event) => setEmail(event.target.value)} className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm" />
        </div>
        <div className="space-y-1.5">
          <label htmlFor="new-user-password" className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Senha (mínimo 10 caracteres)</label>
          <input id="new-user-password" type="password" minLength={10} required value={password} onChange={(event) => setPassword(event.target.value)} className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm" />
        </div>
        <div className="space-y-1.5">
          <label htmlFor="new-user-role" className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Perfil</label>
          <select id="new-user-role" value={role} onChange={(event) => setRole(event.target.value as "viewer" | "admin")} className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm">
            <option value="viewer">Visualizador</option>
            <option value="admin">Administrador</option>
          </select>
        </div>
        <button type="submit" disabled={busy} className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
          {busy ? "Cadastrando..." : "Cadastrar usuário"}
        </button>
        {message && <p className="text-sm text-muted-foreground">{message}</p>}
      </form>

      <div className="space-y-2">
        <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Acessos cadastrados</h3>
        {users.map((user) => (
          <div key={user.id} className="flex items-center justify-between rounded-md border border-border px-3 py-2 text-sm">
            <span>{user.email}</span>
            <span className="text-xs text-muted-foreground">{user.role === "admin" ? "Administrador" : "Visualizador"}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
