import {
  type ReactNode,
  useEffect,
  useRef,
  useState,
} from "react";

import { setAccessTokenProvider } from "../lib/http";
import { AuthContext, type AuthValue, type Identity } from "./authState";


type AuthConfig = {
  mode: string;
  region?: string;
  user_pool_id?: string;
  client_id?: string;
  domain?: string;
  scopes?: string[];
};

type TokenSet = {
  access_token: string;
  id_token?: string;
  refresh_token?: string;
  expires_in?: number;
  token_type?: string;
};

const TOKEN_KEY = "fip-cognito-token-session";
const STATE_KEY = "fip-cognito-oauth-state";
const VERIFIER_KEY = "fip-cognito-pkce-verifier";


function randomBase64Url(byteLength: number): string {
  const bytes = new Uint8Array(byteLength);
  crypto.getRandomValues(bytes);
  let binary = "";
  bytes.forEach(value => { binary += String.fromCharCode(value); });
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}


async function sha256Base64Url(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  let binary = "";
  new Uint8Array(digest).forEach(byte => { binary += String.fromCharCode(byte); });
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}


function decodeDisplayClaims(token?: string): Record<string, unknown> {
  if (!token) return {};
  try {
    const payload = token.split(".")[1];
    if (!payload) return {};
    const normalized = payload.replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=");
    return JSON.parse(atob(padded)) as Record<string, unknown>;
  } catch {
    return {};
  }
}


function readTokens(): TokenSet | null {
  try {
    const raw = sessionStorage.getItem(TOKEN_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as TokenSet;
    return typeof parsed.access_token === "string" ? parsed : null;
  } catch {
    return null;
  }
}


function tokenIsUsable(token: string): boolean {
  const claims = decodeDisplayClaims(token);
  return typeof claims.exp === "number" && claims.exp > Date.now() / 1000 + 60;
}


async function tokenRequest(config: AuthConfig, body: URLSearchParams): Promise<TokenSet> {
  if (!config.domain) throw new Error("Managed login domain is unavailable.");
  const response = await fetch(`${config.domain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!response.ok) throw new Error("Managed login token exchange failed.");
  const tokens = await response.json() as TokenSet;
  if (!tokens.access_token) throw new Error("Managed login returned no access token.");
  return tokens;
}


export function AuthProvider({ children }: { children: ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [config, setConfig] = useState<AuthConfig>({ mode: "loading" });
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [error, setError] = useState<string | null>(null);
  const initialized = useRef(false);
  const tokenRef = useRef<TokenSet | null>(null);

  const applyTokens = (next: TokenSet | null) => {
    tokenRef.current = next;
    if (next) sessionStorage.setItem(TOKEN_KEY, JSON.stringify(next));
    else sessionStorage.removeItem(TOKEN_KEY);
  };

  useEffect(() => {
    setAccessTokenProvider(() => tokenRef.current?.access_token || null);
    return () => setAccessTokenProvider(null);
  }, []);

  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    const initialize = async () => {
      try {
        const response = await fetch("/api/auth/config", { credentials: "omit" });
        if (!response.ok) throw new Error("Authentication configuration is unavailable.");
        const loadedConfig = await response.json() as AuthConfig;
        setConfig(loadedConfig);
        if (loadedConfig.mode !== "cognito_rbac") {
          setIdentity({
            sub: loadedConfig.mode === "public_readonly" ? "public-readonly-demo" : "local-user",
            username: loadedConfig.mode === "public_readonly" ? "Public demo" : "Local user",
            roles: [loadedConfig.mode === "public_readonly" ? "customer" : "admin"],
          });
          return;
        }
        if (!loadedConfig.client_id || !loadedConfig.domain) {
          throw new Error("Managed login configuration is incomplete.");
        }

        let currentTokens = readTokens();
        if (window.location.pathname === "/auth/callback") {
          const query = new URLSearchParams(window.location.search);
          const code = query.get("code");
          const returnedState = query.get("state");
          const expectedState = sessionStorage.getItem(STATE_KEY);
          const verifier = sessionStorage.getItem(VERIFIER_KEY);
          history.replaceState({}, "", "/");
          sessionStorage.removeItem(STATE_KEY);
          sessionStorage.removeItem(VERIFIER_KEY);
          if (query.get("error")) throw new Error("Managed login was not completed.");
          if (!code || !returnedState || !expectedState || returnedState !== expectedState || !verifier) {
            throw new Error("Managed login callback validation failed.");
          }
          currentTokens = await tokenRequest(loadedConfig, new URLSearchParams({
            grant_type: "authorization_code",
            client_id: loadedConfig.client_id,
            code,
            redirect_uri: `${window.location.origin}/auth/callback`,
            code_verifier: verifier,
          }));
        } else if (currentTokens && !tokenIsUsable(currentTokens.access_token) && currentTokens.refresh_token) {
          const refreshed = await tokenRequest(loadedConfig, new URLSearchParams({
            grant_type: "refresh_token",
            client_id: loadedConfig.client_id,
            refresh_token: currentTokens.refresh_token,
          }));
          currentTokens = { ...currentTokens, ...refreshed, refresh_token: refreshed.refresh_token || currentTokens.refresh_token };
        }

        if (!currentTokens || !tokenIsUsable(currentTokens.access_token)) {
          applyTokens(null);
          return;
        }
        applyTokens(currentTokens);
        const me = await fetch("/api/auth/me", {
          headers: { Authorization: `Bearer ${currentTokens.access_token}` },
          credentials: "omit",
        });
        if (!me.ok) throw new Error("Authenticated identity could not be verified.");
        const verified = await me.json() as Identity;
        const idClaims = decodeDisplayClaims(currentTokens.id_token);
        setIdentity({
          ...verified,
          email: typeof idClaims.email === "string" ? idClaims.email : undefined,
        });
      } catch (caught) {
        applyTokens(null);
        setIdentity(null);
        setError((caught as Error).message || "Authentication failed.");
      } finally {
        setLoading(false);
      }
    };
    void initialize();
  }, []);

  const login = async () => {
    if (!config.client_id || !config.domain) {
      setError("Managed login configuration is unavailable.");
      return;
    }
    const verifier = randomBase64Url(64);
    const state = randomBase64Url(32);
    const challenge = await sha256Base64Url(verifier);
    sessionStorage.setItem(VERIFIER_KEY, verifier);
    sessionStorage.setItem(STATE_KEY, state);
    const query = new URLSearchParams({
      response_type: "code",
      client_id: config.client_id,
      redirect_uri: `${window.location.origin}/auth/callback`,
      scope: (config.scopes || ["openid", "email", "profile"]).join(" "),
      state,
      code_challenge: challenge,
      code_challenge_method: "S256",
    });
    window.location.assign(`${config.domain}/oauth2/authorize?${query.toString()}`);
  };

  const logout = () => {
    applyTokens(null);
    setIdentity(null);
    sessionStorage.removeItem(STATE_KEY);
    sessionStorage.removeItem(VERIFIER_KEY);
    if (config.mode === "cognito_rbac" && config.client_id && config.domain) {
      const query = new URLSearchParams({
        client_id: config.client_id,
        logout_uri: `${window.location.origin}/`,
      });
      window.location.assign(`${config.domain}/logout?${query.toString()}`);
    }
  };

  const value: AuthValue = {
    loading,
    authenticated: Boolean(identity),
    mode: config.mode,
    identity,
    role: identity?.roles[0] || null,
    error,
    login,
    logout,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
