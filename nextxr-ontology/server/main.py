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

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

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
from feed.simulate import simulate_temperature, FindingsLoop

from server.query_api import router as query_router
from server.write_api import router as write_router
from server.schema_routes import router as schema_router

# ── App setup ───────────────────────────────────────────────────────

app = FastAPI(
    title="NextXR Digital Twin",
    version="1.0.0",
    description="Live dashboard + REST API for the NextXR Digital Twin.",
)

app.include_router(query_router)
app.include_router(write_router)
app.include_router(schema_router)

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
    "error": None,
}
_feed_lock = threading.Lock()


# ── Seed + Feed ─────────────────────────────────────────────────────

def _ensure_schema():
    """Apply graph schema (idempotent, silenced)."""
    from graph import schema
    with contextlib.redirect_stdout(io.StringIO()):
        schema.apply_schema(dry_run=False)
    # apply_schema closes the driver; re-acquire
    get_driver()


def _seed_facility(writer: GraphWriter, tenant: str) -> str:
    """Seed a demo HVAC facility if it doesn't exist. Returns the AHU node_id."""
    query = GraphQuery()
    existing = query.list_by_label(tenant, "PhysicalAsset", limit=1)
    if existing:
        # Already seeded — find the AHU
        for node in query.list_by_label(tenant, "PhysicalAsset", limit=50):
            if "AHU" in node.get("displayName", ""):
                return node["id"]
        return existing[0]["id"]

    site = writer.create(
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


def _run_feed_loop(tenant: str, ahu_id: str, cl: ChangeLog):
    """Background thread: run simulated feed through behavior registry.
    Loops continuously with fresh simulations until stopped."""
    global _feed_state

    try:
        writer = GraphWriter(changelog=cl)
        query = GraphQuery()

        run_number = 0

        while True:
            # Check if stopped
            with _feed_lock:
                if not _feed_state["running"]:
                    break

            # Fresh behaviors each run so baselines reset cleanly
            registry = BehaviorRegistry()
            registry.register(TemperatureThresholdRule(offset_c=3.0, duration_minutes=3.0))
            registry.register(TemperatureZScoreBaseline(warmup=12, z_threshold=3.0))
            registry.register(ThermalPhysicsBehavior())

            loop = FindingsLoop(registry, writer, query)
            samples = list(simulate_temperature(tenant, ahu_id, setpoint=22.0, minutes=60))

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

                # Pace the feed — 0.5s per sample so the dashboard can show progress
                time.sleep(0.5)

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


# ── Dashboard serving ───────────────────────────────────────────────

DASHBOARD_DIR = ROOT / "server" / "static"


@app.get("/", response_class=HTMLResponse)
def dashboard():
    """Serve the live dashboard."""
    index = DASHBOARD_DIR / "index.html"
    if index.exists():
        return HTMLResponse(content=index.read_text(), status_code=200)
    return HTMLResponse(content="<h1>Dashboard not found</h1>", status_code=404)


# ── Startup ─────────────────────────────────────────────────────────

@app.on_event("startup")
def on_startup():
    """Apply schema on boot so the server is ready immediately."""
    _ensure_schema()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server.main:app", host="0.0.0.0", port=8000, reload=False)
