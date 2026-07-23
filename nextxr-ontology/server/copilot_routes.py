"""
copilot_routes.py — the embedded agent surface of the digital twin.

These are NOT a proxy to an external agent service. Every agent runs in this
process and reads the twin's own machine runtime (twins/runtime.py) directly, so
an agent answer is grounded in the same live physics state the 3-D scene and the
findings engine are reading — no second copy of the truth, no network hop.

  POST /api/v1/copilot/build-twin/message    conversational twin intake
  POST /api/v1/copilot/build-twin/spec       photo + description -> twin spec

  GET  /api/v1/copilot/narrate/{tenant}      live one-line console narration
  POST /api/v1/copilot/narrate               narration on any snapshot
  POST /api/v1/copilot/asset                 click an asset in 3-D -> AI status
  POST /api/v1/copilot/predict-alert         proactive time-to-limit alert

  POST /api/v1/copilot/diagnosis             diagnose the twin
  POST /api/v1/copilot/analysis              present state + horizon forecast
  POST /api/v1/copilot/cascade               cross-subsystem propagation

  POST /api/v1/copilot/work-order            compliant work order
  POST /api/v1/copilot/procurement           work order -> parts, costs, lead times
  POST /api/v1/copilot/incident-report       formal incident report
  POST /api/v1/copilot/procedure             ordered repair procedure (trainer)

  POST /api/v1/copilot/troubleshoot          AI mechanic, multi-turn
  POST /api/v1/copilot/dashboard-chat        live-state Q&A

  GET  /api/v1/copilot/knowledge/search      fault library / compliance / memory
  POST /api/v1/copilot/knowledge/remember    write a resolution back to memory
  GET  /api/v1/copilot/health                is Claude wired up, or stubbing?

ONE endpoint per agent. Each accepts EITHER a live `tenant` or a raw telemetry
snapshot, and reports which it used as `source` — the prototype's split
live/snapshot endpoint pairs are collapsed here, because they were the same
agent twice.

Every response carries an `ai` block saying whether the answer came from Claude
or from the deterministic fallback. A stub must never look like real reasoning.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from copilot import agents as A
from copilot.config import copilot_status
from copilot.knowledge import get_knowledge_store
from twins.runtime import get_machine_engine

router = APIRouter(prefix="/api/v1/copilot", tags=["copilot"])

HORIZONS = {
    "1 hour": 60, "2 hours": 120, "6 hours": 360, "12 hours": 720,
    "24 hours": 1440, "3 days": 4320, "1 week": 10080, "2 weeks": 20160,
    "1 month": 43200,
}


# ── Request shapes ────────────────────────────────────────────────────────

class TwinContext(BaseModel):
    """Identifies WHAT the agent should reason about.

    Give `tenant` to run against a live machine twin — the agent then reads its
    real diagnostics and prediction. Otherwise supply the telemetry directly and
    the same agent runs on the snapshot.
    """
    tenant: str | None = None
    machine: str = ""
    domain: str = ""
    horizon_label: str = "2 hours"
    # snapshot form
    latest: dict = {}
    findings: list = []
    components: list = []
    context: str = ""


class AssetRequest(TwinContext):
    asset: dict = {}      # {id, name, type, status, metrics: {label: value}}


class ChatRequest(TwinContext):
    messages: list = []   # [{role: 'user'|'assistant', content: str}]
    message: str = ""
    history: list = []
    snapshot: dict = {}


class ProcedureRequest(BaseModel):
    machine: str = "Machine"
    domain: str = ""
    fault: str = "none"
    title: str = ""
    context: str = ""


class BuildTwinMessage(BaseModel):
    history: list[dict] = []
    message: str = ""


class BuildTwinSpec(BaseModel):
    description: str = ""
    image_b64: str | None = None
    filename: str = "machine.png"


class RememberRequest(BaseModel):
    domain: str
    title: str
    diagnosis: str
    resolution: str
    metadata: dict | None = None


# ── Binding to the live twin runtime ──────────────────────────────────────

class Bound:
    """What an agent needs about the subject, resolved from either a live twin
    or a caller-supplied snapshot."""

    def __init__(self, req: TwinContext):
        self.horizon_min = HORIZONS.get(req.horizon_label, 120)
        self.horizon_label = req.horizon_label
        self.twin = get_machine_engine().ensure(req.tenant) if req.tenant else None
        if self.twin is not None:
            self.source = "live"
            self.machine = req.machine or self.twin.name
            self.domain = req.domain or self.twin.domain
            self.diagnostics = self.twin.diagnostics()
            self.state = self.twin.state_dict()
        else:
            self.source = "snapshot"
            self.machine = req.machine or "Machine"
            self.domain = req.domain
            self.diagnostics = A.snapshot_diagnostics(
                self.machine, req.latest, req.findings, req.components, req.domain)
            self.state = {"latest": req.latest, "findings": req.findings}
        self._prediction: dict | None = None

    @property
    def prediction(self) -> dict:
        """Forward projection from the twin's own physics. Empty for snapshots —
        there is no state object to integrate forward."""
        if self._prediction is None:
            if self.twin is not None:
                try:
                    self._prediction = self.twin.predict_forward(
                        horizon_min=self.horizon_min)
                except Exception:  # noqa: BLE001 — an agent still runs without it
                    self._prediction = {}
            else:
                self._prediction = {}
        return self._prediction

    def meta(self) -> dict:
        return {"source": self.source, "machine": self.machine,
                "domain": self.domain}


def _live_or_404(tenant: str):
    tw = get_machine_engine().ensure(tenant)
    if tw is None:
        raise HTTPException(
            status_code=404,
            detail=f"'{tenant}' is not a live machine-domain twin.")
    return tw


# ══════════════════════════════════════════════════════════════════════════
# Twin provisioning
# ══════════════════════════════════════════════════════════════════════════

@router.post("/build-twin/message")
def build_twin_message(req: BuildTwinMessage):
    """Twin Builder: converse to learn what the operator wants to twin."""
    with A.agent_trace() as t:
        reply = A.build_twin_reply(req.history, req.message)
    return {**reply.model_dump(), "ai": t}


@router.post("/build-twin/spec")
def build_twin_spec(req: BuildTwinSpec):
    """Vision Agent: a photo + a description -> a structured twin spec
    (machine, components, sensors with live signal keys and 3-D hotspots)."""
    with A.agent_trace() as t:
        spec = A.vision_to_twin_spec(req.image_b64, req.description, req.filename)
    return {"spec": spec.model_dump(), "ai": t}


# ══════════════════════════════════════════════════════════════════════════
# Live monitoring
# ══════════════════════════════════════════════════════════════════════════

@router.get("/narrate/{tenant}")
def narrate_live(tenant: str, machine: str = ""):
    """Narration on a live twin — convenient for the console to poll."""
    tw = _live_or_404(tenant)
    state = tw.state_dict()
    with A.agent_trace() as t:
        text = A.narrate_sensors(state, machine or tw.name)
    return {"narration": text, "tenant": tenant, "source": "live",
            "domain": tw.domain, "ai": t}


@router.post("/narrate")
def narrate(req: TwinContext):
    """Narration on a live twin or on any telemetry snapshot."""
    b = Bound(req)
    with A.agent_trace() as t:
        text = A.narrate_sensors(b.state, b.machine)
    return {"narration": text, **b.meta(), "ai": t}


@router.post("/asset")
def asset(req: AssetRequest):
    """The operator clicked one component in the 3-D scene — explain it."""
    b = Bound(req)
    with A.agent_trace() as t:
        status = A.asset_status(req.asset, b.machine, b.domain)
    return {"status": status, **b.meta(), "ai": t}


@router.post("/predict-alert")
def predict_alert(req: TwinContext):
    """Proactive alert when an operating limit is projected to be crossed."""
    b = Bound(req)
    pred = b.prediction
    with A.agent_trace() as t:
        alert = A.predictive_alert(pred, b.machine)
    return {
        "alert": alert,
        "prediction_summary": {"horizon_min": b.horizon_min,
                               "rul": pred.get("rul", []),
                               "severity": pred.get("severity", "nominal")},
        **b.meta(), "ai": t,
    }


# ══════════════════════════════════════════════════════════════════════════
# Diagnosis & prediction
# ══════════════════════════════════════════════════════════════════════════

@router.post("/diagnosis")
def diagnosis(req: TwinContext):
    """Diagnose the twin: component health, out-of-band sensors, root cause and
    actions — grounded in the fault library and the domain's compliance regime."""
    b = Bound(req)
    with A.agent_trace() as t:
        report = A.diagnosis_agent(b.diagnostics, b.machine, b.domain,
                                   source=b.source)
    return {"report": report, "diagnostics": b.diagnostics, **b.meta(), "ai": t}


