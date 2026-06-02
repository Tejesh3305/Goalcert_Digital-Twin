"""
write_api.py — Mutation REST API for the NextXR graph.

Exposes the Graph Writer's four methods over HTTP:
  - POST   /api/v1/entities          → create
  - PATCH  /api/v1/entities/{id}     → update
  - DELETE /api/v1/entities/{id}     → delete
  - POST   /api/v1/entities/{id}/rel → relate

Every mutation flows through the Graph Writer (validate → commit → emit).
Invalid payloads are rejected with 422 and the SHACL violations returned.
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

from graph.writer import GraphWriter, Rel
from changelog.service import ChangeLog

router = APIRouter(prefix="/api/v1", tags=["write"])

# Sensor subclasses need sosa:observes pointing at an ontology concept.
# The writer handles this via ontology_ref=True (skips Neo4j integrity check,
# renders the raw IRI in validation TTL). This map auto-injects the correct
# observable property when the frontend creates a sensor without specifying one.
_SENSOR_OBSERVES: dict[str, str] = {
    "https://ontology.nextxr.io/v3/cfp#TemperatureSensor": "cfp:temperature",
    "https://ontology.nextxr.io/v3/cfp#HumiditySensor": "cfp:relativeHumidity",
    "https://ontology.nextxr.io/v3/cfp#AirQualitySensor": "cfp:airQualityIndex",
    "https://ontology.nextxr.io/v3/cfp#OccupancySensor": "cfp:occupancyCount",
    "https://ontology.nextxr.io/v3/cfp#LightSensor": "cfp:illuminance",
    "https://ontology.nextxr.io/v3/cfp#VibrationSensor": "cfp:vibrationVelocity",
    "https://ontology.nextxr.io/v3/cfp#NoiseSensor": "cfp:soundLevel",
    "https://ontology.nextxr.io/v3/cfp#WaterQualitySensor": "cfp:waterQuality",
    "https://ontology.nextxr.io/v3/cfp#SmokeDetector": "cfp:smokeObscuration",
    "https://ontology.nextxr.io/v3/cfp#AspiratingDetector": "cfp:smokeObscuration",
    "https://ontology.nextxr.io/v3/cfp#HeatDetector": "cfp:temperature",
    "https://ontology.nextxr.io/v3/cfp#FlameDetector": "cfp:flameSignal",
    "https://ontology.nextxr.io/v3/cfp#Camera": "cfp:illuminance",
    "https://ontology.nextxr.io/v3/cfp#AccessReader": "cfp:doorState",
    "https://ontology.nextxr.io/v3/cfp#IntrusionSensor": "cfp:doorState",
    "https://ontology.nextxr.io/v3/cfp#LeakSensor": "cfp:leakState",
    "https://ontology.nextxr.io/v3/cfp#WaterMeter": "cfp:flowRate",
    "https://ontology.nextxr.io/v3/cfp#EnergyMeter": "cfp:energy",
    "https://ontology.nextxr.io/v3/hvac#TemperatureSensor": "cfp:temperature",
}

_writer: Optional[GraphWriter] = None


def _get_writer() -> GraphWriter:
    global _writer
    if _writer is None:
        _writer = GraphWriter(changelog=ChangeLog())
    return _writer


# ── Request models ─────────────────────────────────────────────────

class CreateEntityRequest(BaseModel):
    tenant: str
    canonical_type: str = Field(..., description="Full IRI of the class, e.g. https://ontology.nextxr.io/v3/core#Site")
    actor: str = "api"
    properties: dict = Field(default_factory=dict)
    relationships: list[dict] = Field(default_factory=list, description="List of {predicate, target_id}")


class UpdateEntityRequest(BaseModel):
    tenant: str
    actor: str = "api"
    properties: dict = Field(..., description="Properties to update (merged into existing)")


class RelateRequest(BaseModel):
    tenant: str
    actor: str = "api"
    predicate: str = Field(..., description="Relationship CURIE, e.g. 'nxr:flags' or 'hvac:servesSpace'")
    target_id: str


# ── Endpoints ──────────────────────────────────────────────────────

@router.post("/entities")
def create_entity(req: CreateEntityRequest):
    """Create a new entity. Validates through the SHACL gate before committing."""
    w = _get_writer()

    rels = [Rel(predicate=r["predicate"], target_id=r["target_id"]) for r in req.relationships] if req.relationships else []

    # Auto-inject sosa:observes for sensor subclasses if not already provided.
    # This lets the frontend create sensors without knowing about ontology refs.
    has_observes = any(r.predicate == "sosa:observes" for r in rels)
    if not has_observes and req.canonical_type in _SENSOR_OBSERVES:
        rels.append(Rel(
            predicate="sosa:observes",
            target_id=_SENSOR_OBSERVES[req.canonical_type],
            ontology_ref=True,
        ))

    result = w.create(
        tenant_id=req.tenant,
        canonical_type=req.canonical_type,
        actor=req.actor,
        properties=req.properties,
        relationships=rels or None,
    )

    if not result.ok:
        raise HTTPException(status_code=422, detail={
            "error": result.error,
            "violations": result.violations,
        })

    return {
        "status": "created",
        "node_id": result.node_id,
        "label": result.label,
        "event_id": result.event_id,
    }


@router.patch("/entities/{node_id}")
def update_entity(node_id: str, req: UpdateEntityRequest):
    """Update properties on an existing entity. Full post-update state is validated."""
    w = _get_writer()

    result = w.update(
        tenant_id=req.tenant,
        node_id=node_id,
        actor=req.actor,
        properties=req.properties,
    )

    if not result.ok:
        raise HTTPException(status_code=422, detail={
            "error": result.error,
            "violations": result.violations,
        })

    return {
        "status": "updated",
        "node_id": result.node_id,
        "label": result.label,
        "event_id": result.event_id,
    }


@router.delete("/entities/{node_id}")
def delete_entity(node_id: str, tenant: str, actor: str = "api"):
    """Delete an entity. Logged in the Change Log for auditability."""
    w = _get_writer()

    result = w.delete(
        tenant_id=tenant,
        node_id=node_id,
        actor=actor,
    )

    if not result.ok:
        raise HTTPException(status_code=404, detail={
            "error": result.error,
        })

    return {
        "status": "deleted",
        "node_id": result.node_id,
        "label": result.label,
    }


@router.post("/entities/{node_id}/rel")
def relate_entity(node_id: str, req: RelateRequest):
    """Add a relationship from this entity to another. Source is re-validated."""
    w = _get_writer()

    result = w.relate(
        tenant_id=req.tenant,
        actor=req.actor,
        source_id=node_id,
        predicate=req.predicate,
        target_id=req.target_id,
    )

    if not result.ok:
        raise HTTPException(status_code=422, detail={
            "error": result.error,
            "violations": result.violations,
        })

    return {
        "status": "related",
        "node_id": result.node_id,
        "event_id": result.event_id,
    }
