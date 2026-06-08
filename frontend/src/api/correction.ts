/* ════════════════════════════════════════════
 *  Stage 6 - Agent Stream 反向通道 API client
 *  /correct /clarify_response /cancel /status /active_workers
 * ════════════════════════════════════════════ */

export type CorrectionAction = "abort" | "redirect" | "param_override";

const authHeaders = (extra?: Record<string, string>): Record<string, string> => {
  const headers: Record<string, string> = { ...(extra ?? {}) };
  const token = localStorage.getItem("token");
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
};

const jsonPost = async <T>(url: string, body: unknown): Promise<T> => {
  const r = await fetch(url, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
    credentials: "include",
  });
  if (!r.ok) throw new Error(`POST ${url} failed: ${r.status}`);
  return (await r.json()) as T;
};

export const submitCorrection = (
  traceId: string,
  body: { action: CorrectionAction; instruction: string },
) =>
  jsonPost<{ ok: boolean }>(`/api/query/stream/${traceId}/correct`, {
    correction_type: body.action,
    instruction: body.instruction,
  });

export const submitClarifyResponse = (
  traceId: string,
  body: { pending_id: number; answer: string },
) => jsonPost<{ ok: boolean }>(`/api/query/stream/${traceId}/clarify_response`, body);

export const cancelStream = (traceId: string) =>
  jsonPost<{ cancelled: boolean }>(`/api/query/stream/${traceId}/cancel`, {});

export const fetchStreamStatus = async (
  traceId: string,
): Promise<{ status: string; trace_id: string }> => {
  const r = await fetch(`/api/query/stream/${traceId}/status`, {
    headers: authHeaders(),
    credentials: "include",
  });
  if (!r.ok) throw new Error(`GET status failed: ${r.status}`);
  return r.json();
};

export const fetchActiveWorkers = async (): Promise<{ trace_ids: string[] }> => {
  const r = await fetch("/api/query/active_workers", {
    headers: authHeaders(),
    credentials: "include",
  });
  if (!r.ok) throw new Error(`GET active_workers failed: ${r.status}`);
  return r.json();
};
