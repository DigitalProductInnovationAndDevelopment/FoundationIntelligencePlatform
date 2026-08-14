import { createContext, useContext } from "react";


export type AppRole = "customer" | "operator" | "admin";

export type Identity = {
  sub: string;
  username?: string;
  roles: AppRole[];
  email?: string;
};

export type AuthValue = {
  loading: boolean;
  authenticated: boolean;
  mode: string;
  identity: Identity | null;
  role: AppRole | null;
  error: string | null;
  login: () => Promise<void>;
  logout: () => void;
};

export const AuthContext = createContext<AuthValue | null>(null);

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
