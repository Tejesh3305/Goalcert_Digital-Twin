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

    # Tag the file kind from its extension so the Plan Parser can branch:
    #   image  → PNG/JPG/etc. (and PDFs, which the client rasterises to PNG)
    #   bim    → .ifc / .dxf  (true-BIM fast-follow path)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("ifc", "dxf"):
        ftype = "bim"
    else:
        ftype = "image"

    if data and not url:
        # Convert raw base64 to a data URI the OpenAI vision API accepts.
        if not data.startswith("data:"):
            mime = "image/png" if filename.endswith(".png") else "image/jpeg"
            url = f"data:{mime};base64,{data}"
        else:
            url = data

    files = list(cur.get("uploaded_files") or [])
    files.append({"url": url, "type": ftype, "filename": filename})
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


@router.get("/twin/scene/{tenant}")
def twin_scene_by_tenant(tenant: str):
    """Rebuild a renderable `nxr-scene/1` for any committed twin straight from
    its graph geometry — no live session needed (powers the Dashboard hero and
    re-loading a twin's 3-D view)."""
    from agents import bim_support as bs

    # Fast path: a cached scene from the build (includes furniture + entityIds).
    cached = bs.load_scene_cache(tenant)
    if cached and cached.get("nodes"):
        return {"tenant": tenant, "scene_result": cached}

    try:
        from graph.query import GraphQuery
        q = GraphQuery()
        locations = q.list_by_label(tenant, "Location", limit=400)
        assets = q.list_by_label(tenant, "PhysicalAsset", limit=400)
    except Exception:
        locations, assets = [], []

    bm = bs.graph_entities_to_bim_model(locations, assets)
    if not bm:
        # Non-BIM twin (or geometry missing): synthesize from the asset list.
        if not assets:
            return {"tenant": tenant, "scene_result": {
                "format": "nxr-scene/1", "status": "empty", "nodes": [],
                "message": "No assets to visualize for this twin."}}
        hints = [{"label": a.get("displayName", "Equipment"), "count": 1} for a in assets]
        bm = bs.synthesize_bim_model("office", 1, equipment_hints=hints)
        by_name = {a.get("displayName"): a.get("id") for a in assets}
        id_map = {eq["id"]: by_name.get(eq["label"]) for eq in bm.get("equipment", [])}
    else:
        id_map = {}
        for r in bm.get("rooms", []):
            id_map[r["id"]] = r["id"]
        for eq in bm.get("equipment", []):
            id_map[eq["id"]] = eq["id"]

    scene = bs.bim_model_to_scene(bm, id_map=id_map)
    scene["status"] = "ok"
    scene["twin_id"] = tenant
    bs.save_scene_cache(tenant, scene)
    return {"tenant": tenant, "scene_result": scene}


# ── One-shot 2-D plan → 3-D scene → live digital twin ──────────────
# Mirrors the standalone apps/2d-to-3d "upload → Build 3-D" feature, but instead
# of stopping at the rendered model it commits the reconstructed building as a
# real twin (graph + registry + cached scene) so it streams live physics. This
# is what the "Build a Twin → From a 2-D Plan" panel calls.
class BuildFromPlan(BaseModel):
    data: str                          # image data URL (PDFs rasterised client-side)
    filename: str = "plan.png"
    name: Optional[str] = None
    facility: Optional[str] = None
    floors: int = 1


