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
