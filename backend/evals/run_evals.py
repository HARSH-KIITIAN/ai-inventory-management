"""Evaluation harness.

Runs the agent against each fixture and checks the assignment's criteria:
  - Decision correct?      (category + quantity vs. the oracle ground truth)
  - Necessary info?        (required read tools appear in the trace)
  - Respected constraints? (the proposed order passes hard-constraint validation)
  - Appropriate action?    (create_po / none / escalate as expected)
  - Validated result?      (agent pre-validates; system post-validates on execute)
  - Failure handled?       (a stale/invalid order is blocked at execution time)

Usage:
    python -m evals.run_evals            # offline, deterministic stub (default)
    python -m evals.run_evals --live     # use the LLM_PROVIDER configured in .env
"""
from __future__ import annotations

import os
import sys

# Use an isolated DB so evals never clobber a dev/app database. Must be set
# before importing anything under app.* (config reads it at import time).
os.environ.setdefault("DATABASE_URL", "sqlite:///./evals.db")

from sqlmodel import Session  # noqa: E402

from app.agent.agent import run_agent  # noqa: E402
from app.agent.llm import StubClient, get_client  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import engine  # noqa: E402
from app.domain import orders, world  # noqa: E402
from app.engine import replenishment as R  # noqa: E402
from app.seed import reset_scenario, seed_all  # noqa: E402
from app.service import execute_decision, store  # noqa: E402

from .scenarios import CASES, EvalCase  # noqa: E402

OK, BAD = "✓", "✗"


def oracle_ground_truth(session: Session, sid: str, mode: str):
    """Independently compute the correct supplier + quantity from the oracle."""
    scenario = world.get_scenario(session, sid)
    sku, node = scenario.recommended_sku, scenario.recommended_node
    offers = world.get_offers(session, sid, sku)

    failed = None
    if mode == "reconcile" and scenario.focus_po_id:
        for po in world.active_pos(session, sid, sku, node):
            if po.id == scenario.focus_po_id:
                failed = po.supplier_id
    candidates = [o for o in offers if o.supplier_id != failed] or offers
    chosen = min(candidates, key=lambda o: o.unit_price)

    inp = world.build_replenishment_input(session, sid, sku, node, chosen.supplier_id)
    res = R.compute(inp)
    target = res.constrained_qty if (res.feasible and res.constrained_qty > 0) else res.recommended_qty
    return chosen.supplier_id, target, res


def tools_called(trace: list[dict]) -> set[str]:
    return {e["name"] for e in trace if e.get("type") == "tool_call"}


def run_case(case: EvalCase, live: bool) -> dict:
    client = get_client(get_settings()) if live else StubClient()
    checks: dict[str, bool] = {}
    detail: list[str] = []

    with Session(engine) as session:
        reset_scenario(session, case.sid)
        if case.pre_confirm:
            orders.confirm_po(session, case.pre_confirm[0], case.pre_confirm[1])

        sup, oracle_qty, ores = oracle_ground_truth(session, case.sid, case.mode)
        result = run_agent(session, case.sid, case.mode, client=client)
        decision = result.decision
        action = decision.proposed_action

        # 1) Decision category correct.
        checks["decision"] = decision.category in case.expected_categories
        if not checks["decision"]:
            detail.append(f"category={decision.category} not in {case.expected_categories}")

        # 2) Necessary info gathered.
        called = tools_called(result.trace)
        checks["info"] = case.required_tools.issubset(called)
        if not checks["info"]:
            detail.append(f"missing tools {case.required_tools - called}")

        # 3) Appropriate action.
        checks["action"] = action.action in case.expected_action
        if not checks["action"]:
            detail.append(f"action={action.action} not in {case.expected_action}")

        # 4) Quantity matches the oracle ground truth (within a tolerance).
        if case.check_qty_vs_oracle and action.qty is not None:
            pack = ores.breakdown.get("pack_size", 1)
            tol = max(pack, round(0.15 * max(oracle_qty, 1)))
            checks["qty"] = abs(action.qty - oracle_qty) <= tol
            if not checks["qty"]:
                detail.append(f"qty={action.qty} vs oracle={oracle_qty} (tol {tol})")
        else:
            checks["qty"] = True

        # 5) Constraints respected (agent pre-validated a feasible order).
        if case.expect_validation_event:
            pv = result.pre_validation
            checks["constraints"] = pv is not None and pv.ok
            if not checks["constraints"]:
                detail.append(f"pre_validation={pv.model_dump() if pv else None}")
        else:
            checks["constraints"] = True

        # 6) Result validated / failure handled (execute the approved decision).
        record = store.save(result)
        if case.sabotage_budget_to is not None:
            # Collapse the budget AFTER the proposal to force a blocked execution.
            budget = world.get_budget(session, case.sid, action.node_id)
            budget.committed = budget.amount - case.sabotage_budget_to
            session.add(budget)
            session.commit()
            execution = execute_decision(session, record)
            checks["validated"] = (not execution.executed) and (not execution.outcome_ok) \
                and record.status == "blocked"
            if not checks["validated"]:
                detail.append(f"expected blocked, got status={record.status} "
                              f"executed={execution.executed}")
        elif case.execute_and_verify:
            execution = execute_decision(session, record)
            post = execution.post_validation
            checks["validated"] = execution.outcome_ok and (
                action.action == "none" or post is not None
            )
            if not checks["validated"]:
                detail.append(f"execution outcome_ok={execution.outcome_ok} "
                              f"post={post.model_dump() if post else None}")
        else:
            checks["validated"] = True

    return {
        "id": case.id,
        "provider": result.provider,
        "category": decision.category,
        "qty": action.qty,
        "oracle_qty": oracle_qty,
        "checks": checks,
        "passed": all(checks.values()),
        "detail": detail,
        "note": case.note,
    }


def main() -> int:
    live = "--live" in sys.argv
    seed_all(reset=True)

    print(f"\nAI Purchasing Agent — evaluation ({'LIVE: ' + get_settings().llm_provider if live else 'offline stub'})\n")
    cols = ["decision", "info", "constraints", "action", "qty", "validated"]
    header = f"{'case':<42} {'cat':<10} " + " ".join(f"{c[:5]:<5}" for c in cols) + "  result"
    print(header)
    print("-" * len(header))

    results = [run_case(c, live) for c in CASES]
    for r in results:
        marks = " ".join(f"{(OK if r['checks'][c] else BAD):<5}" for c in cols)
        status = "PASS" if r["passed"] else "FAIL"
        print(f"{r['id']:<42} {r['category']:<10} {marks}  {status}")
        if not r["passed"]:
            for d in r["detail"]:
                print(f"    - {d}")

    passed = sum(r["passed"] for r in results)
    total = len(results)
    print("-" * len(header))
    print(f"\n{passed}/{total} cases passed.\n")

    print("Legend: decision=category correct | info=gathered required data | "
          "constraints=order respects limits\n        action=right action type | "
          "qty=matches oracle ground truth | validated=result verified / failure handled\n")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
