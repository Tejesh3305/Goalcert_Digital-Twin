"""
pipeline.py — the ONE path every telemetry source funnels through.

    HTTP batch ─┐
    MQTT/Sparkplug ─┤
    OPC-UA ─┼──> submit() ──> validate ──> historian ──> bus ──> behaviours
    CSV backfill ─┤                                                    │
    simulator ─┘                                              Findings via
                                                              the Graph Writer

WHY A SINGLE FUNNEL
-------------------
Every rule that makes telemetry trustworthy — unit handling, quality codes,
timestamp sanity, idempotency, what counts as a duplicate, when a behaviour is
allowed to fire — has to be applied identically no matter how the data arrived.
Let each connector write to the store itself and those rules get reimplemented
per protocol, which is how a platform ends up with data-quality bugs that
reproduce on exactly one customer's site because only their gateway speaks
Modbus. A new protocol connector here is an adapter that produces dicts; it
inherits every guarantee automatically and can add none of its own.

WHAT HAPPENS TO A SAMPLE, IN ORDER
----------------------------------
1. COERCE + VALIDATE (historian.coerce / historian.write). Bad samples are
   rejected individually with a reason and never abort the batch — one bad
   timestamp in 500 must not discard 499 good readings.
2. PERSIST to the historian. Idempotent by primary key, so a replayed buffer
   from an edge agent cannot double-count.
3. FAN OUT on the event bus, so an SSE-attached dashboard updates without
   polling. Best-effort by the bus's own contract: a Redis outage must never
   fail an ingest that is already durable.
4. EVALUATE BEHAVIOURS. This is the step that makes ingested data mean
   something: the same three-tier registry, Graph Writer, change log and
   diagnosis chain the simulator drives, now on real readings. It is guarded by
   `NXR_INGEST_BEHAVIOURS` (default on) and its failures are COUNTED rather than
   swallowed — a rule that silently stops firing is the worst outcome available,
   because the twin looks healthy precisely when it has stopped watching.

ORDERING NOTE: persist before evaluate. A behaviour can be slow (it reads the
graph) and a rule change should never be able to lose a measurement. History is
the durable fact; a finding is a derived opinion about it.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import historian
from historian import Measurement


def _truthy(value, default: bool = True) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if raw == "":
        return default
    return raw in ("1", "true", "yes", "on")


def behaviours_enabled() -> bool:
    return _truthy(os.environ.get("NXR_INGEST_BEHAVIOURS"), default=True)


@dataclass
class IngestResult:
    """What happened to one submitted batch. Every count is reported rather than
    summarised into a single status, because "accepted" and "stored" and
    "evaluated" fail independently and an operator debugging a silent pipeline
    needs to know which stage stopped."""
    submitted: int = 0
    accepted: int = 0
    duplicates: int = 0
    rejected: list[dict] = field(default_factory=list)
    findings: int = 0
    published: int = 0
    errors: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "submitted": self.submitted,
            "accepted": self.accepted,
            "duplicates": self.duplicates,
            "rejected": len(self.rejected),
            "rejections": self.rejected[:50],
            "findings": self.findings,
            "published": self.published,
            "signals": self.signals[:50],
            "errors": self.errors[:10],
        }

    @property
    def ok(self) -> bool:
        """A batch is OK when nothing was lost. Duplicates are not a failure —
        they are the idempotency guarantee working."""
        return not self.errors and not self.rejected


# ── Behaviour evaluation ────────────────────────────────────────────────────
#
# One FindingsLoop per tenant, built lazily and reused. Behaviours are STATEFUL
# (a z-score baseline needs its warm-up window; a sustained-threshold rule tracks
# when the breach started), so rebuilding the registry per request would reset
# every one of them and no duration-based rule could ever fire.

_loops: dict[str, object] = {}
_loops_lock = threading.Lock()
_loop_errors: dict[str, str] = {}
_MAX_LOOPS = 32


def _loop_for(tenant_id: str):
    """The tenant's findings loop, or None if it cannot be built."""
    with _loops_lock:
        existing = _loops.get(tenant_id)
        if existing is not None:
            return existing

    try:
        from behaviors.registry import BehaviorRegistry
        from changelog.service import ChangeLog
        from feed.simulate import FindingsLoop
        from graph.query import GraphQuery
        from graph.writer import GraphWriter

        query = GraphQuery()
        registry = BehaviorRegistry()

        # Reuse main.py's registry construction so ingested data is evaluated by
        # exactly the same rule set as the simulated feed. Importing the server
        # module from here is not ideal, and it is much better than a second,
        # drifting copy of the registry wiring.
        try:
            from server.main import _augment_registry_with_bindings, _build_registry
            registry = _build_registry()
            _augment_registry_with_bindings(registry, tenant_id, query)
        except Exception as e:
            _loop_errors[tenant_id] = f"registry build degraded: {e}"

        loop = FindingsLoop(registry, GraphWriter(changelog=ChangeLog()), query)
    except Exception as e:
        _loop_errors[tenant_id] = str(e)[:200]
        return None

    with _loops_lock:
        if len(_loops) >= _MAX_LOOPS:
            _loops.pop(next(iter(_loops)), None)
        _loops[tenant_id] = loop
    return loop


