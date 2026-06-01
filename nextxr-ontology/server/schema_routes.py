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


@router.post("/validate")
def validate(turtle: str = Body(..., media_type="text/turtle")):
    """Run a proposed Turtle fragment through the SHACL write gate."""
    return _get_svc().validate(turtle)


@router.get("/governance")
def governance():
    """Check: does the ontology itself obey the closed taxonomy rule?"""
    return _get_svc().validate_governance()
