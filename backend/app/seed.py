"""Seed data: four self-contained purchasing worlds, one per assignment scenario.

Each world's numbers are deliberately chosen so the *correct* decision is
well-defined and derivable from the deterministic oracle — and, importantly,
is NOT simply "do what the system recommended". See README / docs for the
expected outcome of each.
"""
from __future__ import annotations

from sqlmodel import Session, select, delete

from .db import engine, init_db
from .models import (
    Budget,
    DemandForecast,
    Inventory,
    Node,
    Product,
    PurchaseOrder,
    SalesHistory,
    Scenario,
    Supplier,
    SupplierOffer,
    PO_OPEN,
)

PERIOD = "2026-11"

# All ORM table classes, for wholesale reset.
_ALL_TABLES = [
    Scenario, Product, Node, Supplier, SupplierOffer, Inventory,
    DemandForecast, SalesHistory, PurchaseOrder, Budget,
]


def _sales(scenario: str, sku: str, node: str, daily: int, days: int) -> list[SalesHistory]:
    return [
        SalesHistory(scenario_id=scenario, sku=sku, node_id=node, day_offset=d, units_sold=daily)
        for d in range(1, days + 1)
    ]


def _rows_for_scenario(sid: str) -> list:
    """Return the full list of ORM rows that make up one scenario world."""
    if sid == "S1":
        return [
            Scenario(
                id="S1",
                title="Purchase Recommendation Review",
                situation=(
                    "The purchasing system recommends buying 800 units of OATMILK-1L for "
                    "fulfillment node BOG-1. Review whether that recommendation should be "
                    "accepted, modified, rejected or investigated, and explain why."
                ),
                recommended_sku="OATMILK-1L", recommended_node="BOG-1", recommended_qty=800,
            ),
            Product(sku="OATMILK-1L", scenario_id="S1", name="Oat Milk 1L",
                    unit_cost=1.5, unit_volume_l=1.0, shelf_life_days=30),
            Node(id="BOG-1", scenario_id="S1", name="Bogota FC 1",
                 storage_capacity_l=900, storage_used_l=0),
            Supplier(id="SUP-A1", scenario_id="S1", name="FreshFarm Dairy"),
            SupplierOffer(scenario_id="S1", supplier_id="SUP-A1", sku="OATMILK-1L",
                          unit_price=1.5, lead_time_days=7, moq=100, pack_size=50, reliability=0.95),
            Inventory(scenario_id="S1", sku="OATMILK-1L", node_id="BOG-1", on_hand=300),
            DemandForecast(scenario_id="S1", sku="OATMILK-1L", node_id="BOG-1",
                           forecast_daily=40, review_period_days=7, safety_stock_days=7),
            *_sales("S1", "OATMILK-1L", "BOG-1", daily=40, days=7),
            PurchaseOrder(id="PO-1001", scenario_id="S1", sku="OATMILK-1L", node_id="BOG-1",
                          supplier_id="SUP-A1", qty_ordered=400, unit_price=1.5,
                          status=PO_OPEN, expected_lead_days=7, created_by="system",
                          notes="Existing open replenishment order."),
            Budget(scenario_id="S1", node_id="BOG-1", period=PERIOD, amount=2000, committed=1000),
        ]

    if sid == "S2":
        return [
            Scenario(
                id="S2",
                title="Supplier Cannot Fulfil the Purchase",
                situation=(
                    "Open purchase order PO-2001 ordered 500 units of SPARKWTR-500 for node "
                    "SAO-1 from supplier AquaPrime. First review whether current inventory plus "
                    "this incoming order covers expected demand. Supplier fulfillment can change: "
                    "if the supplier later reports it can only supply part of the order, the plan "
                    "must be reconciled (use the control to report a shortfall)."
                ),
                recommended_sku="SPARKWTR-500", recommended_node="SAO-1",
                recommended_qty=500, focus_po_id="PO-2001",
            ),
            Product(sku="SPARKWTR-500", scenario_id="S2", name="Sparkling Water 500ml",
                    unit_cost=0.8, unit_volume_l=0.5, shelf_life_days=120),
            Node(id="SAO-1", scenario_id="S2", name="Sao Paulo FC 1",
                 storage_capacity_l=2000, storage_used_l=0),
            Supplier(id="SUP-A2", scenario_id="S2", name="AquaPrime"),
            Supplier(id="SUP-B2", scenario_id="S2", name="ClearSpring (alternate)"),
            SupplierOffer(scenario_id="S2", supplier_id="SUP-A2", sku="SPARKWTR-500",
                          unit_price=0.80, lead_time_days=5, moq=50, pack_size=50, reliability=0.90),
            SupplierOffer(scenario_id="S2", supplier_id="SUP-B2", sku="SPARKWTR-500",
                          unit_price=0.95, lead_time_days=9, moq=100, pack_size=50, reliability=0.85),
            Inventory(scenario_id="S2", sku="SPARKWTR-500", node_id="SAO-1", on_hand=150),
            DemandForecast(scenario_id="S2", sku="SPARKWTR-500", node_id="SAO-1",
                           forecast_daily=30, review_period_days=7, safety_stock_days=5),
            *_sales("S2", "SPARKWTR-500", "SAO-1", daily=30, days=7),
            PurchaseOrder(id="PO-2001", scenario_id="S2", sku="SPARKWTR-500", node_id="SAO-1",
                          supplier_id="SUP-A2", qty_ordered=500, unit_price=0.80,
                          status=PO_OPEN, expected_lead_days=5, created_by="system",
                          notes="Original order; supplier fulfillment at risk."),
            Budget(scenario_id="S2", node_id="SAO-1", period=PERIOD, amount=1000, committed=400),
        ]

    if sid == "S3":
        return [
            Scenario(
                id="S3",
                title="Demand / Forecast Has Changed",
                situation=(
                    "ENERGYBAR at node MEX-1 was forecast at normal demand, but recent daily "
                    "sales have risen sharply. There is an open order PO-3001 for 300 units. "
                    "Investigate whether the current inventory plus incoming quantity still "
                    "covers expected demand, and whether the purchasing plan must change."
                ),
                recommended_sku="ENERGYBAR", recommended_node="MEX-1",
                recommended_qty=None, focus_po_id="PO-3001",
            ),
            Product(sku="ENERGYBAR", scenario_id="S3", name="Energy Bar 60g",
                    unit_cost=1.0, unit_volume_l=0.3, shelf_life_days=180),
            Node(id="MEX-1", scenario_id="S3", name="Mexico City FC 1",
                 storage_capacity_l=2000, storage_used_l=0),
            Supplier(id="SUP-A3", scenario_id="S3", name="SnackCo"),
            SupplierOffer(scenario_id="S3", supplier_id="SUP-A3", sku="ENERGYBAR",
                          unit_price=1.0, lead_time_days=6, moq=100, pack_size=100, reliability=0.92),
            Inventory(scenario_id="S3", sku="ENERGYBAR", node_id="MEX-1", on_hand=200),
            DemandForecast(scenario_id="S3", sku="ENERGYBAR", node_id="MEX-1",
                           forecast_daily=50, review_period_days=7, safety_stock_days=5),
            # Recent actuals ~3x the forecast — the demand spike.
            *_sales("S3", "ENERGYBAR", "MEX-1", daily=150, days=10),
            PurchaseOrder(id="PO-3001", scenario_id="S3", sku="ENERGYBAR", node_id="MEX-1",
                          supplier_id="SUP-A3", qty_ordered=300, unit_price=1.0,
                          status=PO_OPEN, expected_lead_days=6, created_by="system",
                          notes="Order placed under the old (normal) forecast."),
            Budget(scenario_id="S3", node_id="MEX-1", period=PERIOD, amount=3000, committed=300),
        ]

    if sid == "S4":
        return [
            Scenario(
                id="S4",
                title="Purchasing Constraint",
                situation=(
                    "Inventory of PREMIUMCOFFEE at node BOG-2 is low and replenishment is "
                    "needed, but purchasing constraints may prevent ordering the full required "
                    "quantity. Determine an appropriate course of action rather than blindly "
                    "ordering the computed amount."
                ),
                recommended_sku="PREMIUMCOFFEE", recommended_node="BOG-2",
                recommended_qty=None,
            ),
            Product(sku="PREMIUMCOFFEE", scenario_id="S4", name="Premium Coffee 1kg",
                    unit_cost=12.0, unit_volume_l=2.0, shelf_life_days=365),
            Node(id="BOG-2", scenario_id="S4", name="Bogota FC 2",
                 storage_capacity_l=600, storage_used_l=0),
            Supplier(id="SUP-A4", scenario_id="S4", name="BeanMaster"),
            SupplierOffer(scenario_id="S4", supplier_id="SUP-A4", sku="PREMIUMCOFFEE",
                          unit_price=12.0, lead_time_days=10, moq=50, pack_size=50, reliability=0.90),
            Inventory(scenario_id="S4", sku="PREMIUMCOFFEE", node_id="BOG-2", on_hand=50),
            DemandForecast(scenario_id="S4", sku="PREMIUMCOFFEE", node_id="BOG-2",
                           forecast_daily=20, review_period_days=7, safety_stock_days=7),
            *_sales("S4", "PREMIUMCOFFEE", "BOG-2", daily=20, days=7),
            Budget(scenario_id="S4", node_id="BOG-2", period=PERIOD, amount=2400, committed=0),
        ]

    raise ValueError(f"Unknown scenario {sid!r}")


ALL_SCENARIOS = ["S1", "S2", "S3", "S4"]


def reset_scenario(session: Session, sid: str) -> None:
    """Wipe and re-create a single scenario world (used by the API reset endpoint)."""
    for table in _ALL_TABLES:
        # The Scenario table keys on `id`; every other table carries `scenario_id`.
        key = table.id if table is Scenario else table.scenario_id
        session.exec(delete(table).where(key == sid))
    for row in _rows_for_scenario(sid):
        session.add(row)
    session.commit()


def seed_all(reset: bool = True) -> None:
    """(Re)create tables and populate all scenario worlds."""
    init_db()
    with Session(engine) as session:
        if reset:
            for table in _ALL_TABLES:
                session.exec(delete(table))
            session.commit()
        for sid in ALL_SCENARIOS:
            for row in _rows_for_scenario(sid):
                session.add(row)
        session.commit()


def ensure_seeded() -> None:
    """Seed only if the Scenario table is empty (safe on app startup)."""
    init_db()
    with Session(engine) as session:
        existing = session.exec(select(Scenario)).first()
    if existing is None:
        seed_all(reset=False)


if __name__ == "__main__":
    seed_all(reset=True)
    print("Seeded scenarios:", ", ".join(ALL_SCENARIOS))
