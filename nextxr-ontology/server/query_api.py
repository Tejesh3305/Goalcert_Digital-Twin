"""
query_api.py — Read-only REST API for the NextXR graph.

Provides tenant-scoped endpoints for:
  - Listing entities by label
  - Getting a single entity + its relationships
  - Listing findings (optionally filtered by entity)
  - Change Log history for an entity
  - KPI stats (counts, latest findings)

All reads — no mutations. Mutations go through the Graph Writer only.

Usage (standalone):
    uvicorn server.query_api:router --reload --port 8001
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure parent packages are importable
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, HTTPException, Query
from graph.connection import get_driver
from graph.query import GraphQuery, LEGAL_LABELS
from changelog.service import ChangeLog
from bus import get_event_bus
import bus as bus_pkg
import db
import historian
import storage

router = APIRouter(prefix="/api/v1", tags=["graph"])

_query = None
_changelog = None


def _get_query() -> GraphQuery:
    global _query
    if _query is None:
        _query = GraphQuery()
    return _query


def _get_changelog() -> ChangeLog:
    global _changelog
    if _changelog is None:
        _changelog = ChangeLog()
    return _changelog


# ── Graceful-degradation helper ─────────────────────────────────────
# Neo4j-backed reads should not 500 the polled UI when the database is down
# (e.g. Docker off). They return empty results + a `degraded: true` flag so the
# frontend can show "database offline" instead of breaking. Real query errors
# (bad label, etc.) are still raised before we get here.

def _is_conn_error(exc: Exception) -> bool:
    """True if this looks like Neo4j being unreachable (vs. a query bug)."""
    from neo4j.exceptions import ServiceUnavailable, SessionExpired, AuthError
    return isinstance(exc, (ServiceUnavailable, SessionExpired, AuthError, OSError))


# ── Entities ────────────────────────────────────────────────────────

@router.get("/entities")
def list_entities(tenant: str, label: str = "PhysicalAsset", limit: int = 100):
    """List entities of a given label for a tenant."""
    if label not in LEGAL_LABELS:
        raise HTTPException(status_code=400,
                            detail=f"Unknown label {label!r}. Legal: {sorted(LEGAL_LABELS)}")
    try:
        q = _get_query()
        nodes = q.list_by_label(tenant, label, limit=limit)
        return {"tenant": tenant, "label": label, "count": len(nodes), "nodes": nodes}
    except Exception as e:
        if _is_conn_error(e):
            return {"tenant": tenant, "label": label, "count": 0, "nodes": [],
                    "degraded": True}
        raise


@router.get("/entities/{node_id}")
def get_entity(node_id: str, tenant: str):
    """Get a single entity by ID, with its outgoing relationships."""
    q = _get_query()
    node = q.get_node(tenant, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
    neighbors = q.neighbors(tenant, node_id)
    return {"node": node, "relationships": neighbors}


# ── Live per-asset telemetry (on-demand, no feed required) ──────────────
# A read-only DynamicsEngine is kept per tenant and advanced by wall-clock on
# each poll, so any asset's sensor values are always available and evolve over
# time — the equipment info panel reads this. Independent of the scripted/live
# feed (which persists to the graph); this never writes.
import threading as _threading
import time as _time

_tele_engines: dict = {}          # tenant -> [engine, last_wall_time]
_tele_lock = _threading.Lock()
_TELE_MAX = 8                      # cap cached engines (evict least-recently-used)


def _tele_engine(tenant: str):
    """Get/create the tenant's telemetry engine and advance it to 'now'."""
    from dynamics import build_dynamics_registry, DynamicsEngine
    now = _time.time()
    entry = _tele_engines.get(tenant)
    if entry is None:
        eng = DynamicsEngine(tenant, build_dynamics_registry(), _get_query(), speed=60.0)
        eng.load_topology()
        for _ in range(20):
            eng.tick(30.0)                     # warm to a plausible mid-run state
        if len(_tele_engines) >= _TELE_MAX:
            oldest = min(_tele_engines, key=lambda t: _tele_engines[t][1])
            _tele_engines.pop(oldest, None)
        _tele_engines[tenant] = [eng, now]
        return eng
    eng, last = entry
    sim = min(30.0, max(0.0, now - last)) * eng.speed   # cap catch-up per poll
    while sim > 0:                                       # sub-step so dt stays small
        d = min(120.0, sim)
        eng.tick(d)
        sim -= d
    entry[1] = now
    return eng


