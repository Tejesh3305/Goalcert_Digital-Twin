# NextXR Platform Ontology — v3 core

The semantic backbone of the Universal Modular Digital Twin. Everything
that enters the graph is validated against this. Build it once, with rigor;
every future domain pack extends it without ever modifying it.

## The layers (and the one rule that protects them)

```
imports/        Layer 1 + 2  — BFO (ISO 21838) and SOSA/SSN (W3C). BORROWED. Never edited.
platform/       Layer 3      — the NextXR mid-level. THE PART YOU AUTHOR. Frozen; bumped by version.
                  nxr-classes / nxr-properties / nxr-units   the T-Box
                  nxr-base-shape / nxr-shapes                the SHACL contract
                  nxr-taxonomy                               the ten closed categories
                  nxr-governance                             enforces the closed set on the T-Box
packs/          Layer 4      — per-vertical extensions (hvac/ is the test pack). Where verticals live.
tools/          ontology_graph (shared loader) · gate (the validate() write-gate)
                load (assemble + reason) · validate (harness) · schema_service + schema_api (query)
```

**The freeze rule:** packs add classes *downward* as `rdfs:subClassOf` the platform
mid-level. They never edit layers 1–3. Adding a class to `platform/` is a platform
version bump (v3 → v4), governed and migration-checked. This is what guarantees a
hospital twin and a port twin remain semantically interoperable forever.

## What's in the platform mid-level (~29 core classes)

| Group | Classes | BFO/SOSA grounding |
|---|---|---|
| Material — fixed | PhysicalAsset, Equipment, Sensor | BFO material entity (+ sosa:Sensor) |
| Material — mobile | MobileAsset | BFO material entity + motion disposition |
| Agents | Actor | BFO continuant bearing agent role |
| Places | Site, Space | BFO site |
| Occurrents | Process, Incident, MaintenanceEvent, Action | BFO process |
| Observation | Observation, QuantitativeResult | SOSA |
| Reasoning chain | Finding, FailureMode, Diagnosis, Recommendation | BFO disposition / information |
| Information | Document | BFO generically dependent continuant |
| Self-model | Capability, CapabilityBundle, IntegrationAdapter, MLModel | BFO disposition |
| Structure | Port, InputPort, OutputPort | BFO specifically dependent continuant |
| State machines | StateMachine, State, Transition | — |

Plus: the **10-property base shape** every entity inherits, the **predicate
vocabulary** with domain/range and logical properties (transitive, inverse,
functional, symmetric), **QUDT units** so every measurement is self-describing,
**typed ports** for structural connection rigor, and a **state-machine pattern**
so any class can declare its own legal lifecycle.

## The closed taxonomy (and how it's enforced)

The ten top-level categories — `PhysicalAsset, MobileAsset, Actor, Location,
Process, Observation, Finding, Incident, Document, Capability` — are a **fixed,
closed set**. Every entity class (core or pack) declares exactly one via
`nxr:taxonomyCategory`; the category becomes the Neo4j label. `nxr-governance.ttl`
makes this machine-checkable: `SchemaService.validate_governance()` rejects any
class that omits a category or invents an eleventh one. Plumbing classes
(state-machine vocabulary, quantitative results) are marked `nxr:isStructural`
and exempt.

## The write gate

`tools/gate.py` exposes the function the whole platform is built around:

```python
from gate import validate          # validate(turtle_or_graph) -> ValidationResult
result = validate(proposed_mutation)
if result.ok:
    commit(...)
else:
    for v in result.violations:    # focus_node, path, message, severity, value
        log(str(v))
```

Inference is deliberately **off**: pySHACL still honours `rdfs:subClassOf` for
`sh:targetClass`/`sh:class`, but RDFS *entailment* would apply `rdfs:range` to
every relation (inferring the target of `connectsTo` "is a" Port) and silently
legalise the very illegal relationships the gate must reject.

## The schema-query API

So other services ask the ontology instead of hardcoding it. Library core +
a thin HTTP wrapper give identical answers:

```python
from schema_service import SchemaService
svc = SchemaService.load()
svc.version                      # 'v3.0.0'
svc.legal_types(instantiable_only=True)
svc.taxonomy_categories()        # the ten
svc.predicates()                 # relations + domain/range/characteristics
svc.properties_of("Equipment")   # everything an Equipment can carry, base + scoped
svc.class_info("hvac:AirHandler")
```

## Run it

```bash
pip install rdflib owlrl pyshacl              # core
pip install fastapi uvicorn                   # only for the HTTP surface

python tools/load.py            # assemble layers + OWL-RL reasoning + summary
python tools/validate.py        # gate harness: valid PASS, invalid FAIL, + governance
python tools/load.py --export materialised.ttl   # dump the reasoned graph

# HTTP schema-query API (from the tools/ dir):
uvicorn schema_api:app --port 8000
# e.g. GET http://localhost:8000/v3/schema/class/Equipment/properties
```

## The import stubs

`imports/bfo.ttl` and `imports/sosa.ttl` declare only the upper-ontology terms we
reference, with their **official IRIs**. For full reasoning, download the official
releases and drop them in alongside — the IRIs line up exactly.
- BFO: https://github.com/BFO-ontology/BFO-2020
- SOSA/SSN: https://www.w3.org/TR/vocab-ssn/
- QUDT: https://qudt.org/

## Next

This is the T-Box, plus the runnable contract around it: a reusable
`gate.validate()` and a versioned schema-query service. The next backbone piece
(Track 2) is the **Graph Writer** — the single component that calls
`gate.validate()` before every Neo4j commit and emits a Change Log event. It
imports the gate as-is; it does not re-implement validation.
