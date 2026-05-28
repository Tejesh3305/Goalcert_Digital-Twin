"""
registry.py — the Behavior Model Registry and its generic contract.

A behaviour declares THREE things and nothing domain-specific leaks into the
registry itself:

  * watches : the signal keys / entity types it reacts to (strings)
  * reads   : a human description of the graph inputs it consumes
  * emits   : a human description of the Finding it produces

and implements one method:

  * evaluate(sample, query) -> list[Finding]

The registry routes each incoming TelemetrySample to every behaviour whose
`watches` set contains the sample's signal, collects the Findings, and hands
them back to the caller (the findings-loop driver), which writes each one
through the Graph Writer. The registry never touches Neo4j and never writes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Tier(str, Enum):
    A = "A"   # physics
    B = "B"   # statistical / learned
    C = "C"   # rule


@dataclass
class TelemetrySample:
    """One reading flowing in from an adapter / the simulated feed."""
    signal: str          # e.g. "hvac:AirTemperature"
    entity_id: str       # the asset the reading concerns (must exist in graph)
    value: float
    unit: str            # e.g. "DEG_C"
    timestamp: datetime
    tenant_id: str


@dataclass
class Finding:
    """What a behaviour emits. The findings-loop driver turns this into a
    Finding-node mutation through the Graph Writer (it never persists itself).
    `flags` is the id of the entity the finding is about."""
    behavior_id: str
    tier: Tier
    flags: str                       # entity_id the finding concerns
    severity: str                    # "info" | "warning" | "critical"
    message: str
    confidence: float = 1.0
    evidence: dict = field(default_factory=dict)

    def display_name(self) -> str:
        return f"[{self.tier.value}] {self.message}"


class Behavior(ABC):
    """Base class for every behaviour, of every tier."""

    #: stable identifier, e.g. "hvac.temp_threshold"
    behavior_id: str = "abstract.behavior"
    #: which tier this behaviour belongs to
    tier: Tier = Tier.C
    #: signal keys / entity types this behaviour reacts to
    watches: list[str] = []
    #: human description of graph inputs consumed (the read contract)
    reads: list[str] = []
    #: human description of the Finding produced
    emits: str = ""

    @abstractmethod
    def evaluate(self, sample: TelemetrySample, query) -> list[Finding]:
        """Given a new sample and a read-only GraphQuery, return 0+ Findings.
        Implementations MUST NOT write to the graph."""
        raise NotImplementedError


class BehaviorRegistry:
    """Generic, domain-agnostic registry of behaviours."""

    def __init__(self):
        self._behaviors: dict[str, Behavior] = {}

    def register(self, behavior: Behavior) -> None:
        if not getattr(behavior, "behavior_id", None):
            raise ValueError("Behavior must declare a behavior_id.")
        if behavior.behavior_id in self._behaviors:
            raise ValueError(f"Behavior '{behavior.behavior_id}' already registered.")
        self._behaviors[behavior.behavior_id] = behavior

    def unregister(self, behavior_id: str) -> None:
        self._behaviors.pop(behavior_id, None)

    def all(self) -> list[Behavior]:
        return list(self._behaviors.values())

    def by_tier(self, tier: Tier) -> list[Behavior]:
        return [b for b in self._behaviors.values() if b.tier == tier]

    def watching(self, signal: str) -> list[Behavior]:
        """Behaviours whose `watches` set includes this signal key."""
        return [b for b in self._behaviors.values() if signal in b.watches]

    def evaluate(self, sample: TelemetrySample, query) -> list[Finding]:
        """Route a sample to every watching behaviour, collect all Findings."""
        findings: list[Finding] = []
        for b in self.watching(sample.signal):
            findings.extend(b.evaluate(sample, query) or [])
        return findings

    def describe(self) -> list[dict]:
        """Introspection: what's registered (for the UI / capability model)."""
        return [
            {
                "behavior_id": b.behavior_id,
                "tier": b.tier.value,
                "watches": list(b.watches),
                "reads": list(b.reads),
                "emits": b.emits,
            }
            for b in self._behaviors.values()
        ]
