let accessTokenProvider: (() => string | null) | null = null;

export function setAccessTokenProvider(provider: (() => string | null) | null): void {
  accessTokenProvider = provider;
}

export function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = accessTokenProvider?.();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(input, { ...init, headers });
}

export function mutationHeaders(reason: string, json = false): Record<string, string> {
  const key = typeof globalThis.crypto?.randomUUID === "function"
    ? globalThis.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return {
    ...(json ? { "Content-Type": "application/json" } : {}),
    "Idempotency-Key": key,
    "X-Action-Reason": reason,
  };
}