@router.get("/entities/{node_id}/telemetry")
def entity_telemetry(node_id: str, tenant: str):
    """Live, physics-computed telemetry for one asset — always available, no feed
    required. Returns the asset's current signals (short name + value) + status."""
    try:
        with _tele_lock:
            eng = _tele_engine(tenant)
            st = eng._states.get(node_id)
        if st is None:
            return {"node_id": node_id, "signals": [], "status": None, "available": False}
        sigs = []
        for iri, val in (st.signals or {}).items():
            try:
                name = iri.rsplit("#", 1)[-1] if "#" in iri else iri.rsplit("/", 1)[-1]
                sigs.append({"signal": iri, "name": name, "value": round(float(val), 3)})
            except (TypeError, ValueError):
                continue
        return {"node_id": node_id, "signals": sigs, "status": st.status, "available": True}
    except Exception as e:
        if _is_conn_error(e):
            return {"node_id": node_id, "signals": [], "status": None,
                    "available": False, "degraded": True}
        return {"node_id": node_id, "signals": [], "status": None,
                "available": False, "error": str(e)[:160]}


# ── Findings ────────────────────────────────────────────────────────

@router.get("/findings")
def list_findings(tenant: str, entity_id: str = None, limit: int = 50):
    """List Finding nodes, optionally filtered by the entity they flag."""
    try:
        q = _get_query()
        if entity_id:
            findings = q.get_findings(tenant, flagged_entity_id=entity_id)
        else:
            findings = q.get_findings(tenant)
        return {"tenant": tenant, "count": len(findings[:limit]), "findings": findings[:limit]}
    except Exception as e:
        if _is_conn_error(e):
            return {"tenant": tenant, "count": 0, "findings": [], "degraded": True}
        raise


# ── Change Log ──────────────────────────────────────────────────────

@router.get("/changelog/{entity_id}")
def entity_changelog(entity_id: str, tenant: str):
    """Get the Change Log history for a specific entity."""
    cl = _get_changelog()
    events = cl.list_for_entity(tenant, entity_id)
    return {
        "tenant": tenant,
        "entity_id": entity_id,
        "count": len(events),
        "events": [
            {
                "event_id": e.event_id,
                "action": e.action,
                "actor": e.actor,
                "ts": e.ts,
                "field_changes": e.field_changes,
            }
            for e in events
        ],
    }


@router.get("/changelog")
def tenant_changelog(tenant: str, limit: int = 50):
    """Get the latest Change Log events for a tenant."""
    cl = _get_changelog()
    events = cl.list_for_tenant(tenant)
    # Return most recent first
    recent = list(reversed(events[-limit:]))
    return {"tenant": tenant, "count": len(recent), "events": [
        {
            "event_id": e.event_id,
            "entity_id": e.entity_id,
            "action": e.action,
            "actor": e.actor,
            "ts": e.ts,
        }
        for e in recent
    ]}


# ── Stats / KPIs ───────────────────────────────────────────────────

@router.get("/stats")
def stats(tenant: str):
    """Quick KPI summary: entity counts by label, finding counts by severity.
    Degrades gracefully: if Neo4j is down, graph counts are empty but the
    change-log count (relational store) still reports, and `degraded: true`
    is set."""
    label_counts = {}
    severity_counts = {}
    latest_findings = []
    degraded = False

    try:
        driver = get_driver()
        with driver.session() as s:
            for label in ["PhysicalAsset", "Location", "Finding", "Incident",
                          "Process", "Observation", "Capability", "Document",
                          "Actor", "MobileAsset"]:
                rec = s.run(
                    f"MATCH (n:{label} {{tenantId:$t}}) RETURN count(n) AS c",
                    t=tenant,
                ).single()
                cnt = rec["c"] if rec else 0
                if cnt > 0:
                    label_counts[label] = cnt

            recs = s.run(
                "MATCH (f:Finding {tenantId:$t}) RETURN f.severity AS sev, count(f) AS c",
                t=tenant,
            )
            for r in recs:
                if r["sev"]:
                    severity_counts[r["sev"]] = r["c"]

            latest_recs = s.run(
                "MATCH (f:Finding {tenantId:$t}) "
                "RETURN properties(f) AS p ORDER BY f.createdAt DESC LIMIT 5",
                t=tenant,
            )
            latest_findings = [dict(r["p"]) for r in latest_recs]
    except Exception as e:
        if not _is_conn_error(e):
            raise
        degraded = True

    # The change log lives in the relational store — available when Neo4j isn't.
    try:
        event_count = _get_changelog().count(tenant)
    except Exception:
        event_count = 0

    return {
        "tenant": tenant,
        "entity_counts": label_counts,
        "total_entities": sum(label_counts.values()),
        "finding_severity": severity_counts,
        "total_findings": sum(severity_counts.values()),
        "changelog_events": event_count,
        "latest_findings": latest_findings,
        "degraded": degraded,
    }


