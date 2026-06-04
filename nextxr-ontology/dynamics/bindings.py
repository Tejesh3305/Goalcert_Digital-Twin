"""
bindings.py — resolve a class's behaviour profile from the binding layer.

Reads the `nxr:dynamicsArchetype` / `nxr:archetypeParams` / `nxr:monitoringRules`
annotations authored in platform/nxr-behavior-bindings.ttl (loaded into the shared
ontology graph), subclass-aware: if a class has no binding of its own we walk up
rdfs:subClassOf until we find one (ultimately cfp:FacilityEquipment ->
"DefaultEquipment"). This is how the core ships behaviour for every class while
packs/agents override by adding a more specific binding — no code.

    from dynamics.bindings import resolve_binding
    b = resolve_binding("https://ontology.nextxr.io/v3/cfp#Chiller")
    b.archetype        # "ThermalTransferDevice"
    b.params           # {"ratedCapacity": 500, ...}
    b.monitoring       # [ {rule dict}, ... ]
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

_TOOLS = str(Path(__file__).resolve().parent.parent / "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

NXR = "https://ontology.nextxr.io/v3/core#"


@dataclass
class Binding:
    archetype: Optional[str] = None
    params: dict = field(default_factory=dict)
    monitoring: list = field(default_factory=list)
    source_class: Optional[str] = None      # which class the binding was found on


def _graph():
    import gate
    return gate.ontology_graph()


def _json(val, default):
    if not val:
        return default
    try:
        return json.loads(str(val))
    except Exception:
        return default


@lru_cache(maxsize=512)
def resolve_binding(canonical_type: str) -> Binding:
    """Most-specific binding for a class, walking rdfs:subClassOf. Always returns a
    Binding (archetype may be None if nothing — not even FacilityEquipment — binds)."""
    from rdflib import URIRef
    g = _graph()
    DYN = URIRef(NXR + "dynamicsArchetype")
    PARAMS = URIRef(NXR + "archetypeParams")
    MON = URIRef(NXR + "monitoringRules")
    SUBCLASS = URIRef("http://www.w3.org/2000/01/rdf-schema#subClassOf")

    seen, frontier = set(), [URIRef(canonical_type)]
    # BFS up the hierarchy; nearest class with a dynamicsArchetype wins. Monitoring
    # rules are collected from the SAME class that supplies the archetype (so an
    # override replaces, not merges, by default — predictable for authors).
    while frontier:
        cur = frontier.pop(0)
        if cur in seen:
            continue
        seen.add(cur)
        arch = g.value(cur, DYN)
        if arch is not None:
            return Binding(
                archetype=str(arch),
                params=_json(g.value(cur, PARAMS), {}),
                monitoring=_json(g.value(cur, MON), []),
                source_class=str(cur),
            )
        frontier.extend(g.objects(cur, SUBCLASS))
    return Binding()


def monitoring_rules_for(canonical_type: str) -> list:
    """The monitoring rule dicts that apply to a class (its binding's rules).
    Used to build live detection behaviours per twin."""
    return list(resolve_binding(canonical_type).monitoring)


def clear_cache():
    """Drop the resolution cache (after a bundle publishes new bindings)."""
    resolve_binding.cache_clear()
