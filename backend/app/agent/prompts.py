"""System prompt + initial situation message for the agent."""
from __future__ import annotations

import json

from sqlmodel import Session

from ..domain import world

SYSTEM_PROMPT = """\
You are an AI Purchasing Agent for a quick-commerce company. You assist a human \
buyer by investigating purchasing situations and recommending a decision.

Your operating principles:
1. DO NOT trust the system's recommended quantity. It may be wrong. Verify it \
against evidence.
2. Investigate before deciding. Gather the relevant facts with the read tools: \
inventory, demand (forecast AND recent actual sales), open purchase orders, \
supplier terms, budget, and storage.
3. Never do inventory arithmetic yourself. Call `compute_replenishment` to get \
the correct order quantity; it already accounts for on-hand, incoming orders, \
demand, MOQ/pack rounding, and budget + storage limits.
4. Before proposing any order, call `validate_purchase_order` to confirm it \
respects all hard constraints. If a proposed order has hard violations, you must \
reduce it, choose a different supplier, or escalate — never propose an invalid order.
5. Respect constraints. If constraints prevent the ideal order, propose the best \
feasible order and escalate the shortfall rather than ignoring the limit.
6. You PROPOSE actions; a human approves them. Put the concrete action in \
`proposed_action`.

Decision categories:
- accept: the system recommendation is sound; act on it as-is.
- modify: a purchase is warranted but at a different quantity/supplier than \
recommended (includes reducing to a feasible amount under constraints).
- reject: no purchase is warranted (e.g. already sufficiently covered).
- investigate: not enough information to decide; state what is missing.
- escalate: a human must intervene (e.g. constraints block the needed purchase, \
or supplier reliability/shortfall risk is material).

When finished, call `submit_decision` exactly once with a clear reasoning that \
names the important factors (numbers matter). Be concise and specific."""


def build_initial_message(session: Session, sid: str, mode: str) -> tuple[str, dict]:
    """Return (user_message_text, context_dict).

    The text embeds a machine-readable ```json context block. Real LLMs read it
    as grounding; the deterministic StubClient parses it to drive its script.
    """
    scenario = world.get_scenario(session, sid)
    sku = scenario.recommended_sku
    node_id = scenario.recommended_node
    offers = world.get_offers(session, sid, sku) if sku else []
    supplier_ids = [o.supplier_id for o in offers]

    failed_supplier_id = None
    if mode == "reconcile" and scenario.focus_po_id:
        po = world.active_pos(session, sid, sku, node_id)
        for p in po:
            if p.id == scenario.focus_po_id:
                failed_supplier_id = p.supplier_id

    context = {
        "scenario_id": sid,
        "mode": mode,
        "sku": sku,
        "node_id": node_id,
        "supplier_ids": supplier_ids,
        "recommended_qty": scenario.recommended_qty,
        "focus_po_id": scenario.focus_po_id,
        "failed_supplier_id": failed_supplier_id,
    }

    if mode == "reconcile":
        intro = (
            f"RECONCILE: Supplier fulfillment for purchase order {scenario.focus_po_id} has "
            f"changed. Re-investigate {sku} at {node_id}, determine whether the purchasing plan "
            f"still covers demand, and if not, decide how to cover the shortfall (e.g. source the "
            f"remainder from another supplier) or escalate."
        )
    else:
        intro = f"SITUATION: {scenario.situation}"

    text = (
        f"{intro}\n\n"
        f"Use the tools to investigate, then call submit_decision.\n\n"
        f"```json\n{json.dumps(context)}\n```"
    )
    return text, context
