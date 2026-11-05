"""Agent tool catalog + dispatcher.

Tools are declared once in a provider-agnostic JSON-schema form; the LLM clients
adapt them to each SDK. The agent is given READ + ANALYSIS tools plus one
terminal tool, ``submit_decision``. It is deliberately NOT given write tools:
executing a purchase order happens only after human approval, outside the loop.
"""
from __future__ import annotations

from typing import Any

from sqlmodel import Session

from ..domain import world
from ..engine import replenishment as R
from ..engine.validation import validate_po

# Name of the terminal tool the agent must call to finalize its decision.
SUBMIT_DECISION = "submit_decision"


def _proposed_action_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create_po", "modify_po", "cancel_po", "escalate", "none"],
                "description": "The action to take if approved.",
            },
            "sku": {"type": "string"},
            "node_id": {"type": "string"},
            "supplier_id": {"type": "string"},
            "qty": {"type": "integer", "description": "Order quantity for create_po/modify_po."},
            "po_id": {"type": "string", "description": "Target PO id for modify_po/cancel_po."},
            "rationale": {"type": "string"},
        },
        "required": ["action", "rationale"],
    }


# Provider-agnostic tool definitions.
TOOLS: list[dict] = [
    {
        "name": "get_product",
        "description": "Get product master data: unit cost, storage volume per unit, shelf life.",
        "input_schema": {
            "type": "object",
            "properties": {"sku": {"type": "string"}},
            "required": ["sku"],
        },
    },
    {
        "name": "get_inventory",
        "description": "Get current on-hand inventory for a SKU at a node.",
        "input_schema": {
            "type": "object",
            "properties": {"sku": {"type": "string"}, "node_id": {"type": "string"}},
            "required": ["sku", "node_id"],
        },
    },
    {
        "name": "get_demand",
        "description": "Get the demand forecast, review period, safety-stock policy and recent "
                       "actual daily sales for a SKU at a node.",
        "input_schema": {
            "type": "object",
            "properties": {"sku": {"type": "string"}, "node_id": {"type": "string"}},
            "required": ["sku", "node_id"],
        },
    },
    {
        "name": "get_open_orders",
        "description": "List active (open/partial) purchase orders for a SKU at a node, with the "
                       "outstanding quantity still expected to arrive.",
        "input_schema": {
            "type": "object",
            "properties": {"sku": {"type": "string"}, "node_id": {"type": "string"}},
            "required": ["sku", "node_id"],
        },
    },
    {
        "name": "get_suppliers",
        "description": "List suppliers offering a SKU with price, lead time, MOQ, pack size and "
                       "reliability.",
        "input_schema": {
            "type": "object",
            "properties": {"sku": {"type": "string"}},
            "required": ["sku"],
        },
    },
    {
        "name": "get_budget",
        "description": "Get the purchasing budget for a node: total, committed and remaining.",
        "input_schema": {
            "type": "object",
            "properties": {"node_id": {"type": "string"}},
            "required": ["node_id"],
        },
    },
    {
        "name": "get_storage",
        "description": "Get storage capacity and remaining free space (litres) for a node.",
        "input_schema": {
            "type": "object",
            "properties": {"node_id": {"type": "string"}},
            "required": ["node_id"],
        },
    },
    {
        "name": "compute_replenishment",
        "description": "Compute the recommended order quantity using the order-up-to policy, "
                       "accounting for on-hand, incoming POs, demand, MOQ/pack rounding, and "
                       "clamping by budget and storage. Returns the ideal and the "
                       "constraint-feasible quantity with a full breakdown. Use this instead of "
                       "doing arithmetic yourself.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sku": {"type": "string"},
                "node_id": {"type": "string"},
                "supplier_id": {"type": "string",
                                "description": "Optional; defaults to the cheapest supplier."},
            },
            "required": ["sku", "node_id"],
        },
    },
    {
        "name": "validate_purchase_order",
        "description": "Validate a proposed order quantity against hard constraints (budget, "
                       "storage, MOQ, pack size, supplier offer) and soft warnings (overstock, "
                       "spoilage, low reliability). Call this before proposing any order.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sku": {"type": "string"},
                "node_id": {"type": "string"},
                "supplier_id": {"type": "string"},
                "qty": {"type": "integer"},
            },
            "required": ["sku", "node_id", "supplier_id", "qty"],
        },
    },
    {
        "name": SUBMIT_DECISION,
        "description": "Finalize your decision. Call this exactly once, at the end, after you have "
                       "gathered evidence and validated any proposed order.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["accept", "modify", "reject", "investigate", "escalate"],
                },
                "summary": {"type": "string", "description": "One-line disposition."},
                "reasoning": {"type": "string",
                              "description": "The important factors behind the decision."},
                "key_factors": {"type": "array", "items": {"type": "string"}},
                "proposed_action": _proposed_action_schema(),
                "confidence": {"type": "number"},
            },
            "required": ["category", "summary", "reasoning", "proposed_action"],
        },
    },
]


