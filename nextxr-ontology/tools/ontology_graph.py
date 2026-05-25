#!/usr/bin/env python3
"""
ontology_graph.py — the single source of truth for WHICH files make up
the ontology and in WHAT ORDER they load. The loader, the validation
gate, and the schema-query service all import from here so they can
never drift out of sync.
"""

from pathlib import Path

try:
    from rdflib import Graph
except ImportError:  # pragma: no cover
    raise SystemExit("Install deps first:  pip install rdflib owlrl pyshacl")

ROOT = Path(__file__).resolve().parent.parent

NXR_CORE = "https://ontology.nextxr.io/v3/core#"
NXR_BASE = "https://ontology.nextxr.io/"

# Layer 1+2 (borrowed) and Layer 3 (platform). Load order matters:
# upper ontologies first so subclass links resolve.
PLATFORM_FILES = [
    "imports/bfo.ttl",
    "imports/sosa.ttl",
    "platform/nxr-classes.ttl",
    "platform/nxr-properties.ttl",
    "platform/nxr-base-shape.ttl",
    "platform/nxr-units.ttl",
    "platform/nxr-taxonomy.ttl",
    "platform/nxr-shapes.ttl",
]

# Layer 4 — domain packs.
PACK_FILES = [
    "packs/hvac/hvac-classes.ttl",
    "packs/hvac/hvac-shapes.ttl",
]

# Governance shapes validate the T-Box, NOT tenant mutations. They are
# intentionally excluded from the gate's bundle so they never fire on a
# per-write basis; SchemaService.validate_governance() loads them on demand.
GOVERNANCE_FILES = [
    "platform/nxr-governance.ttl",
]

ALL_FILES = PLATFORM_FILES + PACK_FILES


def build_graph(include_packs=True, reason=False):
    """Assemble the layers into one graph. If reason=True, materialise
    OWL-RL closure so subclass/inverse/transitive facts are explicit."""
    g = Graph()
    g.bind("nxr", NXR_CORE)
    files = PLATFORM_FILES + (PACK_FILES if include_packs else [])
    for rel in files:
        path = ROOT / rel
        if path.exists():
            g.parse(path, format="turtle")
    if reason:
        import owlrl
        owlrl.DeductiveClosure(owlrl.OWLRL_Semantics).expand(g)
    return g


def build_governance_shapes():
    """The governance shape graph, on its own — used to validate the T-Box."""
    g = Graph()
    g.bind("nxr", NXR_CORE)
    for rel in GOVERNANCE_FILES:
        path = ROOT / rel
        if path.exists():
            g.parse(path, format="turtle")
    return g
