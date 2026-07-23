"""
twin_runtime_routes.py — live machine-twin read/control surfaces.

These expose the machine-twin runtime (twins/runtime.py) over HTTP with stable
read/control shapes the Integration Hub (and any client) can call, so twin physics
runs as a service rather than in-process:

  GET  /api/v1/twins/{tenant}/state        latest frame + health + findings
  GET  /api/v1/twins/{tenant}/diagnostics  subsystems + sensors + machine health
  GET  /api/v1/twins/{tenant}/predict      forward trajectory + RUL
  POST /api/v1/twins/{tenant}/project      non-destructive what-if (fault/control)
  GET  /api/v1/twins/{tenant}/network      fleet network map (fleet domain only)
  POST /api/v1/twins/{tenant}/running      start/stop the live ticker
  POST /api/v1/twins/{tenant}/simulate     advance one step with an input/fault

Only twins whose domain is a registered machine domain are served here; other
tenants get 404 (they run on the HVAC/CFP feed instead).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from twins.runtime import get_machine_engine, SPECS, MACHINE_DOMAINS

router = APIRouter(prefix="/api/v1/twins", tags=["twin-runtime"])


def _twin_or_404(tenant: str):
    tw = get_machine_engine().ensure(tenant)
    if tw is None:
        raise HTTPException(
            status_code=404,
            detail=f"'{tenant}' is not a live machine-domain twin. Known machine "
                   f"domains: {sorted(MACHINE_DOMAINS)}.",
        )
    return tw


class ProjectRequest(BaseModel):
    fault: Optional[str] = None
    severity: float = 0.85
    control: Optional[float] = None
    horizon_min: float = 120.0
    points: int = 120


class SimulateRequest(BaseModel):
    throttle: Optional[float] = None      # generic control input
    control: Optional[float] = None       # alias for throttle
    fault: Optional[str] = None
    severity: float = 0.6


@router.get("/domains")
def list_machine_domains():
    """The machine domains this runtime can twin (for the build-a-twin UI).

    Each entry carries the pack's blueprint — the components (subsystems),
    sensors (with labels + units), the control input, the physics model name and
    the injectable fault catalogue — so Build-a-Twin can show exactly what it
    maps for a chosen domain, grounded in the domain pack, not invented."""
    from twins.service import TEMPLATES
    out = []
    for s in SPECS.values():
        tpl = TEMPLATES.get(s["key"], {})
        out.append({
            "key": s["key"],
            "label": s["label"],
            "description": tpl.get("description", ""),
            "control": s["control"],
            "class_iri": s.get("class_iri"),
            "physics": getattr(s.get("physics"), "__name__", "physics model"),
            # components = the pack's subsystems
            "subsystems": [{"key": k, "label": lbl} for k, lbl in s.get("subsystems", [])],
            # sensors = signal + human label + unit (from the SPEC sensor map)
            "sensors": [{"signal": sig, "label": lbl, "unit": unit}
                        for sig, (lbl, unit) in s.get("sensors", {}).items()],
            "signals": list(s["signals"].values()),
            "faults": list(s.get("faults", [])),
        })
    return {"domains": out}


@router.get("/{tenant}/state")
def twin_state(tenant: str):
    return _twin_or_404(tenant).state_dict()


@router.get("/{tenant}/diagnostics")
def twin_diagnostics(tenant: str):
    return _twin_or_404(tenant).diagnostics()


@router.get("/{tenant}/predict")
def twin_predict(tenant: str, horizon_min: float = 120.0, points: int = 120):
    return _twin_or_404(tenant).predict_forward(horizon_min=horizon_min, points=points)


@router.post("/{tenant}/project")
def twin_project(tenant: str, req: ProjectRequest):
    tw = _twin_or_404(tenant)
    return tw.project(fault=req.fault, severity=req.severity, control=req.control,
                      horizon_min=req.horizon_min, points=req.points)


@router.get("/{tenant}/network")
def twin_network(tenant: str):
    net = _twin_or_404(tenant).network()
    if net is None:
        raise HTTPException(status_code=404,
                            detail="This twin's domain has no network map.")
    return net


@router.post("/{tenant}/running")
def twin_running(tenant: str, running: bool = True):
    if not get_machine_engine().set_running(tenant, running):
        raise HTTPException(status_code=404, detail=f"No machine twin '{tenant}'.")
    return {"tenant": tenant, "running": running}


@router.post("/{tenant}/simulate")
def twin_simulate(tenant: str, req: SimulateRequest):
    tw = _twin_or_404(tenant)
    frame = tw.simulate(throttle=req.throttle if req.throttle is not None else req.control,
                        fault=req.fault, severity=req.severity, dt=2.0)
    return {"status": "ok", "frame": frame, "state": tw.state_dict()}
