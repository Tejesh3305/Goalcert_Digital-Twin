"""
twins_routes.py — the Twin lifecycle API (the 'create a digital twin' surface).

  GET    /api/v1/twins              list all twins
  GET    /api/v1/twins/templates    available domain templates
  POST   /api/v1/twins              create + seed a twin (through the Graph Writer)
  GET    /api/v1/twins/{tenant}     one twin's metadata + a quick entity summary
  DELETE /api/v1/twins/{tenant}     remove a twin (registry + its graph entities)

Creation seeds real entities via the single write path, so a new twin is alive
immediately and the simulated feed can run against it.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from graph.writer import GraphWriter
from graph.query import GraphQuery, LEGAL_LABELS
from graph.connection import get_driver
from changelog.service import ChangeLog
from twins import TwinRegistry, TEMPLATES

router = APIRouter(prefix="/api/v1/twins", tags=["twins"])

_registry: Optional[TwinRegistry] = None
_writer: Optional[GraphWriter] = None
_query: Optional[GraphQuery] = None


def _get_registry() -> TwinRegistry:
    global _registry
    if _registry is None:
        _registry = TwinRegistry()
    return _registry


def _get_writer() -> GraphWriter:
    global _writer
    if _writer is None:
        _writer = GraphWriter(changelog=ChangeLog())
    return _writer


def _get_query() -> GraphQuery:
    global _query
    if _query is None:
        _query = GraphQuery()
    return _query


def _live_state(tenant: str) -> dict:
    """The hub-shaped live snapshot: {latest:{signal:number}, findings:[{id,
    displayName,severity}], health:0..1}. Machine twins read the physics runtime;
    facility twins derive health from their live findings."""
    # Machine-domain twin? → physics runtime frame + findings + health.
    try:
        from twins.runtime import get_machine_engine
        tw = get_machine_engine().ensure(tenant)
    except Exception:
        tw = None
    if tw is not None:
        st = tw.state_dict()
        findings = [{"id": f.get("behaviorId") or f.get("signal") or "",
                     "displayName": f.get("displayName") or f.get("message"),
                     "severity": f.get("severity")} for f in st.get("findings", [])]
        return {"latest": st.get("latest", {}), "findings": findings,
                "health": st.get("health")}

    # Facility twin → findings from the graph; health from their severity.
    try:
        fs = _get_query().get_findings(tenant)
    except Exception:
        fs = []
    findings = [{"id": f.get("id"), "displayName": f.get("displayName"),
                 "severity": f.get("severity")} for f in fs]
    crit = sum(1 for f in fs if f.get("severity") == "critical")
    warn = sum(1 for f in fs if f.get("severity") == "warning")
    health = round(max(0.0, 1.0 - min(100, crit * 25 + warn * 8) / 100.0), 3)
    return {"latest": {}, "findings": findings, "health": health}


def _entity_summary(tenant: str) -> dict:
    """Quick per-label counts so the UI can show a twin's size at a glance."""
    q = _get_query()
    counts = {}
    total = 0
    for label in ("PhysicalAsset", "Location", "Finding", "Incident",
                  "Process", "Observation", "Capability", "Document",
                  "Actor", "MobileAsset"):
        try:
            n = len(q.list_by_label(tenant, label, limit=1000))
        except Exception:
            n = 0
        if n:
            counts[label] = n
            total += n
    return {"by_label": counts, "total": total}


# ── Request models ─────────────────────────────────────────────────

class CreateTwinRequest(BaseModel):
    name: str = Field(..., description="Human name, e.g. 'Melbourne Plant'")
    domain: str = Field("hvac", description="Template key: 'hvac' or 'blank'")
    actor: str = "twin-factory"


# ── Endpoints ──────────────────────────────────────────────────────

@router.get("/templates")
def list_templates():
    """Available domain templates for new twins."""
    return {"templates": [{"key": k, **v} for k, v in TEMPLATES.items()]}


@router.get("")
def list_twins():
    """List all registered twins, each with a quick entity summary.

    Probe the graph ONCE for the whole listing. _entity_summary runs ten label
    queries per twin, and with the database unreachable every one of them burns
    the driver's full retry budget before giving up — ~4s each, so ~40s per twin.
    Measured against a 14-twin registry with Neo4j down that made this endpoint
    take ~9 MINUTES. It is the first call the UI makes, so the whole app looked
    frozen rather than degraded. One probe up front keeps the failure fast: the
    registry rows still render, just without counts.
    """
    reg = _get_registry()
    try:
        get_driver().verify_connectivity()
        graph_up = True
    except Exception:
        graph_up = False

    out = []
    for t in reg.list():
        d = t.to_dict()
        d["summary"] = (_entity_summary(t.tenant_id) if graph_up
                        else {"by_label": {}, "total": 0})
        out.append(d)
    # `degraded` lets the UI say "database offline" instead of implying the
    # twins are genuinely empty.
    return {"count": len(out), "twins": out, "degraded": not graph_up}


@router.post("")
def create_twin(req: CreateTwinRequest):
    """Create + seed a new twin through the Graph Writer.

    Seeding writes entities to Neo4j, so the database must be reachable. If it
    isn't (e.g. Docker not running), we fail fast with a clear 503 and do NOT
    register an orphan twin — rather than a raw 500."""
    # 1. Require Neo4j up front — seeding can't work without it.
    try:
        get_driver().verify_connectivity()
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Database is offline, so a twin can't be seeded. Start the "
                   "database first: run `docker compose up -d` (or ./start.ps1), "
                   "wait for Neo4j on :7687, then try again.",
        )

    reg = _get_registry()
    writer = _get_writer()

    # 2. Ensure schema/constraints exist before seeding (idempotent).
    try:
        from graph import schema
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            # close=False so the shared driver stays alive for the writer below.
            schema.apply_schema(dry_run=False, close=False)
    except Exception:
        pass  # best-effort; the seed below surfaces any real error

    # 3. Create the registry row + seed the graph. On seed failure, roll back
    #    the registry row so we never leave an empty orphan twin.
    try:
        twin = reg.create(name=req.name, domain=req.domain,
                          writer=writer, actor=req.actor)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Twin seeding failed: {e}")

    d = twin.to_dict()
    d["summary"] = _entity_summary(twin.tenant_id)
    # Top-level id fields so the hub can read tenant from any of tenant_id|id|tenant.
    return {"status": "created", "twin": d,
            "tenant_id": twin.tenant_id, "tenant": twin.tenant_id, "id": twin.tenant_id}


@router.get("/{tenant}")
def get_twin(tenant: str):
    """One twin's metadata + entity summary."""
    reg = _get_registry()
    twin = reg.get(tenant)
    if twin is None:
        raise HTTPException(status_code=404, detail=f"Twin '{tenant}' not found")
    d = twin.to_dict()
    d["summary"] = _entity_summary(tenant)
    # Augment with the hub-shaped live snapshot (latest/findings/health) so the
    # Integration Hub can poll this one endpoint for live twin state.
    return {"twin": d, **_live_state(tenant)}


@router.delete("/{tenant}")
def delete_twin(tenant: str):
    """Remove a twin: its graph entities (DETACH DELETE) and registry row.
    The Change Log is intentionally NOT purged — it's the audit record."""
    reg = _get_registry()
    if reg.get(tenant) is None:
        raise HTTPException(status_code=404, detail=f"Twin '{tenant}' not found")

    driver = get_driver()
    with driver.session() as s:
        s.run("MATCH (n {tenantId:$t}) DETACH DELETE n", t=tenant)
    reg.delete(tenant)
    return {"status": "deleted", "tenant": tenant}