def tool_names() -> list[str]:
    return [t["name"] for t in TOOLS]


# --- dispatcher ---------------------------------------------------------------------

def execute_tool(session: Session, sid: str, name: str, args: dict[str, Any]) -> dict:
    """Run a read/analysis tool and return a JSON-serializable result."""
    if name == "get_product":
        p = world.get_product(session, sid, args["sku"])
        return p.model_dump() if p else {"error": "product_not_found"}

    if name == "get_inventory":
        return {
            "sku": args["sku"],
            "node_id": args["node_id"],
            "on_hand": world.get_on_hand(session, sid, args["sku"], args["node_id"]),
        }

    if name == "get_demand":
        f = world.get_forecast(session, sid, args["sku"], args["node_id"])
        if not f:
            return {"error": "forecast_not_found"}
        return {
            **f.model_dump(),
            "recent_daily": world.recent_daily_avg(session, sid, args["sku"], args["node_id"]),
        }

    if name == "get_open_orders":
        pos = world.active_pos(session, sid, args["sku"], args["node_id"])
        return {
            "orders": [
                {
                    "id": po.id, "supplier_id": po.supplier_id, "qty_ordered": po.qty_ordered,
                    "qty_confirmed": po.qty_confirmed, "qty_received": po.qty_received,
                    "status": po.status, "outstanding": world.po_outstanding(po),
                    "expected_lead_days": po.expected_lead_days,
                }
                for po in pos
            ],
            "total_incoming": world.incoming_qty(session, sid, args["sku"], args["node_id"]),
        }

    if name == "get_suppliers":
        offers = world.get_offers(session, sid, args["sku"])
        return {
            "suppliers": [
                {
                    "supplier_id": o.supplier_id,
                    "name": world.supplier_name(session, sid, o.supplier_id),
                    "unit_price": o.unit_price, "lead_time_days": o.lead_time_days,
                    "moq": o.moq, "pack_size": o.pack_size, "reliability": o.reliability,
                }
                for o in offers
            ]
        }

    if name == "get_budget":
        b = world.get_budget(session, sid, args["node_id"])
        if not b:
            return {"error": "budget_not_found"}
        return {
            "node_id": b.node_id, "period": b.period, "amount": b.amount,
            "committed": b.committed, "remaining": round(b.amount - b.committed, 2),
        }

    if name == "get_storage":
        node = world.get_node(session, sid, args["node_id"])
        if not node:
            return {"error": "node_not_found"}
        return {
            "node_id": node.id, "capacity_l": node.storage_capacity_l,
            "occupied_l": world.node_occupied_volume(session, sid, node.id),
            "remaining_l": world.remaining_storage(session, sid, node.id),
        }

    if name == "compute_replenishment":
        inp = world.build_replenishment_input(
            session, sid, args["sku"], args["node_id"], args.get("supplier_id")
        )
        if inp is None:
            return {"error": "insufficient_data_for_replenishment"}
        res = R.compute(inp)
        return res.to_dict()

    if name == "validate_purchase_order":
        ctx = world.build_validation_context(
            session, sid, args["sku"], args["node_id"], args["supplier_id"]
        )
        if ctx is None:
            # Supplier likely does not offer this SKU — report it as a hard failure.
            return {
                "ok": False,
                "violations": ["supplier_does_not_offer_sku_or_missing_data"],
                "warnings": [], "metrics": {},
            }
        return validate_po(int(args["qty"]), ctx).to_dict()

    return {"error": f"unknown_tool:{name}"}
