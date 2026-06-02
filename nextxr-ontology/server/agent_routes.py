"""
agent_routes.py — HTTP surface for the agentic core.

Twin-building (Concierge chat that builds a real twin):
  POST /api/v1/agents/twin/start         {tenant?, twin_name?}      -> session + first reply
  POST /api/v1/agents/twin/message       {session_id, message}      -> next reply / result
  GET  /api/v1/agents/twin/{session_id}                              -> current state

Bundle Author (author a new vertical, with a human approval gate):
  POST /api/v1/agents/bundle/start       {domain?, bundle_name?}    -> session + first reply
  POST /api/v1/agents/bundle/message     {session_id, message}      -> next reply / draft
  POST /api/v1/agents/bundle/approve     {session_id}               -> publish (gated)
  GET  /api/v1/agents/bundle/{session_id}                            -> current state

  GET  /api/v1/agents/info                                          -> gateway + registry status

A "turn" runs the graph until it interrupts (asks the human) or reaches END.
The frontend just posts messages and renders state — it never sees the graph.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agents.twin_graph import app as twin_app
from agents.bundle_graph import app as bundle_app
from agents.state import new_twin_state, new_bundle_state
from agents.engine import INTERRUPT_KEY
from agents.gateway import get_gateway
from agents.registry import get_registry

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


def _new_session(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _public(state: dict) -> dict:
    """Trim internal keys before returning state to the UI."""
    if not state:
        return {}
    out = {k: v for k, v in state.items() if not k.startswith("_")}
    out.pop(INTERRUPT_KEY, None)
    out["awaiting_input"] = bool(state.get(INTERRUPT_KEY))
    return out


# ── Request models ─────────────────────────────────────────────────
class TwinStart(BaseModel):
    tenant: Optional[str] = None
    twin_name: Optional[str] = None


class Message(BaseModel):
    session_id: str
    message: str


class BundleStart(BaseModel):
    domain: Optional[str] = None
    bundle_name: Optional[str] = None


class SessionRef(BaseModel):
    session_id: str


# ── Info ───────────────────────────────────────────────────────────
@router.get("/info")
def info():
    gw = get_gateway()
    return {"llm": gw.stats(),
            "published_bundles": get_registry().list_published()}


# ── Twin-building flow ─────────────────────────────────────────────
@router.post("/twin/start")
def twin_start(req: TwinStart):
    session_id = _new_session("twin")
    tenant = req.tenant or session_id  # a twin == a tenant; default to the session
    state = new_twin_state(tenant, session_id, twin_name=req.twin_name)
    out = twin_app.invoke(state, thread_id=session_id)
    return {"session_id": session_id, "tenant": tenant, "state": _public(out)}


@router.post("/twin/message")
def twin_message(req: Message):
    cur = twin_app.get_state(req.session_id)
    if cur is None:
        raise HTTPException(404, "Unknown session. Start a twin session first.")
    cur.setdefault("conversation", []).append({"role": "user", "content": req.message})
    # Re-enter at the concierge (it owns the dialogue + decides to proceed).
    out = twin_app.invoke(cur, thread_id=req.session_id, start_at="concierge")
    return {"session_id": req.session_id, "state": _public(out)}


@router.get("/twin/{session_id}")
def twin_state(session_id: str):
    cur = twin_app.get_state(session_id)
    if cur is None:
        raise HTTPException(404, "Unknown session")
    return {"session_id": session_id, "state": _public(cur)}


# ── Bundle Author flow ─────────────────────────────────────────────
@router.post("/bundle/start")
def bundle_start(req: BundleStart):
    session_id = _new_session("bundle")
    state = new_bundle_state("bundle-author", session_id,
                             domain=req.domain, bundle_name=req.bundle_name)
    out = bundle_app.invoke(state, thread_id=session_id)
    return {"session_id": session_id, "state": _public(out)}


@router.post("/bundle/message")
def bundle_message(req: Message):
    cur = bundle_app.get_state(req.session_id)
    if cur is None:
        raise HTTPException(404, "Unknown session. Start a bundle session first.")
    cur.setdefault("conversation", []).append({"role": "user", "content": req.message})
    out = bundle_app.invoke(cur, thread_id=req.session_id, start_at="interviewer")
    return {"session_id": req.session_id, "state": _public(out)}


@router.post("/bundle/approve")
def bundle_approve(req: SessionRef):
    """The human gate. Sets approved=true and resumes into the Publisher."""
    cur = bundle_app.get_state(req.session_id)
    if cur is None:
        raise HTTPException(404, "Unknown session")
    if (cur.get("lint_result") or {}).get("ok") is not True:
        raise HTTPException(400, "Bundle hasn't passed lint yet — cannot approve.")
    cur["approved"] = True
    out = bundle_app.invoke(cur, thread_id=req.session_id, start_at="approval_gate")
    return {"session_id": req.session_id, "state": _public(out)}


@router.get("/bundle/{session_id}")
def bundle_state(session_id: str):
    cur = bundle_app.get_state(session_id)
    if cur is None:
        raise HTTPException(404, "Unknown session")
    return {"session_id": session_id, "state": _public(cur)}
