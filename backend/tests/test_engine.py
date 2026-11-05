"""Unit tests for the deterministic engine (replenishment oracle + validator).

These pin down the arithmetic the whole system depends on. No LLM, no DB.
"""
from __future__ import annotations

import math

import pytest

from app.engine.replenishment import (
    ReplenishmentInput,
    compute,
    effective_daily_demand,
)
from app.engine.validation import POValidationContext, validate_po


def _base_input(**overrides) -> ReplenishmentInput:
    defaults = dict(
        on_hand=20,
        incoming=0,
        forecast_daily=10.0,
        lead_time_days=5,
        review_period_days=7,
        safety_stock_days=3,
        moq=50,
        pack_size=10,
        unit_price=2.0,
        unit_volume_l=1.0,
        remaining_budget=10_000.0,
        remaining_storage_l=10_000.0,
        recent_daily=None,
        shelf_life_days=60,
    )
    defaults.update(overrides)
    return ReplenishmentInput(**defaults)


# --- replenishment: core math -------------------------------------------------------

def test_basic_need_rounds_to_pack_grid():
    # order_up_to = 10*(5+7) + 10*3 = 150 ; position=20 ; raw_need=130
    res = compute(_base_input())
    assert res.order_up_to_level == 150.0
    assert res.raw_need == 130.0
    assert res.recommended_qty == 130      # already on the pack grid
    assert res.constrained_qty == 130
    assert res.feasible is True
    assert res.binding_constraints == ["none"]


def test_already_covered_returns_zero():
    res = compute(_base_input(on_hand=500, incoming=0))
    assert res.recommended_qty == 0
    assert res.constrained_qty == 0
    assert "already_covered" in res.binding_constraints


def test_moq_floor_applied():
    # raw_need small but below MOQ -> bumped up to MOQ (50)
    res = compute(_base_input(on_hand=118))  # order_up_to=150, position=118, raw_need=32
    assert res.raw_need == 32.0
    assert res.recommended_qty == 50        # max(32, moq=50) -> 50 (pack multiple)


def test_pack_rounding_up():
    res = compute(_base_input(moq=10, pack_size=25, on_hand=20))  # raw_need=130
    # max(130,10)=130 ; ceil(130/25)*25 = 150
    assert res.recommended_qty == 150


def test_recent_actuals_shift_demand_up():
    # Scenario-3 flavour: recent sales triple the forecast.
    assert effective_daily_demand(10.0, 30.0) == pytest.approx(0.7 * 30 + 0.3 * 10)
    res = compute(_base_input(forecast_daily=10.0, recent_daily=30.0))
    assert res.demand_rate == pytest.approx(24.0)
    # order_up_to = 24*12 + 24*3 = 360 ; position 20 ; raw_need 340 -> pack grid 340
    assert res.recommended_qty == 340


def test_budget_constraint_binds():
    # recommended 130 units @ $2 = $260, but only $150 budget -> 75 units -> pack(10)=70
    res = compute(_base_input(remaining_budget=150.0))
    assert res.recommended_qty == 130
    assert res.constrained_qty == 70
    assert "budget" in res.binding_constraints
    assert res.feasible is True            # 70 >= moq(50)


def test_storage_constraint_binds():
    # 130 units need 130 L but only 90 L free -> 90 units -> pack(10)=90
    res = compute(_base_input(unit_volume_l=1.0, remaining_storage_l=95.0))
    assert res.constrained_qty == 90
    assert "storage" in res.binding_constraints


def test_infeasible_when_constraints_push_below_moq():
    # Only $60 budget @ $2 = 30 units, below MOQ 50 -> no compliant order possible.
    res = compute(_base_input(remaining_budget=60.0))
    assert res.constrained_qty == 0
    assert res.feasible is False
    assert "budget" in res.binding_constraints


# --- validation ---------------------------------------------------------------------

def _base_ctx(**overrides) -> POValidationContext:
    defaults = dict(
        moq=50,
        pack_size=10,
        unit_price=2.0,
        unit_volume_l=1.0,
        remaining_budget=10_000.0,
        remaining_storage_l=10_000.0,
        on_hand=20,
        incoming=0,
        demand_rate=10.0,
        supplier_offers_sku=True,
        reliability=0.95,
        shelf_life_days=60,
        target_min_cover_days=7,
        target_max_cover_days=45,
    )
    defaults.update(overrides)
    return POValidationContext(**defaults)


def test_validation_clean_pass():
    res = validate_po(130, _base_ctx())
    assert res.ok is True
    assert res.violations == []


def test_validation_over_budget():
    res = validate_po(130, _base_ctx(remaining_budget=100.0))  # 130*2=260 > 100
    assert res.ok is False
    assert any("over_budget" in v for v in res.violations)


def test_validation_over_storage():
    res = validate_po(130, _base_ctx(remaining_storage_l=50.0))
    assert res.ok is False
    assert any("over_storage" in v for v in res.violations)


def test_validation_below_moq_and_pack():
    res = validate_po(45, _base_ctx())  # below moq 50 AND not multiple of 10
    assert res.ok is False
    assert any("below_moq" in v for v in res.violations)
    assert any("not_pack_multiple" in v for v in res.violations)


def test_validation_supplier_missing_sku():
    res = validate_po(100, _base_ctx(supplier_offers_sku=False))
    assert res.ok is False
    assert "supplier_does_not_offer_sku" in res.violations


def test_validation_low_reliability_warns_but_passes():
    res = validate_po(100, _base_ctx(reliability=0.5))
    assert res.ok is True
    assert any("low_supplier_reliability" in w for w in res.warnings)


def test_validation_overstock_and_spoilage_warn():
    # Huge order vs low demand -> very long cover -> overstock + spoilage warnings.
    res = validate_po(1000, _base_ctx(demand_rate=5.0, shelf_life_days=30))
    cover = (20 + 0 + 1000) / 5.0
    assert res.metrics["projected_cover_days"] == pytest.approx(cover, rel=1e-3)
    assert any("overstock" in w for w in res.warnings)
    assert any("spoilage_risk" in w for w in res.warnings)
    assert res.ok is True  # warnings do not block
