"""Write-side domain service: the purchase-order actions the agent can take.

Each mutation keeps the budget commitment consistent so that the post-action
read-back (the feedback loop) sees a truthful world state. Storage is tracked
dynamically from active POs (see world.node_occupied_volume), so it needs no
explicit update here.
"""
from __future__ import annotations

from typing import Optional

from sqlmodel import Session, select

from ..models import (
    Budget,
    PO_CANCELLED,
    PO_OPEN,
    PO_PARTIAL,
    PO_RECEIVED,
    PurchaseOrder,
)
from . import world


class OrderError(ValueError):
    """Raised when a requested PO action is not possible."""


def _adjust_committed(session: Session, sid: str, node_id: str, delta: float) -> None:
    budget = world.get_budget(session, sid, node_id)
    if budget is None:
        return
    budget.committed = max(0.0, round(budget.committed + delta, 2))
    session.add(budget)


def _next_po_id(session: Session) -> str:
    """Agent-created POs get ids from the PO-9xxx band to distinguish from seeded ones."""
    ids = session.exec(select(PurchaseOrder.id)).all()
    max_n = 9000
    for pid in ids:
        if isinstance(pid, str) and pid.startswith("PO-"):
            try:
                n = int(pid.split("-", 1)[1])
            except ValueError:
                continue
            if n >= 9000:
                max_n = max(max_n, n)
    return f"PO-{max_n + 1}"


def create_po(
    session: Session,
    sid: str,
    sku: str,
    node_id: str,
    supplier_id: str,
    qty: int,
    created_by: str = "agent",
    notes: str = "",
) -> PurchaseOrder:
    offer = world.get_offer(session, sid, sku, supplier_id)
    if offer is None:
        raise OrderError(f"Supplier {supplier_id} does not offer {sku}")
    if qty <= 0:
        raise OrderError("Quantity must be positive")

    po = PurchaseOrder(
        id=_next_po_id(session),
        scenario_id=sid,
        sku=sku,
        node_id=node_id,
        supplier_id=supplier_id,
        qty_ordered=qty,
        unit_price=offer.unit_price,
        status=PO_OPEN,
        expected_lead_days=offer.lead_time_days,
        created_by=created_by,
        notes=notes,
    )
    session.add(po)
    _adjust_committed(session, sid, node_id, qty * offer.unit_price)
    session.commit()
    session.refresh(po)
    return po


def modify_po(session: Session, po_id: str, new_qty: int) -> PurchaseOrder:
    po = session.get(PurchaseOrder, po_id)
    if po is None:
        raise OrderError(f"PO {po_id} not found")
    if po.status in (PO_RECEIVED, PO_CANCELLED):
        raise OrderError(f"PO {po_id} is {po.status} and cannot be modified")
    if new_qty <= 0:
        raise OrderError("Quantity must be positive")

    old_value = po.qty_ordered * po.unit_price
    po.qty_ordered = new_qty
    new_value = new_qty * po.unit_price
    _adjust_committed(session, po.scenario_id, po.node_id, new_value - old_value)
    session.add(po)
    session.commit()
    session.refresh(po)
    return po


def cancel_po(session: Session, po_id: str) -> PurchaseOrder:
    po = session.get(PurchaseOrder, po_id)
    if po is None:
        raise OrderError(f"PO {po_id} not found")
    freed = world.po_outstanding(po) * po.unit_price
    po.status = PO_CANCELLED
    _adjust_committed(session, po.scenario_id, po.node_id, -freed)
    session.add(po)
    session.commit()
    session.refresh(po)
    return po


def confirm_po(session: Session, po_id: str, confirmed_qty: int) -> PurchaseOrder:
    """Record a supplier's fulfillment confirmation (Scenario 2 trigger).

    If the supplier confirms fewer units than ordered, the PO becomes ``partial``
    and the unfulfilled budget commitment is freed up for re-sourcing.
    """
    po = session.get(PurchaseOrder, po_id)
    if po is None:
        raise OrderError(f"PO {po_id} not found")
    if confirmed_qty < 0:
        raise OrderError("Confirmed quantity cannot be negative")

    shortfall_units = max(0, po.qty_ordered - confirmed_qty)
    po.qty_confirmed = confirmed_qty
    po.status = PO_PARTIAL if confirmed_qty < po.qty_ordered else PO_OPEN
    _adjust_committed(session, po.scenario_id, po.node_id, -shortfall_units * po.unit_price)
    session.add(po)
    session.commit()
    session.refresh(po)
    return po


def get_po(session: Session, po_id: str) -> Optional[PurchaseOrder]:
    return session.get(PurchaseOrder, po_id)
