"""
runtime.py — the machine-twin runtime: per-tenant live physics + findings.

This is the platform-native machine-twin engine. Unlike a database-free in-process
demo engine, it is built on the platform's own graph + bus, so:

  * findings are PERSISTED through the Graph Writer (validate → commit → changelog
    → bus), not kept only in a Python list — so history/query/topology work;
  * domains are discovered from self-describing SPECs, not a hard-coded dict;
  * twins hydrate lazily from the TwinRegistry (the durable source of truth), so a
    server restart doesn't lose them.

The runtime idea: a background ~1 Hz ticker advances each live twin's physics, runs
its 3-tier behaviour registry, and keeps the latest frame + recent findings in
memory for the read surfaces (state / diagnostics / predict / project / network).
"""
from __future__ import annotations

import copy
import threading
import time
from datetime import datetime, timezone

from behaviors.registry import TelemetrySample
from feed.simulate import FindingsLoop
from graph.writer import GraphWriter
from graph.query import GraphQuery
from changelog.service import ChangeLog


def _local(sig: str) -> str:
    """Short local name of a signal key: 'turbine:oilTemp' -> 'oiltemp' lookups
    are done case-insensitively by the frame query."""
    return sig.split("#")[-1].split(":")[-1]


def load_specs() -> dict:
    """Discover the available machine domains from their SPECs. Missing/broken
    domains are skipped so one bad module never takes the runtime down.

    A domain module may expose a single `SPEC` (turbine/edm) or a `SPECS`
    list of several specs backed by the same package (railway exposes both the
    metro-network and the rolling-stock twins). Each machine-twin domain is a
    self-contained pack under `packs/<domain>/` (ontology TTL + physics + behaviours
    + prediction co-located)."""
    specs: dict[str, dict] = {}
    for mod in ("turbine", "edm", "railway", "fleet", "hospital", "ev", "defence"):
        try:
            m = __import__(f"packs.{mod}", fromlist=["SPEC", "SPECS"])
            candidates = list(getattr(m, "SPECS", None) or [])
            spec = getattr(m, "SPEC", None)
            if spec and spec not in candidates:
                candidates.append(spec)
            for s in candidates:
                if s:
                    specs[s["key"]] = s
        except Exception:
            pass
    return specs


SPECS = load_specs()
MACHINE_DOMAINS = set(SPECS)


class _FrameQuery:
    """Read-only view a behaviour sees during evaluate(): the co-located signals
    of the twin's LATEST frame, keyed by short local name (case-insensitive).
    Lets Tier-A cross-signal rules read siblings without touching Neo4j."""

    def __init__(self, twin: "LiveTwin"):
        self._twin = twin

    def _props(self) -> dict:
        return {_local(k).lower(): v for k, v in self._twin.latest.items()}

    def get_property(self, tenant, entity, key, default=None):
        return self._props().get(str(key).lower(), default)

    def get_node(self, tenant, entity):
        return self._props()

    def list_by_label(self, *a, **k):
        return []

    def get_findings(self, *a, **k):
        return []


