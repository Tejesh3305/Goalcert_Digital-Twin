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

import base64
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


class ExpandRequest(BaseModel):
    tenant: str
    message: str
    session_id: Optional[str] = None


@router.post("/twin/expand")
def twin_expand(req: ExpandRequest):
    """Add assets to an existing twin conversationally.

    The user says something like 'add 3 temperature sensors to Zone 1' and
    the agents handle type resolution, validation, and commit. This reuses
    the Schema Mapper + Validator + Graph Writer without re-classifying.
    """
    from agents.twin_agents import schema_mapper, validator, graph_writer

    session_id = req.session_id or _new_session("expand")
    tenant = req.tenant

    # Check the twin exists.
    try:
        from twins import TwinRegistry
        if TwinRegistry().get(tenant) is None:
            raise HTTPException(404, f"No twin for tenant '{tenant}'.")
    except HTTPException:
        raise
    except Exception:
        pass

    # Detect the twin's domain from the registry.
    domain = "hvac"
    bundles = []
    try:
        from twins import TwinRegistry
        twin = TwinRegistry().get(tenant)
        if twin:
            domain = twin.get("domain") or twin.domain if hasattr(twin, "domain") else "hvac"
        from agents.registry import get_registry
        matches = get_registry().query(domain)
        bundles = [m["bundle_id"] for m in matches[:1]]
    except Exception:
        pass

    # Build a minimal state for the mapper → validator → writer chain.
    state = new_twin_state(tenant, session_id)
    state["conversation"] = [{"role": "user", "content": req.message}]
    state["domain"] = domain
    state["loaded_bundles"] = bundles
    state["committed"] = False
    state["next_action"] = "map"

    # Run Schema Mapper.
    update = schema_mapper(state)
    state.update(update)

    if not state.get("draft_entities"):
        return {"session_id": session_id, "state": _public(state),
                "error": "Could not map any entities from your description."}

    # Run Validator.
    update = validator(state)
    state.update(update)

    v = state.get("validation") or {}
    if not v.get("ok"):
        return {"session_id": session_id, "state": _public(state),
                "error": "Validation failed.",
                "violations": v.get("errors", [])}

    # Run Graph Writer.
    update = graph_writer(state)
    state.update(update)

    return {"session_id": session_id, "state": _public(state),
            "committed": state.get("committed", False),
            "reply": state.get("reply_to_user", "")}


@router.post("/twin/upload")
def twin_upload(req: dict):
    """Attach an image (base64 data-URI or URL) to an active twin session for
    the Vision Agent. Accepts {session_id, url} or {session_id, data, filename}.
    """
    session_id = req.get("session_id")
    if not session_id:
        raise HTTPException(400, "session_id required")
    cur = twin_app.get_state(session_id)
    if cur is None:
        raise HTTPException(404, "Unknown session. Start a twin session first.")

    url = req.get("url")
    data = req.get("data")  # base64 data URI
    filename = req.get("filename", "upload.png")

    if not url and not data:
        raise HTTPException(400, "Provide 'url' or 'data' (base64 data-URI).")

    if data and not url:
        # Convert raw base64 to a data URI the OpenAI vision API accepts.
        if not data.startswith("data:"):
            mime = "image/png" if filename.endswith(".png") else "image/jpeg"
            url = f"data:{mime};base64,{data}"
        else:
            url = data

    files = list(cur.get("uploaded_files") or [])
    files.append({"url": url, "type": "image", "filename": filename})
    cur["uploaded_files"] = files
    twin_app.checkpointer.save(session_id, "twin_build", cur, None)
    return {"session_id": session_id, "uploaded_count": len(files)}


