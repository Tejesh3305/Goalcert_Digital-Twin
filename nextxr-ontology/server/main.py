#!/usr/bin/env python3
"""
main.py — NextXR Digital Twin orchestration server.

Ties everything together:
  1. Mounts the read-only Graph Query API (/api/v1/...)
  2. Serves the live dashboard (static HTML at /)
  3. Runs a background findings loop (feed → registry → writer → graph)
  4. Provides /api/v1/feed/start and /api/v1/feed/status to control the loop

Usage:
    cd nextxr-ontology
    python -m server.main

    # Then open http://localhost:8000 for the dashboard
    # API docs at http://localhost:8000/docs
"""

from __future__ import annotations

import contextlib
import io
import sys
import threading
import time
from pathlib import Path

# Ensure imports work
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from server.auth import AuthMiddleware

from graph.connection import get_driver, close_driver
from graph.writer import GraphWriter, Rel
from graph.query import GraphQuery
from changelog.service import ChangeLog
from behaviors.registry import BehaviorRegistry, Tier
from behaviors.hvac import (
    TemperatureThresholdRule,
    TemperatureZScoreBaseline,
    ThermalPhysicsBehavior,
)
from behaviors.cfp.tier_c_power import (
    UPSOnBatteryRule, GeneratorFuelLowRule, TransformerOverTempRule,
)
from behaviors.cfp.tier_c_fire import SmokeAlarmRule
from behaviors.cfp.tier_c_security import DoorForcedRule, RepeatedDenyRule
from behaviors.cfp.tier_c_water import (
    LeakDetectedRule, TankLowLevelRule, ContinuousFlowLeakRule,
)
from behaviors.cfp.tier_c_network import HeartbeatLossRule
from behaviors.cfp.tier_c_filter import FilterCloggedRule
from behaviors.cfp.tier_b_chiller import ChillerCOPBaseline
from behaviors.cfp.tier_b_vibration import PumpVibrationBaseline
from feed.simulate import simulate_temperature, FindingsLoop
from behaviors.diagnosis import DiagnosisEngine

from server.query_api import router as query_router
from server.write_api import router as write_router
from server.schema_routes import router as schema_router
from server.twins_routes import router as twins_router
from server.agent_routes import router as agent_router

# ── App setup ───────────────────────────────────────────────────────