# ── Health ──────────────────────────────────────────────────────────

@router.get("/topology")
def topology(tenant: str):
    """Return infrastructure nodes + edges with finding counts per entity.
    Findings are NOT returned as nodes — they're aggregated as counts on
    the entities they flag, keeping the graph clean and readable.
    Degrades gracefully to an empty graph when Neo4j is unreachable."""
    try:
        driver = get_driver()
        driver.verify_connectivity()
    except Exception as e:
        if _is_conn_error(e):
            return {"nodes": [], "edges": [], "finding_counts": {}, "degraded": True}
        raise
    nodes = []
    edges = []
    seen = set()

    # Categories that are "infrastructure" (not findings)
    infra_labels = {"PhysicalAsset", "Location", "Incident", "Process",
                    "Actor", "Capability", "Document", "MobileAsset", "Observation"}

    with driver.session() as s:
        # Get infrastructure nodes only
        for label in infra_labels:
            recs = s.run(
                f"MATCH (n:{label} {{tenantId:$t}}) "
                f"RETURN properties(n) AS p LIMIT 50",
                t=tenant,
            )
            for r in recs:
                props = dict(r["p"])
                nid = props.get("id")
                if nid and nid not in seen:
                    seen.add(nid)
                    nodes.append({
                        "id": nid,
                        "label": label,
                        "displayName": props.get("displayName", ""),
                        "status": props.get("status"),
                        "severity": props.get("severity"),
                    })

        # Count findings per flagged entity
        recs = s.run(
            "MATCH (f:Finding {tenantId:$t})-[:FLAGS]->(e) "
            "RETURN e.id AS entity_id, f.severity AS sev, count(f) AS cnt",
            t=tenant,
        )
        finding_counts: dict = {}  # entity_id -> {critical: N, warning: N}
        for r in recs:
            eid = r["entity_id"]
            if eid not in finding_counts:
                finding_counts[eid] = {"critical": 0, "warning": 0, "total": 0}
            sev = r["sev"] or "info"
            finding_counts[eid][sev] = finding_counts[eid].get(sev, 0) + r["cnt"]
            finding_counts[eid]["total"] += r["cnt"]

        # Attach finding counts to nodes
        for n in nodes:
            fc = finding_counts.get(n["id"])
            if fc:
                n["findings"] = fc

        # Get edges between infrastructure nodes only
        recs = s.run(
            "MATCH (a {tenantId:$t})-[r]->(b {tenantId:$t}) "
            "WHERE NOT 'Finding' IN labels(a) AND NOT 'Finding' IN labels(b) "
            "RETURN a.id AS src, type(r) AS rel, b.id AS tgt LIMIT 200",
            t=tenant,
        )
        for r in recs:
            if r["src"] and r["tgt"] and r["src"] in seen and r["tgt"] in seen:
                edges.append({
                    "source": r["src"],
                    "target": r["tgt"],
                    "type": r["rel"],
                })

    return {"nodes": nodes, "edges": edges, "finding_counts": finding_counts}


# ── Event bus ───────────────────────────────────────────────────────

@router.get("/bus/stats")
def bus_stats():
    """Event-bus backend + counters (backend, published, skipped, errors).
    Lets the dashboard show which bus is live (redis / memory / null)."""
    return get_event_bus().stats()


@router.get("/bus/events")
def bus_events(tenant: str, last_id: str = "0", count: int = 100):
    """Read events from a tenant's stream after `last_id`. Returns each event
    plus the stream message id (pass it back as `last_id` to page forward).
    This is the read path the SSE feed and agents will build on."""
    bus = get_event_bus()
    items = bus.read(tenant, last_id=last_id, count=count)
    events = [{"message_id": mid, "event": ev.to_dict()} for mid, ev in items]
    next_id = items[-1][0] if items else last_id
    return {"tenant": tenant, "count": len(events),
            "next_id": next_id, "events": events}


