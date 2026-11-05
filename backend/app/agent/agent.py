"""The agent loop: investigate (tool use) -> decide -> pre-validate.

Provider-agnostic. Takes a scenario + mode, runs the tool-use loop against the
configured LLM client, captures the structured decision, and validates any
proposed order BEFORE it is shown to the human for approval. Emits trace events
so the UI can render the agent's reasoning step by step.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Optional

from sqlmodel import Session

from ..config import Settings, get_settings
from ..domain import po_check
from ..schemas import Decision, ValidationReport
from .llm import LLMClient, get_client
from .prompts import SYSTEM_PROMPT, build_initial_message
from .tools import SUBMIT_DECISION, TOOLS, execute_tool

EmitFn = Callable[[dict], None]


@dataclass
class RunResult:
    scenario_id: str
    mode: str
    provider: str
    trace: list[dict] = field(default_factory=list)
    decision: Optional[Decision] = None
    pre_validation: Optional[ValidationReport] = None
    context: dict = field(default_factory=dict)


def _fallback_decision(reason: str) -> Decision:
    return Decision(
        category="investigate",
        summary="Could not finalize a decision.",
        reasoning=reason,
        key_factors=[reason],
        confidence=0.2,
    )


def run_agent(
    session: Session,
    sid: str,
    mode: str = "review",
    settings: Optional[Settings] = None,
    client: Optional[LLMClient] = None,
    emit: Optional[EmitFn] = None,
) -> RunResult:
    settings = settings or get_settings()
    client = client or get_client(settings)

    def _emit(event: dict) -> None:
        result.trace.append(event)
        if emit:
            emit(event)

    result = RunResult(scenario_id=sid, mode=mode, provider=client.name)

    initial_text, context = build_initial_message(session, sid, mode)
    result.context = context
    _emit({"type": "situation", "text": initial_text, "mode": mode, "provider": client.name})

    messages: list[dict] = [{"role": "user", "content": initial_text}]
    decision_args: Optional[dict] = None

    for _ in range(settings.agent_max_steps):
        resp = client.complete(SYSTEM_PROMPT, messages, TOOLS)

        if resp.text:
            _emit({"type": "reasoning", "text": resp.text})

        if not resp.tool_calls:
            # Model ended without using the decision tool.
            break

        messages.append({"role": "assistant", "content": resp.text,
                         "tool_calls": resp.tool_calls})

        stop = False
        for call in resp.tool_calls:
            if call.name == SUBMIT_DECISION:
                decision_args = call.arguments
                stop = True
                break

            _emit({"type": "tool_call", "name": call.name, "args": call.arguments})
            output = execute_tool(session, sid, call.name, call.arguments)
            _emit({"type": "tool_result", "name": call.name, "result": output})
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "name": call.name, "content": json.dumps(output)})

        if stop:
            break

    # --- finalize decision ---------------------------------------------------
    if decision_args is None:
        result.decision = _fallback_decision("Agent did not submit a decision within the step limit.")
    else:
        try:
            result.decision = Decision(**decision_args)
        except Exception as exc:  # noqa: BLE001 - defensive against malformed tool args
            result.decision = _fallback_decision(f"Malformed decision payload: {exc}")

    _emit({"type": "decision", "decision": result.decision.model_dump()})

    # --- pre-execution validation of the proposed action --------------------
    result.pre_validation = _pre_validate(session, sid, result.decision)
    if result.pre_validation is not None:
        _emit({"type": "pre_validation", "report": result.pre_validation.model_dump()})

    _emit({"type": "done"})
    return result


def _pre_validate(session: Session, sid: str, decision: Decision) -> Optional[ValidationReport]:
    """Validate a proposed create/modify order before asking for approval."""
    return po_check.validate_action(session, sid, decision.proposed_action)
