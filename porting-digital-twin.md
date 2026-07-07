# Porting Guide — Digital Twin Platform

**Your role:** own the live, physics-grounded model of the asset and its world —
telemetry, findings, health, prediction, the knowledge graph, and BIM semantics.
Everything else asks *you* what's true about the asset.

**Good news:** you are the *least* work of the four. The demo already imports its
physics straight from your codebase — most of what's "in the demo" is actually a thin
in-process wrapper around code that already lives with you. Your job is mostly to
**expose what you already have as service routes**, plus add two new surfaces.

---

## 1. Your source of truth in the reference

| Reference file | What it is | Note |
|---|---|---|
| `nextxr-ontology/{edm,turbine,fleet}/` | the domain physics, predict, behaviours | **already yours** — the demo imports these |
| `collins-demo/orchestrator/engine.py` | the in-process twin runtime: `LiveTwin`, `DOMAINS`, the 1 Hz ticker, `network()` | the runtime behaviour to reproduce as a service |
| `collins-demo/orchestrator/nextxr.py` | the client the demo calls — its function names/shapes are your **de-facto API contract** | mirror these shapes exactly |
| `collins-demo/orchestrator/bim_ifc.py` | IFC → discipline-layer split + element metadata | the *semantics* half belongs to you (see §4) |
| `collins-demo/orchestrator/routes.py` (twin routes) | `/twins/*`, `/twin/{tenant}/*`, `/twins/{tenant}/network` | the exact endpoint list to serve |

## 2. Build ON your existing patterns — don't rebuild

You already have the real service skeleton in `nextxr-ontology/server/`:
`twins_routes.py`, `ingest_routes.py`, `scenario_routes.py`, `bim_routes.py`,
`query_api.py`, `schema_routes.py`, plus an event bus. **Extend these — do not port
`engine.py` verbatim.** `engine.py` exists only because the demo went database-free;
your platform has the graph + bus that `engine.py` deliberately skipped.

The one genuinely portable idea from `engine.py` is the **per-tenant live ticker**:
a background loop that advances each twin's physics ~1 Hz, runs the behaviour registry,
and keeps the latest frame + findings in memory. Wrap that around your existing
`ingest` service rather than around a bare dict.

## 3. Feature-by-feature mapping

| Feature | In the demo (`engine.py` / physics) | Build in your platform |
|---|---|---|
| Twin lifecycle & templates | `Engine.build()`, `DOMAINS` registry | extend `twins_routes.py` (`/templates`, create) — register `edm`, `turbine`, **`tram-network`** |
| Live telemetry ticker | `Engine._tick()`, `LiveTwin.simulate()` | a background task feeding `ingest` per tenant |
| Behaviour rules → findings | `LiveTwin._step()` calls `registry.evaluate()` | already in `behaviors/`; surface via `ingest_routes` diagnostics |
| Health, prediction, RUL | `predict_forward()`, `component_health` | already in `{domain}/predict.py`; surface via `/predict` |
| What-if projection | `LiveTwin.project()` | already have `scenario_routes.project`; confirm it matches the contract |
| **Fleet network map** (NEW) | `LiveTwin.network()` + `fleet/physics.py` `network_state()` | **new endpoint** `/twins/{tenant}/network` — see §4 |
| **BIM discipline layers** (NEW) | `bim_ifc.py` classify + split | **semantics** into `bim_routes.py`; conversion goes to Generation (see §4) |
| Start/stop twin | `set_running()` toggles `LiveTwin.live` | `/twins/{tenant}/running` gates the ticker |

## 4. The two things that are genuinely new

**(a) Fleet network endpoint.** The tram/fleet domain (`nextxr-ontology/fleet/`) is new
and already yours, but its *spatial* surface only exists in the demo's `engine.py`
(`LiveTwin.network()`). Port that method: it returns the network geometry (nodes,
routes, depots, substations), live vehicle positions, and per-route status. Expose as
`GET /twins/{tenant}/network`. The physics (`fleet/physics.py` `network_state()`,
`_advance_vehicles()`) is already in your tree — you're only adding the route.

**(b) BIM — split of responsibility.** `bim_ifc.py` does two jobs; they split across
two platforms:
- **Conversion** (IFC → per-discipline GLB geometry) → **Generation platform**.
- **Semantics** (element index: guid, class, storey, discipline, fault-to-element
  mapping, the X-ray overlay) → **you**. Your `bim_routes.py` already has
  `upload` / `model.glb` / `mapping` / `status` — extend `mapping` to carry the
  discipline + element metadata so a twin finding can highlight the exact pipe/duct.

## 5. Contract you must expose

Exactly the **Digital Twin** block in [README.md](README.md#digital-twin--apiv1). The demo's
`nextxr.py` function names map 1:1 — if `nextxr.py` returns it, your route must return
the same shape, because the hub (and the demo, during migration) call these verbatim.

## 6. Do NOT copy these demo shortcuts

- **In-memory-only findings.** The demo keeps findings in a Python list because it has no
  graph. You have one — persist findings + changelog so history/query work.
- **The single-process ticker for everything.** Fine for a demo; for you it should be a
  proper background worker per tenant (or an event-driven consumer) so it scales.
- **Hard-coded `DOMAINS` dict as the source of truth.** Domains should be discoverable
  from your registered packs/templates, not a literal dict copied from `engine.py`.

## 7. Acceptance criteria

- [ ] `POST /twins {domain:"tram-network"}` seeds a twin; `GET /twins/{t}/state` streams 22 fleet signals with health.
- [ ] `GET /twins/{t}/network` returns geometry + moving vehicles + route status; injecting `ohl_damage` marks a route blocked.
- [ ] `GET /twins/{t}/predict` returns a trajectory + RUL; `POST /project` runs a non-destructive what-if.
- [ ] `edm-machine` and `turbine-engine` twins behave identically to the demo (regression check against `nextxr.py` shapes).
- [ ] BIM `mapping` returns element→discipline→storey so a finding can point at a component.
- [ ] Toggling `running:false` freezes the ticker; `true` resumes it.

**Recommended first PR:** register the three domains + the live ticker over your existing
`ingest`/`twins` routes, then add `/network`. That alone makes the demo runnable against
your service instead of its in-process engine.