@router.get("/bus/stream")
async def bus_stream(tenant: str, last_id: str = "$"):
    """Server-Sent Events stream of a tenant's live mutations, straight off the
    event bus. The frontend's `useEventStream` hook subscribes here for a true
    push feed (no polling). `last_id="$"` means 'only new events from now'.

    Each SSE message is `data: <json BusEvent>\\n\\n`. A keepalive comment is
    sent periodically so proxies don't close an idle connection."""
    import asyncio
    import json as _json
    from starlette.responses import StreamingResponse

    bus = get_event_bus()
    # "$" (Redis convention for 'new only') -> resolve to the current tail so
    # both Redis and the in-memory bus behave the same.
    if last_id == "$":
        tail = bus.read(tenant, last_id="0", count=10_000)
        cursor = tail[-1][0] if tail else "0"
    else:
        cursor = last_id

    async def gen():
        nonlocal cursor
        # Greet immediately so the client knows the stream is open.
        yield f"event: ready\ndata: {_json.dumps({'tenant': tenant})}\n\n"
        idle = 0
        while True:
            items = bus.read(tenant, last_id=cursor, count=100)
            if items:
                idle = 0
                for mid, ev in items:
                    cursor = mid
                    yield f"data: {_json.dumps(ev.to_dict())}\n\n"
            else:
                idle += 1
                if idle >= 15:  # ~15s with no events -> keepalive comment
                    idle = 0
                    yield ": keepalive\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@router.get("/health")
def health():
    """Health check — ALWAYS returns 200 so the frontend can tell "server up,
    database down" (degraded) from "server unreachable" (network error). The
    body carries the real component status:

      status:   "healthy"   — every dependency up and correctly configured
                "degraded"  — server up, something below is not
      neo4j:    "connected" | "unreachable"
      database: relational store (RDS Postgres, or SQLite in dev) — backend,
                redacted endpoint, pool bounds and reachability
      blobs:    object store (S3, or local filesystem in dev) — backend,
                bucket/prefix and a real write+read+delete probe
      bus:      event-bus backend (redis / memory / null) + whether Redis is
                required by configuration

    Returning 503 here made the whole UI look dead whenever Neo4j was down,
    even though the app shell, twins registry, schema, and event bus all work.
    The frontend shows an amber 'degraded' state instead — which is also why
    the ALB target-group check cannot see a database outage on its own, and
    why AWS_DEPLOYMENT.md §10 asks for a CloudWatch alarm on this payload.

    NOTE on `bus`: an in-memory bus reports healthy: true, because as a process
    it IS working. It is nonetheless wrong on a multi-task deploy — each task
    would see only its own events. `required` and `scale_safe` make that visible
    here rather than leaving it to be discovered as "live updates sometimes stop"."""
    bus_obj = get_event_bus()
    bus_info = dict(bus_obj.stats())
    bus_info["required"] = bus_pkg.redis_required()
    # The one field an operator can alarm on before scaling out.
    bus_info["scale_safe"] = bus_obj.backend in ("redis", "null")
    if bus_obj.backend == "redis":
        bus_info["url"] = bus_pkg.redacted_redis_url()

    db_info = db.info()
    blob_info = storage.info()

    # Which twins' physics THIS task drives. Across the fleet every tenant
    # should appear exactly once; a tenant in two tasks' `owned` lists at the
    # same time means leases are not holding and findings are being written
    # twice (twins/coordinator.py).
    try:
        from twins.coordinator import get_coordinator
        runtime_info = get_coordinator().stats()
    except Exception as e:
        runtime_info = {"enabled": False, "detail": str(e)}

    detail = None
    try:
        get_driver().verify_connectivity()
        neo4j = "connected"
    except Exception as e:
        neo4j = "unreachable"
        detail = str(e)

    # Telemetry history. Reported separately from `database` even though it lives
    # on the same Postgres instance, because they fail independently in the way
    # that matters: the relational store can be perfectly healthy while the
    # historian has no hypertable, no compression and no retention policy — a
    # configuration that works today and fills the disk later. `scale_safe` is
    # the field to alarm on, exactly like the bus's.
    hist_info = historian.info()

    healthy = (neo4j == "connected"
               and db_info.get("status") == "connected"
               and blob_info.get("status") == "connected"
               # Only a *configured* requirement can make the bus fail health;
               # local dev on the in-memory bus stays "healthy".
               and (not bus_pkg.redis_required() or bus_obj.backend == "redis")
               # Same rule for the historian: a dev box on SQLite is healthy, a
               # deploy that declared Timescale mandatory and did not get it is not.
               and (not historian.timescale_required()
                    or hist_info.get("backend") == "timescale"))
    out = {"status": "healthy" if healthy else "degraded",
           "neo4j": neo4j, "database": db_info, "blobs": blob_info,
           "bus": bus_info, "historian": hist_info,
           "twin_runtime": runtime_info}
    if detail:
        out["detail"] = detail
    return out
