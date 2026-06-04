#!/usr/bin/env python3
"""
schema_service.py — the versioned schema-query service (library core).

Other services ask the ontology "what is legal?" through THIS, instead
of parsing Turtle or hardcoding the schema:

    from tools.schema_service import SchemaService
    svc = SchemaService.load()
    svc.version                      # 'v3.0.0'
    svc.legal_types()                # every class a pack may instantiate
    svc.taxonomy_categories()        # the ten closed categories
    svc.predicates()                 # the relation vocabulary + characteristics
    svc.properties_of("Equipment")   # everything an Equipment can carry
    svc.class_info("hvac:AirHandler")
    svc.validate(turtle)             # delegate to the write gate
    svc.validate_governance()        # is the ONTOLOGY itself well-formed?

A thin HTTP surface over this lives in schema_api.py.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from functools import lru_cache
from typing import Dict, List, Optional

from rdflib import Graph, RDF, RDFS, OWL, URIRef
from rdflib.namespace import SH, SKOS

from ontology_graph import (build_graph, build_governance_shapes,
                            NXR_CORE, NXR_BASE)
import gate

NXR = NXR_CORE
HVAC = "https://ontology.nextxr.io/v3/hvac#"
CFP = "https://ontology.nextxr.io/v3/cfp#"
OWL_NS = str(OWL)

# Logical characteristics we surface on predicates.
_CHARACTERISTICS = {
    OWL.TransitiveProperty: "transitive",
    OWL.FunctionalProperty: "functional",
    OWL.SymmetricProperty: "symmetric",
    OWL.InverseFunctionalProperty: "inverse-functional",
}


def _qname(iri: str) -> str:
    if iri.startswith(NXR):
        return "nxr:" + iri[len(NXR):]
    if iri.startswith(HVAC):
        return "hvac:" + iri[len(HVAC):]
    return iri


class SchemaService:
    def __init__(self, graph: Graph):
        self.g = graph

    @classmethod
    @lru_cache(maxsize=1)
    def load(cls) -> "SchemaService":
        # No OWL-RL: we want the AUTHORED schema, not its deductive closure.
        # Superclass ancestry is computed manually over rdfs:subClassOf (see
        # _supers), which keeps "what properties does Equipment have?" scoped
        # to its real ancestors instead of sweeping in owl:Thing-range noise.
        return cls(build_graph(include_packs=True, reason=False))

    # ---------- versioning ----------
    @property
    def version(self) -> str:
        v = self.g.value(URIRef("https://ontology.nextxr.io/v3/core"),
                         OWL.versionInfo)
        return str(v) if v else "unknown"

    # ---------- the closed taxonomy ----------
    def taxonomy_categories(self) -> List[Dict]:
        cat_cls = URIRef(NXR + "TaxonomyCategory")
        out = []
        for c in self.g.subjects(RDF.type, cat_cls):
            out.append({
                "id": _qname(str(c)),
                "label": self._label(c),
                "definition": self._def(c),
            })
        return sorted(out, key=lambda x: x["label"])

    # ---------- legal types ----------
    def legal_types(self, instantiable_only: bool = False) -> List[Dict]:
        """Every NextXR class. With instantiable_only, drop abstract
        classes (those a pack must subclass rather than instantiate)."""
        out = []
        for c in self.g.subjects(RDF.type, OWL.Class):
            iri = str(c)
            if not iri.startswith(NXR_BASE):
                continue
            abstract = self._bool(c, NXR + "isAbstract")
            structural = self._bool(c, NXR + "isStructural")
            if instantiable_only and (abstract or structural):
                continue
            out.append({
                "id": _qname(iri),
                "iri": iri,
                "label": self._label(c),
                "category": self._str(c, NXR + "taxonomyCategory"),
                "abstract": abstract,
                "structural": structural,
                "definition": self._def(c),
            })
        return sorted(out, key=lambda x: x["id"])

    # ---------- predicates ----------
    def predicates(self) -> List[Dict]:
        out = []
        seen = set()
        for ptype in (OWL.ObjectProperty, OWL.DatatypeProperty):
            for p in self.g.subjects(RDF.type, ptype):
                iri = str(p)
                if not iri.startswith(NXR_BASE) or iri in seen:
                    continue
                seen.add(iri)
                chars = [name for owlt, name in _CHARACTERISTICS.items()
                         if (p, RDF.type, owlt) in self.g]
                inv = self.g.value(p, OWL.inverseOf)
                out.append({
                    "id": _qname(iri),
                    "label": self._label(p),
                    "kind": ("object" if ptype == OWL.ObjectProperty
                             else "datatype"),
                    "domain": self._qlist(self.g.objects(p, RDFS.domain)),
                    "range": self._qlist(self.g.objects(p, RDFS.range)),
                    "characteristics": chars,
                    "inverseOf": _qname(str(inv)) if inv else None,
                    "definition": self._def(p),
                })
        return sorted(out, key=lambda x: x["id"])

    # ---------- "what properties does an Equipment have?" ----------
    def properties_of(self, class_name: str) -> Dict:
        iri = self._resolve(class_name)
        if iri is None:
            raise KeyError(f"Unknown class: {class_name}")
        supers = self._supers(iri)
        props: Dict[str, Dict] = {}

        # 1) The 10-property base — every entity carries it.
        for path, req, dt in self._shape_props(URIRef(NXR + "BaseEntityShape")):
            self._add_prop(props, path, origin="base", required=req, datatype=dt)

        # 2) Properties whose rdfs:domain is this class or an ancestor.
        for ptype in (OWL.ObjectProperty, OWL.DatatypeProperty):
            for p in self.g.subjects(RDF.type, ptype):
                domains = set(self.g.objects(p, RDFS.domain))
                if domains and (domains & supers):
                    self._add_prop(props, p, origin="declared",
                                   kind="object" if ptype == OWL.ObjectProperty
                                   else "datatype")

        # 3) Paths required/allowed by any shape targeting this branch.
        for shape in self.g.subjects(RDF.type, SH.NodeShape):
            targets = set(self.g.objects(shape, SH.targetClass))
            if targets & supers:
                for path, req, dt in self._shape_props(shape):
                    self._add_prop(props, path, origin="shape",
                                   required=req, datatype=dt)

        return {
            "class": _qname(iri),
            "iri": iri,
            "supertypes": [_qname(str(s)) for s in sorted(map(str, supers))
                           if str(s).startswith(NXR_BASE) and str(s) != iri],
            "properties": sorted(props.values(), key=lambda x: x["id"]),
        }

    # ---------- one class, fully described ----------
    def class_info(self, class_name: str) -> Dict:
        iri = self._resolve(class_name)
        if iri is None:
            raise KeyError(f"Unknown class: {class_name}")
        ref = URIRef(iri)
        sm = self.g.value(ref, URIRef(NXR + "hasStateMachine"))
        return {
            "id": _qname(iri),
            "iri": iri,
            "label": self._label(ref),
            "definition": self._def(ref),
            "category": self._str(ref, NXR + "taxonomyCategory"),
            "abstract": self._bool(ref, NXR + "isAbstract"),
            "directSuperclasses": self._qlist(
                o for o in self.g.objects(ref, RDFS.subClassOf)
                if isinstance(o, URIRef)),
            "stateMachine": self._state_machine(sm) if sm else None,
        }

    # ---------- "how does this class behave?" (the binding layer) ----------
    def behavior_profile(self, class_name: str) -> Dict:
        """Resolve a class's behaviour binding: its dynamics archetype + default
        params and its monitoring rules, walking rdfs:subClassOf for the nearest
        binding (so subclasses inherit, packs/agents override). This is the
        agent-facing 'how does X behave?' surface, paired with legal_types/
        properties_of ('what is X?'). Reads the nxr-behavior-bindings layer."""
        iri = self._resolve(class_name)
        if iri is None:
            raise KeyError(f"Unknown class: {class_name}")
        DYN = URIRef(NXR + "dynamicsArchetype")
        PARAMS = URIRef(NXR + "archetypeParams")
        MON = URIRef(NXR + "monitoringRules")
        seen, frontier = set(), [URIRef(iri)]
        while frontier:
            cur = frontier.pop(0)
            if cur in seen:
                continue
            seen.add(cur)
            arch = self.g.value(cur, DYN)
            if arch is not None:
                def _j(p, d):
                    v = self.g.value(cur, p)
                    if not v:
                        return d
                    try:
                        return json.loads(str(v))
                    except Exception:
                        return d
                return {
                    "class": _qname(iri), "iri": iri,
                    "boundOn": _qname(str(cur)),
                    "dynamics": {"archetype": str(arch), "params": _j(PARAMS, {})},
                    "monitoring": _j(MON, []),
                }
            frontier.extend(self.g.objects(cur, RDFS.subClassOf))
        return {"class": _qname(iri), "iri": iri, "boundOn": None,
                "dynamics": None, "monitoring": []}

    # ---------- delegate to the write gate ----------
    def validate(self, mutation) -> Dict:
        result = gate.validate(mutation)
        return {
            "conforms": result.conforms,
            "violations": [asdict(v) for v in result.violations],
        }

    # ---------- is the ontology itself well-formed? ----------
    def validate_governance(self) -> Dict:
        """Validate the T-Box against the taxonomy-closure shape: every
        entity class carries exactly one of the ten categories, none beyond.
        Uses the governance shapes ONLY (not the per-mutation gate shapes),
        with the ontology itself as the data graph."""
        from pyshacl import validate as shacl_validate
        data = build_graph(include_packs=True, reason=False)
        gov_shapes = build_governance_shapes()
        conforms, report_graph, _ = shacl_validate(
            data_graph=data, shacl_graph=gov_shapes, ont_graph=data,
            inference="none", advanced=True, debug=False,
        )
        violations = [] if conforms else gate._extract_violations(report_graph)
        return {
            "conforms": conforms,
            "violations": [asdict(v) for v in violations],
        }

    # ================= internals =================
    def _resolve(self, name: str) -> Optional[str]:
        if name.startswith("http"):
            iri = name
        elif name.startswith("nxr:"):
            iri = NXR + name[4:]
        elif name.startswith("hvac:"):
            iri = HVAC + name[5:]
        elif name.startswith("cfp:"):
            iri = CFP + name[4:]
        else:
            iri = NXR + name  # bare name defaults to core
        if (URIRef(iri), RDF.type, OWL.Class) in self.g:
            return iri
        # fall back: try cfp then hvac namespace for a bare name
        for ns in (CFP, HVAC):
            alt = ns + name
            if (URIRef(alt), RDF.type, OWL.Class) in self.g:
                return alt
        alt = HVAC + name
        if (URIRef(alt), RDF.type, OWL.Class) in self.g:
            return alt
        return None

    def _supers(self, iri: str) -> set:
        """Transitive closure over authored rdfs:subClassOf, self included."""
        supers = set()
        frontier = [URIRef(iri)]
        while frontier:
            cur = frontier.pop()
            if cur in supers:
                continue
            supers.add(cur)
            for s in self.g.objects(cur, RDFS.subClassOf):
                if isinstance(s, URIRef) and s not in supers:
                    frontier.append(s)
        return supers

    def _shape_props(self, shape: URIRef):
        """Yield (path, required, datatype_qname) for each sh:property of a shape."""
        for pshape in self.g.objects(shape, SH.property):
            path = self.g.value(pshape, SH.path)
            if path is None:
                continue
            minc = self.g.value(pshape, SH.minCount)
            required = minc is not None and int(minc) >= 1
            dt = self.g.value(pshape, SH.datatype)
            yield path, required, (_qname(str(dt)) if dt else None)

    def _add_prop(self, props, path, origin, kind=None, required=False,
                  datatype=None):
        key = str(path)
        q = _qname(key)
        existing = props.get(key)
        if existing is None:
            props[key] = {
                "id": q,
                "label": self._label(path) or q.split(":")[-1],
                "kind": kind or self._prop_kind(path),
                "range": self._qlist(self.g.objects(path, RDFS.range)) or
                         ([datatype] if datatype else []),
                "required": required,
                "origin": origin,
            }
        else:
            existing["required"] = existing["required"] or required

    def _prop_kind(self, p) -> str:
        if (p, RDF.type, OWL.ObjectProperty) in self.g:
            return "object"
        if (p, RDF.type, OWL.DatatypeProperty) in self.g:
            return "datatype"
        return "unknown"

    def _state_machine(self, sm) -> Dict:
        states = [self._label(s) for s in self.g.objects(sm, URIRef(NXR + "hasState"))]
        init = self.g.value(sm, URIRef(NXR + "initialState"))
        transitions = []
        for t in self.g.objects(sm, URIRef(NXR + "allowsTransition")):
            f = self.g.value(t, URIRef(NXR + "fromState"))
            to = self.g.value(t, URIRef(NXR + "toState"))
            transitions.append({"from": self._label(f), "to": self._label(to)})
        return {
            "id": _qname(str(sm)),
            "initial": self._label(init) if init else None,
            "states": sorted(states),
            "transitions": transitions,
        }

    # tiny rdflib helpers
    def _label(self, s) -> str:
        v = self.g.value(s, RDFS.label)
        return str(v) if v else ""

    def _def(self, s) -> str:
        v = self.g.value(s, SKOS.definition)
        return str(v) if v else ""

    def _str(self, s, pred) -> Optional[str]:
        v = self.g.value(s, URIRef(pred))
        return str(v) if v is not None else None

    def _bool(self, s, pred) -> bool:
        v = self.g.value(s, URIRef(pred))
        return bool(v) and str(v).lower() == "true"

    def _qlist(self, nodes) -> List[str]:
        return sorted({_qname(str(n)) for n in nodes if isinstance(n, URIRef)})
