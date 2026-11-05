"""Run registry + action execution with post-action verification.

This module owns the part of the feedback loop that happens AFTER the agent
decides: it stores pending decisions for human approval, executes an approved
action, and then RE-READS the resulting world to verify the outcome is
acceptable. If execution-time re-validation fails, the action is blocked; if the
post-action read-back finds a problem, it is surfaced rather than ignored.

The registry is an in-process dict — fine for this single-process demo.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Optional

from sqlmodel import Session

from .agent.agent import RunResult
from .domain import orders, world
from .engine import replenishment as R
from .engine.validation import validate_po
from .schemas import Decision, ExecutionResult, ValidationReport

_LOCK = threading.Lock()


@dataclass
class RunRecord:
    run_id: str
    scenario_id: str
    mode: str
    provider: str
    trace: list[dict]
    decision: Decision
    pre_validation: Optional[ValidationReport]
    status: str                              # pending_approval | no_action | approved | rejected | executed | blocked
    execution: Optional[ExecutionResult] = None

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "scenario_id": self.scenario_id,
            "mode": self.mode,
            "provider": self.provider,
            "trace": self.trace,
            "decision": self.decision.model_dump(),
            "pre_validation": self.pre_validation.model_dump() if self.pre_validation else None,
            "status": self.status,
            "execution": self.execution.model_dump() if self.execution else None,
        }


class RunStore:
    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}

    def save(self, result: RunResult) -> RunRecord:
        run_id = uuid.uuid4().hex[:12]
        action = result.decision.proposed_action.action
        status = "no_action" if action in ("none",) else "pending_approval"
        record = RunRecord(
            run_id=run_id,
            scenario_id=result.scenario_id,
            mode=result.mode,
            provider=result.provider,
            trace=result.trace,
            decision=result.decision,
            pre_validation=result.pre_validation,
            status=status,
        )
        with _LOCK:
            self._runs[run_id] = record
        return record

    def get(self, run_id: str) -> Optional[RunRecord]:
        return self._runs.get(run_id)


store = RunStore()


# --- execution-time re-validation ---------------------------------------------------

def _revalidate(session: Session, sid: str, decision: Decision) -> ValidationReport:
    a = decision.proposed_action
    ctx = world.build_validation_context(session, sid, a.sku, a.node_id, a.supplier_id)
    if ctx is None:
        return ValidationReport(ok=False, violations=["missing_data_or_supplier_offer"])
    res = validate_po(int(a.qty), ctx)
    return ValidationReport(ok=res.ok, violations=res.violations,
                            warnings=res.warnings, metrics=res.metrics)


# --- post-action read-back verification ---------------------------------------------

def verify_world_invariants(session: Session, sid: str, po_id: str) -> ValidationReport:
    """Re-read the world after an action and confirm it is in an acceptable state."""
    po = orders.get_po(session, po_id)
    if po is None:
        return ValidationReport(ok=False, violations=["po_not_found_after_execution"])

    sku, node, sup = po.sku, po.node_id, po.supplier_id
    rem_budget = world.remaining_budget(session, sid, node)
    rem_storage = world.remaining_storage(session, sid, node)

    violations: list[str] = []
    warnings: list[str] = []
    if rem_budget < -1e-6:
        violations.append(f"budget_exceeded (remaining={rem_budget:.2f})")
    if rem_storage < -1e-6:
        violations.append(f"storage_exceeded (remaining={rem_storage:.1f}L)")

    # Recompute the replenishment picture now that the PO is live.
    inp = world.build_replenishment_input(session, sid, sku, node, sup)
    ctx = world.build_validation_context(session, sid, sku, node, sup)
    position = inp.on_hand + inp.incoming if inp else 0
    res = R.compute(inp) if inp else None
    cover = round(position / res.demand_rate, 1) if (res and res.demand_rate > 0) else None

    if res and res.recommended_qty > 0:
        warnings.append(f"still_short_by_{res.recommended_qty}_units_after_order")
    if ctx and cover is not None and ctx.target_max_cover_days and cover > ctx.target_max_cover_days:
        warnings.append(f"overstock_after_order (cover={cover}d)")

    return ValidationReport(
        ok=len(violations) == 0,
        violations=violations,
        warnings=warnings,
        metrics={
            "po_id": po_id,
            "po_qty": po.qty_ordered,
            "remaining_budget": rem_budget,
            "remaining_storage_l": rem_storage,
            "inventory_position": position,
            "projected_cover_days": cover,
        },
    )


# --- execute an approved decision ---------------------------------------------------

def execute_decision(session: Session, record: RunRecord) -> ExecutionResult:
    decision = record.decision
    action = decision.proposed_action

    if action.action == "none":
        record.status = "no_action"
        result = ExecutionResult(executed=False, action=action, outcome_ok=True,
                                 message="No action required; current plan is adequate.")
        record.execution = result
        return result

    if action.action == "escalate":
        record.status = "executed"
        result = ExecutionResult(executed=True, action=action, outcome_ok=True,
                                 message="Escalated to a human buyer for a decision.")
        record.execution = result
        return result

    if action.action in ("create_po", "modify_po"):
        # Guard: re-validate against the CURRENT world (it may have changed since proposal).
        recheck = _revalidate(session, sid=record.scenario_id, decision=decision)
        if not recheck.ok:
            record.status = "blocked"
            result = ExecutionResult(
                executed=False, action=action, outcome_ok=False, post_validation=recheck,
                message="Execution blocked: the proposed order violates constraints at "
                        "execution time. Re-run the agent against the current state.",
            )
            record.execution = result
            return result

        if action.action == "create_po":
            po = orders.create_po(session, record.scenario_id, action.sku, action.node_id,
                                  action.supplier_id, int(action.qty), created_by="agent",
                                  notes=action.rationale)
        else:
            po = orders.modify_po(session, action.po_id, int(action.qty))

        post = verify_world_invariants(session, record.scenario_id, po.id)
        record.status = "executed"
        result = ExecutionResult(
            executed=True, action=action, po_id=po.id, post_validation=post,
            outcome_ok=post.ok,
            message=(f"{action.action} succeeded: {po.id}. Post-action verification "
                     f"{'passed' if post.ok else 'FAILED'}."),
        )
        record.execution = result
        return result

    if action.action == "cancel_po":
        orders.cancel_po(session, action.po_id)
        record.status = "executed"
        result = ExecutionResult(executed=True, action=action, po_id=action.po_id,
                                 outcome_ok=True, message=f"Cancelled {action.po_id}.")
        record.execution = result
        return result

    record.status = "blocked"
    result = ExecutionResult(executed=False, action=action, outcome_ok=False,
                             message=f"Unknown action {action.action}.")
    record.execution = result
    return result
