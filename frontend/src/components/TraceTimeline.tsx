import type { TraceEvent } from "../types";

interface Step {
  kind: "tool" | "reasoning";
  name?: string;
  args?: unknown;
  result?: unknown;
  text?: string;
}

const TOOL_LABEL: Record<string, string> = {
  get_product: "Product data",
  get_inventory: "Inventory",
  get_demand: "Demand & forecast",
  get_open_orders: "Open purchase orders",
  get_suppliers: "Suppliers",
  get_budget: "Budget",
  get_storage: "Storage",
  compute_replenishment: "Compute replenishment (oracle)",
  validate_purchase_order: "Validate proposed order",
};

function buildSteps(events: TraceEvent[]): Step[] {
  const steps: Step[] = [];
  for (const ev of events) {
    if (ev.type === "tool_call") {
      steps.push({ kind: "tool", name: ev.name as string, args: ev.args });
    } else if (ev.type === "tool_result") {
      const last = [...steps].reverse().find((s) => s.kind === "tool" && s.name === ev.name && s.result === undefined);
      if (last) last.result = ev.result;
    } else if (ev.type === "reasoning") {
      steps.push({ kind: "reasoning", text: ev.text as string });
    }
  }
  return steps;
}

function compact(args: unknown): string {
  if (!args || typeof args !== "object") return "";
  const entries = Object.entries(args as Record<string, unknown>);
  return entries.map(([k, v]) => `${k}=${v}`).join(", ");
}

export function TraceTimeline({ events, running }: { events: TraceEvent[]; running: boolean }) {
  const steps = buildSteps(events);
  return (
    <div className="timeline">
      {steps.map((s, idx) =>
        s.kind === "reasoning" ? (
          <div key={idx} className="tl-item tl-reasoning">
            <div className="tl-dot">💭</div>
            <div className="tl-body"><p>{s.text}</p></div>
          </div>
        ) : (
          <div key={idx} className="tl-item">
            <div className="tl-dot">{s.name === "compute_replenishment" ? "🧮" : s.name === "validate_purchase_order" ? "✅" : "🔧"}</div>
            <div className="tl-body">
              <div className="tl-head">
                <b>{TOOL_LABEL[s.name!] ?? s.name}</b>
                <code className="tl-args">{compact(s.args)}</code>
              </div>
              {s.result !== undefined && (
                <details>
                  <summary>result</summary>
                  <pre>{JSON.stringify(s.result, null, 2)}</pre>
                </details>
              )}
            </div>
          </div>
        )
      )}
      {running && (
        <div className="tl-item">
          <div className="tl-dot pulse">●</div>
          <div className="tl-body"><p className="muted">investigating…</p></div>
        </div>
      )}
    </div>
  );
}
