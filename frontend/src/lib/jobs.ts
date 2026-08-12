import { apiFetch } from "./http.js";

export type DurableJob = {
  job_id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "timed_out" | "dead_lettered";
  result?: Record<string, unknown> | null;
  error_message?: string | null;
  failure_reason?: string | null;
};

const terminal = new Set(["succeeded", "failed", "cancelled", "timed_out", "dead_lettered"]);

export async function pollDurableJob(
  apiBase: string,
  jobId: string,
  onUpdate: (job: DurableJob) => void,
  signal?: AbortSignal,
  intervalMs = 1_000,
): Promise<DurableJob> {
  for (;;) {
    const response = await apiFetch(
      `${apiBase}/api/admin/pipeline/jobs/${encodeURIComponent(jobId)}`,
      { credentials: "omit", signal },
    );
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || `Job status request failed (${response.status}).`);
    const job = body as DurableJob;
    onUpdate(job);
    if (terminal.has(job.status)) return job;
    await new Promise<void>((resolve, reject) => {
      const onAbort = () => {
        window.clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      };
      const timer = window.setTimeout(() => {
        signal?.removeEventListener("abort", onAbort);
        resolve();
      }, intervalMs);
      signal?.addEventListener("abort", onAbort, { once: true });
    });
  }
}
