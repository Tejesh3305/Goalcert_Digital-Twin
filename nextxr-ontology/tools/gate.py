#!/usr/bin/env python3
"""
gate.py — THE write gate.

This is the reusable `validate(proposed_mutation) -> pass | violation`
function the spec calls for: the single function every write passes
through before it touches the graph. Track 2's Graph Writer imports
THIS; it does not re-implement validation.

    from tools.gate import validate
    result = validate(turtle_or_graph)
    if result.ok:
        commit(...)
    else:
        for v in result.violations:
            log(v.message)

Design notes:
  * The shapes+ontology graph is built once and cached (loading it is
    the expensive part; validating a single mutation is cheap).
  * Inference is OFF by design. pySHACL already honours rdfs:subClassOf
    when matching sh:targetClass and checking sh:class (SHACL "instance
    of" semantics), so an hvac:AirHandler still inherits nxr:Equipment /
    nxr:PhysicalAsset shapes. Turning RDFS *entailment* on would, by
    contrast, apply rdfs:range to every relation — inferring that the
    target of nxr:connectsTo "is a" Port — which would silently legalise
    exactly the illegal relationships this gate must reject.
  * It returns a structured result, never raises on a *validation*
    failure — a failed mutation is a normal, expected outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import List, Union

try:
    from rdflib import Graph, RDF
    from rdflib.namespace import SH
    from pyshacl import validate as _shacl_validate
except ImportError:  # pragma: no cover
    raise SystemExit("Install deps first:  pip install rdflib owlrl pyshacl")

from ontology_graph import build_graph


@dataclass
class Violation:
    """One SHACL constraint violation, flattened for easy logging."""
    focus_node: str
    path: str
    message: str
    severity: str
    value: str = ""

    def __str__(self) -> str:
        loc = f"{self.focus_node}"
        if self.path:
            loc += f" [{self.path}]"
        return f"{self.severity}: {loc} — {self.message}"


@dataclass
class ValidationResult:
    """The verdict of the gate. Truthy iff the mutation conforms."""
    conforms: bool
    violations: List[Violation] = field(default_factory=list)
    report_text: str = ""

    # 'pass | violation' — read either way.
    @property
    def ok(self) -> bool:
        return self.conforms

    def __bool__(self) -> bool:
        return self.conforms


@lru_cache(maxsize=1)
def _shapes_graph() -> Graph:
    """Ontology + shapes, built once. Serves as both shacl_graph and
    ont_graph so class/subclass definitions are available for sh:node
    inheritance and sh:class checks."""
    return build_graph(include_packs=True, reason=False)


def ontology_graph() -> Graph:
    """The cached ontology + shapes graph, exposed for callers that need to
    query the T-Box itself — e.g. the Graph Writer resolving a class's
    nxr:taxonomyCategory (its Neo4j label) before a write. Read-only by
    convention; do not mutate the returned graph."""
    return _shapes_graph()


def _as_graph(mutation: Union[str, Graph]) -> Graph:
    if isinstance(mutation, Graph):
        return mutation
    g = Graph()
    g.parse(data=mutation, format="turtle")
    return g


def _extract_violations(report_graph: Graph) -> List[Violation]:
    out: List[Violation] = []
    for res in report_graph.subjects(RDF.type, SH.ValidationResult):
        def one(p):
            v = report_graph.value(res, p)
            return str(v) if v is not None else ""
        sev = one(SH.resultSeverity).rsplit("#", 1)[-1] or "Violation"
        out.append(Violation(
            focus_node=one(SH.focusNode),
            path=one(SH.resultPath),
            message=one(SH.resultMessage),
            severity=sev,
            value=one(SH.value),
        ))
    return out


def validate(mutation: Union[str, Graph]) -> ValidationResult:
    """Validate a proposed mutation (a Turtle string or an rdflib Graph
    describing the node(s) about to be written) against the full
    NextXR shape set. Returns a ValidationResult — pass or violation."""
    data = _as_graph(mutation)
    shapes = _shapes_graph()
    conforms, report_graph, report_text = _shacl_validate(
        data_graph=data,
        shacl_graph=shapes,
        ont_graph=shapes,
        inference="none",     # see module docstring: rdfs:range entailment
        advanced=True,        # would legalise illegal relationships
        debug=False,
    )
    violations = [] if conforms else _extract_violations(report_graph)
    return ValidationResult(conforms=conforms, violations=violations,
                            report_text=report_text)


# Convenience alias matching the spec's wording.
validate_mutation = validate
