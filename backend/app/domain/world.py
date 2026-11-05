"""Read-side domain services + builders that assemble engine inputs from the DB.

This is the single place that knows how to turn raw rows into the derived facts
the agent and engine reason about (inventory position, incoming quantity,
remaining budget, free storage, effective demand, target coverage window).
Keeping it in one module means the agent tools, the validator and the eval
harness all compute these the same way.
"""
from __future__ import annotations

from typing import Optional

from sqlmodel import Session, select

from ..engine.replenishment import ReplenishmentInput, effective_daily_demand
from ..engine.validation import POValidationContext
from ..models import (
    Budget,
    DemandForecast,
    Inventory,
    Node,
    PO_OPEN,
    PO_PARTIAL,
    Product,
    PurchaseOrder,
    SalesHistory,
    Scenario,
    Supplier,
    SupplierOffer,
)

ACTIVE_PO_STATUSES = (PO_OPEN, PO_PARTIAL)
# How much past the order-up-to horizon (in days of cover) counts as overstock.
OVERSTOCK_SLACK = 1.3


# --- basic lookups ------------------------------------------------------------------

def get_scenario(session: Session, sid: str) -> Optional[Scenario]:
    return session.get(Scenario, sid)


def get_product(session: Session, sid: str, sku: str) -> Optional[Product]:
    return session.exec(
        select(Product).where(Product.scenario_id == sid, Product.sku == sku)
    ).first()


def get_node(session: Session, sid: str, node_id: str) -> Optional[Node]:
    return session.exec(
        select(Node).where(Node.scenario_id == sid, Node.id == node_id)
    ).first()


def get_forecast(session: Session, sid: str, sku: str, node_id: str) -> Optional[DemandForecast]:
    return session.exec(
        select(DemandForecast).where(
            DemandForecast.scenario_id == sid,
            DemandForecast.sku == sku,
            DemandForecast.node_id == node_id,
        )
    ).first()


def get_on_hand(session: Session, sid: str, sku: str, node_id: str) -> int:
    inv = session.exec(
        select(Inventory).where(
            Inventory.scenario_id == sid,
            Inventory.sku == sku,
            Inventory.node_id == node_id,
        )
    ).first()
    return inv.on_hand if inv else 0


def recent_daily_avg(session: Session, sid: str, sku: str, node_id: str) -> Optional[float]:
    rows = session.exec(
        select(SalesHistory).where(
            SalesHistory.scenario_id == sid,
            SalesHistory.sku == sku,
            SalesHistory.node_id == node_id,
        )
    ).all()
    if not rows:
        return None
    return round(sum(r.units_sold for r in rows) / len(rows), 3)


def get_offers(session: Session, sid: str, sku: str) -> list[SupplierOffer]:
    return session.exec(
        select(SupplierOffer).where(
            SupplierOffer.scenario_id == sid, SupplierOffer.sku == sku
        )
    ).all()


def get_offer(session: Session, sid: str, sku: str, supplier_id: str) -> Optional[SupplierOffer]:
    return session.exec(
        select(SupplierOffer).where(
            SupplierOffer.scenario_id == sid,
            SupplierOffer.sku == sku,
            SupplierOffer.supplier_id == supplier_id,
        )
    ).first()


def supplier_name(session: Session, sid: str, supplier_id: str) -> str:
    sup = session.exec(
        select(Supplier).where(Supplier.scenario_id == sid, Supplier.id == supplier_id)
    ).first()
    return sup.name if sup else supplier_id


def get_budget(session: Session, sid: str, node_id: str) -> Optional[Budget]:
    return session.exec(
        select(Budget).where(Budget.scenario_id == sid, Budget.node_id == node_id)
    ).first()


def remaining_budget(session: Session, sid: str, node_id: str) -> float:
    b = get_budget(session, sid, node_id)
    return round(b.amount - b.committed, 2) if b else 0.0


# --- derived quantities -------------------------------------------------------------

def po_outstanding(po: PurchaseOrder) -> int:
    """Units still expected to arrive from a PO (confirmed-or-ordered minus received)."""
    if po.status not in ACTIVE_PO_STATUSES:
        return 0
    committed = po.qty_confirmed if po.qty_confirmed is not None else po.qty_ordered
    return max(0, committed - po.qty_received)


def active_pos(session: Session, sid: str, sku: str, node_id: str) -> list[PurchaseOrder]:
    return session.exec(
        select(PurchaseOrder).where(
            PurchaseOrder.scenario_id == sid,
            PurchaseOrder.sku == sku,
            PurchaseOrder.node_id == node_id,
            PurchaseOrder.status.in_(ACTIVE_PO_STATUSES),  # type: ignore[attr-defined]
        )
    ).all()


def incoming_qty(session: Session, sid: str, sku: str, node_id: str) -> int:
    return sum(po_outstanding(po) for po in active_pos(session, sid, sku, node_id))


def node_occupied_volume(session: Session, sid: str, node_id: str) -> float:
    """Volume currently committed at a node: baseline + on-hand + all inbound POs."""
    node = get_node(session, sid, node_id)
    baseline = node.storage_used_l if node else 0.0

    products = {
        p.sku: p
        for p in session.exec(select(Product).where(Product.scenario_id == sid)).all()
    }

    occupied = baseline
    invs = session.exec(
        select(Inventory).where(Inventory.scenario_id == sid, Inventory.node_id == node_id)
    ).all()
    for inv in invs:
        vol = products[inv.sku].unit_volume_l if inv.sku in products else 0.0
        occupied += inv.on_hand * vol

    pos = session.exec(
        select(PurchaseOrder).where(
            PurchaseOrder.scenario_id == sid,
            PurchaseOrder.node_id == node_id,
            PurchaseOrder.status.in_(ACTIVE_PO_STATUSES),  # type: ignore[attr-defined]
        )
    ).all()
    for po in pos:
        vol = products[po.sku].unit_volume_l if po.sku in products else 0.0
        occupied += po_outstanding(po) * vol

    return round(occupied, 2)


