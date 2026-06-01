"""NextXR Event Bus — the live fan-out layer over committed mutations.

Every mutation that passes the Graph Writer (and is therefore recorded in the
hash-chained Change Log) is ALSO published to the event bus as a single event.
The Change Log (SQLite, tamper-evident) remains the durable source of truth;
the bus is the decoupling layer that lets downstream consumers — the dashboard
SSE feed, correlation/diagnosis agents, external adapters — react to changes in
real time without polling the graph.

Backbone implementation is Redis Streams (one stream per tenant, preserving the
tenant-isolation contract on the wire). Publishing is BEST-EFFORT: if Redis is
unreachable the write still commits and logs — the bus publish is skipped and
counted, never raised. An in-memory implementation is used as an automatic
fallback (and for tests / no-Docker runs).
"""

from .event_bus import (
    BusEvent,
    EventBus,
    InMemoryBus,
    NullBus,
    RedisStreamBus,
    get_event_bus,
    reset_event_bus,
    stream_key,
)

__all__ = [
    "BusEvent",
    "EventBus",
    "InMemoryBus",
    "NullBus",
    "RedisStreamBus",
    "get_event_bus",
    "reset_event_bus",
    "stream_key",
]
