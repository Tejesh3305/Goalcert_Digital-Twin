# Domain packs

Every domain vertical lives in **one self-contained folder** under `packs/<domain>/`.
A pack co-locates its ontology **and** its runtime code — nothing domain-specific
sits next to the platform core anymore.

## Layout of a machine-twin pack

```
packs/<domain>/
  <domain>-classes.ttl     OWL classes, observable properties, object properties,
                           failure modes  (Layer-4 ontology, imports v3/core)
  <domain>-shapes.ttl      SHACL node/property shapes (datatype + value ranges)
  physics.py               component physics engines + the live twin's forward
                           model (init_state / forward / inject / residuals /
                           health_index [+ network_state])
  behaviors.py             3-tier edge-latched behaviour registry (build_*_registry)
  predict.py               subsystem component_health + forward predict / RUL
  __init__.py              the self-describing SPEC (or SPEC + a SPECS list when a
                           pack ships more than one twin, e.g. railway metro +
                           trainset, ev network + battery, defence base + warship)
```

Some packs add extra twin modules alongside (`railway/trainset.py`, `ev/battery.py`,
`defence/warship.py`) — each exposes its own SPEC that `__init__.py` collects into
`SPECS`.

## How a pack is discovered

* **Ontology** — the TTL files are registered in
  [`tools/ontology_graph.py`](../tools/ontology_graph.py) (`MACHINE_FILES` /
  `DOMAIN_FILES`) and loaded by the SHACL gate.
* **Runtime** — [`twins/runtime.py`](../twins/runtime.py) `load_specs()` imports
  `packs.<domain>` and reads its `SPEC` / `SPECS`. Add a new pack's name to that
  tuple and it appears as a live machine-twin domain (state / diagnostics /
  predict / network routes + the build-a-twin UI) with no other wiring.
* **Seeding** — `twins/service.py` holds a template + `_seed_*` builders that
  create each pack's entity-relationship graph through the Graph Writer.
* **Relationship predicates** — the CURIE prefix for a pack's object properties is
  registered in [`graph/writer.py`](../graph/writer.py) `PREFIXES`.

## Machine-twin packs

| Pack | Twins (SPEC keys) |
|------|-------------------|
| `turbine`  | `turbine-engine` |
| `edm`      | `edm-machine` |
| `fleet`    | `tram-network` |
| `railway`  | `railway-metro`, `railway-trainset` |
| `hospital` | `hospital-campus` (also holds the CFP functional hospital pack TTL) |
| `ev`       | `ev-charging-network`, `ev-battery-pack` |
| `defence`  | `defence-base`, `defence-warship` |

## Ontology-only / functional packs

`cfp`, `bim`, `hvac`, `datacenter` (and the CFP-feed side of `hospital`) are
ontology + behaviour-binding packs consumed by the plan→3D / simulated-feed path,
not the machine-twin runtime.
