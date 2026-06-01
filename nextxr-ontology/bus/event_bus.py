"""
event_bus.py — the NextXR Event Bus (Redis Streams + graceful fallback).

Topology
--------
One Redis Stream per tenant: ``nxr:events:<tenant_id>``. This keeps the
platform's tenant-isolation contract intact on the bus — a consumer reading one
tenant's stream cannot see another tenant's events. Consumer groups (for agents)
are created per stream when needed.

Event shape (BusEvent)
----------------------
A bus event mirrors the Change Log event so consumers get the same canonical
facts without re-reading SQLite::

    event_id     ULID of the Change Log event this mutation produced
    tenant_id    isolation key (also encoded in the stream name)
    entity_id    the node the mutation is about
    entity_type  canonical class IRI
    label        resolved Neo4j label / taxonomy category
    action       "create" | "update" | "delete"
    actor        who/what caused it
    ts           ISO-8601 UTC (the Change Log event's ts)
    seq          monotonically-increasing per-process counter (ordering aid)
    field_changes  optional {field: {"old":..., "new":...}} (kept compact)

Delivery contract
------------------
Publishing is BEST-EFFORT and MUST NOT affect the write that triggered it:
``publish()`` never raises. A Redis outage is caught, counted in ``stats()``,
and the write proceeds. The Change Log remains the durable source of truth; the
bus is live fan-out only.

Selection
---------
``get_event_bus()`` returns a process-wide singleton:
  * If ``NXR_BUS_DISABLED`` is truthy            -> NullBus (no-op).
  * Else try Redis (env ``NXR_REDIS_URL`` / ``REDIS_URL``, default
    ``redis://localhost:6379/0``); if the client imports AND connects
    -> RedisStreamBus.
  * Else                                          -> InMemoryBus (fallback).

So the platform ALWAYS has a working bus object — never ``None`` — and runs
fine today with no Docker / no Redis.
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import threading
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from typing import Optional

log = logging.getLogger("nxr.bus")

STREAM_PREFIX = "nxr:events:"
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
# Per-stream cap so an in-memory fallback / unbounded stream can't grow forever.
DEFAULT_MAXLEN = 10_000


def stream_key(tenant_id: str) -> str:
    """The Redis Stream key for a tenant. One stream per tenant."""
    return f"{STREAM_PREFIX}{tenant_id}"


# --------------------------------------------------------------------------
#  Event
# --------------------------------------------------------------------------
_seq_counter = itertools.count(1)


@dataclass
class BusEvent:
    """One event published to the bus. Mirrors the Change Log event."""
    event_id: str
    tenant_id: str
    entity_id: str
    entity_type: str
    label: Optional[str]
    action: str
    actor: str
    ts: str
    seq: int = field(default_factory=lambda: next(_seq_counter))
    field_changes: Optional[dict] = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_wire(self) -> dict:
        """Flatten to the str->str map Redis Streams stores. Nested structures
        are JSON-encoded; None becomes an empty string."""
        out = {}
        for k, v in self.to_dict().items():
            if v is None:
                out[k] = ""
            elif isinstance(v, (dict, list)):
                out[k] = json.dumps(v, separators=(",", ":"))
            else:
                out[k] = str(v)
        return out

    @classmethod
    def from_wire(cls, fields: dict) -> "BusEvent":
        """Rebuild a BusEvent from the Redis (or in-memory) wire map."""
        def _get(key, default=""):
            # Redis-py may hand back bytes depending on decode settings.
            val = fields.get(key, fields.get(key.encode() if isinstance(key, str) else key, default))
            if isinstance(val, bytes):
                val = val.decode("utf-8")
            return val

        fc_raw = _get("field_changes", "")
        field_changes = json.loads(fc_raw) if fc_raw else None
        seq_raw = _get("seq", "0")
        return cls(
            event_id=_get("event_id"),
            tenant_id=_get("tenant_id"),
            entity_id=_get("entity_id"),
            entity_type=_get("entity_type"),
            label=_get("label") or None,
            action=_get("action"),
            actor=_get("actor"),
            ts=_get("ts"),
            seq=int(seq_raw) if str(seq_raw).isdigit() else 0,
            field_changes=field_changes,
        )


# --------------------------------------------------------------------------
#  Abstract bus
# --------------------------------------------------------------------------
class EventBus(ABC):
    """The contract every bus implementation honours."""

    #: human-readable backend name, for /health and logs
    backend: str = "abstract"

    @abstractmethod
    def publish(self, event: BusEvent) -> Optional[str]:
        """Publish an event to its tenant's stream. BEST-EFFORT: never raises.
        Returns the stream message id on success, or None if it was skipped."""

    @abstractmethod
    def read(self, tenant_id: str, *, last_id: str = "0", count: int = 100,
             block_ms: int = 0) -> list[tuple[str, BusEvent]]:
        """Read events from a tenant's stream after `last_id`. Returns a list of
        (message_id, BusEvent). `block_ms > 0` blocks up to that long for new
        events (Redis only; in-memory returns immediately)."""

    @abstractmethod
    def stats(self) -> dict:
        """Counters for observability (published, skipped, errors, backend)."""

    def healthy(self) -> bool:
        """Whether the backend is currently usable."""
        return True

    def close(self) -> None:  # noqa: B027 - optional override
        """Release any resources. Default no-op."""


# --------------------------------------------------------------------------
#  Null bus — fully disabled
# --------------------------------------------------------------------------
class NullBus(EventBus):
    """No-op bus. Used when NXR_BUS_DISABLED is set. publish() does nothing."""

    backend = "null"

    def __init__(self):
        self._published = 0  # always 0; kept for a uniform stats() shape

    def publish(self, event: BusEvent) -> Optional[str]:
        return None

    def read(self, tenant_id, *, last_id="0", count=100, block_ms=0):
        return []

    def stats(self) -> dict:
        return {"backend": self.backend, "published": 0, "skipped": 0,
                "errors": 0, "healthy": True}


# --------------------------------------------------------------------------
#  In-memory bus — fallback + tests (no Redis needed)
# --------------------------------------------------------------------------
class InMemoryBus(EventBus):
    """Thread-safe, per-tenant, bounded in-memory streams. A faithful stand-in
    for Redis Streams' semantics (append-only, id-ordered, readable after an id)
    so the platform — and the upcoming SSE feed and agents — work identically
    whether or not Redis is up. NOT durable: process exit loses events (that's
    fine; the Change Log is the durable record)."""

    backend = "memory"

    def __init__(self, maxlen: int = DEFAULT_MAXLEN):
        self._maxlen = maxlen
        self._streams: dict[str, deque] = defaultdict(lambda: deque(maxlen=maxlen))
        self._counters: dict[str, itertools.count] = {}
        self._lock = threading.Lock()
        self._published = 0
        self._skipped = 0
        self._errors = 0

    def _next_id(self, tenant_id: str) -> str:
        ctr = self._counters.get(tenant_id)
        if ctr is None:
            ctr = itertools.count(1)
            self._counters[tenant_id] = ctr
        # Mimic Redis "<ms>-<seq>" shape loosely with a monotonic integer.
        return f"{next(ctr)}-0"

    def publish(self, event: BusEvent) -> Optional[str]:
        try:
            with self._lock:
                key = stream_key(event.tenant_id)
                msg_id = self._next_id(event.tenant_id)
                self._streams[key].append((msg_id, event))
                self._published += 1
            return msg_id
        except Exception as e:  # pragma: no cover - in-memory shouldn't fail
            self._errors += 1
            log.warning("InMemoryBus.publish failed: %s", e)
            return None

    def read(self, tenant_id, *, last_id="0", count=100, block_ms=0):
        key = stream_key(tenant_id)
        # Integer compare on the "<n>-0" id prefix; "0" means "from the start".
        def _num(mid: str) -> int:
            return int(str(mid).split("-")[0])

        after = _num(last_id) if last_id and last_id != "0" else 0
        with self._lock:
            items = [(mid, ev) for (mid, ev) in self._streams.get(key, ())
                     if _num(mid) > after]
        return items[:count]

    def stats(self) -> dict:
        return {"backend": self.backend, "published": self._published,
                "skipped": self._skipped, "errors": self._errors,
                "healthy": True, "tenants": len(self._streams)}


# --------------------------------------------------------------------------
#  Redis Streams bus — the backbone implementation
# --------------------------------------------------------------------------
class RedisStreamBus(EventBus):
    """Redis Streams, one stream per tenant. publish() is best-effort: any Redis
    error is caught, counted, and swallowed so a write is never affected."""

    backend = "redis"

    def __init__(self, client, *, maxlen: int = DEFAULT_MAXLEN):
        self._r = client
        self._maxlen = maxlen
        self._published = 0
        self._skipped = 0
        self._errors = 0
        self._degraded = False  # set True after a publish failure until next ok

    @classmethod
    def connect(cls, url: Optional[str] = None, *, maxlen: int = DEFAULT_MAXLEN,
                socket_timeout: float = 1.0) -> "RedisStreamBus":
        """Build a client and verify connectivity with a ping. Raises if the
        redis package is missing or the server is unreachable — the factory
        catches that and falls back to in-memory."""
        import redis  # local import: optional dependency

        resolved = url or os.getenv("NXR_REDIS_URL") or os.getenv("REDIS_URL") \
            or DEFAULT_REDIS_URL
        client = redis.Redis.from_url(
            resolved, decode_responses=True,
            socket_connect_timeout=socket_timeout, socket_timeout=socket_timeout,
        )
        client.ping()  # raises on unreachable
        return cls(client, maxlen=maxlen)

    def publish(self, event: BusEvent) -> Optional[str]:
        try:
            msg_id = self._r.xadd(
                stream_key(event.tenant_id), event.to_wire(),
                maxlen=self._maxlen, approximate=True,
            )
            self._published += 1
            self._degraded = False
            return msg_id
        except Exception as e:
            self._errors += 1
            self._skipped += 1
            if not self._degraded:
                # Log once per degradation episode, not per event.
                log.warning("Event bus publish skipped (Redis unavailable): %s", e)
                self._degraded = True
            return None

    def read(self, tenant_id, *, last_id="0", count=100, block_ms=0):
        try:
            block = block_ms if block_ms and block_ms > 0 else None
            resp = self._r.xread({stream_key(tenant_id): last_id},
                                 count=count, block=block)
            if not resp:
                return []
            out = []
            for _stream, messages in resp:
                for msg_id, fields in messages:
                    out.append((msg_id, BusEvent.from_wire(fields)))
            return out
        except Exception as e:
            self._errors += 1
            log.warning("Event bus read failed: %s", e)
            return []

    def healthy(self) -> bool:
        try:
            return bool(self._r.ping())
        except Exception:
            return False

    def stats(self) -> dict:
        return {"backend": self.backend, "published": self._published,
                "skipped": self._skipped, "errors": self._errors,
                "healthy": not self._degraded}

    def close(self) -> None:
        try:
            self._r.close()
        except Exception:
            pass


# --------------------------------------------------------------------------
#  Factory / singleton
# --------------------------------------------------------------------------
_bus_singleton: Optional[EventBus] = None
_bus_lock = threading.Lock()


def _truthy(val: Optional[str]) -> bool:
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


def get_event_bus() -> EventBus:
    """Return the process-wide bus, creating it on first use.

    Selection order:
      1. NXR_BUS_DISABLED truthy            -> NullBus
      2. Redis reachable                    -> RedisStreamBus
      3. otherwise                          -> InMemoryBus (fallback)
    """
    global _bus_singleton
    if _bus_singleton is not None:
        return _bus_singleton

    with _bus_lock:
        if _bus_singleton is not None:
            return _bus_singleton

        if _truthy(os.getenv("NXR_BUS_DISABLED")):
            log.info("Event bus disabled via NXR_BUS_DISABLED -> NullBus")
            _bus_singleton = NullBus()
            return _bus_singleton

        try:
            bus: EventBus = RedisStreamBus.connect()
            log.info("Event bus: connected to Redis (%s)", DEFAULT_REDIS_URL)
        except Exception as e:
            log.info("Event bus: Redis unavailable (%s) -> in-memory fallback", e)
            bus = InMemoryBus()
        _bus_singleton = bus
        return _bus_singleton


def reset_event_bus(new_bus: Optional[EventBus] = None) -> None:
    """Replace (or clear) the singleton. For tests and for re-selecting the
    backend after Redis comes up. Passing a bus injects it directly."""
    global _bus_singleton
    with _bus_lock:
        if _bus_singleton is not None and new_bus is not _bus_singleton:
            try:
                _bus_singleton.close()
            except Exception:
                pass
        _bus_singleton = new_bus