@router.post("/analysis")
def analysis(req: TwinContext):
    """Present state plus the projected assessment over the chosen horizon.

    On a live twin this uses the twin's own forward physics. On a snapshot there
    is no state to integrate, so it falls back to a qualitative forecast.
    """
    b = Bound(req)
    if b.source == "live":
        pred = b.prediction
        with A.agent_trace() as t:
            report = A.analysis_agent(b.diagnostics, pred, b.machine,
                                      b.horizon_label, b.domain)
        return {"report": report, "horizon_label": b.horizon_label,
                "horizon_min": b.horizon_min, "diagnostics": b.diagnostics,
                "prediction": pred, "forecast_basis": "physics", **b.meta(), "ai": t}
    with A.agent_trace() as t:
        report = A.forecast_snapshot(b.machine, b.domain, req.latest,
                                     b.horizon_label, req.context)
    return {"report": report, "horizon_label": b.horizon_label,
            "horizon_min": b.horizon_min, "diagnostics": b.diagnostics,
            "prediction": {}, "forecast_basis": "qualitative", **b.meta(), "ai": t}


@router.post("/cascade")
def cascade(req: TwinContext):
    """How degradation in one subsystem propagates to the others."""
    b = Bound(req)
    with A.agent_trace() as t:
        report = A.cascade_analysis(b.diagnostics, b.prediction, b.machine, b.domain)
    return {"cascade_analysis": report, "diagnostics": b.diagnostics,
            **b.meta(), "ai": t}


