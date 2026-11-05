"""Validate a proposed action against hard constraints.

Single source of truth used both by the agent (pre-proposal) and the executor
(execution-time re-check). Handles the two write actions correctly:

* ``create_po`` — a brand-new order: validate the full quantity against the
  remaining budget / storage.
* ``modify_po`` — changing an EXISTING order: this is incremental, so we credit
  back the order's current budget + storage commitment before validating the new
  quantity. Validating the absolute quantity without the credit would
  double-count the existing commitment and wrongly reject most increases.
"""
from __future__ import annotations

import dataclasses
from typing import Optional

from sqlmodel import Session

from ..engine.validation import validate_po
from ..schemas import ProposedAction, ValidationReport
from . import orders, world


def _report(res) -> ValidationReport:
    return ValidationReport(ok=res.ok, violations=res.violations,
                            warnings=res.warnings, metrics=res.metrics)


def validate_action(session: Session, sid: str, action: ProposedAction) -> Optional[ValidationReport]:
    """Return a validation report for a create/modify action, or None if the
    action is not a PO write (none/escalate/cancel)."""
    if action.action == "create_po":
        if not (action.sku and action.node_id and action.supplier_id and action.qty):
            return ValidationReport(ok=False, violations=["incomplete_create_action"])
        ctx = world.build_validation_context(session, sid, action.sku, action.node_id,
                                             action.supplier_id)
        if ctx is None:
            return ValidationReport(ok=False, violations=["missing_data_or_supplier_offer"])
        return _report(validate_po(int(action.qty), ctx))

    if action.action == "modify_po":
        if not (action.po_id and action.qty):
            return ValidationReport(ok=False, violations=["incomplete_modify_action"])
        po = orders.get_po(session, action.po_id)
        if po is None:
            return ValidationReport(ok=False, violations=[f"po_not_found:{action.po_id}"])
        ctx = world.build_validation_context(session, sid, po.sku, po.node_id, po.supplier_id)
        if ctx is None:
            return ValidationReport(ok=False, violations=["missing_data_or_supplier_offer"])
        # Credit back this PO's existing commitment so we validate the DELTA.
        outstanding = world.po_outstanding(po)
        ctx = dataclasses.replace(
            ctx,
            remaining_budget=ctx.remaining_budget + po.qty_ordered * po.unit_price,
            remaining_storage_l=ctx.remaining_storage_l + outstanding * ctx.unit_volume_l,
            incoming=max(0, ctx.incoming - outstanding),  # replacing, not adding to, this PO
        )
        return _report(validate_po(int(action.qty), ctx))

    return None  # none / escalate / cancel_po: nothing to pre-validate here
