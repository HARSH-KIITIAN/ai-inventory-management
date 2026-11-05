"""SQLModel table definitions — the mock purchasing domain.

Design notes
------------
* Every row carries a ``scenario_id`` so the single SQLite file can hold several
  independent "worlds" (one per assignment scenario) that can be reset in
  isolation. Business keys (``sku``, ``node_id`` ...) are globally unique in the
  seed data, so cross-table references stay simple while still being groupable
  by scenario.
* Monetary values are plain floats (USD). Volumes are litres. This is a demo —
  we do not model currencies, units, or decimals rigorously.
"""
from __future__ import annotations

from typing import Optional

from sqlmodel import Field, SQLModel


# --- Purchase-order lifecycle states -------------------------------------------------
PO_OPEN = "open"            # placed, not yet confirmed/received
PO_PARTIAL = "partial"      # supplier confirmed fewer units than ordered
PO_RECEIVED = "received"    # fully delivered
PO_CANCELLED = "cancelled"
PO_STATUSES = {PO_OPEN, PO_PARTIAL, PO_RECEIVED, PO_CANCELLED}


class Scenario(SQLModel, table=True):
    """A self-contained purchasing situation presented to the agent."""

    id: str = Field(primary_key=True)                 # e.g. "S1"
    title: str
    situation: str                                    # natural-language brief for the agent
    recommended_sku: Optional[str] = None             # the SKU the "system" flagged
    recommended_node: Optional[str] = None
    recommended_qty: Optional[int] = None             # the (possibly wrong) system recommendation
    focus_po_id: Optional[str] = None                 # PO under discussion (S2)


class Product(SQLModel, table=True):
    sku: str = Field(primary_key=True)
    scenario_id: str = Field(index=True)
    name: str
    unit_cost: float                                  # reference cost (USD/unit)
    unit_volume_l: float                              # storage footprint per unit (litres)
    shelf_life_days: int


class Node(SQLModel, table=True):
    """A fulfillment node / warehouse."""

    id: str = Field(primary_key=True)                 # e.g. "BOG-1"
    scenario_id: str = Field(index=True)
    name: str
    storage_capacity_l: float                         # total usable storage (litres)
    storage_used_l: float = 0.0                       # already-occupied storage (litres)


class Supplier(SQLModel, table=True):
    id: str = Field(primary_key=True)                 # e.g. "SUP-A"
    scenario_id: str = Field(index=True)
    name: str


class SupplierOffer(SQLModel, table=True):
    """What a supplier can provide for a SKU, with commercial terms."""

    id: Optional[int] = Field(default=None, primary_key=True)
    scenario_id: str = Field(index=True)
    supplier_id: str = Field(index=True)
    sku: str = Field(index=True)
    unit_price: float
    lead_time_days: int
    moq: int                                          # minimum order quantity
    pack_size: int                                    # order must be a multiple of this
    reliability: float                                # 0..1 historical fulfillment reliability


class Inventory(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    scenario_id: str = Field(index=True)
    sku: str = Field(index=True)
    node_id: str = Field(index=True)
    on_hand: int                                      # units physically available now


class DemandForecast(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    scenario_id: str = Field(index=True)
    sku: str = Field(index=True)
    node_id: str = Field(index=True)
    forecast_daily: float                             # planned/forecast daily demand (units/day)
    review_period_days: int                           # days between replenishment reviews
    safety_stock_days: int                            # desired buffer expressed in days of cover


class SalesHistory(SQLModel, table=True):
    """Recent actual daily sales, one row per day. day_offset is days ago (1 = yesterday)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    scenario_id: str = Field(index=True)
    sku: str = Field(index=True)
    node_id: str = Field(index=True)
    day_offset: int
    units_sold: int


class PurchaseOrder(SQLModel, table=True):
    id: str = Field(primary_key=True)                 # e.g. "PO-1001"
    scenario_id: str = Field(index=True)
    sku: str = Field(index=True)
    node_id: str = Field(index=True)
    supplier_id: str = Field(index=True)
    qty_ordered: int
    qty_confirmed: Optional[int] = None               # what the supplier committed to (S2)
    qty_received: int = 0
    unit_price: float
    status: str = Field(default=PO_OPEN)
    expected_lead_days: int                           # lead time at time of ordering
    created_by: str = "system"                        # "system" | "agent"
    notes: str = ""


class Budget(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    scenario_id: str = Field(index=True)
    node_id: str = Field(index=True)
    period: str                                       # e.g. "2026-11"
    amount: float                                     # total budget for the period
    committed: float                                  # already-committed spend
