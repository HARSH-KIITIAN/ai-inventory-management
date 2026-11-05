"""Evaluation fixtures: the expected behaviour for each scenario.

Each case encodes what a *correct* run looks like. Quantities are NOT hard-coded
here — the harness recomputes the ground-truth quantity from the deterministic
oracle and compares the agent against it, so the fixtures stay valid even if the
seed numbers change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EvalCase:
    id: str
    sid: str
    mode: str = "review"
    # Optional supplier-confirmation pre-step: (po_id, confirmed_qty).
    pre_confirm: Optional[tuple[str, int]] = None
    expected_categories: set[str] = field(default_factory=set)
    expected_action: set[str] = field(default_factory=set)
    # The irreducible facts a buyer needs: demand, on-hand, what's on order, and
    # the replenishment computation (which itself folds in supplier terms, budget
    # and storage and reports the binding constraints). Explicitly fetching
    # budget/storage/suppliers is good practice but their substance is captured
    # by compute_replenishment, so we don't require those specific tool calls.
    required_tools: set[str] = field(default_factory=lambda: {
        "get_demand", "get_inventory", "get_open_orders", "compute_replenishment",
    })
    check_qty_vs_oracle: bool = True
    expect_validation_event: bool = True   # agent validated its proposed order
    execute_and_verify: bool = True        # approve + execute, then check read-back
    # Special scripted case: shrink budget after proposal to force a blocked execution.
    sabotage_budget_to: Optional[float] = None
    note: str = ""


CASES: list[EvalCase] = [
    EvalCase(
        id="S1-review-overrecommendation",
        sid="S1", mode="review",
        expected_categories={"modify"},
        expected_action={"create_po"},
        note="System recommends 800; correct is to modify down to the oracle quantity.",
    ),
    EvalCase(
        id="S2-review-covered",
        sid="S2", mode="review",
        # "accept" (keep the current plan) and "reject" (reject buying more) are
        # semantically equivalent here: both correctly result in NO new order.
        # We assert on that outcome (action == none) rather than the wording.
        expected_categories={"accept", "reject"},
        expected_action={"none"},
        check_qty_vs_oracle=False,
        expect_validation_event=False,
        execute_and_verify=True,       # no_action path still "succeeds" (outcome_ok)
        note="With the full 500-unit PO, demand is covered; no new order.",
    ),
    EvalCase(
        id="S2-reconcile-partial-fulfilment",
        sid="S2", mode="reconcile",
        pre_confirm=("PO-2001", 250),
        expected_categories={"modify"},
        expected_action={"create_po"},
        note="Supplier confirms only 250/500; source the shortfall from the alternate supplier.",
    ),
    EvalCase(
        id="S3-demand-spike",
        sid="S3", mode="review",
        expected_categories={"modify"},
        # Adding the needed units via a NEW PO (create_po) or by raising the
        # existing open PO (modify_po) are both valid ways to cover the spike.
        expected_action={"create_po", "modify_po"},
        note="Recent sales ~3x forecast; the existing plan no longer covers demand -> buy more.",
    ),
    EvalCase(
        id="S4-constraint-blocks-full-order",
        sid="S4", mode="review",
        expected_categories={"escalate", "modify"},
        expected_action={"create_po", "escalate"},
        note="Budget caps the order below the ideal; order the feasible amount and escalate.",
    ),
    EvalCase(
        id="S3-blocked-execution-when-budget-shrinks",
        sid="S3", mode="review",
        expected_categories={"modify"},
        expected_action={"create_po", "modify_po"},
        check_qty_vs_oracle=True,
        execute_and_verify=False,      # handled specially: expect BLOCKED, not executed
        sabotage_budget_to=100.0,
        note="After the agent proposes a valid order, the budget collapses; execution must be "
             "blocked by execution-time re-validation (feedback loop: outcome != expected).",
    ),
]