def remaining_storage(session: Session, sid: str, node_id: str) -> float:
    node = get_node(session, sid, node_id)
    if not node:
        return 0.0
    return round(node.storage_capacity_l - node_occupied_volume(session, sid, node_id), 2)


def _primary_offer(offers: list[SupplierOffer]) -> Optional[SupplierOffer]:
    """Cheapest offer is treated as the default/primary supplier."""
    return min(offers, key=lambda o: o.unit_price) if offers else None


# --- engine-input builders ----------------------------------------------------------

def build_replenishment_input(
    session: Session, sid: str, sku: str, node_id: str, supplier_id: Optional[str] = None
) -> Optional[ReplenishmentInput]:
    product = get_product(session, sid, sku)
    forecast = get_forecast(session, sid, sku, node_id)
    offers = get_offers(session, sid, sku)
    if not (product and forecast and offers):
        return None

    offer = get_offer(session, sid, sku, supplier_id) if supplier_id else _primary_offer(offers)
    if offer is None:
        return None

    return ReplenishmentInput(
        on_hand=get_on_hand(session, sid, sku, node_id),
        incoming=incoming_qty(session, sid, sku, node_id),
        forecast_daily=forecast.forecast_daily,
        lead_time_days=offer.lead_time_days,
        review_period_days=forecast.review_period_days,
        safety_stock_days=forecast.safety_stock_days,
        moq=offer.moq,
        pack_size=offer.pack_size,
        unit_price=offer.unit_price,
        unit_volume_l=product.unit_volume_l,
        remaining_budget=remaining_budget(session, sid, node_id),
        remaining_storage_l=remaining_storage(session, sid, node_id),
        recent_daily=recent_daily_avg(session, sid, sku, node_id),
        shelf_life_days=product.shelf_life_days,
    )


def build_validation_context(
    session: Session, sid: str, sku: str, node_id: str, supplier_id: str
) -> Optional[POValidationContext]:
    product = get_product(session, sid, sku)
    forecast = get_forecast(session, sid, sku, node_id)
    offer = get_offer(session, sid, sku, supplier_id)
    if not (product and forecast and offer):
        return None

    demand_rate = effective_daily_demand(
        forecast.forecast_daily, recent_daily_avg(session, sid, sku, node_id)
    )
    horizon = offer.lead_time_days + forecast.review_period_days + forecast.safety_stock_days

    return POValidationContext(
        moq=offer.moq,
        pack_size=offer.pack_size,
        unit_price=offer.unit_price,
        unit_volume_l=product.unit_volume_l,
        remaining_budget=remaining_budget(session, sid, node_id),
        remaining_storage_l=remaining_storage(session, sid, node_id),
        on_hand=get_on_hand(session, sid, sku, node_id),
        incoming=incoming_qty(session, sid, sku, node_id),
        demand_rate=demand_rate,
        supplier_offers_sku=True,
        reliability=offer.reliability,
        shelf_life_days=product.shelf_life_days,
        target_min_cover_days=offer.lead_time_days,
        target_max_cover_days=round(horizon * OVERSTOCK_SLACK, 1),
    )


# --- snapshot for the API / UI ------------------------------------------------------

def world_snapshot(session: Session, sid: str) -> dict:
    """A full, UI-friendly view of a scenario world."""
    scenario = get_scenario(session, sid)
    if not scenario:
        return {}

    products = session.exec(select(Product).where(Product.scenario_id == sid)).all()
    nodes = session.exec(select(Node).where(Node.scenario_id == sid)).all()
    suppliers = session.exec(select(Supplier).where(Supplier.scenario_id == sid)).all()
    offers = session.exec(select(SupplierOffer).where(SupplierOffer.scenario_id == sid)).all()
    inv = session.exec(select(Inventory).where(Inventory.scenario_id == sid)).all()
    forecasts = session.exec(select(DemandForecast).where(DemandForecast.scenario_id == sid)).all()
    pos = session.exec(select(PurchaseOrder).where(PurchaseOrder.scenario_id == sid)).all()
    budgets = session.exec(select(Budget).where(Budget.scenario_id == sid)).all()

    return {
        "scenario": scenario.model_dump(),
        "products": [p.model_dump() for p in products],
        "nodes": [
            {
                **n.model_dump(),
                "occupied_l": node_occupied_volume(session, sid, n.id),
                "remaining_l": remaining_storage(session, sid, n.id),
            }
            for n in nodes
        ],
        "suppliers": [s.model_dump() for s in suppliers],
        "offers": [o.model_dump() for o in offers],
        "inventory": [i.model_dump() for i in inv],
        "forecasts": [
            {**f.model_dump(), "recent_daily": recent_daily_avg(session, sid, f.sku, f.node_id)}
            for f in forecasts
        ],
        "purchase_orders": [
            {**po.model_dump(), "outstanding": po_outstanding(po)} for po in pos
        ],
        "budgets": [
            {**b.model_dump(), "remaining": round(b.amount - b.committed, 2)} for b in budgets
        ],
    }
