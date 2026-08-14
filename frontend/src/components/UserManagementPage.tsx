import { useCallback, useEffect, useState } from "react";
import { LoaderCircle, UserPlus } from "lucide-react";

import { apiFetch, mutationHeaders } from "../lib/http";


type AppRole = "customer" | "operator" | "admin";

type ManagedUser = {
  id: string;
  email: string | null;
  enabled: boolean;
  status: string;
  role: AppRole | null;
};

type UserPage = {
  users: ManagedUser[];
  next_token: string | null;
};


export default function UserManagementPage({ apiBase }: { apiBase: string }) {
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [nextToken, setNextToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<AppRole>("customer");

  const load = useCallback(async (token?: string) => {
    setLoading(true);
    setError(null);
    try {
      const query = new URLSearchParams({ page_size: "50" });
      if (token) query.set("next_token", token);
      const response = await apiFetch(`${apiBase}/api/admin/users?${query.toString()}`, { credentials: "omit" });
      if (!response.ok) throw new Error(`User list request failed (${response.status}).`);
      const page = await response.json() as UserPage;
      setUsers(current => token ? [...current, ...page.users] : page.users);
      setNextToken(page.next_token);
    } catch (caught) {
      setError((caught as Error).message || "User management is unavailable.");
    } finally {
      setLoading(false);
    }
  }, [apiBase]);

  useEffect(() => { void load(); }, [load]);

  const parseFailure = async (response: Response): Promise<string> => {
    const body = await response.json().catch(() => ({}));
    return typeof body.detail === "string" ? body.detail : `User operation failed (${response.status}).`;
  };

  const invite = async () => {
    setPending("invite");
    setError(null);
    try {
      const response = await apiFetch(`${apiBase}/api/admin/users`, {
        method: "POST",
        credentials: "omit",
        headers: mutationHeaders("invite Cognito user", true),
        body: JSON.stringify({ email, role }),
      });
      if (!response.ok) throw new Error(await parseFailure(response));
      setEmail("");
      await load();
    } catch (caught) {
      setError((caught as Error).message || "User invitation failed.");
    } finally {
      setPending(null);
    }
  };

  const mutate = async (user: ManagedUser, action: "role" | "disable" | "enable" | "reset-password", nextRole?: AppRole) => {
    setPending(`${user.id}:${action}`);
    setError(null);
    const suffix = action === "role" ? "role" : action;
    try {
      const response = await apiFetch(`${apiBase}/api/admin/users/${encodeURIComponent(user.id)}/${suffix}`, {
        method: action === "role" ? "PATCH" : "POST",
        credentials: "omit",
        headers: mutationHeaders(`admin user ${action}`, action === "role"),
        body: action === "role" ? JSON.stringify({ role: nextRole }) : undefined,
      });
      if (!response.ok) throw new Error(await parseFailure(response));
      await load();
    } catch (caught) {
      setError((caught as Error).message || "User operation failed.");
    } finally {
      setPending(null);
    }
  };

  return (
    <div className="flex-col-gap">
      <div className="page-header-row">
        <div><span className="page-eyebrow">Administration</span><h2>User Management</h2><p>Invite users and maintain exactly one application role per account.</p></div>
      </div>
      {error && <div className="data-notice data-notice-error" role="alert">{error}</div>}
      <div className="glass-card user-invite-card">
        <label className="filter-group"><span className="filter-label">Email</span><input className="form-input" type="email" value={email} onChange={event => setEmail(event.target.value)} /></label>
        <label className="filter-group"><span className="filter-label">Role</span><select className="form-input" value={role} onChange={event => setRole(event.target.value as AppRole)}><option value="customer">Customer</option><option value="operator">Operator</option><option value="admin">Admin</option></select></label>
        <button type="button" className="btn btn-primary" disabled={!email.trim() || pending === "invite"} onClick={() => void invite()}>{pending === "invite" ? <LoaderCircle className="spin" size={16} /> : <UserPlus size={16} />} Invite user</button>
      </div>
      <div className="glass-card user-table-card">
        {loading && users.length === 0 ? <p><LoaderCircle className="spin" size={16} /> Loading users…</p> : (
          <div className="user-table-scroll"><table className="user-table"><thead><tr><th>User</th><th>Status</th><th>Role</th><th>Actions</th></tr></thead><tbody>{users.map(user => <tr key={user.id}><td><strong>{user.email || "Email unavailable"}</strong><small>{user.id}</small></td><td>{user.enabled ? user.status : "DISABLED"}</td><td><select aria-label={`Role for ${user.email || user.id}`} value={user.role || "customer"} disabled={pending?.startsWith(`${user.id}:`)} onChange={event => void mutate(user, "role", event.target.value as AppRole)}><option value="customer">Customer</option><option value="operator">Operator</option><option value="admin">Admin</option></select></td><td><div className="user-row-actions"><button type="button" className="btn btn-secondary" disabled={pending?.startsWith(`${user.id}:`)} onClick={() => void mutate(user, user.enabled ? "disable" : "enable")}>{user.enabled ? "Disable" : "Enable"}</button><button type="button" className="btn btn-secondary" disabled={pending?.startsWith(`${user.id}:`)} onClick={() => void mutate(user, "reset-password")}>Reset password</button></div></td></tr>)}</tbody></table></div>
        )}
        {nextToken && <button type="button" className="btn btn-secondary" disabled={loading} onClick={() => void load(nextToken)}>Load more</button>}
      </div>
    </div>
  );
}
