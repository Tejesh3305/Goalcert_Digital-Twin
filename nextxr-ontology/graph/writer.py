"""
writer.py — THE Graph Writer (Day 3 keystone).

The single, disciplined doorway through which all data changes must pass:

    validate → commit → emit

Every mutation (create, update, delete) flows through write_node / update_node /
delete_node. The writer:
  1. Converts the incoming dict into a Turtle RDF string.
  2. Calls gate.validate() against the full SHACL shape set.
  3. If valid, writes to Neo4j via crud.py.
  4. Appends a Change Log entry via changelog.py.
  5. Returns a WriteResult (ok + the node + the log entry, or violations).

Nothing bypasses this. crud.py still exists for raw DB access (tests, migrations),
but all application-level writes go through the writer.

Usage:
    from graph.writer import write_node, update_node, delete_node

    result = write_node("tenant-1", "hvac:AirHandler", {
        "displayName": "AHU-01",
        "status": "running",
    }, related_turtle='<urn:space:lobby> a nxr:Space ; ...',
       actor="api-user-42")

    if result.ok:
        print(result.node)       # the Neo4j node dict
        print(result.log_entry)  # the Change Log entry dict
    else:
        for v in result.violations:
            print(v)
"""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# Add tools/ to sys.path so we can import gate.py
_TOOLS_DIR = str(Path(__file__).resolve().parent.parent / "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

from graph.crud import create_node, read_node, delete_node as crud_delete, _now_iso
from graph.changelog import append_entry

# gate.py import — needs tools/ on the path (done above)
import gate as _gate


# ── Namespace constants ─────────────────────────────────────────────

NXR = "https://ontology.nextxr.io/v3/core#"
HVAC = "https://ontology.nextxr.io/v3/hvac#"

PREFIXES = """
@prefix nxr:  <https://ontology.nextxr.io/v3/core#> .
@prefix hvac: <https://ontology.nextxr.io/v3/hvac#> .
@prefix sosa: <http://www.w3.org/ns/sosa/> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
"""

# Maps short class names (as used by callers) to their full prefixed form
# and the taxonomy category (Neo4j label).
# This gets the caller out of having to know RDF prefix syntax.
CLASS_MAP = {
    # Platform classes
    "Site": ("nxr:Site", "Location", f"{NXR}Site"),
    "Space": ("nxr:Space", "Location", f"{NXR}Space"),
    "Incident": ("nxr:Incident", "Incident", f"{NXR}Incident"),
    "MaintenanceEvent": ("nxr:MaintenanceEvent", "Process", f"{NXR}MaintenanceEvent"),
    "Observation": ("nxr:Observation", "Observation", f"{NXR}Observation"),
    "Finding": ("nxr:Finding", "Finding", f"{NXR}Finding"),
    "Diagnosis": ("nxr:Diagnosis", "Document", f"{NXR}Diagnosis"),
    "Recommendation": ("nxr:Recommendation", "Document", f"{NXR}Recommendation"),
    "Action": ("nxr:Action", "Process", f"{NXR}Action"),
    "Document": ("nxr:Document", "Document", f"{NXR}Document"),
    "CapabilityBundle": ("nxr:CapabilityBundle", "Capability", f"{NXR}CapabilityBundle"),
    "IntegrationAdapter": ("nxr:IntegrationAdapter", "Capability", f"{NXR}IntegrationAdapter"),
    "MLModel": ("nxr:MLModel", "Capability", f"{NXR}MLModel"),
    "InputPort": ("nxr:InputPort", "Capability", f"{NXR}InputPort"),
    "OutputPort": ("nxr:OutputPort", "Capability", f"{NXR}OutputPort"),
    # HVAC pack classes
    "hvac:AirHandler": ("hvac:AirHandler", "PhysicalAsset", f"{HVAC}AirHandler"),
    "hvac:Chiller": ("hvac:Chiller", "PhysicalAsset", f"{HVAC}Chiller"),
    "hvac:TemperatureSensor": ("hvac:TemperatureSensor", "PhysicalAsset", f"{HVAC}TemperatureSensor"),
    "hvac:FilterClogged": ("hvac:FilterClogged", "Capability", f"{HVAC}FilterClogged"),
    # Aliases (callers can use either form)
    "AirHandler": ("hvac:AirHandler", "PhysicalAsset", f"{HVAC}AirHandler"),
    "Chiller": ("hvac:Chiller", "PhysicalAsset", f"{HVAC}Chiller"),
    "TemperatureSensor": ("hvac:TemperatureSensor", "PhysicalAsset", f"{HVAC}TemperatureSensor"),
}


# ── Result types ────────────────────────────────────────────────────

@dataclass
class WriteResult:
    """Outcome of a write_node / update_node / delete_node call."""
    ok: bool
    node: Optional[dict] = None
    log_entry: Optional[dict] = None
    violations: List[str] = field(default_factory=list)

    def __bool__(self):
        return self.ok


# ── Internal helpers ────────────────────────────────────────────────

def _new_id():
    try:
        return str(uuid.uuid7())
    except AttributeError:
        return str(uuid.uuid4())


def _resolve_class(class_name):
    """Resolve a class name to (prefixed_rdf_type, neo4j_label, canonical_type_iri)."""
    if class_name in CLASS_MAP:
        return CLASS_MAP[class_name]
    # If caller passes a fully prefixed form we don't recognise, pass through
    # and let gate.validate() decide if it's legal.
    raise ValueError(
        f"Unknown class {class_name!r}. Use one of: {sorted(set(CLASS_MAP.keys()))}"
    )


def _build_turtle(rdf_type, canonical_iri, node_id, tenant_id, properties, actor,
                   related_turtle=""):
    """
    Convert a properties dict into a Turtle string that satisfies
    the SHACL base shape + class-specific shapes.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    urn = f"<urn:nxr:{node_id}>"

    # Base 10 properties (required by BaseEntityShape)
    lines = [
        f'{urn} a {rdf_type} ;',
        f'    nxr:id "{node_id}" ;',
        f'    nxr:tenantId "{tenant_id}" ;',
        f'    nxr:canonicalType "{canonical_iri}"^^xsd:anyURI ;',
        f'    nxr:createdAt "{now}"^^xsd:dateTime ;',
        f'    nxr:updatedAt "{now}"^^xsd:dateTime ;',
        f'    nxr:createdBy "{actor}" ;',
    ]

    # Optional base properties
    if "displayName" in properties:
        lines.append(f'    nxr:displayName "{properties["displayName"]}" ;')
    if "status" in properties:
        lines.append(f'    nxr:status "{properties["status"]}" ;')

    # Class-specific RDF properties (object properties like servesSpace, targetsAsset)
    rdf_props = properties.get("_rdf", {})
    for pred, obj in rdf_props.items():
        lines.append(f'    {pred} {obj} ;')

    # Close the entity — replace last semicolon with period
    lines[-1] = lines[-1].rstrip(" ;") + " ."

    turtle = PREFIXES + "\n" + "\n".join(lines) + "\n"

    # Append related entities if provided (e.g., the Space that an AirHandler serves)
    if related_turtle:
        turtle += "\n" + related_turtle + "\n"

    return turtle


# ── Public API ──────────────────────────────────────────────────────

def write_node(tenant_id, class_name, properties, related_turtle="", actor="system"):
    """
    The guarded door: validate → commit → emit.

    Parameters
    ----------
    tenant_id       : str  — which tenant
    class_name      : str  — e.g. "AirHandler", "Site", "hvac:Chiller"
    properties      : dict — displayName, status, and _rdf for object properties
    related_turtle  : str  — extra Turtle for related entities needed by validation
    actor           : str  — who triggered this write

    Returns WriteResult.
    """
    rdf_type, neo4j_label, canonical_iri = _resolve_class(class_name)
    node_id = properties.get("id") or _new_id()

    # 1. Build Turtle for validation
    turtle = _build_turtle(rdf_type, canonical_iri, node_id, tenant_id,
                           properties, actor, related_turtle)

    # 2. VALIDATE against SHACL shapes
    result = _gate.validate(turtle)
    if not result.ok:
        return WriteResult(
            ok=False,
            violations=[str(v) for v in result.violations],
        )

    # 3. COMMIT to Neo4j
    node_props = dict(properties)
    node_props.pop("_rdf", None)  # _rdf is for Turtle only, not Neo4j
    node_props["id"] = node_id
    node_props["canonicalType"] = canonical_iri
    node = create_node(tenant_id, neo4j_label, node_props, created_by=actor)

    # 4. EMIT Change Log entry
    log_entry = append_entry(
        tenant_id=tenant_id,
        entity_id=node_id,
        entity_label=neo4j_label,
        action="CREATE",
        payload=node,
        actor=actor,
    )

    # Update the node's changeLogRef to point to this log entry
    from graph.connection import get_driver
    driver = get_driver()
    with driver.session() as session:
        session.run(
            f"MATCH (n:{neo4j_label} {{tenantId: $tid, id: $nid}}) "
            f"SET n.changeLogRef = $ref",
            tid=tenant_id, nid=node_id, ref=log_entry["id"],
        )
    node["changeLogRef"] = log_entry["id"]

    return WriteResult(ok=True, node=node, log_entry=log_entry)


def update_node(tenant_id, node_id, class_name, properties, related_turtle="", actor="system"):
    """
    Update an existing node: validate the new state → update → emit.

    Only the properties in the dict are changed; others are preserved.
    """
    rdf_type, neo4j_label, canonical_iri = _resolve_class(class_name)

    # Read existing node
    existing = read_node(tenant_id, node_id, label=neo4j_label)
    if existing is None:
        return WriteResult(ok=False, violations=[f"Node {node_id} not found for tenant {tenant_id}"])

    # Merge properties
    merged = dict(existing)
    for k, v in properties.items():
        if k != "_rdf":
            merged[k] = v

    # Build Turtle of the merged (post-update) state for validation
    turtle = _build_turtle(rdf_type, canonical_iri, node_id, tenant_id,
                           merged, actor, related_turtle)

    result = _gate.validate(turtle)
    if not result.ok:
        return WriteResult(
            ok=False,
            violations=[str(v) for v in result.violations],
        )

    # Apply update in Neo4j
    now = _now_iso()
    update_props = {k: v for k, v in properties.items() if k != "_rdf"}
    update_props["updatedAt"] = now

    from graph.connection import get_driver
    driver = get_driver()
    with driver.session() as session:
        set_clauses = ", ".join(f"n.{k} = ${k}" for k in update_props)
        result_db = session.run(
            f"MATCH (n:{neo4j_label} {{tenantId: $tid, id: $nid}}) "
            f"SET {set_clauses} RETURN properties(n) AS props",
            tid=tenant_id, nid=node_id, **update_props,
        )
        record = result_db.single()
        updated_node = dict(record["props"]) if record else None

    if updated_node is None:
        return WriteResult(ok=False, violations=["Update failed — node disappeared"])

    # Emit Change Log
    log_entry = append_entry(
        tenant_id=tenant_id,
        entity_id=node_id,
        entity_label=neo4j_label,
        action="UPDATE",
        payload=update_props,
        actor=actor,
    )

    # Update changeLogRef
    with driver.session() as session:
        session.run(
            f"MATCH (n:{neo4j_label} {{tenantId: $tid, id: $nid}}) "
            f"SET n.changeLogRef = $ref",
            tid=tenant_id, nid=node_id, ref=log_entry["id"],
        )
    updated_node["changeLogRef"] = log_entry["id"]

    return WriteResult(ok=True, node=updated_node, log_entry=log_entry)


def delete_node(tenant_id, node_id, class_name, actor="system"):
    """
    Delete a node: no validation needed, but the deletion IS logged.
    """
    _rdf_type, neo4j_label, _canonical_iri = _resolve_class(class_name)

    # Read existing node before deleting (for the log payload)
    existing = read_node(tenant_id, node_id, label=neo4j_label)
    if existing is None:
        return WriteResult(ok=False, violations=[f"Node {node_id} not found for tenant {tenant_id}"])

    # Delete from Neo4j
    deleted = crud_delete(tenant_id, node_id, label=neo4j_label)
    if not deleted:
        return WriteResult(ok=False, violations=["Delete failed"])

    # Emit Change Log
    log_entry = append_entry(
        tenant_id=tenant_id,
        entity_id=node_id,
        entity_label=neo4j_label,
        action="DELETE",
        payload=existing,
        actor=actor,
    )

    return WriteResult(ok=True, node=existing, log_entry=log_entry)
