"""
simulate.py — a deterministic HVAC telemetry generator and the findings-loop
driver that closes the Track 3 loop.

  feed sample -> registry.evaluate(sample) -> Finding(s)
              -> GraphWriter.create(Finding)  [validate -> commit -> emit -> stamp]
              -> Finding node in the graph, carrying a Change Log event

The driver does NOT write Cypher. It only calls the Graph Writer, exactly as
every adapter and agent must.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from behaviors.registry import BehaviorRegistry, Finding, TelemetrySample
from graph.writer import GraphWriter, Rel, WriteResult

FINDING_TYPE = "https://ontology.nextxr.io/v3/core#Finding"


def simulate_temperature(tenant_id: str, entity_id: str, *,
                         setpoint: float = 22.0, minutes: int = 30,
                         normal_minutes: int = 15, seed: int = 42):
    """Yield one TemperatureSample per simulated minute.

    Profile: `normal_minutes` of healthy operation near the setpoint (so a
    Tier-B learner can fit a baseline), then a fault — temperature ramps up
    and stays high (so a Tier-C threshold rule fires after its sustain window,
    and the Tier-B learner flags the deviation)."""
    rng = random.Random(seed)
    t0 = datetime(2026, 5, 26, 8, 0, tzinfo=timezone.utc)
    for m in range(minutes):
        ts = t0 + timedelta(minutes=m)
        if m < normal_minutes:
            value = setpoint + rng.gauss(0, 0.3)
        else:
            ramp = min(6.0, (m - (normal_minutes - 1)) * 1.5)
            value = setpoint + ramp + rng.gauss(0, 0.3)
        yield TelemetrySample(
            signal="hvac:AirTemperature",
            entity_id=entity_id,
            value=round(value, 2),
            unit="DEG_C",
            timestamp=ts,
            tenant_id=tenant_id,
        )


@dataclass
class LoopOutcome:
    """One (finding, write-result) pair the loop produced."""
    finding: Finding
    result: WriteResult


class FindingsLoop:
    """Routes telemetry through the registry and writes Findings via the
    Graph Writer. The only thing it knows how to do with a Finding is hand
    it to the writer — never Neo4j directly."""

    def __init__(self, registry: BehaviorRegistry, writer: GraphWriter, query):
        self.registry = registry
        self.writer = writer
        self.query = query

    def _write_finding(self, finding: Finding, sample: TelemetrySample) -> WriteResult:
        return self.writer.create(
            tenant_id=sample.tenant_id,
            canonical_type=FINDING_TYPE,
            actor=f"behavior:{finding.behavior_id}",
            properties={
                "displayName": finding.display_name(),
                "status": "open",
                "severity": finding.severity,
                "message": finding.message,
                "confidence": float(finding.confidence),
                "tier": finding.tier.value,
                "behaviorId": finding.behavior_id,
            },
            relationships=[Rel("nxr:flags", finding.flags)],
        )

    def process(self, sample: TelemetrySample) -> list[LoopOutcome]:
        outcomes = []
        for finding in self.registry.evaluate(sample, self.query):
            res = self._write_finding(finding, sample)
            outcomes.append(LoopOutcome(finding=finding, result=res))
        return outcomes

    def run(self, samples) -> list[LoopOutcome]:
        out = []
        for sample in samples:
            out.extend(self.process(sample))
        return out
