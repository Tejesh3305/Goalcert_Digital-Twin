"""
hub_routes.py — top-level surfaces the Integration Hub calls that don't sit under
/twins or /agents.

  POST /api/v1/predict                    forecast + RUL for a tenant (machine
                                          physics, or a findings-driven facility
                                          outlook) — powers the Prediction page.
  GET  /api/v1/assets/{id}/ar-overlay     versioned AR maintenance steps for an
                                          asset, grounded in its type + live findings.

These reuse the machine-twin runtime and the graph query layer. Every handler is
resilient to the database being offline (returns a graceful, empty-but-valid
payload) so the hub's fall-through stays clean.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1", tags=["hub"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Prediction ──────────────────────────────────────────────────────
class PredictReq(BaseModel):
    tenant: str
    horizon_min: float = 360.0
    points: int = 60


def _facility_outlook(tenant: str) -> dict:
    """Findings-driven forecast for a non-machine (graph-fed) twin: no physics
    trajectory, but a real severity + heuristic RUL derived from live findings."""
    try:
        from graph.query import GraphQuery
        findings = GraphQuery().get_findings(tenant)
    except Exception:
        findings = []
    crit = [f for f in findings if f.get("severity") == "critical"]
    warn = [f for f in findings if f.get("severity") == "warning"]
    severity = "critical" if crit else "warning" if warn else "nominal"
    # Heuristic service window per finding: critical ~2 d, warning ~14 d.
    rul = [{"component": f.get("displayName") or f.get("id") or "finding",
            "minutes": 2 * 1440 if f.get("severity") == "critical" else 14 * 1440,
            "severity": f.get("severity")}
           for f in (crit + warn)]
    return {"tenant": tenant, "kind": "facility", "trajectory": [], "rul": rul,
            "severity": severity,
            "findings_outlook": [{"id": f.get("id"), "displayName": f.get("displayName"),
                                  "severity": f.get("severity")} for f in findings[:20]],
            "generated_at": _now_iso()}


@router.post("/predict")
def predict(req: PredictReq):
    """Forecast + remaining-useful-life for a tenant. Machine twins get the physics
    trajectory + RUL; facility twins get a findings-driven outlook."""
    try:
        from twins.runtime import get_machine_engine
        tw = get_machine_engine().ensure(req.tenant)
    except Exception:
        tw = None
    if tw is not None:
        pred = tw.predict_forward(horizon_min=req.horizon_min, points=req.points)
        pred.update({"tenant": req.tenant, "kind": "machine", "generated_at": _now_iso()})
        return pred
    return _facility_outlook(req.tenant)


# ── AR overlay ──────────────────────────────────────────────────────
def _ar_steps(node: dict, findings: list[dict]) -> list[dict]:
    """A guided AR maintenance/inspection procedure for an asset, tailored to its
    type and any active findings. Anchors name where each step attaches in AR."""
    name = node.get("displayName") or "asset"
    ctype = (node.get("canonicalType") or "").split("#")[-1] or "Asset"
    steps = [
        {"n": 1, "title": "Locate & identify", "anchor": "asset",
         "instruction": f"Point your device at the {name}. Confirm the highlighted "
                        f"{ctype} matches the work order."},
        {"n": 2, "title": "Isolate & make safe", "anchor": "isolation-point",
         "instruction": "Apply lock-out / tag-out at the upstream isolation point and "
                        "verify zero energy before touching the unit."},
    ]
    n = 3
    active = [f for f in findings if f.get("severity") in ("critical", "warning")]
    if active:
        for f in active[:4]:
            steps.append({
                "n": n, "title": f"Address: {f.get('displayName') or 'finding'}",
                "anchor": f.get("signal") or "component", "severity": f.get("severity"),
                "instruction": f"Inspect the component behind “{f.get('displayName') or 'the finding'}”. "
                               f"Follow the {'immediate' if f.get('severity') == 'critical' else 'scheduled'} "
                               f"corrective procedure for this {ctype}.",
            })
            n += 1
    else:
        steps.append({"n": n, "title": "Routine inspection", "anchor": "asset",
                      "instruction": f"No active faults. Perform the routine visual + "
                                     f"sensor check for this {ctype}."})
        n += 1
    steps.append({"n": n, "title": "Verify & restore", "anchor": "asset",
                  "instruction": "Remove isolation, restore service, and confirm the "
                                 "twin reads nominal before closing the work order."})
    return steps


@router.get("/assets/{asset_id}/ar-overlay")
def ar_overlay(asset_id: str, tenant: str, version: Optional[int] = None):
    """Versioned AR overlay (guided steps) for one asset, grounded in its live
    findings. `version` lets a client pin a specific revision; we return the
    current one when omitted."""
    try:
        from graph.query import GraphQuery
        q = GraphQuery()
        node = q.get_node(tenant, asset_id)
        if node is None:
            raise HTTPException(404, f"Asset '{asset_id}' not found in tenant '{tenant}'.")
        findings = q.get_findings(tenant, flagged_entity_id=asset_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"Database unavailable: {e}")

    steps = _ar_steps(node, findings)
    return {
        "asset_id": asset_id, "tenant": tenant,
        "version": version or 1, "revision": _now_iso()[:10],
        "asset": {"id": node.get("id"), "displayName": node.get("displayName"),
                  "type": (node.get("canonicalType") or "").split("#")[-1]},
        "steps": steps,
        "findings": [{"id": f.get("id"), "displayName": f.get("displayName"),
                      "severity": f.get("severity")} for f in findings],
        "generated_at": _now_iso(),
    }
