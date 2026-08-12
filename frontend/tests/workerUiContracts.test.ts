import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const source = (relativePath: string) => readFileSync(join(process.cwd(), relativePath), "utf8");

test("organisation enrichment polls durable backend state and refetches persisted data", () => {
  const donor = source("src/components/DonorDirectoryPage.tsx");
  const registry = source("src/components/RegistryDirectory.tsx");
  const jobs = source("src/lib/jobs.ts");

  assert.match(donor, /pollDurableJob\(apiBase, body\.job_id/);
  assert.match(donor, /fetchDetail\(selectedKey, activityLoaded\)/);
  assert.match(registry, /pollDurableJob\(apiBase, payload\.job_id/);
  assert.match(registry, /setDetailRefreshRevision\(current => current \+ 1\)/);
  assert.match(jobs, /job\.status/);
  assert.match(jobs, /terminal\.has\(job\.status\)/);
});

test("queued and running remain distinct and fabricated numeric progress is absent", () => {
  const donor = source("src/components/DonorDirectoryPage.tsx");
  const registry = source("src/components/RegistryDirectory.tsx");
  const app = source("src/App.tsx");
  const combined = `${donor}\n${registry}\n${app}`;

  assert.match(donor, /status: job\.status === "queued" \? "queued" : "running"/);
  assert.match(registry, /status: job\.status === "queued" \? "queued" : "running"/);
  assert.match(combined, /is-indeterminate/);
  assert.doesNotMatch(combined, /Math\.min\(94/);
  assert.doesNotMatch(combined, /progress:\s*94/);
});

test("failed durable jobs expose a safe terminal error instead of running forever", () => {
  const donor = source("src/components/DonorDirectoryPage.tsx");
  const registry = source("src/components/RegistryDirectory.tsx");
  assert.match(donor, /completed\.failure_reason \|\| completed\.error_message/);
  assert.match(registry, /completed\.failure_reason \|\| completed\.error_message/);
  assert.match(donor, /status: "failed"/);
  assert.match(registry, /status: "failed"/);
});
