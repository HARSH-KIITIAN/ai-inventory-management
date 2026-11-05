"""HTTP API: scenarios, world state, the streaming agent run, approval, and the
supplier-confirmation trigger for the reconcile loop."""
from __future__ import annotations

import json
import queue
import threading

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from ..agent.agent import run_agent
from ..config import get_settings
from ..db import engine, get_session
from ..domain import orders, world
from ..models import Scenario
from ..schemas import AgentRunRequest, ConfirmRequest
from ..seed import reset_scenario
from ..service import execute_decision, store

router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/config")
def config() -> dict:
    s = get_settings()
    return {
        "provider": s.llm_provider,
        "model": s.anthropic_model if s.llm_provider == "anthropic"
        else s.openai_model if s.llm_provider == "openai" else "deterministic-stub",
    }


@router.get("/scenarios")
def list_scenarios() -> dict:
    with Session(engine) as session:
        rows = session.exec(select(Scenario)).all()
    return {"scenarios": [s.model_dump() for s in rows]}


@router.get("/scenarios/{sid}/state")
def scenario_state(sid: str) -> dict:
    with Session(engine) as session:
        snap = world.world_snapshot(session, sid)
    if not snap:
        raise HTTPException(status_code=404, detail=f"Scenario {sid} not found")
    return snap


@router.post("/scenarios/{sid}/reset")
def reset(sid: str) -> dict:
    with Session(engine) as session:
        reset_scenario(session, sid)
        snap = world.world_snapshot(session, sid)
    return {"status": "reset", "state": snap}


@router.post("/agent/run")
def agent_run(req: AgentRunRequest) -> StreamingResponse:
    """Run the agent and stream trace events as Server-Sent Events."""
    events: "queue.Queue[dict]" = queue.Queue()

    def worker() -> None:
        with Session(engine) as session:
            try:
                result = run_agent(session, req.scenario_id, req.mode, emit=events.put)
                record = store.save(result)
                events.put({"type": "saved", "run_id": record.run_id, "status": record.status})
            except Exception as exc:  # noqa: BLE001 - surface any failure to the client
                events.put({"type": "error", "message": str(exc)})
            finally:
                events.put({"type": "__end__"})

    threading.Thread(target=worker, daemon=True).start()

    def event_stream():
        while True:
            ev = events.get()
            if ev.get("type") == "__end__":
                break
            yield f"data: {json.dumps(ev)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    record = store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return record.to_dict()


@router.post("/runs/{run_id}/approve")
def approve(run_id: str) -> dict:
    record = store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")
    with Session(engine) as session:
        result = execute_decision(session, record)
    return {"status": record.status, "execution": result.model_dump()}


@router.post("/runs/{run_id}/reject")
def reject(run_id: str) -> dict:
    record = store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")
    record.status = "rejected"
    return {"status": record.status}


@router.post("/suppliers/confirm")
def supplier_confirm(req: ConfirmRequest) -> dict:
    """Record a supplier fulfillment confirmation (the Scenario-2 trigger).

    Returns the updated PO; the client then starts a reconcile agent run.
    """
    with Session(engine) as session:
        po = orders.confirm_po(session, req.po_id, req.confirmed_qty)
        return {
            "po": {
                "id": po.id, "sku": po.sku, "node_id": po.node_id,
                "qty_ordered": po.qty_ordered, "qty_confirmed": po.qty_confirmed,
                "status": po.status, "shortfall": max(0, po.qty_ordered - (po.qty_confirmed or 0)),
            }
        }
