"""
model.py — the generative Entity-Dynamics contract.

This is the GENERATIVE counterpart to behaviors/registry.py. A behavior DETECTS
anomalies in telemetry; a DynamicsModel PRODUCES telemetry by advancing an
entity's physical state one tick at a time, given:

  * the entity's own state (hidden internal state + last signals),
  * its graph node properties (ratings, setpoint, conditionIndex, …),
  * the resolved EntityState of every UPSTREAM neighbour (its inputs), grouped
    by the ontology predicate that connects them, and
  * the time step.

Models are STRUCTURALLY decoupled: a model never imports or calls another model.
It only reads the published `signals` of its neighbours, which the DynamicsEngine
(the mediator) hands it. This keeps the system loosely coupled in code while the
physics is tightly coupled in behaviour — and, crucially, the engine is fully
domain-agnostic, so the same coupling mechanism works for HVAC, power, maritime,
hospital, or any future pack with zero engine changes.

Every model composes the same four-layer reality stack:

    output = ideal_model(inputs, params)            # the physics/engineering eq.
             * efficiency(load, condition, temp)     # losses (η<1, part-load)
             + degradation(runHours, wear, fouling)  # slow drift over time
             + fluctuation(rng, sigma) / events(mode)# noise + discrete faults

so "ideal vs real" is consistent and configurable everywhere.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


# --------------------------------------------------------------------------
#  State + context
# --------------------------------------------------------------------------
@dataclass
class EntityState:
    """The evolving state of one entity. `signals` is what sensors/the feed read;
    `internal` is hidden physical state (SoC, oil_temp, dust_mass, bearing_wear…)."""
    status: str = "running"                 # off/running/degraded/fault (state machine)
    signals: dict[str, float] = field(default_factory=dict)   # signal IRI -> value
    internal: dict = field(default_factory=dict)              # hidden physical state

    def signal(self, key: str, default: float = 0.0) -> float:
        return self.signals.get(key, default)


@dataclass
class EntityContext:
    """Everything a model needs for one step(). Built fresh each tick by the engine.

    `inputs` maps a relationship predicate (CURIE/IRI) -> list of upstream
    EntityState. e.g. inputs["nxr:fedBy"] = [<utility feed state>]. A model asks
    for what it needs by flow role via the helpers in flows.py, so it never
    hardcodes a predicate string and stays domain-portable.
    """
    entity_id: str
    canonical_type: str                       # ontology class IRI
    props: dict                               # graph node properties
    inputs: dict[str, list]                   # predicate -> [EntityState] (upstream sources)
    space: Optional[EntityState]              # the Zone/Room it sits in (ambient)
    t: float                                  # sim time, seconds since start
    dt: float                                 # tick length, sim seconds
    rng: "random.Random"                      # per-entity deterministic RNG
    outputs: dict[str, list] = field(default_factory=dict)  # predicate -> [downstream loads]
    contained: list = field(default_factory=list)  # [EntityState] inside this space
    params: dict = field(default_factory=dict)  # merged graph+bundle dynamics params

    # ---- param resolution: graph prop > bundle dynamics block > default -----
    def param(self, key: str, default):
        """Resolve a model parameter. Order: live graph node property, then the
        vertical's bundle `dynamics` block, then the model's default."""
        if key in self.props and self.props[key] is not None:
            return self.props[key]
        if key in self.params and self.params[key] is not None:
            return self.params[key]
        return default

    def fnum(self, key: str, default: float) -> float:
        try:
            return float(self.param(key, default))
        except (TypeError, ValueError):
            return default


# --------------------------------------------------------------------------
#  Model contract
# --------------------------------------------------------------------------
class DynamicsModel(ABC):
    """Base class for every generative entity model.

    Subclasses set `models` to the ontology class IRI(s) they drive. Resolution
    is subclass-aware (see DynamicsRegistry): a model registered on a superclass
    (e.g. cfp:FacilityEquipment) is the default for all subclasses unless a more
    specific model is registered."""

    #: archetype name (the unit of reuse). Bindings map a class IRI -> this name.
    archetype: str = ""
    #: ontology class IRIs this model drives directly (optional; bindings preferred)
    models: list[str] = []
    #: observable-property IRIs this model emits (for introspection/UI)
    produces: list[str] = []
    #: input flow roles it consumes, as flows.FlowRole names (for introspection)
    consumes: list[str] = []

    def init_state(self, ctx: EntityContext) -> EntityState:
        """Initial state at twin start. Override to seed internal state (SoC=100,
        oil_temp=ambient, …). Default: running, empty."""
        return EntityState(status=str(ctx.props.get("status") or "running"))

    @abstractmethod
    def step(self, ctx: EntityContext, state: EntityState) -> EntityState:
        """Advance the entity by ctx.dt seconds. Return the new state (mutating
        and returning `state` is fine). Populate state.signals with the produced
        telemetry. MUST NOT write the graph or call other models."""
        raise NotImplementedError


# --------------------------------------------------------------------------
#  Registry (subclass-aware, mirrors graph.writer.resolve_label)
# --------------------------------------------------------------------------
class DynamicsRegistry:
    """Maps an ontology class IRI -> the DynamicsModel that drives it, resolving
    up the rdfs:subClassOf chain so a model on a base class covers every subclass.
    """

    def __init__(self):
        self._by_type: dict[str, DynamicsModel] = {}
        self._by_name: dict[str, DynamicsModel] = {}
        self._resolve_cache: dict[str, Optional[DynamicsModel]] = {}

    def register(self, model: DynamicsModel) -> None:
        if not model.archetype and not model.models:
            raise ValueError(f"{model.__class__.__name__} declares no `archetype`.")
        if model.archetype:
            self._by_name[model.archetype] = model
        for iri in model.models:
            self._by_type[iri] = model
        self._resolve_cache.clear()

    def get(self, archetype_name: str) -> Optional[DynamicsModel]:
        """Fetch an archetype by its name (the binding-layer key)."""
        return self._by_name.get(archetype_name)

    def resolve(self, canonical_type: str) -> Optional[DynamicsModel]:
        """Find the most-specific model for a class, walking up subClassOf.
        Returns None if no model (and no ancestor model) is registered."""
        if canonical_type in self._resolve_cache:
            return self._resolve_cache[canonical_type]
        # direct hit
        m = self._by_type.get(canonical_type)
        if m is None:
            m = self._walk_superclasses(canonical_type)
        self._resolve_cache[canonical_type] = m
        return m

    def _walk_superclasses(self, canonical_type: str) -> Optional[DynamicsModel]:
        """BFS up rdfs:subClassOf in the cached ontology graph for a registered
        ancestor model. Best-effort: if the ontology can't be loaded, return None."""
        try:
            import sys
            from pathlib import Path
            tools = str(Path(__file__).resolve().parent.parent / "tools")
            if tools not in sys.path:
                sys.path.insert(0, tools)
            import gate
            from rdflib import URIRef
            SUBCLASS = URIRef("http://www.w3.org/2000/01/rdf-schema#subClassOf")
            g = gate.ontology_graph()
        except Exception:
            return None
        seen, frontier = set(), [URIRef(canonical_type)]
        while frontier:
            cur = frontier.pop()
            if cur in seen:
                continue
            seen.add(cur)
            m = self._by_type.get(str(cur))
            if m is not None:
                return m
            frontier.extend(g.objects(cur, SUBCLASS))
        return None

    def describe(self) -> list[dict]:
        """Catalog of registered archetypes (by name — the binding-layer key).
        Includes name-only archetypes that bind no IRI directly."""
        out, seen = [], set()
        for name, m in self._by_name.items():
            if id(m) in seen:
                continue
            seen.add(id(m))
            out.append({"archetype": name, "model": m.__class__.__name__,
                        "models": m.models, "produces": m.produces,
                        "consumes": m.consumes})
        return out