def reset_loops() -> None:
    """Drop cached loops so the next batch rebuilds them. Called after a rule or
    bundle change, and by the test suite."""
    with _loops_lock:
        _loops.clear()
        _loop_errors.clear()


def loop_errors() -> dict:
    return dict(_loop_errors)


def _evaluate(tenant_id: str, measurements: Sequence[Measurement],
              result: IngestResult) -> None:
    """Run the behaviour registry over the batch."""
    loop = _loop_for(tenant_id)
    if loop is None:
        result.errors.append(
            f"behaviours not evaluated: {_loop_errors.get(tenant_id, 'unavailable')}")
        return

    from behaviors.registry import TelemetrySample

    for m in measurements:
        # A BAD-quality reading must not drive a rule. Firing "temperature
        # critical" off a disconnected sensor reporting 0 is how an operator
        # learns to ignore the alerts, which costs more than the missed detection
        # ever would.
        if m.value is None or m.quality < historian.QUALITY_UNCERTAIN:
            continue
        try:
            outcomes = loop.process(TelemetrySample(
                signal=m.signal, entity_id=m.asset_id, value=float(m.value),
                unit=m.unit or "", timestamp=m.ts, tenant_id=m.tenant_id))
            result.findings += len(outcomes)
        except Exception as e:
            # Counted, not swallowed. One asset missing from the graph must not
            # stop the rest of the batch being evaluated, but it must be visible.
            msg = f"{m.asset_id}/{m.signal}: {str(e)[:120]}"
            if msg not in result.errors:
                result.errors.append(msg)


# ── Live fan-out ────────────────────────────────────────────────────────────


def _publish(tenant_id: str, measurements: Sequence[Measurement],
             result: IngestResult) -> None:
    """Announce new readings on the event bus for live UI updates.

    One event per (asset, signal) carrying the newest value, not one per sample:
    a 500-sample backfill batch should not emit 500 UI updates for the same
    gauge. The bus is best-effort by contract — `publish()` never raises — so
    this cannot fail an ingest.
    """
    try:
        import bus
        from bus.event_bus import BusEvent
    except Exception:
        return

    newest: dict[tuple[str, str], Measurement] = {}
    for m in measurements:
        key = (m.asset_id, m.signal)
        current = newest.get(key)
        if current is None or m.ts >= current.ts:
            newest[key] = m

    try:
        event_bus = bus.get_event_bus()
    except Exception:
        return

    for (asset_id, signal), m in newest.items():
        try:
            event_bus.publish(BusEvent(
                event_id=f"tele-{asset_id}-{signal}-{int(m.ts.timestamp() * 1000)}",
                tenant_id=tenant_id, entity_id=asset_id,
                entity_type="nxr:Observation", label="Observation",
                action="update", actor=f"ingest:{m.source}",
                ts=m.ts.isoformat(),
                field_changes={signal: {"old": None, "new": m.value}},
            ))
            result.published += 1
        except Exception:
            pass       # the bus's own contract: never affect the write


