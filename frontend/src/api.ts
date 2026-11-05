import type { Scenario, TraceEvent, WorldState } from "./types";

const BASE = "/api";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

export const api = {
  getConfig: () => fetch(`${BASE}/config`).then(json<{ provider: string; model: string }>),

  listScenarios: () =>
    fetch(`${BASE}/scenarios`).then(json<{ scenarios: Scenario[] }>).then((d) => d.scenarios),

  getState: (sid: string) => fetch(`${BASE}/scenarios/${sid}/state`).then(json<WorldState>),

  reset: (sid: string) =>
    fetch(`${BASE}/scenarios/${sid}/reset`, { method: "POST" }).then(json<{ state: WorldState }>),

  getRun: (runId: string) => fetch(`${BASE}/runs/${runId}`).then(json<any>),

  approve: (runId: string) =>
    fetch(`${BASE}/runs/${runId}/approve`, { method: "POST" }).then(json<any>),

  reject: (runId: string) =>
    fetch(`${BASE}/runs/${runId}/reject`, { method: "POST" }).then(json<any>),

  confirmSupplier: (poId: string, qty: number) =>
    fetch(`${BASE}/suppliers/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ po_id: poId, confirmed_qty: qty }),
    }).then(json<any>),

  // Stream agent trace events via a POST + chunked reader (SSE-style).
  runAgent: async (
    sid: string,
    mode: "review" | "reconcile",
    onEvent: (ev: TraceEvent) => void
  ): Promise<void> => {
    const res = await fetch(`${BASE}/agent/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scenario_id: sid, mode }),
    });
    if (!res.body) throw new Error("No response stream");

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() ?? "";
      for (const part of parts) {
        const line = part.trim();
        if (line.startsWith("data:")) {
          try {
            onEvent(JSON.parse(line.slice(5).trim()));
          } catch {
            /* ignore malformed chunk */
          }
        }
      }
    }
  },
};
