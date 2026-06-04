"""
engine.py — the generic, domain-agnostic coupled dynamics engine (the mediator).

Responsibilities (NONE of them domain-specific — it never mentions HVAC):
  1. Load the tenant's topology (nodes + edges) from Neo4j once per run.
  2. Build inbound adjacency (who feeds whom) from the relationship edges.
  3. Order entities so producers update before consumers (topological sort over
     the directed edges); cycles (zone<->server) are broken with a 1-tick lag.
  4. Each tick: build an EntityContext per entity (resolving upstream EntityStates
     by predicate + the space it sits in), call its model.step(), collect signals.
  5. Emit each produced signal as a TelemetrySample into the SAME FindingsLoop the
     scripted feed used, so the existing 16 detection behaviours fire unchanged.
  6. Periodically persist evolving state (status, key signals) via GraphWriter.

Time model: REAL-TIME WITH A SPEED MULTIPLIER. Each wall-clock interval advances
sim time by `speed * wall_elapsed`. So speed=1 is real-time; speed=60 means one
real second = one sim-minute (good for watching faults develop quickly).

This engine is what makes coupling universal: add any pack, declare its edges in
the ontology, register its DynamicsModels — the engine couples them identically.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

from dynamics.model import DynamicsModel, DynamicsRegistry, EntityState, EntityContext
from dynamics import flows
from dynamics.bindings import resolve_binding


# Predicates whose DIRECTION defines "produces before consumes". Source --pred--> target
# means the source must update first (it feeds the target). Spatial/observation edges
# are handled separately (a sensor/space is resolved as ambient, not a producer chain).
_FEED_PREDICATES_LOCAL = {"feeds", "suppliesAirTo", "serves", "servesSpace",
                          "backsUp", "controls", "dependsOn"}
_SPATIAL_LOCAL = {"locatedAt", "containedIn", "partOf"}
_OBSERVE_LOCAL = {"monitors", "observes"}


class DynamicsEngine:
    """Drives generative entity models for one tenant, coupled via the graph."""

    def __init__(self, tenant_id: str, registry: DynamicsRegistry, query, *,
                 speed: float = 60.0, seed: int = 1234,
                 bundle_dynamics: Optional[dict] = None):
        self.tenant_id = tenant_id
        self.registry = registry
        self.query = query
        self.speed = float(speed)            # sim seconds per real second
        self.seed = seed
        self.bundle_dynamics = bundle_dynamics or {}   # {canonical_type: {param: val}}
        # runtime
        self.t = 0.0                         # sim seconds elapsed
        self._states: dict[str, EntityState] = {}
        self._nodes: dict[str, dict] = {}    # id -> {canonicalType, props}
        self._inbound: dict[str, dict[str, list[str]]] = {}  # id -> {predicate: [src ids]} (upstream)
        self._outbound: dict[str, dict[str, list[str]]] = {}  # id -> {predicate: [tgt ids]} (downstream)
        self._space_of: dict[str, str] = {}  # id -> containing space id
        self._contained: dict[str, list[str]] = {}  # space id -> [entity ids]
        self._order: list[str] = []          # update order (producers first)
        self._rngs: dict[str, "random.Random"] = {}
        self._model_cache: dict[str, tuple] = {}   # canonical_type -> (model, params)
        self._loaded = False

    # ---- topology -----------------------------------------------------
    def load_topology(self) -> None:
        """Pull every node + edge for the tenant and build adjacency. Read-only;
        uses the same driver the rest of the platform shares."""
        import random
        from graph.connection import get_driver
        driver = get_driver()
        nodes, edges = {}, []
        with driver.session() as s:
            recs = s.run(
                "MATCH (n {tenantId:$t}) RETURN n.id AS id, "
                "n.canonicalType AS ct, properties(n) AS p", t=self.tenant_id)
            for r in recs:
                if r["id"]:
                    nodes[r["id"]] = {"canonicalType": r["ct"] or "",
                                      "props": dict(r["p"] or {})}
            recs = s.run(
                "MATCH (a {tenantId:$t})-[r]->(b {tenantId:$t}) "
                "RETURN a.id AS src, r.predicateIri AS pred, type(r) AS rtype, "
                "b.id AS tgt", t=self.tenant_id)
            for r in recs:
                if r["src"] and r["tgt"]:
                    edges.append((r["src"], r["pred"] or r["rtype"], r["tgt"]))

        self._nodes = nodes
        self._inbound = {nid: {} for nid in nodes}
        self._outbound = {nid: {} for nid in nodes}
        self._space_of = {}
        self._contained = {nid: [] for nid in nodes}   # space id -> [entity ids in it]
        for src, pred, tgt in edges:
            local = flows.predicate_local(pred)
            # Inbound feed/observe edges: the TARGET consumes from the SOURCE.
            if local in _FEED_PREDICATES_LOCAL or local in _OBSERVE_LOCAL:
                self._inbound.setdefault(tgt, {}).setdefault(pred, []).append(src)
                # and the SOURCE sees the TARGET as a downstream load it serves.
                self._outbound.setdefault(src, {}).setdefault(pred, []).append(tgt)
            # Spatial: source is locatedAt/containedIn target (the space).
            if local in _SPATIAL_LOCAL:
                self._space_of[src] = tgt
                self._contained.setdefault(tgt, []).append(src)
            if local == "contains":      # inverse spatial edge
                self._space_of[tgt] = src
                self._contained.setdefault(src, []).append(tgt)

        # per-entity deterministic RNG so runs are reproducible
        self._rngs = {nid: random.Random(hash((self.seed, nid)) & 0xFFFFFFFF)
                      for nid in nodes}
        self._order = self._topo_order(nodes, edges)
        self._loaded = True

    def _topo_order(self, nodes, edges) -> list[str]:
        """Kahn topological sort over feed edges (source -> target). Remaining
        nodes in any cycle are appended (they use last-tick upstream values =
        the 1-tick lag that keeps coupling loose)."""
        succ = {nid: [] for nid in nodes}     # source -> [targets]
        indeg = {nid: 0 for nid in nodes}
        for src, pred, tgt in edges:
            if flows.predicate_local(pred) in _FEED_PREDICATES_LOCAL:
                if src in nodes and tgt in nodes:
                    succ[src].append(tgt)
                    indeg[tgt] += 1
        from collections import deque
        q = deque([n for n in nodes if indeg[n] == 0])
        order = []
        while q:
            n = q.popleft()
            order.append(n)
            for m in succ[n]:
                indeg[m] -= 1
                if indeg[m] == 0:
                    q.append(m)
        # nodes still with indeg>0 are in cycles — append in stable order
        order += [n for n in nodes if n not in order]
        return order

    # ---- binding-driven resolution (cached per class) -----------------
    def _resolve(self, canonical_type: str):
        """Return (model, params) for a class via the binding layer: class ->
        archetype name + params (subclass-aware), then archetype name -> model.
        Bundle dynamics overrides merge on top of the binding's default params."""
        cached = self._model_cache.get(canonical_type)
        if cached is not None:
            return cached
        binding = resolve_binding(canonical_type)
        model = self.registry.get(binding.archetype) if binding.archetype else None
        params = dict(binding.params)
        params.update(self.bundle_dynamics.get(canonical_type, {}))  # bundle overrides
        self._model_cache[canonical_type] = (model, params)
        return model, params

    # ---- init / step --------------------------------------------------
    def _ensure_states(self) -> None:
        for nid in self._order:
            if nid in self._states:
                continue
            node = self._nodes[nid]
            model, _ = self._resolve(node["canonicalType"])
            if model is None:
                continue
            ctx = self._context(nid, node, dt=0.0)
            self._states[nid] = model.init_state(ctx)

    def _context(self, nid: str, node: dict, dt: float) -> EntityContext:
        # resolve upstream states by predicate (last computed this/prev tick)
        inputs: dict[str, list] = {}
        for pred, srcs in self._inbound.get(nid, {}).items():
            states = [self._states[s] for s in srcs if s in self._states]
            if states:
                inputs[pred] = states
        outputs: dict[str, list] = {}
        for pred, tgts in self._outbound.get(nid, {}).items():
            states = [self._states[s] for s in tgts if s in self._states]
            if states:
                outputs[pred] = states
        space_id = self._space_of.get(nid)
        space_state = self._states.get(space_id) if space_id else None
        contained = [self._states[c] for c in self._contained.get(nid, [])
                     if c in self._states]
        params = dict(self._resolve(node["canonicalType"])[1])  # binding + bundle params
        return EntityContext(
            entity_id=nid, canonical_type=node["canonicalType"],
            props=node["props"], inputs=inputs, space=space_state,
            t=self.t, dt=dt, rng=self._rngs[nid], outputs=outputs,
            contained=contained, params=params)

    def tick(self, dt: float) -> list:
        """Advance all entities by dt sim-seconds. Returns the produced
        TelemetrySamples (already shaped for the FindingsLoop)."""
        from behaviors.registry import TelemetrySample
        if not self._loaded:
            self.load_topology()
        self._ensure_states()
        self.t += dt
        ts = datetime.now(timezone.utc)
        samples: list = []
        for nid in self._order:
            node = self._nodes.get(nid)
            if not node:
                continue
            model, _ = self._resolve(node["canonicalType"])
            if model is None:
                continue
            state = self._states.get(nid) or model.init_state(
                self._context(nid, node, 0.0))
            ctx = self._context(nid, node, dt)
            try:
                state = model.step(ctx, state)
            except Exception:
                continue  # one model must never crash the whole tick
            self._states[nid] = state
            for sig_iri, val in state.signals.items():
                samples.append(TelemetrySample(
                    signal=sig_iri, entity_id=nid, value=float(val),
                    unit="", timestamp=ts, tenant_id=self.tenant_id))
        return samples

    # ---- persistence (best-effort) ------------------------------------
    def persist(self, writer) -> None:
        """Write evolving status back to the graph through the single write path.
        Called occasionally (not every tick) — status changes flow through the
        state-machine gate automatically; illegal transitions are skipped."""
        for nid, state in self._states.items():
            node = self._nodes.get(nid)
            if not node:
                continue
            cur = node["props"].get("status")
            if state.status and state.status != cur:
                res = writer.update(tenant_id=self.tenant_id, node_id=nid,
                                    actor="dynamics-engine",
                                    properties={"status": state.status})
                if res.ok:
                    node["props"]["status"] = state.status

    # ---- real-time driver loop ---------------------------------------
    def run_realtime(self, on_samples, *, should_stop, wall_interval: float = 0.5):
        """Drive the engine in real time. Each wall_interval seconds, advance sim
        time by speed*wall_interval and emit samples via on_samples(samples).
        `should_stop()` is polled to exit. This is what the feed loop calls."""
        if not self._loaded:
            self.load_topology()
        last = time.time()
        while not should_stop():
            now = time.time()
            wall = now - last
            last = now
            dt = self.speed * wall
            samples = self.tick(dt)
            try:
                on_samples(samples)
            except Exception:
                pass
            time.sleep(wall_interval)
