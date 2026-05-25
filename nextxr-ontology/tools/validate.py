#!/usr/bin/env python3
"""
validate.py — the validation harness.

It no longer re-implements validation: it drives the SAME `gate.validate()`
function the Graph Writer will call, against a cohort of fixtures. This is
how we test the gate hard, in isolation, before anything wires it to Neo4j.

  - VALID fixtures   -> must PASS
  - INVALID fixtures -> must FAIL (and we show the first violation)
  - a GOVERNANCE check -> the ontology itself obeys the closed taxonomy

Each VALID fixture is a self-consistent subgraph: every *typed* node carries
the 10-property base, because the gate validates every node it is handed.
(A real Graph Writer hands the gate the new node plus the existing graph;
here we make the subgraph complete so the test is isolated and honest.)

Usage:
    python tools/validate.py
"""

import sys
import uuid

import gate
from schema_service import SchemaService

PREFIXES = """
@prefix nxr:  <https://ontology.nextxr.io/v3/core#> .
@prefix hvac: <https://ontology.nextxr.io/v3/hvac#> .
@prefix sosa: <http://www.w3.org/ns/sosa/> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
"""

IRI_BASE = "https://ontology.nextxr.io/v3/"


def entity(uri, rtype, canonical_suffix, *extra):
    """Render a node that satisfies the 10-property base shape, plus any
    extra predicate lines. `canonical_suffix` is e.g. 'core#MaintenanceEvent'."""
    lines = [
        f'nxr:id "{uuid.uuid4()}"',
        'nxr:tenantId "jnpa-sheva"',
        f'nxr:canonicalType "{IRI_BASE}{canonical_suffix}"^^xsd:anyURI',
        'nxr:createdAt "2026-05-25T10:00:00Z"^^xsd:dateTime',
        'nxr:updatedAt "2026-05-25T10:00:00Z"^^xsd:dateTime',
        'nxr:createdBy "agent:graph-writer"',
        *extra,
    ]
    return f"{uri} a {rtype} ;\n    " + " ;\n    ".join(lines) + " .\n"


# ---- A well-formed air handler serving a complete space: should PASS ----
VALID_AIRHANDLER = PREFIXES + (
    entity("<urn:ah:1>", "hvac:AirHandler", "hvac#AirHandler",
           'nxr:displayName "AHU-01"', 'nxr:status "running"',
           "hvac:servesSpace <urn:space:lobby>")
    + entity("<urn:space:lobby>", "nxr:Space", "core#Space",
             'nxr:displayName "Main Lobby"')
)

# ---- A well-formed maintenance event targeting a real asset: should PASS ----
VALID_MAINTENANCE = PREFIXES + (
    entity("<urn:me:1>", "nxr:MaintenanceEvent", "core#MaintenanceEvent",
           "nxr:targetsAsset <urn:chiller:9>")
    + entity("<urn:chiller:9>", "hvac:Chiller", "hvac#Chiller",
             'nxr:displayName "CH-09"')
)

# ---- Missing tenantId AND missing servesSpace: should FAIL ----
INVALID_AIRHANDLER = PREFIXES + """
<urn:ah:2> a hvac:AirHandler ;
    nxr:id "018f4c2a-1b2c-7d3e-8f90-000000000000" ;
    nxr:canonicalType "https://ontology.nextxr.io/v3/hvac#AirHandler"^^xsd:anyURI ;
    nxr:createdAt "2026-05-25T10:00:00Z"^^xsd:dateTime ;
    nxr:updatedAt "2026-05-25T10:00:00Z"^^xsd:dateTime ;
    nxr:createdBy "agent:graph-writer" .
"""

# ---- A quantitative result WITHOUT a unit: should FAIL ----
INVALID_RESULT_NO_UNIT = PREFIXES + """
<urn:res:1> a nxr:QuantitativeResult ;
    nxr:numericValue "4.0"^^xsd:double ;
    nxr:hasQuantityKind <http://qudt.org/vocab/quantitykind/Temperature> .
"""

# ---- An action with a bad execution mode: should FAIL ----
INVALID_ACTION_MODE = PREFIXES + entity(
    "<urn:act:1>", "nxr:Action", "core#Action",
    'nxr:executionMode "auto-pilot"')

# ---- ILLEGAL RELATIONSHIP: a Port connected to a non-Port (a Space): FAIL ----
#  Every node here is complete, so the ONLY violation is the illegal link.
INVALID_ILLEGAL_RELATIONSHIP = PREFIXES + (
    entity("<urn:port:1>", "nxr:InputPort", "core#InputPort",
           "nxr:portOf <urn:chiller:9>",
           'nxr:carries "chilled-water"',
           "nxr:connectsTo <urn:space:lobby>")          # illegal: Space is not a Port
    + entity("<urn:chiller:9>", "hvac:Chiller", "hvac#Chiller")
    + entity("<urn:space:lobby>", "nxr:Space", "core#Space")
)

FIXTURES = [
    ("VALID  air handler",                          VALID_AIRHANDLER,             True),
    ("VALID  maintenance event",                    VALID_MAINTENANCE,            True),
    ("INVALID air handler (no tenant/space)",       INVALID_AIRHANDLER,           False),
    ("INVALID result (no unit)",                    INVALID_RESULT_NO_UNIT,       False),
    ("INVALID action (bad mode)",                   INVALID_ACTION_MODE,          False),
    ("INVALID illegal relationship (port->space)",  INVALID_ILLEGAL_RELATIONSHIP, False),
]


def run():
    print("Validation harness — driving gate.validate(); "
          "valid must PASS, invalid must FAIL\n")
    passed_meta = 0
    for name, data_ttl, should_pass in FIXTURES:
        result = gate.validate(data_ttl)
        ok = (result.conforms == should_pass)
        passed_meta += ok
        verdict = "OK " if ok else "XX "
        outcome = "PASS" if result.conforms else "FAIL"
        print(f"  [{verdict}] {name:<46} -> {outcome} "
              f"({'as expected' if ok else 'UNEXPECTED'})")
        if result.violations and not should_pass:
            print(f"          {result.violations[0].message}")

    # --- Governance: the ontology itself obeys the closed taxonomy ---
    gov = SchemaService.load().validate_governance()
    gov_ok = gov["conforms"]
    passed_meta += gov_ok
    print(f"\n  [{'OK ' if gov_ok else 'XX '}] GOVERNANCE: ontology obeys the "
          f"closed 10-category taxonomy -> {'CONFORMS' if gov_ok else 'VIOLATION'}")
    if not gov_ok:
        for v in gov["violations"][:3]:
            print(f"          {v['message']}")

    total = len(FIXTURES) + 1
    print(f"\n  Harness result: {passed_meta}/{total} behaved as expected.")
    return passed_meta == total


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
