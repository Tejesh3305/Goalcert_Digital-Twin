"""NextXR Twins — the digital-twin registry.

A "twin" is a named, isolated instance of the platform: it maps 1:1 to a
tenant_id (the isolation key already threaded through the graph, change log,
and event bus). This package keeps the lightweight registry of twins (name,
domain, created time) in SQLite and knows how to SEED a twin's initial graph
through the Graph Writer — so a freshly-created twin is immediately populated
and the simulated feed can run against it.

The registry is metadata only; the twin's actual entities live in Neo4j under
its tenant_id, and every mutation still flows through the single write path.
"""

from .service import TwinRegistry, Twin, TEMPLATES

__all__ = ["TwinRegistry", "Twin", "TEMPLATES"]
