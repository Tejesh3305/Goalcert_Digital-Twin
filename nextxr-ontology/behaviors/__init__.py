"""NextXR Behavior Model Registry — the three-tier behaviour substrate.

The registry is GENERIC: it knows nothing about HVAC, maritime, or hospitals.
A behaviour registers itself declaring what it watches, what it reads, and
what Finding it emits. Domain packs supply the concrete behaviours.

Tiers:
  A — physics (human-authored equations; the registry just holds the slot)
  B — statistical (learned baselines: z-score, Isolation Forest, ...)
  C — rule (hand-written thresholds)
"""

from .registry import (
    Behavior, BehaviorRegistry, Finding, TelemetrySample, Tier,
)

__all__ = ["Behavior", "BehaviorRegistry", "Finding", "TelemetrySample", "Tier"]