@router.post("/twin/scene")
def twin_request_scene(req: SessionRef):
    """Request scene generation for a committed twin."""
    cur = twin_app.get_state(req.session_id)
    if cur is None:
        raise HTTPException(404, "Unknown session")
    if not cur.get("committed"):
        raise HTTPException(400, "Twin is not committed yet.")
    # Re-enter the graph at the scene_generator node.
    cur["scene_result"] = None  # clear to allow re-generation
    out = twin_app.invoke(cur, thread_id=req.session_id,
                          start_at="scene_generator")
    return {"session_id": req.session_id, "state": _public(out)}


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


# ── Operational flow (Diagnosis + Recommender — Team 2) ────────────
from agents.operational_graph import app as ops_app
from agents.state import new_operational_state


class DiagnoseRequest(BaseModel):
    tenant: str
    incident_id: str
    finding_ids: list[str]
    affected_entity_id: str


@router.post("/ops/diagnose")
def ops_diagnose(req: DiagnoseRequest):
    """Trigger LLM-enhanced diagnosis for an incident."""
    session_id = _new_session("ops")
    state = new_operational_state(
        tenant_id=req.tenant,
        session_id=session_id,
        incident_id=req.incident_id,
        finding_ids=req.finding_ids,
        affected_entity_id=req.affected_entity_id,
    )
    out = ops_app.invoke(state, thread_id=session_id)
    return {"session_id": session_id, "state": _public(out)}


@router.get("/ops/{session_id}")
def ops_state(session_id: str):
    cur = ops_app.get_state(session_id)
    if cur is None:
        raise HTTPException(404, "Unknown session")
    return {"session_id": session_id, "state": _public(cur)}


# ── Plugin Scaffolder (Team 4) ───────────────────────────────────
from agents.plugin_graph import app as plugin_app
from agents.state import new_plugin_state


@router.post("/plugin/start")
def plugin_start():
    session_id = _new_session("plugin")
    state = new_plugin_state("platform", session_id)
    out = plugin_app.invoke(state, thread_id=session_id)
    return {"session_id": session_id, "state": _public(out)}


@router.post("/plugin/message")
def plugin_message(req: Message):
    cur = plugin_app.get_state(req.session_id)
    if cur is None:
        raise HTTPException(404, "Unknown session. Start a plugin session first.")
    cur.setdefault("conversation", []).append({"role": "user", "content": req.message})
    out = plugin_app.invoke(cur, thread_id=req.session_id, start_at="interviewer")
    return {"session_id": req.session_id, "state": _public(out)}


@router.get("/plugin/{session_id}")
def plugin_state(session_id: str):
    cur = plugin_app.get_state(session_id)
    if cur is None:
        raise HTTPException(404, "Unknown session")
    return {"session_id": session_id, "state": _public(cur)}


# ── Accelerator Pack Composer (Team 4) ───────────────────────────
from agents.accelerator_graph import app as accel_app
from agents.state import new_accelerator_state


class AccelStart(BaseModel):
    domain: Optional[str] = None
    pack_name: Optional[str] = None


@router.post("/accelerator/start")
def accelerator_start(req: AccelStart):
    session_id = _new_session("accel")
    state = new_accelerator_state("platform", session_id)
    if req.domain:
        state["target_domain"] = req.domain
    if req.pack_name:
        state["pack_name"] = req.pack_name
    out = accel_app.invoke(state, thread_id=session_id)
    return {"session_id": session_id, "state": _public(out)}


@router.post("/accelerator/message")
def accelerator_message(req: Message):
    cur = accel_app.get_state(req.session_id)
    if cur is None:
        raise HTTPException(404, "Unknown session. Start an accelerator session first.")
    cur.setdefault("conversation", []).append({"role": "user", "content": req.message})
    out = accel_app.invoke(cur, thread_id=req.session_id, start_at="interviewer")
    return {"session_id": req.session_id, "state": _public(out)}


@router.get("/accelerator/{session_id}")
def accelerator_state(session_id: str):
    cur = accel_app.get_state(session_id)
    if cur is None:
        raise HTTPException(404, "Unknown session")
    return {"session_id": session_id, "state": _public(cur)}
