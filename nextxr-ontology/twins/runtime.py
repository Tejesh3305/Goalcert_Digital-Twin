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
from twins.coordinator import (
    apply_state_dict, get_coordinator, state_to_dict,
)


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
        self._synced_at = 0.0           # ts of the owner state last applied
        self._adopted = False           # have we taken over the current lease?
        # Prime so the twin opens with real numbers — but WITHOUT persisting.
        # _step() normally evaluates the registry and commits findings; doing
        # that here wrote the same warm-up findings every time a process
        # hydrated the twin, which with several tasks means several times over.
        # Warm-up is not an observation of anything.
        for _ in range(3):
            self._step(dt=1.0, persist=False)

    # ── control + stepping ──
    def _set_control(self, v: float):
        setattr(self.state, self.control, float(v))

    # ── ownership (see twins/coordinator.py) ──
    def owned(self) -> bool:
        """Whether THIS process drives this twin's physics. Always True with no
        Redis configured, i.e. in a single-process run."""
        return get_coordinator().owns(self.tenant)

    def _apply_owner_state(self, payload: dict) -> None:
        """Adopt the owner's published state as our own."""
        with self.lock:
            try:
                apply_state_dict(self.state, payload.get("state") or {})
            except Exception:
                return
            self.latest = payload.get("latest") or self.latest
            self.findings = payload.get("findings") or []
            self.frames = int(payload.get("frames") or self.frames)
            self.live = bool(payload.get("live", self.live))
            self._synced_at = float(payload.get("ts") or 0.0)

    def sync(self) -> None:
        """Readers pull the owner's state before answering.

        Every read surface goes through this, so `state_dict`, `diagnostics`,
        `predict_forward`, `project` and `network` all describe the SAME
        simulation regardless of which task the load balancer picked. Without a
        published state — no Redis, or a hand-over in progress — we keep serving
        our own last frame rather than erroring.
        """
        if self.owned():
            return
        payload = get_coordinator().fetch(self.tenant)
        if payload and float(payload.get("ts") or 0.0) > self._synced_at:
            self._apply_owner_state(payload)

    def publish(self) -> None:
        """Owners hand their state to everyone else."""
        with self.lock:
            payload = {
                "state": state_to_dict(self.state),
                "latest": dict(self.latest),
                "findings": list(self.findings),
                "frames": self.frames,
                "live": self.live,
                "ts": time.time(),
                "owner": get_coordinator().owner_id,
            }
        get_coordinator().publish(self.tenant, payload)

    def apply_command(self, cmd: dict) -> None:
        """Run a control action queued by a task that does not own this twin."""
        kind = cmd.get("kind")
        if kind == "simulate":
            self.simulate(throttle=cmd.get("throttle"), fault=cmd.get("fault"),
                          severity=float(cmd.get("severity", 0.6)),
                          dt=float(cmd.get("dt", 1.0)))
        elif kind == "running":
            self.live = bool(cmd.get("running", True))

    # ── stepping ──
    def simulate(self, throttle=None, fault=None, severity: float = 0.6,
                 dt: float = 1.0) -> dict:
        """Advance one step, applying an optional control change / fault.

        Control actions MUST reach the owner: applying a fault locally on a
        non-owning task would inject it into a copy that nothing else can see
        and that the next sync overwrites — the "I injected a fault and it
        vanished on refresh" symptom. So a reader queues the command and returns
        the owner's current view; the owner applies it on its next tick (~1s).
        """
        if not self.owned():
            queued = get_coordinator().push_command(self.tenant, {
                "kind": "simulate", "throttle": throttle, "fault": fault,
                "severity": severity, "dt": dt,
            })
            if queued:
                self.sync()
                return dict(self.latest)
            # Redis unreachable: fall through and apply locally. Better a
            # divergent frame than a control that silently does nothing.
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

    def _step_owned(self, dt: float = 1.0) -> dict:
        """The owner's tick: advance physics and persist findings. Called only
        from the ticker, after the lease has been confirmed."""
        with self.lock:
            return self._step(dt=dt)

    def _step(self, dt: float = 1.0, persist: bool = True) -> dict:
        frame = self.physics.forward(self.state, dt=dt)
        self.latest = frame
        self.frames += 1
        if not persist:
            return frame
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
        self.sync()
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
        self.sync()
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
        self.sync()
        with self.lock:
            st = copy.deepcopy(self.state)
        return self.spec["predict"](st, horizon_min=horizon_min, points=points,
                                    physics=self.physics)

    def project(self, fault=None, severity=0.85, control=None,
                horizon_min=120.0, points=120) -> dict:
        self.sync()
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
        self.sync()
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
        """Advance only the twins THIS task owns.

        The ownership check is the whole point: a task that does not hold the
        tenant's lease never steps the physics and therefore never evaluates the
        behaviour registry, so a threshold breach is written to the graph once
        no matter how many tasks are serving the twin.
        """
        coord = get_coordinator()
        while True:
            time.sleep(1.0)
            with self._lock:
                twins = list(self._twins.items())
            for tenant, tw in twins:
                try:
                    if not coord.try_own(tenant):
                        tw._adopted = False         # re-adopt if we regain it
                        continue                    # another task drives it
                    self._adopt_if_new_owner(tw)
                    for cmd in coord.drain_commands(tenant):
                        tw.apply_command(cmd)       # control from reader tasks
                    if tw.live:
                        tw._step_owned(dt=2.0)
                    tw.publish()
                except Exception:
                    pass  # never kill the ticker

    def _adopt_if_new_owner(self, tw: "LiveTwin") -> None:
        """On taking over a tenant, continue from the previous owner's state
        instead of from our own stale copy — otherwise a failover shows up as
        the twin jumping backwards to wherever this task last had it."""
        if tw._adopted:
            return
        tw._adopted = True
        payload = get_coordinator().fetch(tw.tenant)
        if payload and float(payload.get("ts") or 0.0) > tw._synced_at:
            tw._apply_owner_state(payload)

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
        """Start/stop the simulation. Like a fault injection this is a control
        action, so it has to reach the owner — setting `live` on a reader would
        pause a copy nobody looks at and be undone by the next sync."""
        tw = self.ensure(tenant)
        if not tw:
            return False
        if not tw.owned():
            if get_coordinator().push_command(
                    tenant, {"kind": "running", "running": bool(running)}):
                return True
        tw.live = bool(running)
        return True

    def drop(self, tenant: str):
        with self._lock:
            self._twins.pop(tenant, None)
        get_coordinator().release(tenant)

    def shutdown(self) -> None:
        """Hand every lease back on the way out, so a rolling deploy transfers
        ownership in the next tick rather than after the lease times out."""
        get_coordinator().release_all()


_engine: MachineEngine | None = None


def get_machine_engine() -> MachineEngine:
    global _engine
    if _engine is None:
        _engine = MachineEngine()
    return _engine
