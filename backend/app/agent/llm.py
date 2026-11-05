"""Pluggable LLM layer.

A single provider-agnostic interface, ``LLMClient.complete(system, messages,
tools) -> LLMResponse``, lets the agent loop in ``agent.py`` stay identical
across providers. Three implementations:

* ``AnthropicClient`` — Claude via the Anthropic SDK (native tool use).
* ``OpenAIClient``    — GPT via the OpenAI SDK (function calling).
* ``StubClient``      — a deterministic, key-free agent that follows a fixed
  investigate-then-decide script driven by the engine's output. It makes the
  whole system runnable and the evals reproducible without any API key.

Internal message format (list of dicts):
    {"role": "user", "content": str}
    {"role": "assistant", "content": str, "tool_calls": [ToolCall...]}
    {"role": "tool", "tool_call_id": str, "name": str, "content": str(json)}
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from ..config import Settings
from .tools import SUBMIT_DECISION, TOOLS


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"


class LLMClient:
    name = "base"

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        raise NotImplementedError


# --- Anthropic ----------------------------------------------------------------------

class AnthropicClient(LLMClient):
    name = "anthropic"

    def __init__(self, settings: Settings):
        import anthropic

        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._model = settings.anthropic_model

    @staticmethod
    def _to_anthropic_messages(messages: list[dict]) -> list[dict]:
        out: list[dict] = []
        pending_tool_results: list[dict] = []

        def flush_tool_results():
            nonlocal pending_tool_results
            if pending_tool_results:
                out.append({"role": "user", "content": pending_tool_results})
                pending_tool_results = []

        for m in messages:
            role = m["role"]
            if role == "tool":
                pending_tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": m["tool_call_id"],
                    "content": m["content"],
                })
                continue
            flush_tool_results()
            if role == "user":
                out.append({"role": "user", "content": [{"type": "text", "text": m["content"]}]})
            elif role == "assistant":
                blocks: list[dict] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for tc in m.get("tool_calls", []):
                    blocks.append({
                        "type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments,
                    })
                out.append({"role": "assistant", "content": blocks})
        flush_tool_results()
        return out

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=2048,
            system=system,
            tools=[{"name": t["name"], "description": t["description"],
                    "input_schema": t["input_schema"]} for t in tools],
            messages=self._to_anthropic_messages(messages),
        )
        text_parts, calls = [], []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))
        return LLMResponse(text="".join(text_parts), tool_calls=calls, stop_reason=resp.stop_reason)


# --- OpenAI -------------------------------------------------------------------------

class OpenAIClient(LLMClient):
    name = "openai"

    def __init__(self, settings: Settings):
        import openai

        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        self._client = openai.OpenAI(api_key=settings.openai_api_key)
        self._model = settings.openai_model

    @staticmethod
    def _to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            role = m["role"]
            if role == "user":
                out.append({"role": "user", "content": m["content"]})
            elif role == "assistant":
                msg: dict = {"role": "assistant", "content": m.get("content") or None}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [
                        {"id": tc.id, "type": "function",
                         "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                        for tc in m["tool_calls"]
                    ]
                out.append(msg)
            elif role == "tool":
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"],
                            "content": m["content"]})
        return out

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=self._to_openai_messages(system, messages),
            tools=[{"type": "function",
                    "function": {"name": t["name"], "description": t["description"],
                                 "parameters": t["input_schema"]}} for t in tools],
            tool_choice="auto",
        )
        choice = resp.choices[0]
        calls = []
        for tc in (choice.message.tool_calls or []):
            try:
                arguments = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
            calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=arguments))
        return LLMResponse(text=choice.message.content or "", tool_calls=calls,
                           stop_reason=choice.finish_reason)


# --- Stub (offline, deterministic) --------------------------------------------------

_READ_SEQUENCE = [
    "get_demand", "get_inventory", "get_open_orders",
    "get_suppliers", "get_budget", "get_storage",
]


class StubClient(LLMClient):
    """Deterministic agent: investigates in a fixed order, then decides from the
    oracle output. No LLM, no API key. Mirrors the policy in the system prompt."""

    name = "stub"

    def __init__(self, settings: Optional[Settings] = None):
        pass

    # -- helpers to read the conversation so far ------------------------------
    @staticmethod
    def _context(messages: list[dict]) -> dict:
        first_user = next((m for m in messages if m["role"] == "user"), None)
        if not first_user:
            return {}
        match = re.findall(r"```json\s*(\{.*?\})\s*```", first_user["content"], re.DOTALL)
        return json.loads(match[-1]) if match else {}

    @staticmethod
    def _results_by_name(messages: list[dict]) -> dict:
        results: dict[str, dict] = {}
        for m in messages:
            if m["role"] == "tool":
                try:
                    results[m["name"]] = json.loads(m["content"])
                except (json.JSONDecodeError, KeyError):
                    pass
        return results

    @staticmethod
    def _choose_supplier(suppliers: list[dict], mode: str, failed: Optional[str]) -> Optional[dict]:
        candidates = suppliers
        if mode == "reconcile" and failed:
            candidates = [s for s in suppliers if s["supplier_id"] != failed] or suppliers
        return min(candidates, key=lambda s: s["unit_price"]) if candidates else None

    def _next_call(self, name: str, args: dict) -> LLMResponse:
        return LLMResponse(
            tool_calls=[ToolCall(id=f"stub-{name}", name=name, arguments=args)],
            stop_reason="tool_use",
        )

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        ctx = self._context(messages)
        sku, node = ctx.get("sku"), ctx.get("node_id")
        results = self._results_by_name(messages)

        # 1) Walk the fixed read sequence.
        for tool_name in _READ_SEQUENCE:
            if tool_name in results:
                continue
            if tool_name in ("get_budget", "get_storage"):
                return self._next_call(tool_name, {"node_id": node})
            if tool_name == "get_suppliers":
                return self._next_call(tool_name, {"sku": sku})
            return self._next_call(tool_name, {"sku": sku, "node_id": node})

        suppliers = results.get("get_suppliers", {}).get("suppliers", [])
        chosen = self._choose_supplier(suppliers, ctx.get("mode", "review"),
                                       ctx.get("failed_supplier_id"))

        # 2) Compute replenishment with the chosen supplier.
        if "compute_replenishment" not in results:
            sup_id = chosen["supplier_id"] if chosen else None
            return self._next_call(
                "compute_replenishment",
                {"sku": sku, "node_id": node, **({"supplier_id": sup_id} if sup_id else {})},
            )

        comp = results["compute_replenishment"]
        ideal = comp.get("recommended_qty", 0)
        con = comp.get("constrained_qty", 0)
        feasible = comp.get("feasible", False)
        target = con if (feasible and con > 0) else ideal

        # 3) Validate the proposed order (skip if nothing to order).
        if ideal > 0 and target > 0 and "validate_purchase_order" not in results and chosen:
            return self._next_call("validate_purchase_order", {
                "sku": sku, "node_id": node,
                "supplier_id": chosen["supplier_id"], "qty": target,
            })

        # 4) Decide.
        return LLMResponse(
            tool_calls=[ToolCall(id="stub-decision", name=SUBMIT_DECISION,
                                 arguments=self._build_decision(ctx, comp, results, chosen))],
            stop_reason="tool_use",
        )

    def _build_decision(self, ctx: dict, comp: dict, results: dict,
                        chosen: Optional[dict]) -> dict:
        sku, node = ctx.get("sku"), ctx.get("node_id")
        mode = ctx.get("mode", "review")
        sys_rec = ctx.get("recommended_qty")

        ideal = comp.get("recommended_qty", 0)
        con = comp.get("constrained_qty", 0)
        feasible = comp.get("feasible", False)
        binding = [b for b in comp.get("binding_constraints", []) if b not in ("none",)]
        pos = comp.get("inventory_position", 0)
        oup = comp.get("order_up_to_level", 0)
        demand = comp.get("demand_rate", 0)
        target = con if (feasible and con > 0) else ideal
        pack = comp.get("breakdown", {}).get("pack_size", 1)
        sup_id = chosen["supplier_id"] if chosen else None

        factors = [
            f"Effective daily demand {demand}; order-up-to level {oup} units.",
            f"Inventory position (on-hand + incoming) = {pos} units.",
            f"Oracle ideal order = {ideal}; feasible under constraints = {con}.",
        ]
        constrained = feasible and con < ideal
        if binding:
            factors.append(f"Binding constraint(s): {', '.join(binding)}.")

        def create_action(qty: int, note: str) -> dict:
            return {"action": "create_po", "sku": sku, "node_id": node,
                    "supplier_id": sup_id, "qty": qty, "rationale": note}

        # Already covered -> no new purchase.
        if ideal == 0:
            return {
                "category": "accept",
                "summary": "Demand is already covered; no additional purchase needed.",
                "reasoning": (f"Inventory position {pos} meets the order-up-to level {oup} for "
                              f"{sku} at {node}. No order is warranted."),
                "key_factors": factors,
                "proposed_action": {"action": "none", "rationale": "No purchase required."},
                "confidence": 0.9,
            }

        # No compliant order possible at all.
        if not feasible:
            return {
                "category": "escalate",
                "summary": "Constraints prevent any compliant order; human intervention needed.",
                "reasoning": (f"Need ~{ideal} units of {sku} but budget/storage force the order "
                              f"below MOQ. Binding: {', '.join(binding) or 'n/a'}. Escalating."),
                "key_factors": factors,
                "proposed_action": {"action": "escalate",
                                    "rationale": "Raise budget/storage or source differently."},
                "confidence": 0.8,
            }

        if mode == "reconcile":
            return {
                "category": "modify",
                "summary": f"Source the shortfall: order {target} units from {sup_id}.",
                "reasoning": (f"After the supplier shortfall, inventory position fell to {pos}, "
                              f"below the order-up-to level {oup}. Sourcing {target} units of {sku} "
                              f"from alternate supplier {sup_id} restores coverage."),
                "key_factors": factors,
                "proposed_action": create_action(target, "Cover the shortfall via alternate supplier."),
                "confidence": 0.85,
            }

        if constrained:
            shortfall = ideal - target
            return {
                "category": "escalate",
                "summary": f"Order the feasible {target} units now and escalate the {shortfall}-unit shortfall.",
                "reasoning": (f"The ideal order is {ideal} units but {', '.join(binding)} caps the "
                              f"feasible order at {target}. Proceeding with {target} and escalating "
                              f"the remaining {shortfall} units for a human decision (e.g. raise budget)."),
                "key_factors": factors,
                "proposed_action": create_action(target, f"Feasible partial order; escalate {shortfall}-unit gap."),
                "confidence": 0.8,
            }

        if sys_rec is not None and abs(sys_rec - target) > max(pack, 1):
            return {
                "category": "modify",
                "summary": f"Modify the recommendation from {sys_rec} to {target} units.",
                "reasoning": (f"The system recommended {sys_rec} units, but the correct quantity for "
                              f"{sku} at {node} is {target} (ideal {ideal}). Ordering {sys_rec} would "
                              f"over-stock and breach constraints. Proposing {target}."),
                "key_factors": factors,
                "proposed_action": create_action(target, f"Corrected quantity (was {sys_rec})."),
                "confidence": 0.85,
            }

        if sys_rec is not None:
            return {
                "category": "accept",
                "summary": f"Accept: order {target} units as recommended.",
                "reasoning": f"The recommended {sys_rec} units matches the computed need for {sku}.",
                "key_factors": factors,
                "proposed_action": create_action(target, "Recommendation validated."),
                "confidence": 0.85,
            }

        return {
            "category": "modify",
            "summary": f"Update the plan: order {target} units of {sku}.",
            "reasoning": (f"Current inventory position {pos} is below the order-up-to level {oup} "
                          f"given demand {demand}/day. Ordering {target} units restores coverage."),
            "key_factors": factors,
            "proposed_action": create_action(target, "Replenish to the order-up-to level."),
            "confidence": 0.85,
        }


# --- factory ------------------------------------------------------------------------

def get_client(settings: Settings) -> LLMClient:
    provider = settings.llm_provider
    if provider == "anthropic":
        return AnthropicClient(settings)
    if provider == "openai":
        return OpenAIClient(settings)
    return StubClient(settings)
