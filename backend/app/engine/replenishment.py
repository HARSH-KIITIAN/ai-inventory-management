"""Deterministic replenishment oracle.

This module contains NO LLM calls and NO I/O. It is the single source of truth
for "how many units should we order?". Two consumers rely on it:

1. The agent, which calls it as a tool so it never has to do arithmetic itself.
2. The evaluation harness, which uses it as ground truth to judge the agent.

The model is a classic order-up-to (base-stock) policy:

    order_up_to_level = demand_rate * (lead_time + review_period) + safety_stock
    raw_need          = order_up_to_level - (on_hand + incoming)

The raw need is then rounded up to the supplier's MOQ / pack-size grid, and
finally clamped by the budget and storage constraints.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Optional

# Weight given to recent actual sales vs. the planned forecast when both exist.
# Favouring actuals makes the policy responsive to demand shifts (Scenario 3)
# while retaining some of the forecast signal.
RECENT_WEIGHT = 0.7
FORECAST_WEIGHT = 1.0 - RECENT_WEIGHT


@dataclass
class ReplenishmentInput:
    on_hand: int
    incoming: int                      # outstanding units from open/partial POs
    forecast_daily: float
    lead_time_days: int
    review_period_days: int
    safety_stock_days: int
    moq: int
    pack_size: int
    unit_price: float                  # price used for the budget check
    unit_volume_l: float
    remaining_budget: float
    remaining_storage_l: float
    recent_daily: Optional[float] = None   # recent actual average daily sales
    shelf_life_days: Optional[int] = None


@dataclass
class ReplenishmentResult:
    recommended_qty: int               # ideal order, constraint-free (MOQ/pack rounded)
    constrained_qty: int               # what we can actually order given budget+storage
    feasible: bool                     # can a MOQ/pack-compliant order be placed at all?
    demand_rate: float                 # effective daily demand used
    order_up_to_level: float
    inventory_position: int            # on_hand + incoming
    raw_need: float                    # order_up_to - position (pre-rounding)
    binding_constraints: list[str] = field(default_factory=list)
    projected_cover_days: float = 0.0  # days of cover after the constrained order lands
    breakdown: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _round_up_to_grid(qty: float, moq: int, pack_size: int) -> int:
    """Round a raw quantity UP to satisfy MOQ and pack-size multiples."""
    q = max(qty, moq)
    if pack_size > 1:
        q = math.ceil(q / pack_size) * pack_size
    return int(q)


def _round_down_to_pack(qty: float, pack_size: int) -> int:
    """Round a cap DOWN to a pack-size multiple (never order a partial pack)."""
    if qty <= 0:
        return 0
    if pack_size > 1:
        return int(math.floor(qty / pack_size) * pack_size)
    return int(math.floor(qty))


def effective_daily_demand(forecast_daily: float, recent_daily: Optional[float]) -> float:
    """Blend forecast and recent actuals into the demand rate we plan against."""
    if recent_daily is None:
        return forecast_daily
    return RECENT_WEIGHT * recent_daily + FORECAST_WEIGHT * forecast_daily


def compute(inp: ReplenishmentInput) -> ReplenishmentResult:
    demand_rate = effective_daily_demand(inp.forecast_daily, inp.recent_daily)

    cover_days = inp.lead_time_days + inp.review_period_days
    cycle_demand = demand_rate * cover_days
    safety_stock_units = demand_rate * inp.safety_stock_days
    order_up_to = cycle_demand + safety_stock_units

    position = inp.on_hand + inp.incoming
    raw_need = order_up_to - position

    binding: list[str] = []

    if raw_need <= 0:
        # Already covered — no order needed.
        result = ReplenishmentResult(
            recommended_qty=0,
            constrained_qty=0,
            feasible=True,
            demand_rate=round(demand_rate, 3),
            order_up_to_level=round(order_up_to, 2),
            inventory_position=position,
            raw_need=round(raw_need, 2),
            binding_constraints=["already_covered"],
            projected_cover_days=round(position / demand_rate, 1) if demand_rate > 0 else math.inf,
        )
        result.breakdown = _breakdown(inp, demand_rate, cycle_demand, safety_stock_units,
                                      order_up_to, position, raw_need, 0, 0, 0)
        return result

    recommended_qty = _round_up_to_grid(raw_need, inp.moq, inp.pack_size)

    # Constraint caps, each rounded DOWN to a whole pack.
    max_by_budget = _round_down_to_pack(
        inp.remaining_budget / inp.unit_price if inp.unit_price > 0 else math.inf,
        inp.pack_size,
    )
    max_by_storage = _round_down_to_pack(
        inp.remaining_storage_l / inp.unit_volume_l if inp.unit_volume_l > 0 else math.inf,
        inp.pack_size,
    )

    constrained_qty = min(recommended_qty, max_by_budget, max_by_storage)

    if constrained_qty < recommended_qty:
        if max_by_budget <= max_by_storage and max_by_budget < recommended_qty:
            binding.append("budget")
        if max_by_storage <= max_by_budget and max_by_storage < recommended_qty:
            binding.append("storage")

    # A legal order must still clear MOQ. If the caps force us below MOQ, no
    # compliant order can be placed — the situation needs a different action
    # (escalate / split / negotiate), not a silent sub-MOQ order.
    feasible = constrained_qty >= inp.moq and constrained_qty > 0
    if not feasible:
        constrained_qty = 0
        if "budget" not in binding and "storage" not in binding:
            binding.append("below_moq_after_constraints")

    projected_position = position + constrained_qty
    projected_cover = projected_position / demand_rate if demand_rate > 0 else math.inf

    result = ReplenishmentResult(
        recommended_qty=recommended_qty,
        constrained_qty=constrained_qty,
        feasible=feasible,
        demand_rate=round(demand_rate, 3),
        order_up_to_level=round(order_up_to, 2),
        inventory_position=position,
        raw_need=round(raw_need, 2),
        binding_constraints=binding or ["none"],
        projected_cover_days=round(projected_cover, 1),
    )
    result.breakdown = _breakdown(
        inp, demand_rate, cycle_demand, safety_stock_units, order_up_to, position,
        raw_need, recommended_qty, max_by_budget, max_by_storage,
    )
    return result


def _breakdown(inp, demand_rate, cycle_demand, safety_stock_units, order_up_to,
               position, raw_need, recommended_qty, max_by_budget, max_by_storage) -> dict:
    return {
        "forecast_daily": inp.forecast_daily,
        "recent_daily": inp.recent_daily,
        "effective_daily_demand": round(demand_rate, 3),
        "lead_time_days": inp.lead_time_days,
        "review_period_days": inp.review_period_days,
        "safety_stock_days": inp.safety_stock_days,
        "cycle_demand_units": round(cycle_demand, 1),
        "safety_stock_units": round(safety_stock_units, 1),
        "order_up_to_level": round(order_up_to, 1),
        "on_hand": inp.on_hand,
        "incoming": inp.incoming,
        "inventory_position": position,
        "raw_need_units": round(raw_need, 1),
        "moq": inp.moq,
        "pack_size": inp.pack_size,
        "recommended_qty": recommended_qty,
        "max_by_budget": max_by_budget,
        "max_by_storage": max_by_storage,
    }
