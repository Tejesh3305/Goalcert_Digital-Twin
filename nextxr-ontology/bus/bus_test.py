#!/usr/bin/env python3
"""
bus_test.py — EVENT BUS unit gate. Runs with NO Redis and NO Neo4j.

Proves the bus contract that the rest of the platform relies on:

  1. BusEvent survives a round-trip through the Redis wire format.
  2. InMemoryBus is append-only and id-ordered; read(after=id) pages forward.
  3. Per-tenant isolation: one tenant's read never sees another's events.
  4. NullBus is a no-op (publish drops, read empty) — the disabled path.
  5. The factory always returns a working bus (never None) and the singleton
     is stable; reset_event_bus swaps it.
  6. publish() is best-effort: a backend that raises internally is swallowed,
     never propagated to the caller.

Usage:   python bus/bus_test.py        (from the nextxr-ontology/ dir)
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bus import (  # noqa: E402
    BusEvent, InMemoryBus, NullBus, get_event_bus, reset_event_bus, stream_key,
)

_passed = 0
_failed = 0


def check(label, cond, detail=""):
    global _passed, _failed
    mark = "PASS" if cond else "FAIL"
    if cond:
        _passed += 1
    else:
        _failed += 1
    extra = f"  ({detail})" if detail else ""
    print(f"  [{mark}] {label}{extra}")
    return cond


def _mk(tenant, entity_id, action="create", seq=None):
    ev = BusEvent(
        event_id=f"EVT-{entity_id}", tenant_id=tenant, entity_id=entity_id,
        entity_type="https://ontology.nextxr.io/v3/core#Finding",
        label="Finding", action=action, actor="test",
        ts=datetime.now(timezone.utc).isoformat(),
        field_changes={"status": {"old": None, "new": "open"}},
    )
    if seq is not None:
        ev.seq = seq
    return ev


def main():
    print("=" * 66)
    print("  EVENT BUS GATE — no Redis, no Neo4j required")
    print("=" * 66 + "\n")

    # 1. wire round-trip ------------------------------------------------
    print("--- 1. BusEvent wire round-trip ---")
    ev = _mk("t1", "n1")
    wire = ev.to_wire()
    check("wire form is all strings",
          all(isinstance(v, str) for v in wire.values()))
    back = BusEvent.from_wire(wire)
    check("event_id round-trips", back.event_id == ev.event_id)
    check("field_changes round-trips (JSON)", back.field_changes == ev.field_changes)
    check("label round-trips", back.label == "Finding")

    # 2. append-only + ordered + paging --------------------------------
    print("\n--- 2. InMemoryBus append/read/paging ---")
    bus = InMemoryBus()
    id1 = bus.publish(_mk("t1", "n1"))
    id2 = bus.publish(_mk("t1", "n2"))
    id3 = bus.publish(_mk("t1", "n3"))
    check("publish returns message ids", all([id1, id2, id3]))
    allev = bus.read("t1", last_id="0")
    check("read from start returns all 3", len(allev) == 3,
          f"{len(allev)}")
    check("order preserved",
          [e.entity_id for _, e in allev] == ["n1", "n2", "n3"])
    after1 = bus.read("t1", last_id=id1)
    check("read after id1 skips n1", [e.entity_id for _, e in after1] == ["n2", "n3"])
    after_all = bus.read("t1", last_id=id3)
    check("read after last id is empty", after_all == [])

    # 3. per-tenant isolation ------------------------------------------
    print("\n--- 3. Per-tenant isolation ---")
    bus.publish(_mk("t2", "other"))
    t1 = bus.read("t1", last_id="0")
    t2 = bus.read("t2", last_id="0")
    check("tenant t1 unaffected by t2 write", len(t1) == 3)
    check("tenant t2 sees only its own event",
          len(t2) == 1 and t2[0][1].entity_id == "other")
    check("stream keys are per-tenant",
          stream_key("t1") != stream_key("t2"))
    check("bus stats count all publishes", bus.stats()["published"] == 4,
          bus.stats()["published"])

    # 4. NullBus no-op --------------------------------------------------
    print("\n--- 4. NullBus (disabled) ---")
    nb = NullBus()
    check("NullBus.publish returns None", nb.publish(_mk("t1", "x")) is None)
    check("NullBus.read returns []", nb.read("t1") == [])
    check("NullBus stats report null backend", nb.stats()["backend"] == "null")

    # 5. factory / singleton -------------------------------------------
    print("\n--- 5. Factory + singleton ---")
    reset_event_bus(None)
    b1 = get_event_bus()
    b2 = get_event_bus()
    check("factory never returns None", b1 is not None)
    check("singleton is stable", b1 is b2)
    check("factory backend is redis or memory (Docker may be off)",
          b1.backend in {"redis", "memory"}, b1.backend)
    injected = InMemoryBus()
    reset_event_bus(injected)
    check("reset_event_bus injects a bus", get_event_bus() is injected)

    # 6. best-effort: publish never raises -----------------------------
    print("\n--- 6. publish() is best-effort (never raises) ---")

    class BoomBus(InMemoryBus):
        def publish(self, event):
            # Simulate a backend that always fails internally.
            try:
                raise RuntimeError("backend down")
            except Exception:
                self._errors += 1
                return None

    boom = BoomBus()
    raised = False
    try:
        res = boom.publish(_mk("t1", "n1"))
    except Exception:
        raised = True
    check("publish swallows backend failure", not raised)
    check("failed publish returns None", res is None)

    # ------------------------------------------------------------------
    reset_event_bus(None)  # leave a clean singleton for the next process
    print("\n" + "=" * 66)
    print(f"  EVENT BUS GATE RESULT: {_passed} passed, {_failed} failed")
    if _failed == 0:
        print("  EVENT BUS GATE: CLOSED. The bus contract holds.")
    else:
        print("  EVENT BUS GATE: OPEN. Fix failures before wiring consumers.")
    print("=" * 66)
    sys.exit(0 if _failed == 0 else 1)


if __name__ == "__main__":
    main()
