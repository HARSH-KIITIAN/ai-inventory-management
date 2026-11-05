import type { Decision, ExecutionResult, ValidationReport } from "../types";
import { Badge } from "./ui";

function ValidationView({ title, report }: { title: string; report: ValidationReport }) {
  return (
    <div className={`validation ${report.ok ? "val-ok" : "val-bad"}`}>
      <div className="val-head">
        <b>{title}</b>
        <Badge kind={report.ok ? "ok" : "bad"}>{report.ok ? "PASS" : "FAIL"}</Badge>
      </div>
      {report.violations.length > 0 && (
        <ul className="val-list val-viol">
          {report.violations.map((v, i) => <li key={i}>⛔ {v}</li>)}
        </ul>
      )}
      {report.warnings.length > 0 && (
        <ul className="val-list val-warn">
          {report.warnings.map((w, i) => <li key={i}>⚠️ {w}</li>)}
        </ul>
      )}
      {Object.keys(report.metrics).length > 0 && (
        <div className="metrics">
          {Object.entries(report.metrics).map(([k, v]) => (
            <span key={k} className="metric"><span>{k}</span><b>{String(v)}</b></span>
          ))}
        </div>
      )}
    </div>
  );
}

interface Props {
  decision: Decision;
  preValidation: ValidationReport | null;
  status: string;
  execution: ExecutionResult | null;
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
}

export function DecisionCard({ decision, preValidation, status, execution, busy, onApprove, onReject }: Props) {
  const a = decision.proposed_action;
  const pending = status === "pending_approval";

  return (
    <div className="decision">
      <div className="decision-head">
        <Badge kind={`cat-${decision.category}`}>{decision.category.toUpperCase()}</Badge>
        <span className="decision-summary">{decision.summary}</span>
        <span className="conf">confidence {(decision.confidence * 100).toFixed(0)}%</span>
      </div>

      <p className="reasoning">{decision.reasoning}</p>

      {decision.key_factors.length > 0 && (
        <ul className="factors">
          {decision.key_factors.map((f, i) => <li key={i}>{f}</li>)}
        </ul>
      )}

      <div className="action-box">
        <div className="action-head">Proposed action</div>
        {a.action === "none" ? (
          <p className="muted">No action — current plan is adequate.</p>
        ) : a.action === "escalate" ? (
          <p><b>Escalate</b> — {a.rationale}</p>
        ) : (
          <p>
            <b>{a.action}</b> · <code>{a.qty}</code> units of <code>{a.sku}</code>
            {a.supplier_id && <> from <code>{a.supplier_id}</code></>}
            {a.po_id && <> on <code>{a.po_id}</code></>}
            <br />
            <span className="muted">{a.rationale}</span>
          </p>
        )}
      </div>

      {preValidation && <ValidationView title="Pre-execution validation" report={preValidation} />}

      {pending && (
        <div className="approve-bar">
          <span className="gate">🔒 Human approval required</span>
          <div>
            <button className="btn btn-ghost" disabled={busy} onClick={onReject}>Reject</button>
            <button className="btn btn-primary" disabled={busy} onClick={onApprove}>Approve &amp; execute</button>
          </div>
        </div>
      )}

      {status === "no_action" && <p className="status-note">No action to approve — the agent recommends no purchase.</p>}
      {status === "rejected" && <p className="status-note bad">Rejected by buyer. No action taken.</p>}

      {execution && (
        <div className="execution">
          <div className="exec-head">
            <b>Execution</b>
            <Badge kind={execution.outcome_ok ? "ok" : "bad"}>
              {execution.executed ? (execution.outcome_ok ? "EXECUTED" : "EXECUTED — ISSUE") : "BLOCKED"}
            </Badge>
          </div>
          <p>{execution.message}</p>
          {execution.post_validation && (
            <ValidationView title="Post-action read-back" report={execution.post_validation} />
          )}
        </div>
      )}
    </div>
  );
}