@router.post("/twin/build-from-plan")
def twin_build_from_plan(req: BuildFromPlan):
    """Parse an uploaded 2-D floor plan into a 3-D `nxr-scene/1`, then commit it
    as a live digital twin.

    Pipeline (all reusing the existing agent nodes):
        vision parse  → bim_model
        schema_mapper → Building/Floor/Room/equipment drafts (+ enrich/auto-wire)
        validator     → SHACL gate
        graph_writer  → commit to Neo4j + register the twin
        scene_generator → nxr-scene/1 with live entityIds, cached per-tenant

    Robust: if the database is offline (or the commit fails) the reconstructed
    3-D scene is STILL returned with `committed:false` and cached, so the model
    always renders and the dashboard can re-fetch it. Works with no API key too —
    the parse degrades to a synthesized building for the chosen facility."""
    if not req.data:
        raise HTTPException(400, "Provide an image data URL in `data`.")

    from agents import bim_support as bs
    from agents.twin_agents import (PLAN_PARSER_SYSTEM, schema_mapper,
                                    validator, graph_writer, scene_generator)

    gw = get_gateway()
    facility = req.facility or bs.infer_facility(req.filename)
    floors = max(1, min(12, int(req.floors or 1)))

    # 1 · Vision parse → bim_model (degrades to a synthesized building w/o a key).
    try:
        result = gw.complete_json_vision(
            tenant_id="build", session_id="build",
            system=PLAN_PARSER_SYSTEM,
            user_text=(f"Facility hint: {facility}. Floors hint: {floors}. Parse "
                       f"this floor plan into the bim_model JSON. Capture every "
                       f"labelled room with its printed dimensions; do not omit rooms."),
            image_urls=[req.data], stub={"_synth": True}, max_tokens=12000,
        )
    except Exception as e:
        raise HTTPException(500, f"parse failed: {e}")

    bm = bs.normalize_bim_model(result, facility, floors)
    facility = bm.get("facility") or facility
    synthesized = bool(bm.get("synthesized"))
    parse_note = gw.last_vision_error if synthesized else None

    tenant = _new_session("twin")
    name = (req.name or bm.get("building", {}).get("name")
            or f"{facility.title()} Twin")

    # 2 · Try to commit a live twin. schema_mapper's BIM branch enriches bm
    #     in-place (furnish + auto-wire), so we must not enrich it again.
    committed, scene, enriched = False, None, False
    try:
        state = new_twin_state(tenant, tenant, twin_name=name)
        state["uploaded_files"] = [{"url": req.data, "type": "image",
                                    "filename": req.filename}]
        state["bim_model"] = bm
        state["domain"] = facility
        state["user_intent"] = f"{facility} building"

        state.update(schema_mapper(state))     # bim_model → drafts (+ enrich)
        enriched = True
        state.update(validator(state))

        if (state.get("validation") or {}).get("ok"):
            from graph.connection import get_driver
            get_driver().verify_connectivity()  # raises fast if DB is offline
            state.update(graph_writer(state))
            committed = bool(state.get("committed"))

        if committed:
            state["twin_id"] = tenant
            state.update(scene_generator(state))  # scene w/ live ids + cache
            scene = state.get("scene_result")
    except Exception as e:
        parse_note = parse_note or f"live twin not committed ({e}); 3-D only."

    # 3 · Fallback scene straight from the bim_model (always renders offline).
    if not scene or not scene.get("nodes"):
        if not enriched:
            bs.enrich_domain(bm, facility)
        scene = bs.bim_model_to_scene(bm, id_map={})
        scene["status"] = "ok"
        scene["twin_id"] = tenant
        bs.save_scene_cache(tenant, scene)     # so the dashboard can re-fetch it

    scene["facility"] = facility
    scene["synthesized"] = synthesized
    scene["parse_note"] = parse_note
    scene["vision_backend"] = getattr(gw, "last_vision_backend", None)

    return {"tenant": tenant, "twin_name": name, "committed": committed,
            "facility": facility, "synthesized": synthesized,
            "parse_note": parse_note, "scene": scene}


@router.get("/twin/sample-scene/{facility}")
def twin_sample_scene(facility: str, floors: int = 1):
    """A no-LLM sample building for a facility — lets the plan panel render (and
    demo the flow) with no API key. Not committed as a twin; purely visual."""
    from agents import bim_support as bs
    floors = max(1, min(12, int(floors or 1)))
    bm = bs.synthesize_bim_model(facility, floors)
    bs.enrich_domain(bm, facility)
    scene = bs.bim_model_to_scene(bm, id_map={})
    scene["status"] = "ok"
    scene["facility"] = facility
    scene["synthesized"] = True
    return {"facility": facility, "scene": scene}


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


# ── Ops reasoning: outlook + cascade (markdown for the Integration Hub) ──
# The hub calls these and renders {report | result} as markdown. Both are
# grounded in the twin's real state and degrade gracefully if the DB is offline.
class OpsAnalysisReq(BaseModel):
    tenant: str
    horizon_min: float = 360.0


def _fmt_when(mins: float) -> str:
    return f"~{round(mins / 60, 1)} h" if mins < 1440 else f"~{round(mins / 1440, 1)} d"


