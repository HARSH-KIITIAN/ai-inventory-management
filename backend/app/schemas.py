"""Pydantic DTOs shared across the API and the agent.

The ``Decision`` / ``ProposedAction`` pair is the agent's structured output: the
agent proposes, a human approves, and only then does the system execute. This
separation is what implements the human-approval gate.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

DecisionCategory = Literal["accept", "modify", "reject", "investigate", "escalate"]
ActionType = Literal["create_po", "modify_po", "cancel_po", "escalate", "none"]
RunMode = Literal["review", "reconcile"]


class ProposedAction(BaseModel):
    action: ActionType = "none"
    sku: Optional[str] = None
    node_id: Optional[str] = None
    supplier_id: Optional[str] = None
    qty: Optional[int] = None
    po_id: Optional[str] = None            # target PO for modify/cancel
    rationale: str = ""


class Decision(BaseModel):
    category: DecisionCategory
    summary: str                           # one-line disposition
    reasoning: str                         # full explanation of the important factors
    key_factors: list[str] = Field(default_factory=list)
    proposed_action: ProposedAction = Field(default_factory=ProposedAction)
    confidence: float = 0.8
    requires_approval: bool = True


# --- API request/response ----------------------------------------------------------

class AgentRunRequest(BaseModel):
    scenario_id: str
    mode: RunMode = "review"


class ConfirmRequest(BaseModel):
    po_id: str
    confirmed_qty: int


class ValidationReport(BaseModel):
    ok: bool
    violations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metrics: dict = Field(default_factory=dict)


class ExecutionResult(BaseModel):
    executed: bool
    action: ProposedAction
    po_id: Optional[str] = None
    message: str = ""
    post_validation: Optional[ValidationReport] = None
    outcome_ok: bool = False               # did the post-action read-back pass?
