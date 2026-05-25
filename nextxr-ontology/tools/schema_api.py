#!/usr/bin/env python3
"""
schema_api.py — a thin, versioned HTTP surface over SchemaService.

This is deliberately a wrapper: every endpoint is one call into the
library, so the answers are identical whether a service asks in-process
(import SchemaService) or over HTTP. The path is version-pinned (/v3)
and the payload echoes the ontology's own version string.

    pip install fastapi uvicorn
    uvicorn schema_api:app --reload --port 8000     # from the tools/ dir
    # then: GET http://localhost:8000/v3/schema/class/Equipment/properties

If FastAPI is absent, importing this module raises a clear install hint
rather than a cryptic ImportError, so the library half keeps working.
"""

from __future__ import annotations

try:
    from fastapi import FastAPI, HTTPException, Body
except ImportError:  # pragma: no cover
    raise SystemExit(
        "The HTTP surface needs FastAPI:  pip install fastapi uvicorn\n"
        "(The library — tools/schema_service.py — works without it.)"
    )

from schema_service import SchemaService

svc = SchemaService.load()
app = FastAPI(
    title="NextXR Ontology Schema-Query API",
    version=svc.version,
    description="Read-only schema queries + the write-gate, served over HTTP.",
)

V = "/v3/schema"


@app.get("/version")
def version():
    return {"ontologyVersion": svc.version, "apiPath": "/v3"}


@app.get(V + "/types")
def types(instantiable_only: bool = False):
    return svc.legal_types(instantiable_only=instantiable_only)


@app.get(V + "/categories")
def categories():
    return svc.taxonomy_categories()


@app.get(V + "/predicates")
def predicates():
    return svc.predicates()


@app.get(V + "/class/{name}")
def class_info(name: str):
    try:
        return svc.class_info(name)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get(V + "/class/{name}/properties")
def class_properties(name: str):
    try:
        return svc.properties_of(name)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/v3/validate")
def validate(turtle: str = Body(..., media_type="text/turtle")):
    """Run a proposed mutation (Turtle) through the write gate."""
    return svc.validate(turtle)


@app.get("/v3/governance")
def governance():
    """Is the ontology itself well-formed against the taxonomy-closure rule?"""
    return svc.validate_governance()
