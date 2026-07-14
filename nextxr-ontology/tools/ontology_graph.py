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

# Layer 3.5 — Common Facilities Pack (frozen universal sub-layer).
CFP_FILES = [
    "packs/cfp/cfp-classes.ttl",
    "packs/cfp/cfp-shapes.ttl",
]

# Layer 4 — BIM geometry pack (extends CFP downward; adds plan-to-3D geometry).
BIM_FILES = [
    "packs/bim/bim-classes.ttl",
    "packs/bim/bim-shapes.ttl",
]

# Layer 4 — vertical domain packs (clinical / data-centre specialised assets +
# their dedicated physics bindings). Common services stay in CFP.
DOMAIN_FILES = [
    "packs/hospital/hospital-classes.ttl",
    "packs/hospital/hospital-shapes.ttl",
    "packs/hospital/hospital-bindings.ttl",
    "packs/hospital/hospital-twin-classes.ttl",
    "packs/hospital/hospital-twin-shapes.ttl",
    "packs/datacenter/datacenter-classes.ttl",
    "packs/datacenter/datacenter-shapes.ttl",
    "packs/datacenter/datacenter-bindings.ttl",
]

# Layer 4 — machine-twin domain packs (EDM / turbine / rail / …). Single-asset (or
# network) machine-twin domains; their physics lives in the co-located pack under
# nextxr-ontology/packs/<domain>/.
MACHINE_FILES = [
    "packs/turbine/turbine-classes.ttl",
    "packs/turbine/turbine-shapes.ttl",
    "packs/edm/edm-classes.ttl",
    "packs/edm/edm-shapes.ttl",
    "packs/railway/railway-classes.ttl",
    "packs/railway/railway-shapes.ttl",
    "packs/ev/ev-classes.ttl",
    "packs/ev/ev-shapes.ttl",
    "packs/defence/defence-classes.ttl",
    "packs/defence/defence-shapes.ttl",
]

# Layer 4 — domain packs.
PACK_FILES = CFP_FILES + BIM_FILES + DOMAIN_FILES + MACHINE_FILES + [
    "packs/hvac/hvac-classes.ttl",
    "packs/hvac/hvac-shapes.ttl",
]

# Governance shapes validate the T-Box, NOT tenant mutations. They are
# intentionally excluded from the gate's bundle so they never fire on a
# per-write basis; SchemaService.validate_governance() loads them on demand.
GOVERNANCE_FILES = [
    "platform/nxr-governance.ttl",
]

# Behavior binding layer (NON-FROZEN): class -> dynamics archetype + params +
# monitoring rules. Pure class-level annotations (inert for SHACL: they add no
# shapes and declare no new classes), so loading them into the shared graph is
# safe for the gate and lets SchemaService.behavior_profile() answer
# "how does this class behave?" from the same surface as "what is legal?".
BINDING_FILES = [
    "platform/nxr-behavior-bindings.ttl",
]

ALL_FILES = PLATFORM_FILES + PACK_FILES


# Layer 4 (authored) — bundles published at runtime by the Bundle Author
# meta-agent. Each is a *.ttl fragment dropped into packs/published/. Loading
# them here makes agent-authored classes first-class in the ontology, so the
# Validator/Graph Writer recognise them (this closes the agentic loop).
PUBLISHED_DIR = ROOT / "packs" / "published"


def build_graph(include_packs=True, reason=False):
    """Assemble the layers into one graph. If reason=True, materialise
    OWL-RL closure so subclass/inverse/transitive facts are explicit."""
    g = Graph()
    g.bind("nxr", NXR_CORE)
    files = PLATFORM_FILES + (PACK_FILES if include_packs else []) + BINDING_FILES
    for rel in files:
        path = ROOT / rel
        if path.exists():
            g.parse(path, format="turtle")
    # Authored bundle fragments (best-effort; a malformed one must not break
    # the whole ontology — it just won't contribute classes).
    if include_packs and PUBLISHED_DIR.is_dir():
        for frag in sorted(PUBLISHED_DIR.glob("*.ttl")):
            try:
                g.parse(frag, format="turtle")
            except Exception:
                pass
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
