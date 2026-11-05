"""Deterministic purchase-order validator.

Separates *hard* violations (an order that must not be placed) from *soft*
warnings (placeable, but a human should know). Used in two places:

* **Pre-execution** — the agent validates its proposed PO before asking for
  approval; hard violations force it to modify / escalate instead.
* **Post-execution** — after a PO is written, we re-read the resulting world
  state and re-run the same invariants to confirm the outcome is acceptable.
  This read-back is the core of the feedback loop.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Optional

# Supplier reliability below this triggers a (soft) warning.
RELIABILITY_WARN_THRESHOLD = 0.80


@dataclass
class POValidationContext:
    moq: int
    pack_size: int
    unit_price: float
    unit_volume_l: float
    remaining_budget: float
    remaining_storage_l: float
    on_hand: int
    incoming: int
    demand_rate: float
    supplier_offers_sku: bool
    reliability: float
    shelf_life_days: Optional[int] = None
    target_min_cover_days: Optional[float] = None
    target_max_cover_days: Optional[float] = None


@dataclass
class ValidationResult:
    ok: bool                                   # True when there are no hard violations
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def validate_po(qty: int, ctx: POValidationContext) -> ValidationResult:
    violations: list[str] = []
    warnings: list[str] = []

    order_value = qty * ctx.unit_price
    added_volume = qty * ctx.unit_volume_l
    projected_position = ctx.on_hand + ctx.incoming + qty
    projected_cover = (
        projected_position / ctx.demand_rate if ctx.demand_rate > 0 else math.inf
    )

    # ---- Hard violations ----------------------------------------------------
    if not ctx.supplier_offers_sku:
        violations.append("supplier_does_not_offer_sku")

    if qty <= 0:
        violations.append("non_positive_quantity")
    else:
        if qty < ctx.moq:
            violations.append(f"below_moq (qty={qty} < moq={ctx.moq})")
        if ctx.pack_size > 1 and qty % ctx.pack_size != 0:
            violations.append(f"not_pack_multiple (qty={qty}, pack_size={ctx.pack_size})")

    if order_value > ctx.remaining_budget + 1e-6:
        violations.append(
            f"over_budget (value={order_value:.2f} > remaining={ctx.remaining_budget:.2f})"
        )

    if added_volume > ctx.remaining_storage_l + 1e-6:
        violations.append(
            f"over_storage (added={added_volume:.1f}L > free={ctx.remaining_storage_l:.1f}L)"
        )

    # ---- Soft warnings ------------------------------------------------------
    if ctx.reliability < RELIABILITY_WARN_THRESHOLD:
        warnings.append(
            f"low_supplier_reliability (reliability={ctx.reliability:.2f})"
        )

    if ctx.shelf_life_days is not None and projected_cover > ctx.shelf_life_days:
        warnings.append(
            f"spoilage_risk (cover={projected_cover:.0f}d > shelf_life={ctx.shelf_life_days}d)"
        )

    if (
        ctx.target_max_cover_days is not None
        and projected_cover > ctx.target_max_cover_days
    ):
        warnings.append(
            f"overstock (cover={projected_cover:.0f}d > target_max={ctx.target_max_cover_days:.0f}d)"
        )

    if (
        ctx.target_min_cover_days is not None
        and projected_cover < ctx.target_min_cover_days
    ):
        warnings.append(
            f"under_coverage (cover={projected_cover:.0f}d < target_min={ctx.target_min_cover_days:.0f}d)"
        )

    return ValidationResult(
        ok=len(violations) == 0,
        violations=violations,
        warnings=warnings,
        metrics={
            "order_value": round(order_value, 2),
            "added_volume_l": round(added_volume, 1),
            "projected_cover_days": round(projected_cover, 1),
            "remaining_budget": round(ctx.remaining_budget, 2),
            "remaining_storage_l": round(ctx.remaining_storage_l, 1),
        },
    )