# ── Entry points ────────────────────────────────────────────────────────────


def submit(tenant_id: str, measurements: Iterable[Measurement], *,
           evaluate: bool | None = None,
           publish: bool = True) -> IngestResult:
    """Ingest already-typed measurements. The funnel every source ends at.

    `tenant_id` overrides whatever the measurements claim. The tenant is an
    authorization fact established by the caller's credential, and a payload
    field must never be able to redirect a write into another tenant's history.
    """
    result = IngestResult()
    batch: list[Measurement] = []
    for m in measurements:
        batch.append(m if m.tenant_id == tenant_id
                     else Measurement(**{**m.__dict__, "tenant_id": tenant_id}))
    result.submitted = len(batch)
    if not batch:
        return result

    try:
        written = historian.write(batch)
    except Exception as e:
        result.errors.append(f"historian write failed: {str(e)[:200]}")
        return result

    result.accepted = written.accepted
    result.duplicates = written.duplicates
    result.rejected = written.rejected

    rejected_indices = {r["index"] for r in written.rejected}
    stored = [m for i, m in enumerate(batch) if i not in rejected_indices]
    result.signals = sorted({m.signal for m in stored})

    if publish and stored:
        _publish(tenant_id, stored, result)

    should_evaluate = behaviours_enabled() if evaluate is None else evaluate
    if should_evaluate and stored:
        _evaluate(tenant_id, stored, result)

    return result


def submit_raw(tenant_id: str, rows: Iterable[dict], *,
               default_source: str = "api",
               default_asset_id: str = "",
               evaluate: bool | None = None,
               publish: bool = True) -> IngestResult:
    """Ingest loosely-typed rows (an HTTP payload, an MQTT message, a CSV line).

    Coercion failures are reported per row and do not abort the batch, for the
    same reason validation failures do not: partial success with a precise
    explanation is far more useful to whoever is wiring up a gateway than a 400
    that says "bad request" about 500 samples.
    """
    result = IngestResult()
    measurements: list[Measurement] = []
    index = 0
    for row in rows:
        index += 1
        try:
            if default_asset_id and not (row.get("asset_id") or row.get("asset")):
                row = {**row, "asset_id": default_asset_id}
            measurements.append(historian.coerce(
                row, tenant_id=tenant_id, default_source=default_source))
        except Exception as e:
            result.rejected.append({
                "index": index - 1,
                "asset_id": str(row.get("asset_id") or row.get("asset") or ""),
                "signal": str(row.get("signal") or row.get("tag") or ""),
                "reason": str(e)[:200],
            })

    inner = submit(tenant_id, measurements, evaluate=evaluate, publish=publish)

    # Coercion rejections happened before submit() saw the batch, so both sets
    # are merged and `submitted` counts what the CALLER sent — otherwise a
    # client cannot reconcile its own batch size against the response.
    inner.submitted = index
    inner.rejected = result.rejected + inner.rejected
    return inner


def status(tenant_id: str | None = None) -> dict:
    """Is data actually arriving? The first question anyone asks of an ingest
    pipeline, and one the platform previously could not answer at all."""
    out: dict = {
        "historian": historian.info(),
        "behaviours_enabled": behaviours_enabled(),
        "behaviour_errors": loop_errors(),
        "checked_at": datetime.now(UTC).isoformat(),
    }
    from . import devices
    out["devices"] = devices.stats()
    out["silent_devices"] = devices.silent_devices()
    if tenant_id:
        try:
            out["tenant"] = {"tenant": tenant_id, **historian.tenant_stats(tenant_id)}
        except Exception as e:
            out["tenant"] = {"tenant": tenant_id, "error": str(e)[:160]}
    return out
