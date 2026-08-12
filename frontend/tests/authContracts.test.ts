import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

import { apiFetch, setAccessTokenProvider } from "../src/lib/http.js";


const source = (relativePath: string) => readFileSync(join(process.cwd(), relativePath), "utf8");

test("Managed Login uses authorization code plus PKCE and removes callback data", () => {
  const auth = source("src/auth/AuthContext.tsx");

  assert.match(auth, /response_type:\s*"code"/);
  assert.match(auth, /code_challenge_method:\s*"S256"/);
  assert.match(auth, /code_verifier:\s*verifier/);
  assert.match(auth, /returnedState\s*!==\s*expectedState/);
  assert.match(auth, /history\.replaceState\(\{\},\s*"",\s*"\/"\)/);
  assert.doesNotMatch(auth, /localStorage/);
  assert.match(auth, /sessionStorage\.setItem\(TOKEN_KEY/);
});

test("authenticated API requests use bearer tokens without browser credentials", async () => {
  const originalFetch = globalThis.fetch;
  let captured: RequestInit | undefined;
  globalThis.fetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
    captured = init;
    return new Response(null, { status: 204 });
  }) as typeof fetch;

  try {
    setAccessTokenProvider(() => "verified-access-token");
    await apiFetch("/api/example", { credentials: "omit" });
    const headers = new Headers(captured?.headers);
    assert.equal(headers.get("Authorization"), "Bearer verified-access-token");
    assert.equal(captured?.credentials, "omit");
  } finally {
    setAccessTokenProvider(null);
    globalThis.fetch = originalFetch;
  }
});

test("role-gated UI keeps operational and user administration controls restricted", () => {
  const app = source("src/App.tsx");
  const donor = source("src/components/DonorDirectoryPage.tsx");
  const registry = source("src/components/RegistryDirectory.tsx");
  const users = source("src/components/UserManagementPage.tsx");
  const allFrontend = `${app}\n${donor}\n${registry}\n${users}`;

  assert.match(app, /const canOperate = auth\.role === "operator" \|\| auth\.role === "admin"/);
  assert.match(app, /const isAdmin = auth\.role === "admin"/);
  assert.match(app, /isAdmin && .*User Management/s);
  assert.match(app, /canOperate && <button[\s\S]*Research latest news/);
  assert.match(donor, /canOperate &&/);
  assert.match(registry, /canOperate &&/);
  assert.doesNotMatch(allFrontend, /credentials:\s*"include"/);
  assert.doesNotMatch(users, /method:\s*"DELETE"/);
});

test("Cognito guests use the footer login and the demo reset control is absent", () => {
  const app = source("src/App.tsx");
  const donor = source("src/components/DonorDirectoryPage.tsx");
  const styles = source("src/index.css");

  assert.match(app, /"Netlight Guest"/);
  assert.match(app, /"Not signed in"/);
  assert.match(app, /onClick=\{\(\) => void auth\.login\(\)\}/);
  assert.match(app, />Sign in<\/span>/);
  assert.match(app, />Sign out<\/span>/);
  assert.match(app, /auth\.identity\?\.email \|\| auth\.identity\?\.username/);
  assert.match(app, /auth\.authenticated \? auth\.role : "Not signed in"/);
  assert.doesNotMatch(app, /if \(!auth\.authenticated\)/);
  assert.doesNotMatch(app, /resetActiveSourceFunderToObserved/);
  assert.doesNotMatch(app, /reset-to-observed/);
  assert.doesNotMatch(app, /user-avatar-reset/);
  assert.doesNotMatch(donor, /source-funder-reset-to-observed|active-source-funder-change/);
  assert.doesNotMatch(styles, /user-avatar-reset/);
  assert.match(app, /onClick=\{clearActiveProfileSafely\}/);
  assert.match(app, /event\.key === "Escape"/);
});
