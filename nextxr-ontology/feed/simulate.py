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

import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from behaviors.registry import BehaviorRegistry, Finding, TelemetrySample
from graph.writer import GraphWriter, Rel, WriteResult

logger = logging.getLogger("feed.simulate")

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
    t0 = datetime(2026, 5, 26, 8, 0, tzinfo=UTC)
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
    it to the writer — never Neo4j directly.

    DISPATCH. Once a Finding is committed, the loop offers it to `work/` so a
    person can be told about it (see `work/from_finding.py`). That call is made
    HERE, synchronously, rather than published to the bus, because "the fault
    was detected" and "somebody was told" arriving at different times is exactly
    the gap dispatch exists to close.

    Dispatch never breaks detection. If the work tables are unreachable, or the
    twin is running with no identity layer at all, the Finding is still written
    and the loop still returns it — a plant that stops detecting because a
    staffing database is down would be a far worse failure than one that detects
    without dispatching. Pass `dispatch=False` to switch it off entirely, which
    is what the behaviour tests do: they assert on findings, not on staffing.
    """

    def __init__(self, registry: BehaviorRegistry, writer: GraphWriter, query,
                 *, dispatch: bool = True):
        self.registry = registry
        self.writer = writer
        self.query = query
        self.dispatch = dispatch

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

    def _dispatch(self, finding: Finding, sample: TelemetrySample,
                  res: WriteResult) -> None:
        """Offer a committed Finding to the work layer, best-effort.

        Everything here is guarded: the tenant may belong to no organisation
        (local dev with no identity tables), the work store may be unreachable,
        or no rule may match. None of those is a detection failure, so none of
        them is allowed to propagate.
        """
        if not res or not res.node_id:
            return
        try:
            from identity import store as identity_store
            from work import from_finding

            org_id = identity_store.tenant_owner(sample.tenant_id)
            if not org_id:
                return                      # unowned tenant: nobody to dispatch to

            asset_name = ""
            try:
                node = self.query.get_node(sample.tenant_id, finding.flags)
                asset_name = (node or {}).get("displayName") or ""
            except Exception:
                pass                        # a name is cosmetic; the id is what binds

            from_finding.handle_finding(
                org_id=org_id,
                tenant_id=sample.tenant_id,
                finding_node_id=res.node_id,
                behavior_id=finding.behavior_id,
                severity=finding.severity,
                message=finding.message,
                asset_node_id=finding.flags,
                asset_name=asset_name,
                changelog_ref=res.event_id or "",
            )
        except Exception:
            logger.debug("dispatch failed for finding %s", res.node_id,
                         exc_info=True)

    def process(self, sample: TelemetrySample) -> list[LoopOutcome]:
        outcomes = []
        for finding in self.registry.evaluate(sample, self.query):
            res = self._write_finding(finding, sample)
            if self.dispatch:
                self._dispatch(finding, sample, res)
            outcomes.append(LoopOutcome(finding=finding, result=res))
        return outcomes

    def run(self, samples) -> list[LoopOutcome]:
        out = []
        for sample in samples:
            out.extend(self.process(sample))
        return out