# ══════════════════════════════════════════════════════════════════════════
# Maintenance & compliance output
# ══════════════════════════════════════════════════════════════════════════

@router.post("/work-order")
def work_order(req: TwinContext):
    """A compliant maintenance work order from the current diagnosis."""
    b = Bound(req)
    with A.agent_trace() as t:
        wo = A.generate_work_order(b.diagnostics, b.machine, b.domain)
    return {"work_order": wo.model_dump(), "diagnostics": b.diagnostics,
            **b.meta(), "ai": t}


@router.post("/procurement")
def procurement(req: TwinContext):
    """Work order -> the parts, quantities, costs and lead times to fulfil it."""
    b = Bound(req)
    with A.agent_trace() as t_wo:
        wo = A.generate_work_order(b.diagnostics, b.machine, b.domain)
    with A.agent_trace() as t:
        parts = A.parts_procurement_agent(wo.model_dump(), b.machine, b.domain)
    return {"work_order": wo.model_dump(), "procurement": parts.model_dump(),
            **b.meta(), "ai": t, "ai_work_order": t_wo}


@router.post("/incident-report")
def incident_report(req: TwinContext):
    """A formal incident report with regulatory closure references."""
    b = Bound(req)
    with A.agent_trace() as t:
        report = A.generate_incident_report(
            b.diagnostics, b.diagnostics.get("findings", []), b.machine, b.domain)
    return {"report": report.model_dump(), **b.meta(), "ai": t}


@router.post("/procedure")
def procedure(req: ProcedureRequest):
    """An ordered repair procedure where every step carries its skip and
    wrong-order consequences — the data the interactive trainer runs on."""
    with A.agent_trace() as t:
        p = A.build_procedure(req.machine, req.domain, req.fault,
                              req.title, req.context)
    return {"procedure": p.model_dump(), "ai": t}


# ══════════════════════════════════════════════════════════════════════════
# Conversational
# ══════════════════════════════════════════════════════════════════════════

@router.post("/troubleshoot")
def troubleshoot(req: ChatRequest):
    """The AI Mechanic — multi-turn diagnostic questioning."""
    b = Bound(req)
    with A.agent_trace() as t:
        reply = A.troubleshoot_chat(req.history or req.messages, req.message,
                                    b.diagnostics, b.machine, b.domain)
    return {**reply.model_dump(), **b.meta(), "ai": t}


@router.post("/dashboard-chat")
def dashboard_chat(req: ChatRequest):
    """Live-state Q&A over the twin's current telemetry and findings."""
    b = Bound(req)
    snapshot = req.snapshot or {"latest": b.diagnostics.get("latest", {}),
                                "findings": b.diagnostics.get("findings", []),
                                "sensors": b.diagnostics.get("sensors", []),
                                "components": b.diagnostics.get("components", [])}
    msgs = req.messages or ([{"role": "user", "content": req.message}]
                            if req.message else [])
    with A.agent_trace() as t:
        reply = A.dashboard_chat(msgs, snapshot, b.machine, b.domain)
    return {"reply": reply, **b.meta(), "ai": t}


# ══════════════════════════════════════════════════════════════════════════
# Knowledge / learning loop
# ══════════════════════════════════════════════════════════════════════════

@router.get("/knowledge/search")
def knowledge_search(query: str, domain: str | None = None,
                     category: str | None = None, top_k: int = 5):
    """Semantic search over the fault library, compliance rules and the
    incidents this platform has already resolved."""
    try:
        results = get_knowledge_store().search(query, domain=domain,
                                               category=category, top_k=top_k)
        return {"query": query, "results": results, "total": len(results)}
    except Exception as e:  # noqa: BLE001
        return {"query": query, "results": [], "total": 0, "error": str(e)}


@router.post("/knowledge/remember")
def knowledge_remember(req: RememberRequest):
    """Close the learning loop: a resolved incident becomes retrievable context
    for every future diagnosis on this domain."""
    entry_id = A.remember_resolution(req.domain, req.title, req.diagnosis,
                                     req.resolution, req.metadata)
    return {"stored": bool(entry_id), "entry_id": entry_id,
            "stats": A.knowledge_stats()}


@router.get("/health")
def health():
    """Whether the embedded agents are reasoning with Claude or stubbing, plus
    the state of the knowledge store."""
    return {"copilot": copilot_status(), "knowledge": A.knowledge_stats(),
            "domains": sorted(A.DOMAIN_CONTEXT)}
