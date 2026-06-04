# dynamics — the generative Entity-Dynamics layer

This is the **generative** half of the twin: it *produces* live, coupled,
engineering-grounded telemetry for every entity, driven by the ontology. It
complements `behaviors/` (which *detects* anomalies). The detection behaviours are
untouched — they simply start watching real, fluctuating, coupled signals instead
of the old scripted ramps in `feed/simulate*.py`.

## Why it's universal (not HVAC-specific)

The engine is **domain-agnostic**. It never names a system. It only knows:
1. the **relationship graph** (`feeds`, `backsUp`, `suppliesAirTo`, `controls`,
   `monitors`, `containedIn`, …), loaded from Neo4j, and
2. a tiny **flow vocabulary** (`flows.py`) classifying predicates into ELECTRICAL /
   THERMAL_FLUID / AIR / DATA / CONTROL / SPATIAL / OBSERVATION / BACKUP.

A model declares what it **consumes** and **produces**; the engine resolves edges
and hands each model its neighbours' published `EntityState`. Add a maritime or
hospital pack, declare its edges, register its models — coupling works identically,
zero engine changes.

## Structural coupling stays LOOSE (as originally planned)

Models never import or call each other. A `ChillerModel` knows nothing about
`ServerModel`. Each is a pure `step(ctx, state)` that only reads neighbours'
published `signals`, which the engine (the **mediator**) provides. Behaviourally the
physics is tightly coupled (energy/heat/data propagate); structurally the code is
loosely coupled. Cycles (zone heats server ↔ server heats zone) are broken with a
**1-tick lag** (read last tick's value), so ordering never blocks.

## Files

```
model.py          DynamicsModel contract, EntityState, EntityContext, DynamicsRegistry
flows.py          flow vocabulary + helpers to pick inputs by flow/signal
engine.py         DynamicsEngine: topology load, dependency order, real-time speed loop
registry_build.py registers all models (one line per model)
models/
  spaces.py       ZoneThermalModel (the coupling medium — lumped RC thermal balance)
  electrical.py   UtilityFeed, Transformer (IEEE C57.91 oil thermal), UPS (Peukert)
  hvac.py         Chiller (Carnot/IPLV), AirHandler (fan affinity + coil + filter)
  it.py           Server (nonlinear power curve + heat feedback + thermal throttling)
  default.py      DefaultEquipmentModel — fallback for any unmodelled FacilityEquipment
smoke_test.py     coupled engine proof without Neo4j  (python -m dynamics.smoke_test)
```

## The 4-layer reality stack (every model)

```
output = ideal_model(inputs, params)          # physics/engineering equation
         * efficiency(load, condition, temp)   # losses: η<1, part-load / PSU / COP curves
         + degradation(runHours, wear, fouling)# slow drift; modulated by conditionIndex
         + fluctuation(rng, sigma) / events()  # fast noise/ripple + discrete faults
```
That separation is "ideal vs real": efficiency is where *the desired power/current
isn't perfectly delivered*; degradation is fouling/wear over runtime; fluctuation is
the noise; events flip the state machine and cascade through the topology.

## Parameters: graph node props > bundle `dynamics` block > model default

`ctx.param(key, default)` / `ctx.fnum(key, default)` resolve in that order. So a
twin's per-instance node props (ratedCapacity, setpoint, conditionIndex) win; a
vertical's authored bundle can ship a `dynamics: {<class IRI>: {param: value}}`
block; otherwise the model's sensible default applies.

## Add a model for a new class (the whole workflow)

```python
# dynamics/models/water.py
from dynamics.model import DynamicsModel, EntityState
CFP = "https://ontology.nextxr.io/v3/cfp#"
class WaterTankModel(DynamicsModel):
    models = [CFP + "WaterTank"]
    produces = [CFP + "fillLevel"]
    consumes = ["inflow (pump upstream), demand (downstream)"]
    def init_state(self, ctx):
        return EntityState(internal={"vol": ctx.fnum("initialVolM3", 8.0)})
    def step(self, ctx, state):
        vmax = ctx.fnum("capacityM3", 10.0)
        inflow = ...   # read upstream pump flow from ctx.inputs
        demand = ctx.fnum("demandLps", 0.5)
        leak   = ctx.fnum("leakLps", 0.0)
        vol = max(0.0, state.internal["vol"] + (inflow - demand - leak)*ctx.dt/1000)
        state.internal["vol"] = vol
        state.signals = {CFP+"tankLevel": round(100*vol/vmax,1)}
        return state
```
Then one line in `registry_build.py`: `r.register(WaterTankModel())`. Because
resolution is subclass-aware, a model on a base class covers all subclasses.

## Wiring into the live feed loop (server/main.py)

Replace the scripted sample source with the engine for a tenant. Minimal,
non-destructive version (keeps scripted mode as default):

```python
from dynamics import build_dynamics_registry, DynamicsEngine

def _run_dynamics_loop(tenant, cl, speed=60.0):
    writer = GraphWriter(changelog=cl); query = GraphQuery()
    registry = _build_registry()                 # detection behaviours (unchanged)
    loop = FindingsLoop(registry, writer, query)
    eng = DynamicsEngine(tenant, build_dynamics_registry(), query, speed=speed)

    def on_samples(samples):
        for s in samples:
            loop.process(s)                       # same detection + Finding write path
        with _feed_lock:
            _feed_state["samples_processed"] += len(samples)
            for s in samples:
                _feed_state["signals"][s.signal] = round(s.value, 2)

    eng.run_realtime(on_samples,
                     should_stop=lambda: not _feed_state["running"])
    # periodically call eng.persist(writer) to push status changes to the graph
```

The detection behaviours, change log, event bus, diagnosis chain, and dashboard all
work unchanged — they now see realistic coupled telemetry.

## What's built vs. what remains

**Built + proven (spine):** engine, contract, flow vocabulary, subclass-aware
registry, and engineering-accurate models for Zone, UtilityFeed, Transformer, UPS,
Chiller, AirHandler, Server, plus the Default fallback (so every entity emits
something today).

**Remaining models (same pattern, ~per class):** Boiler, CoolingTower, Pump/WaterPump
(affinity + vibration), Generator (fuel burn), Solar/Battery, EnergyMeter/Circuit
aggregators, smoke/heat/flame detectors, access/door/reader event generators,
water tank/meter/leak, elevator/escalator, environmental sensors (derived observers),
network switch/AP/recorder, occupancy/people/vehicles, work-order lifecycle.
See `BEHAVIOR_MODEL_ANALYSIS.md §4` for the formula for each.