app = FastAPI(
    title="NextXR Digital Twin",
    version="1.0.0",
    description="Live dashboard + REST API for the NextXR Digital Twin.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuthMiddleware)

app.include_router(query_router)
app.include_router(write_router)
app.include_router(schema_router)
app.include_router(twins_router)
app.include_router(agent_router)


# ── Global DB-down handler ──────────────────────────────────────────
# Any endpoint that touches Neo4j while it's unreachable (e.g. Docker off)
# raises a driver connection error. Instead of a raw 500, convert it once,
# here, into a clean 503 with guidance — so every current and future
# DB-backed route degrades the same friendly way.
from neo4j.exceptions import ServiceUnavailable, SessionExpired, AuthError  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402


@app.exception_handler(ServiceUnavailable)
@app.exception_handler(SessionExpired)
@app.exception_handler(AuthError)
async def _neo4j_down_handler(request, exc):
    return JSONResponse(
        status_code=503,
        content={"detail": "Database is offline. Start it with "
                           "`docker compose up -d` (or ./start.ps1), wait for "
                           "Neo4j on :7687, then retry.",
                 "neo4j": "unreachable"},
    )


CORE = "https://ontology.nextxr.io/v3/core#"
HVAC = "https://ontology.nextxr.io/v3/hvac#"
DEFAULT_TENANT = "demo-tenant"

# ── Feed loop state ─────────────────────────────────────────────────

_feed_state = {
    "running": False,
    "tenant": DEFAULT_TENANT,
    "samples_processed": 0,
    "findings_emitted": 0,
    "latest_value": None,
    "latest_timestamp": None,
    "signals": {},          # per-signal latest: {"cfp:upsSoC": 95.2, ...}
    "domain": "hvac",       # twin domain for the active feed
    "error": None,
}
_feed_lock = threading.Lock()


# ── Seed + Feed ─────────────────────────────────────────────────────

def _ensure_schema():
    """Apply graph schema (idempotent, silenced). Verifies connectivity first
    so we fail fast (one timeout) when Neo4j is down instead of grinding through
    every constraint statement."""
    get_driver().verify_connectivity()  # raises fast if unreachable
    from graph import schema
    with contextlib.redirect_stdout(io.StringIO()):
        # close=False: keep the shared driver alive for cached writers/queries.
        schema.apply_schema(dry_run=False, close=False)


def _seed_facility(writer: GraphWriter, tenant: str) -> str:
    """Resolve the asset the feed should target for this tenant.

    Order of preference:
      1. If the tenant is a registered twin with a seed_asset_id, use it.
      2. Else find an existing AirHandler / PhysicalAsset in the graph.
      3. Else seed a demo HVAC facility (back-compat for the default tenant).
    Returns the target asset's node_id."""
    # 1. Registered twin?
    try:
        from twins import TwinRegistry
        twin = TwinRegistry().get(tenant)
        if twin and twin.seed_asset_id:
            return twin.seed_asset_id
    except Exception:
        pass

    # 2. Existing asset?
    query = GraphQuery()
    existing = query.list_by_label(tenant, "PhysicalAsset", limit=50)
    if existing:
        for node in existing:
            if "AHU" in node.get("displayName", ""):
                return node["id"]
        return existing[0]["id"]

    # 3. Seed a demo facility.
    writer.create(
        tenant_id=tenant, canonical_type=CORE + "Site",
        actor="seed", properties={"displayName": "Demo Plant"},
    )
    space = writer.create(
        tenant_id=tenant, canonical_type=CORE + "Space",
        actor="seed", properties={"displayName": "Server Room 1"},
    )
    ahu = writer.create(
        tenant_id=tenant, canonical_type=HVAC + "AirHandler",
        actor="seed",
        properties={"displayName": "AHU-01", "status": "running", "setpoint": 22.0},
        relationships=[Rel("hvac:servesSpace", space.node_id)],
    )
    return ahu.node_id


def _detect_twin_domain(tenant: str) -> str:
    """Look up the domain template for a tenant. Returns 'hvac', 'generic-facility', etc."""
    try:
        from twins import TwinRegistry
        twin = TwinRegistry().get(tenant)
        if twin:
            return twin.domain
    except Exception:
        pass
    return "hvac"  # default for legacy/unregistered tenants


def _resolve_cfp_assets(tenant: str, query) -> dict:
    """Find seeded CFP assets by display name pattern for the feed simulator."""
    try:
        entities = query.list_by_label(tenant, "PhysicalAsset", limit=100)
    except Exception:
        return {}  # graceful degradation — feed runs with empty asset map
    mapping = {}
    name_map = {
        "UPS": "ups", "TX-": "transformer", "GenSet": "generator",
        "Filter-": "filter", "Chiller": "chiller", "Pump": "pump",
        "Main Entry": "door", "Edge-": "edge_node",
    }
    for e in entities:
        name = e.get("displayName", "")
        for pattern, role in name_map.items():
            if pattern in name and role not in mapping:
                mapping[role] = e["id"]
                break
    # Sensors are under PhysicalAsset too (Sensor subclasses)
    for e in entities:
        name = e.get("displayName", "")
        if "Smoke" in name and "smoke_detector" not in mapping:
            mapping["smoke_detector"] = e["id"]
    # Water tank
    for e in entities:
        name = e.get("displayName", "")
        if "Tank" in name and "water_tank" not in mapping:
            mapping["water_tank"] = e["id"]
    return mapping


def _build_registry() -> BehaviorRegistry:
    """Build a fresh behavior registry with all HVAC + CFP behaviors."""
    registry = BehaviorRegistry()
    # HVAC behaviors (existing)
    registry.register(TemperatureThresholdRule(offset_c=3.0, duration_minutes=3.0))
    registry.register(TemperatureZScoreBaseline(warmup=12, z_threshold=3.0))
    registry.register(ThermalPhysicsBehavior())
    # CFP behaviors
    registry.register(UPSOnBatteryRule())
    registry.register(GeneratorFuelLowRule())
    registry.register(TransformerOverTempRule())
    registry.register(SmokeAlarmRule())
    registry.register(DoorForcedRule())
    registry.register(RepeatedDenyRule())
    registry.register(LeakDetectedRule())
    registry.register(TankLowLevelRule())
    registry.register(ContinuousFlowLeakRule())
    registry.register(HeartbeatLossRule())
    registry.register(FilterCloggedRule())
    registry.register(ChillerCOPBaseline())
    registry.register(PumpVibrationBaseline())
    return registry


def _run_feed_loop(tenant: str, ahu_id: str, cl: ChangeLog):
    """Background thread: run simulated feed through behavior registry.
    Detects twin domain and runs the appropriate simulator.
    Loops continuously with fresh simulations until stopped."""
    global _feed_state

    try:
        writer = GraphWriter(changelog=cl)
        query = GraphQuery()

        domain = _detect_twin_domain(tenant)
        run_number = 0

        with _feed_lock:
            _feed_state["domain"] = domain
            _feed_state["signals"] = {}

        while True:
            # Check if stopped
            with _feed_lock:
                if not _feed_state["running"]:
                    break

            # Fresh behaviors each run so baselines reset cleanly
            registry = _build_registry()
            loop = FindingsLoop(registry, writer, query)

            # Choose sample source based on twin domain
            if domain == "generic-facility":
                from feed.simulate_cfp import simulate_facility
                cfp_assets = _resolve_cfp_assets(tenant, query)
                samples = list(simulate_facility(tenant, cfp_assets))
            else:
                # Default HVAC feed
                samples = list(simulate_temperature(tenant, ahu_id,
                                                    setpoint=22.0, minutes=60))

            with _feed_lock:
                _feed_state["run"] = run_number

            for sample in samples:
                with _feed_lock:
                    if not _feed_state["running"]:
                        break

                outcomes = loop.process(sample)

                with _feed_lock:
                    _feed_state["samples_processed"] += 1
                    _feed_state["latest_value"] = sample.value
                    _feed_state["latest_timestamp"] = sample.timestamp.isoformat()
                    _feed_state["findings_emitted"] += len(outcomes)
                    _feed_state["signals"][sample.signal] = round(sample.value, 2)

                # Pace the feed — 0.5s per sample so the dashboard can show progress
                time.sleep(0.5)

            # After each run, invoke diagnosis engine on accumulated findings
            try:
                all_findings = query.get_findings(tenant)
                if all_findings:
                    finding_ids = [f["id"] for f in all_findings
                                   if f.get("id") and not f.get("groupedInto")]
                    if finding_ids:
                        # For CFP twins, target the first affected asset
                        affected_id = ahu_id
                        engine = DiagnosisEngine(writer, query)
                        result = engine.analyze(tenant, finding_ids, affected_id)
                        with _feed_lock:
                            _feed_state["diagnosis"] = {
                                "incident_id": result.incident_id,
                                "diagnosis_id": result.diagnosis_id,
                                "recommendation_id": result.recommendation_id,
                                "action_id": result.action_id,
                                "findings_grouped": result.findings_grouped,
                            }

                        # LLM-enhanced diagnosis (augments, does not replace, the
                        # deterministic pipeline above).
                        if result.incident_id and finding_ids:
                            try:
                                from agents.operational_graph import app as ops_app
                                from agents.state import new_operational_state
                                ops_sid = f"ops-{tenant}-{result.incident_id}"
                                ops_state = new_operational_state(
                                    tenant_id=tenant,
                                    session_id=ops_sid,
                                    incident_id=result.incident_id,
                                    finding_ids=finding_ids,
                                    affected_entity_id=affected_id,
                                )
                                ops_app.invoke(ops_state, thread_id=ops_sid)
                            except Exception:
                                pass  # LLM diagnosis is best-effort
            except Exception:
                pass  # Diagnosis is best-effort, don't crash the feed

            run_number += 1

            # Brief pause between runs
            with _feed_lock:
                if not _feed_state["running"]:
                    break
            time.sleep(2)

        with _feed_lock:
            _feed_state["running"] = False

    except Exception as e:
        with _feed_lock:
            _feed_state["running"] = False
            _feed_state["error"] = str(e)


# ── Feed control endpoints ──────────────────────────────────────────

@app.post("/api/v1/feed/start")
def start_feed(tenant: str = DEFAULT_TENANT):
    """Start the simulated telemetry feed + findings loop."""
    global _feed_state

    with _feed_lock:
        if _feed_state["running"]:
            return {"status": "already_running", "state": dict(_feed_state)}
        # Mark as starting immediately to prevent TOCTOU race
        _feed_state["running"] = True

    try:
        _ensure_schema()
        cl = ChangeLog()
        writer = GraphWriter(changelog=cl)
        ahu_id = _seed_facility(writer, tenant)
    except Exception as e:
        with _feed_lock:
            _feed_state["running"] = False
            _feed_state["error"] = str(e)
        return {"status": "error", "detail": str(e)}

    with _feed_lock:
        _feed_state.update({
            "running": True,
            "tenant": tenant,
            "samples_processed": 0,
            "findings_emitted": 0,
            "latest_value": None,
            "latest_timestamp": None,
            "signals": {},
            "domain": "",
            "error": None,
        })

    thread = threading.Thread(target=_run_feed_loop, args=(tenant, ahu_id, cl), daemon=True)
    thread.start()

    return {"status": "started", "tenant": tenant, "ahu_id": ahu_id}


@app.get("/api/v1/feed/status")
def feed_status():
    """Get the current state of the feed loop."""
    with _feed_lock:
        return dict(_feed_state)


@app.post("/api/v1/feed/stop")
def stop_feed():
    """Stop the feed loop."""
    with _feed_lock:
        _feed_state["running"] = False
    return {"status": "stopped"}


# ── Frontend serving (built React app) ──────────────────────────────
#
# The React app (frontend/) builds to frontend/dist. We serve its static
# assets and fall back to index.html for any non-API path so client-side
# routing works (SPA). If the app hasn't been built yet, we serve a friendly
# placeholder telling you how to build it.

FRONTEND_DIST = ROOT.parent / "frontend" / "dist"

if (FRONTEND_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"),
              name="assets")


_PLACEHOLDER = """<!doctype html><html><head><meta charset=utf-8>
<title>NextXR — build the frontend</title>
<style>body{font-family:system-ui;background:#0f1115;color:#e6e8eb;
display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}
.box{max-width:560px;padding:32px;line-height:1.6}code{background:#1b1e26;
padding:2px 6px;border-radius:4px;color:#7aa2f7}h1{color:#7aa2f7}</style></head>
<body><div class=box><h1>NextXR platform is running</h1>
<p>The API is live at <code>/api/v1</code> and <code>/docs</code>, but the React
frontend hasn't been built yet.</p>
<p>Build it once with:</p>
<pre><code>cd frontend
npm install
npm run build</code></pre>
<p>Then reload this page. For live development run <code>npm run dev</code>
(Vite proxies the API to this server).</p></div></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    """Serve the built React app (or a build placeholder)."""
    idx = FRONTEND_DIST / "index.html"
    if idx.exists():
        return HTMLResponse(content=idx.read_text(encoding="utf-8"))
    return HTMLResponse(content=_PLACEHOLDER)


@app.get("/{full_path:path}", response_class=HTMLResponse)
def spa_fallback(full_path: str):
    """SPA fallback: any non-API, non-asset path returns index.html so the
    client router can handle it. API routes are matched first by FastAPI."""
    # Never swallow API or docs paths.
    if full_path.startswith(("api/", "docs", "openapi.json", "redoc")):
        raise HTTPException(status_code=404, detail="Not found")
    idx = FRONTEND_DIST / "index.html"
    if idx.exists():
        return HTMLResponse(content=idx.read_text(encoding="utf-8"))
    return HTMLResponse(content=_PLACEHOLDER)


# ── Startup ─────────────────────────────────────────────────────────

@app.on_event("startup")
def on_startup():
    """Apply the graph schema on boot, best-effort. If Neo4j is unreachable
    (e.g. Docker is off), we log and continue — the server still serves the
    frontend and the bus/schema/twins APIs. Schema is re-applied lazily when a
    twin is created or the feed starts."""
    try:
        _ensure_schema()
    except Exception as e:
        print(f"[startup] schema apply skipped (Neo4j unavailable): {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server.main:app", host="0.0.0.0", port=8000, reload=False)
