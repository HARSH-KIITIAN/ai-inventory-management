import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import type { Decision, ExecutionResult, Scenario, TraceEvent, ValidationReport, WorldState } from "./types";
import { StatePanel } from "./components/StatePanel";
import { TraceTimeline } from "./components/TraceTimeline";
import { DecisionCard } from "./components/DecisionCard";
import { Badge } from "./components/ui";

export default function App() {
  const [provider, setProvider] = useState<{ provider: string; model: string } | null>(null);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [sid, setSid] = useState<string>("");
  const [state, setState] = useState<WorldState | null>(null);

  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [status, setStatus] = useState<string>("");
  const [decision, setDecision] = useState<Decision | null>(null);
  const [preValidation, setPreValidation] = useState<ValidationReport | null>(null);
  const [execution, setExecution] = useState<ExecutionResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmQty, setConfirmQty] = useState<number>(250);

  useEffect(() => {
    api.getConfig().then(setProvider).catch(() => {});
    api.listScenarios().then((s) => {
      setScenarios(s);
      if (s.length) setSid(s[0].id);
    });
  }, []);

  const loadState = useCallback((id: string) => api.getState(id).then(setState), []);

  const clearRun = useCallback(() => {
    setEvents([]); setRunId(null); setStatus(""); setDecision(null);
    setPreValidation(null); setExecution(null);
  }, []);

  useEffect(() => {
    if (!sid) return;
    clearRun();
    loadState(sid);
  }, [sid, clearRun, loadState]);

  const scenario = scenarios.find((s) => s.id === sid);
  const focusPo = state?.purchase_orders.find((p) => p.id === scenario?.focus_po_id);

  const runAgent = async (mode: "review" | "reconcile") => {
    clearRun();
    setRunning(true);
    try {
      await api.runAgent(sid, mode, (ev) => {
        setEvents((prev) => [...prev, ev]);
        if (ev.type === "decision") setDecision(ev.decision as Decision);
        if (ev.type === "pre_validation") setPreValidation(ev.report as ValidationReport);
        if (ev.type === "saved") { setRunId(ev.run_id as string); setStatus(ev.status as string); }
      });
    } finally {
      setRunning(false);
    }
  };

  const approve = async () => {
    if (!runId) return;
    setBusy(true);
    try {
      const res = await api.approve(runId);
      setExecution(res.execution as ExecutionResult);
      setStatus(res.status as string);
      await loadState(sid);
    } finally {
      setBusy(false);
    }
  };

  const reject = async () => {
    if (!runId) return;
    setBusy(true);
    try {
      await api.reject(runId);
      setStatus("rejected");
    } finally {
      setBusy(false);
    }
  };

  const confirmSupplier = async () => {
    if (!focusPo) return;
    setBusy(true);
    try {
      await api.confirmSupplier(focusPo.id, confirmQty);
      await loadState(sid);
      clearRun();
    } finally {
      setBusy(false);
    }
  };

  const resetScenario = async () => {
    setBusy(true);
    try {
      await api.reset(sid);
      clearRun();
      await loadState(sid);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">▦</span>
          <div>
            <h1>AI Purchasing Agent</h1>
            <p>Investigate · Decide · Execute · Validate</p>
          </div>
        </div>
        {provider && (
          <div className="provider">
            <span className="muted">LLM</span>
            <Badge kind="prov">{provider.provider}</Badge>
            <code>{provider.model}</code>
          </div>
        )}
      </header>

      <div className="layout">
        {/* Left: scenarios + world state */}
        <aside className="sidebar">
          <div className="scenario-list">
            {scenarios.map((s) => (
              <button
                key={s.id}
                className={`scenario-btn ${s.id === sid ? "active" : ""}`}
                onClick={() => setSid(s.id)}
              >
                <b>{s.id}</b>
                <span>{s.title}</span>
              </button>
            ))}
          </div>
          {state && <StatePanel state={state} />}
        </aside>

        {/* Right: agent interaction */}
        <main className="main">
          {scenario && (
            <div className="situation">
              <div className="situation-head">
                <h2>{scenario.id} · {scenario.title}</h2>
                <button className="btn btn-ghost sm" disabled={busy || running} onClick={resetScenario}>Reset</button>
              </div>
              <p>{scenario.situation}</p>
              {scenario.recommended_qty != null && (
                <p className="sysrec">
                  System recommendation: <b>{scenario.recommended_qty} units</b> of{" "}
                  <code>{scenario.recommended_sku}</code> at <code>{scenario.recommended_node}</code>
                  <span className="muted"> — not assumed correct.</span>
                </p>
              )}
              <div className="run-controls">
                <button className="btn btn-primary" disabled={running || busy} onClick={() => runAgent("review")}>
                  {running ? "Running…" : "▶ Run agent"}
                </button>
              </div>
            </div>
          )}

          {/* Scenario-2 supplier fulfillment control */}
          {focusPo && (
            <div className="supplier-event">
              <h3>Supplier fulfillment · {focusPo.id}</h3>
              {focusPo.status === "partial" ? (
                <>
                  <p>
                    Supplier confirmed only <b>{focusPo.qty_confirmed}</b> of {focusPo.qty_ordered} units —{" "}
                    shortfall <b>{focusPo.qty_ordered - (focusPo.qty_confirmed ?? 0)}</b>.
                  </p>
                  <button className="btn btn-warn" disabled={running || busy} onClick={() => runAgent("reconcile")}>
                    ↻ Run reconcile agent
                  </button>
                </>
              ) : (
                <div className="confirm-row">
                  <span>Supplier can only supply</span>
                  <input
                    type="number" value={confirmQty} min={0} max={focusPo.qty_ordered}
                    onChange={(e) => setConfirmQty(Number(e.target.value))}
                  />
                  <span>of {focusPo.qty_ordered}</span>
                  <button className="btn btn-ghost" disabled={busy || running} onClick={confirmSupplier}>
                    Report to system
                  </button>
                </div>
              )}
            </div>
          )}

          {events.length > 0 && (
            <div className="card">
              <h3>Agent investigation</h3>
              <TraceTimeline events={events} running={running} />
            </div>
          )}

          {decision && (
            <div className="card">
              <h3>Decision</h3>
              <DecisionCard
                decision={decision}
                preValidation={preValidation}
                status={status}
                execution={execution}
                busy={busy}
                onApprove={approve}
                onReject={reject}
              />
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
