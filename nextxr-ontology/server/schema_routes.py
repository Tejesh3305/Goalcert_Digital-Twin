"""
schema_routes.py — Schema query endpoints mounted into the main server.

Wraps tools/schema_service.py as FastAPI routes under /api/v1/schema/*.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from fastapi import APIRouter, HTTPException, Body

router = APIRouter(prefix="/api/v1/schema", tags=["schema"])

_svc = None


def _get_svc():
    global _svc
    if _svc is None:
        from schema_service import SchemaService
        _svc = SchemaService.load()
    return _svc


@router.get("/version")
def version():
    """Ontology version."""
    svc = _get_svc()
    return {"ontologyVersion": svc.version, "apiPath": "/api/v1/schema"}


@router.get("/types")
def types(instantiable_only: bool = False):
    """All legal types in the ontology."""
    return _get_svc().legal_types(instantiable_only=instantiable_only)


@router.get("/categories")
def categories():
    """The 10 closed taxonomy categories."""
    return _get_svc().taxonomy_categories()


@router.get("/predicates")
def predicates():
    """Relation vocabulary with domain, range, and logical properties."""
    return _get_svc().predicates()


@router.get("/class/{name}")
def class_info(name: str):
    """Full description of a class (properties, state machines, etc.)."""
    try:
        return _get_svc().class_info(name)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/class/{name}/properties")
def class_properties(name: str):
    """All properties a class can carry (base + scoped + shape-derived)."""
    try:
        return _get_svc().properties_of(name)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/class/{name}/behavior")
def class_behavior(name: str):
    """How a class behaves: its dynamics archetype + default params and monitoring
    rules, from the binding layer (subclass-aware). The agent-facing 'how does X
    behave?' surface used when composing/authoring a twin."""
    try:
        return _get_svc().behavior_profile(name)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/archetypes")
def archetypes():
    """The behaviour archetype catalog an agent picks from: generative dynamics
    archetypes (what each produces/consumes) + the monitoring rule kinds. This is
    the menu of reusable, parameterizable behaviours — no code per asset."""
    out = {"dynamics": [], "monitoring_kinds": []}
    try:
        from dynamics import build_dynamics_registry
        out["dynamics"] = build_dynamics_registry().describe()
    except Exception as e:
        out["dynamics_error"] = str(e)
    try:
        from behaviors.archetypes import _KINDS
        out["monitoring_kinds"] = sorted(_KINDS.keys())
    except Exception as e:
        out["monitoring_error"] = str(e)
    return out


@router.get("/asset-types")
def asset_types():
    """Instantiable entity types across core + packs, grouped by taxonomy
    category. This is what the 'Add Asset' UI dropdown consumes — it spans both
    the core ontology and loaded packs (e.g. HVAC), which `/types` does not."""
    from rdflib import RDF, URIRef
    from rdflib.namespace import OWL, RDFS

    svc = _get_svc()
    g = svc.g
    NXR = "https://ontology.nextxr.io/v3/core#"
    tax = URIRef(NXR + "taxonomyCategory")
    is_abstract = URIRef(NXR + "isAbstract")
    is_structural = URIRef(NXR + "isStructural")

    grouped: dict[str, list] = {}
    for c in g.subjects(RDF.type, OWL.Class):
        iri = str(c)
        if not iri.startswith("https://ontology.nextxr.io/"):
            continue
        # Skip abstract / structural plumbing classes.
        if (c, is_abstract, None) in g and g.value(c, is_abstract):
            continue
        if (c, is_structural, None) in g and g.value(c, is_structural):
            continue
        cat = g.value(c, tax)
        if cat is None:
            continue
        local = iri.split("#")[-1]
        label_node = g.value(c, RDFS.label)
        label = str(label_node) if label_node else local
        prefix = "hvac" if "/hvac#" in iri else "nxr"
        grouped.setdefault(str(cat), []).append({
            "id": f"{prefix}:{local}",
            "iri": iri,
            "label": label,
            "category": str(cat),
        })
    for cat in grouped:
        grouped[cat].sort(key=lambda x: x["label"])
    return {"categories": grouped}


@router.post("/validate")
def validate(turtle: str = Body(..., media_type="text/turtle")):
    """Run a proposed Turtle fragment through the SHACL write gate."""
    return _get_svc().validate(turtle)


@router.get("/governance")
def governance():
    """Check: does the ontology itself obey the closed taxonomy rule?"""
    return _get_svc().validate_governance()