class LiveTwin:
    """One live machine twin: physics state + registry + read surfaces."""

    def __init__(self, tenant: str, spec: dict, entity_id: str, name: str,
                 writer: GraphWriter):
        self.tenant = tenant
        self.spec = spec
        self.domain = spec["key"]
        self.entity_id = entity_id      # the seeded machine node (findings flag it)
        self.name = name
        self.control = spec["control"]
        self.physics = spec["physics"]()
        self.state = self.physics.init_state()
        self.registry = spec["build_registry"]()
        self.latest: dict = {}
        self.findings: list = []        # recent-findings ring for the read surface
        self.frames = 0
        self.live = True
        self.lock = threading.Lock()
        self._loop = FindingsLoop(self.registry, writer, _FrameQuery(self))
        for _ in range(3):              # prime so the twin opens with real numbers
            self._step(dt=1.0)

    # ── control + stepping ──
    def _set_control(self, v: float):
        setattr(self.state, self.control, float(v))

    def simulate(self, throttle=None, fault=None, severity: float = 0.6,
                 dt: float = 1.0) -> dict:
        with self.lock:
            if throttle is not None:
                self._set_control(throttle)
            if fault is not None:
                if fault == "none":
                    self.state.fault = "none"
                    self.state.fault_severity = 0.0
                else:
                    self.physics.inject(self.state, fault, severity)
            return self._step(dt=dt)

    def _step(self, dt: float = 1.0) -> dict:
        frame = self.physics.forward(self.state, dt=dt)
        self.latest = frame
        self.frames += 1
        ts = datetime.now(timezone.utc)
        units = self.spec["units"]
        for sig, val in frame.items():
            sample = TelemetrySample(
                signal=sig, entity_id=self.entity_id, value=float(val),
                unit=units.get(sig, ""), timestamp=ts, tenant_id=self.tenant,
            )
            try:
                outcomes = self._loop.process(sample)   # evaluate + PERSIST
            except Exception:
                outcomes = []                            # never kill the ticker
            for o in outcomes:
                f = o.finding
                self.findings.insert(0, {
                    "displayName": f.message[:80], "severity": f.severity,
                    "message": f.message, "behaviorId": f.behavior_id,
                    "tier": f.tier.value, "signal": f.evidence.get("signal"),
                })
        self.findings = self.findings[:12]
        return frame

    # ── read surfaces (shapes mirror the demo's nextxr client) ──
    def _sensor_status(self, sig, value) -> str:
        if value is None:
            return "unknown"
        lim_dir = self.spec.get("checks", {}).get(sig)
        if not lim_dir:
            return "ok"
        lim, direction = lim_dir
        if direction == "above":
            return "critical" if value >= lim else "warning" if value >= lim * 0.9 else "ok"
        return "critical" if value <= lim else "warning" if value <= lim * 1.1 else "ok"

    def state_dict(self) -> dict:
        with self.lock:
            frame = dict(self.latest)
            findings = list(self.findings)
        health = self.physics.health_index(frame)
        residuals = self.physics.residuals(frame) if hasattr(self.physics, "residuals") else {}
        return {
            "tenant": self.tenant, "domain": self.domain, "entity_id": self.entity_id,
            "name": self.name, "frames": self.frames, "running": self.live,
            "health": round(health, 3), "latest": frame,
            "residuals": {k: round(v, 2) for k, v in residuals.items()},
            "findings": findings,
            "incidents": [f for f in findings if f["severity"] == "critical"][:3],
        }

    def diagnostics(self) -> dict:
        with self.lock:
            frame = dict(self.latest)
            findings = list(self.findings)
        ch = self.spec["component_health"](self.state, frame, self.physics)
        components = [{"name": label, "type": "subsystem",
                       **ch.get(key, {"health": None, "status": "unknown"})}
                      for key, label in self.spec["subsystems"]]
        sensors = [{"name": lbl, "type": "sensor", "signal": sig,
                    "value": frame.get(sig), "unit": unit,
                    "status": self._sensor_status(sig, frame.get(sig))}
                   for sig, (lbl, unit) in self.spec["sensors"].items()]
        machine = {"id": self.entity_id, "name": self.name, **ch.get("overall", {})}
        return {"tenant": self.tenant, "domain": self.domain,
                "engine": machine, "machine": machine,
                "overall_health": ch.get("overall", {}).get("health"),
                "components": components, "sensors": sensors,
                "latest": frame, "findings": findings, "incidents": []}

    def predict_forward(self, horizon_min: float = 120.0, points: int = 120) -> dict:
        with self.lock:
            st = copy.deepcopy(self.state)
        return self.spec["predict"](st, horizon_min=horizon_min, points=points,
                                    physics=self.physics)

    def project(self, fault=None, severity=0.85, control=None,
                horizon_min=120.0, points=120) -> dict:
        with self.lock:
            st = copy.deepcopy(self.state)
        if control is not None:
            setattr(st, self.control, float(control))
        if fault and fault != "none":
            self.physics.inject(st, fault, float(severity))
        return self.spec["predict"](st, horizon_min=horizon_min, points=points,
                                    physics=self.physics)

    def network(self):
        if not hasattr(self.physics, "network_state"):
            return None
        with self.lock:
            payload = self.physics.network_state(self.state)
            payload["latest"] = dict(self.latest)
        return payload


class MachineEngine:
    """Process-wide registry of live machine twins + a 1 Hz ticker. Twins are
    hydrated lazily from the durable TwinRegistry on first access."""

    def __init__(self):
        self._twins: dict[str, LiveTwin] = {}
        self._lock = threading.Lock()
        self._writer = GraphWriter(changelog=ChangeLog())
        self._started = False

    def _ensure_ticker(self):
        if not self._started:
            self._started = True
            threading.Thread(target=self._tick, daemon=True).start()

    def _tick(self):
        while True:
            time.sleep(1.0)
            with self._lock:
                twins = list(self._twins.values())
            for tw in twins:
                if tw.live:
                    try:
                        tw.simulate(dt=2.0)
                    except Exception:
                        pass  # never kill the ticker

    def ensure(self, tenant: str) -> LiveTwin | None:
        """Return the live twin for a tenant, hydrating it from the registry if
        needed. Returns None if the tenant isn't a known machine-domain twin."""
        with self._lock:
            tw = self._twins.get(tenant)
        if tw:
            return tw
        try:
            from twins import TwinRegistry
            rec = TwinRegistry().get(tenant)
        except Exception:
            rec = None
        if not rec or rec.domain not in SPECS or not rec.seed_asset_id:
            return None
        tw = LiveTwin(tenant, SPECS[rec.domain], rec.seed_asset_id, rec.name,
                      self._writer)
        with self._lock:
            self._twins[tenant] = tw
        self._ensure_ticker()
        return tw

    def set_running(self, tenant: str, running: bool) -> bool:
        tw = self.ensure(tenant)
        if not tw:
            return False
        tw.live = bool(running)
        return True

    def drop(self, tenant: str):
        with self._lock:
            self._twins.pop(tenant, None)


_engine: MachineEngine | None = None


def get_machine_engine() -> MachineEngine:
    global _engine
    if _engine is None:
        _engine = MachineEngine()
    return _engine