@router.post("/ops/analysis")
def ops_analysis(req: OpsAnalysisReq):
    """A plain-language operational outlook over the horizon — physics forecast for
    machine twins, or a findings-driven outlook for facility twins."""
    tenant = req.tenant
    hours = round(req.horizon_min / 60, 1)
    lines = [f"## Operational outlook — next {hours} h", ""]
    kind = "facility"
    try:
        try:
            from twins.runtime import get_machine_engine
            tw = get_machine_engine().ensure(tenant)
        except Exception:
            tw = None

        if tw is not None:
            kind = "machine"
            pred = tw.predict_forward(horizon_min=req.horizon_min, points=60)
            state = tw.state_dict()
            health = state.get("health")
            sev = pred.get("severity", "nominal")
            rul = sorted(pred.get("rul", []), key=lambda r: r.get("minutes", 9e9))
            lines.append(f"- **Health now:** {round((health or 0) * 100)}%  ·  **Outlook:** {sev}")
            if rul:
                top = rul[0]
                lines.append(f"- **Earliest constraint:** {top['component'].replace('_', ' ')} "
                             f"reaches its limit in {_fmt_when(top.get('minutes', 0))} — this sets the "
                             f"maintenance window.")
                lines += ["", "### Projected limits within the horizon"]
                lines += [f"- {r['component'].replace('_', ' ')}: {_fmt_when(r.get('minutes', 0))}" for r in rul[:5]]
                action = f"Inspect the {top['component'].replace('_', ' ')} before {_fmt_when(top.get('minutes', 0))}."
            else:
                lines.append("- No subsystem is projected to breach a limit within the horizon.")
                action = "No action needed — continue monitoring."
            findings = state.get("findings", [])
            if findings:
                lines += ["", "### Active findings"]
                lines += [f"- [{f.get('severity')}] {f.get('message') or f.get('displayName')}" for f in findings[:6]]
            lines += ["", f"**Recommended:** {action}"]
        else:
            from graph.query import GraphQuery
            findings = GraphQuery().get_findings(tenant)
            crit = [f for f in findings if f.get("severity") == "critical"]
            warn = [f for f in findings if f.get("severity") == "warning"]
            sev = "critical" if crit else "warning" if warn else "nominal"
            lines.append(f"- **Outlook:** {sev}  ·  {len(findings)} active finding(s) "
                         f"({len(crit)} critical, {len(warn)} warning)")
            if findings:
                lines += ["", "### Active findings"]
                lines += [f"- [{f.get('severity')}] {f.get('displayName') or f.get('id')}" for f in findings[:8]]
            worst = (crit or warn or findings or [None])[0]
            action = (f"Prioritise: {worst.get('displayName')}." if worst
                      else "No action needed — the facility is nominal.")
            lines += ["", f"**Recommended:** {action}"]
    except Exception as e:
        lines.append(f"_Live data unavailable ({e}); outlook could not be computed._")

    md = "\n".join(lines)
    return {"tenant": tenant, "kind": kind, "horizon_min": req.horizon_min,
            "report": md, "result": md}


class OpsCascadeReq(BaseModel):
    tenant: str
    entity_id: Optional[str] = None
    fault: Optional[str] = None
    finding_ids: list[str] = []


@router.post("/ops/cascade")
def ops_cascade(req: OpsCascadeReq):
    """Trace how a fault at an entity propagates downstream (DEPENDS_ON / FED_BY),
    returning a markdown fault-propagation report."""
    lines = ["## Fault-propagation analysis", ""]
    affected = []
    source = {"id": req.entity_id, "displayName": None}
    try:
        from graph.query import GraphQuery
        q = GraphQuery()
        entity_id = req.entity_id
        if not entity_id:
            # Fall back to the entity flagged by the first critical/warning finding.
            for f in q.get_findings(req.tenant):
                if f.get("severity") in ("critical", "warning") and f.get("flaggedEntityId"):
                    entity_id = f["flaggedEntityId"]
                    break
        if not entity_id:
            lines.append("No source entity given and no active finding to anchor on. "
                         "Provide `entity_id` to trace a cascade.")
            md = "\n".join(lines)
            return {"tenant": req.tenant, "source": source, "affected": [], "report": md, "result": md}

        root = q.get_node(req.tenant, entity_id)
        name = (root or {}).get("displayName") or entity_id
        source = {"id": entity_id, "displayName": name}
        deps = q.dependents(req.tenant, entity_id, max_depth=4)
        affected = [{"id": d.get("id"), "displayName": d.get("displayName"),
                     "type": (d.get("canonicalType") or "").split("#")[-1]} for d in deps]
        fault_txt = f" ({req.fault})" if req.fault else ""
        lines.append(f"A fault at **{name}**{fault_txt} propagates to **{len(deps)}** "
                     f"downstream asset(s):")
        lines.append("")
        if deps:
            lines += [f"- {a['displayName'] or a['id']}"
                      f"{(' — ' + a['type']) if a['type'] else ''}" for a in affected[:25]]
        else:
            lines.append("- No downstream dependents — the fault is contained to this asset.")
    except Exception as e:
        lines.append(f"_Live topology unavailable ({e}); cascade could not be traced._")

    md = "\n".join(lines)
    return {"tenant": req.tenant, "source": source, "affected": affected,
            "report": md, "result": md}


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
