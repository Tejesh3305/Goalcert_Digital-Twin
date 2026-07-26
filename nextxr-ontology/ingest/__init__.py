"""
ingest — the telemetry inbound layer.

This is the half of a digital twin the platform did not have. Everything the UI
rendered came from `dynamics/` or a pack's `physics.py`: a simulator, not a twin.
There was no endpoint that accepted a measurement, no device identity, and
nowhere to put a sample if one arrived. This package is the producer side; the
`historian` package is where it lands.

    ingest/devices.py    who may push (per-device credentials, not tenant keys)
    ingest/pipeline.py   the one path every source funnels through
    server/ingest_routes.py   the HTTP surface

DESIGN RULE: ONE PIPELINE, MANY SOURCES
---------------------------------------
HTTP, MQTT/Sparkplug, OPC-UA, a CSV backfill and the existing simulator all
converge on `pipeline.submit()` before anything is stored. Validation, unit
normalisation, quality handling, idempotency, historian write, live-frame update
and behaviour evaluation therefore happen exactly once, in one place, and a new
protocol connector is a thin adapter rather than a second implementation of the
rules. The alternative — each connector writing to the store itself — is how
platforms end up with per-protocol data-quality bugs that only reproduce on one
customer's site.
"""

from __future__ import annotations

from . import devices
from .pipeline import IngestResult, submit, submit_raw

__all__ = ["IngestResult", "devices", "submit", "submit_raw"]
