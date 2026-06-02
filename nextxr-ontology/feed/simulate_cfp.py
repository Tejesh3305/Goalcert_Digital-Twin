"""
simulate_cfp.py — multi-system facility telemetry generator for the CFP demo.

Generates telemetry across power (UPS SoC, transformer temp), fire (smoke),
security (door state), water (tank level, flow), network (heartbeat),
HVAC (filter ΔP, chiller COP), and rotating equipment (pump vibration).

Each generator yields TelemetrySample objects that drive the same FindingsLoop
used by the HVAC feed. The profile for each system includes a normal phase
followed by a fault, so every behavior gets a chance to fire.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from behaviors.registry import TelemetrySample


def _base_time():
    return datetime(2026, 6, 2, 8, 0, tzinfo=timezone.utc)


def simulate_ups(tenant_id: str, entity_id: str, *,
                 minutes: int = 30, fault_at: int = 15, seed: int = 100):
    """UPS state-of-charge: 100% during normal, drops sharply at fault_at
    (simulating mains loss)."""
    rng = random.Random(seed)
    t0 = _base_time()
    soc = 100.0
    for m in range(minutes):
        ts = t0 + timedelta(minutes=m)
        if m < fault_at:
            soc = 100.0 + rng.gauss(0, 0.3)
        else:
            # Drain ~3%/min on battery
            soc = max(0, 100.0 - (m - fault_at) * 3.0 + rng.gauss(0, 0.5))
        yield TelemetrySample(
            signal="cfp:upsSoC", entity_id=entity_id,
            value=round(soc, 1), unit="PERCENT",
            timestamp=ts, tenant_id=tenant_id,
        )


def simulate_transformer_temp(tenant_id: str, entity_id: str, *,
                              minutes: int = 30, fault_at: int = 18, seed: int = 101):
    """Transformer oil temperature: normal ~65°C, ramps past 85°C at fault."""
    rng = random.Random(seed)
    t0 = _base_time()
    for m in range(minutes):
        ts = t0 + timedelta(minutes=m)
        if m < fault_at:
            value = 65.0 + rng.gauss(0, 1.0)
        else:
            ramp = min(30.0, (m - fault_at) * 2.5)
            value = 65.0 + ramp + rng.gauss(0, 0.5)
        yield TelemetrySample(
            signal="cfp:oilTemperature", entity_id=entity_id,
            value=round(value, 1), unit="DEG_C",
            timestamp=ts, tenant_id=tenant_id,
        )


def simulate_smoke(tenant_id: str, entity_id: str, *,
                   minutes: int = 30, fault_at: int = 20, seed: int = 102):
    """Smoke detector obscuration: ~0.5% normal, spikes to 5%+ at fault."""
    rng = random.Random(seed)
    t0 = _base_time()
    for m in range(minutes):
        ts = t0 + timedelta(minutes=m)
        if m < fault_at:
            value = max(0, 0.5 + rng.gauss(0, 0.2))
        else:
            value = 5.0 + (m - fault_at) * 0.5 + rng.gauss(0, 0.3)
        yield TelemetrySample(
            signal="cfp:smokeObscuration", entity_id=entity_id,
            value=round(value, 2), unit="PERCENT",
            timestamp=ts, tenant_id=tenant_id,
        )


def simulate_door(tenant_id: str, entity_id: str, *,
                  minutes: int = 30, forced_at: int = 12, seed: int = 103):
    """Door state: 0=normal, 1=forced at fault_at."""
    t0 = _base_time()
    for m in range(minutes):
        ts = t0 + timedelta(minutes=m)
        value = 1.0 if m == forced_at else 0.0
        yield TelemetrySample(
            signal="cfp:doorState", entity_id=entity_id,
            value=value, unit="NUM",
            timestamp=ts, tenant_id=tenant_id,
        )


def simulate_tank_level(tenant_id: str, entity_id: str, *,
                        minutes: int = 30, drain_at: int = 10, seed: int = 104):
    """Water tank level: starts at 80%, slow drain from drain_at."""
    rng = random.Random(seed)
    t0 = _base_time()
    level = 80.0
    for m in range(minutes):
        ts = t0 + timedelta(minutes=m)
        if m < drain_at:
            level = 80.0 + rng.gauss(0, 0.5)
        else:
            level = max(0, 80.0 - (m - drain_at) * 4.0 + rng.gauss(0, 0.3))
        yield TelemetrySample(
            signal="cfp:tankLevel", entity_id=entity_id,
            value=round(level, 1), unit="PERCENT",
            timestamp=ts, tenant_id=tenant_id,
        )


def simulate_heartbeat(tenant_id: str, entity_id: str, *,
                       minutes: int = 30, offline_at: int = 22, seed: int = 105):
    """Edge node heartbeat: 1=alive, 0=missed starting at offline_at."""
    t0 = _base_time()
    for m in range(minutes):
        ts = t0 + timedelta(minutes=m)
        value = 0.0 if m >= offline_at else 1.0
        yield TelemetrySample(
            signal="cfp:heartbeat", entity_id=entity_id,
            value=value, unit="NUM",
            timestamp=ts, tenant_id=tenant_id,
        )


def simulate_filter_dp(tenant_id: str, entity_id: str, *,
                       minutes: int = 30, clog_at: int = 12, seed: int = 106):
    """AHU filter differential pressure: normal ~120 Pa, rises to 300+ at clog."""
    rng = random.Random(seed)
    t0 = _base_time()
    for m in range(minutes):
        ts = t0 + timedelta(minutes=m)
        if m < clog_at:
            value = 120.0 + rng.gauss(0, 5.0)
        else:
            ramp = min(200, (m - clog_at) * 15.0)
            value = 120.0 + ramp + rng.gauss(0, 3.0)
        yield TelemetrySample(
            signal="cfp:filterDeltaP", entity_id=entity_id,
            value=round(value, 1), unit="PA",
            timestamp=ts, tenant_id=tenant_id,
        )


def simulate_chiller_cop(tenant_id: str, entity_id: str, *,
                         minutes: int = 40, drift_at: int = 20, seed: int = 107):
    """Chiller COP: normal ~5.2, drifts down to ~3.5 at fault."""
    rng = random.Random(seed)
    t0 = _base_time()
    for m in range(minutes):
        ts = t0 + timedelta(minutes=m)
        if m < drift_at:
            value = 5.2 + rng.gauss(0, 0.15)
        else:
            drop = min(2.0, (m - drift_at) * 0.1)
            value = 5.2 - drop + rng.gauss(0, 0.1)
        yield TelemetrySample(
            signal="cfp:chillerCOP", entity_id=entity_id,
            value=round(value, 2), unit="NUM",
            timestamp=ts, tenant_id=tenant_id,
        )


def simulate_vibration(tenant_id: str, entity_id: str, *,
                       minutes: int = 45, fault_at: int = 25, seed: int = 108):
    """Pump vibration: normal ~2.5 mm/s, spikes to 8+ at fault."""
    rng = random.Random(seed)
    t0 = _base_time()
    for m in range(minutes):
        ts = t0 + timedelta(minutes=m)
        if m < fault_at:
            value = 2.5 + rng.gauss(0, 0.3)
        else:
            ramp = min(8.0, (m - fault_at) * 0.4)
            value = 2.5 + ramp + rng.gauss(0, 0.2)
        yield TelemetrySample(
            signal="cfp:vibration", entity_id=entity_id,
            value=round(value, 2), unit="MM_PER_SEC",
            timestamp=ts, tenant_id=tenant_id,
        )


def interleave(*generators):
    """Merge multiple sample generators by timestamp order."""
    import heapq
    heap = []
    for i, gen in enumerate(generators):
        try:
            sample = next(gen)
            heapq.heappush(heap, (sample.timestamp, i, sample, gen))
        except StopIteration:
            pass
    while heap:
        ts, idx, sample, gen = heapq.heappop(heap)
        yield sample
        try:
            nxt = next(gen)
            heapq.heappush(heap, (nxt.timestamp, idx, nxt, gen))
        except StopIteration:
            pass


def simulate_facility(tenant_id: str, asset_ids: dict[str, str]):
    """Run the full CFP facility simulation with all systems.

    asset_ids maps signal role to entity_id:
        {
            "ups": "<ups entity id>",
            "transformer": "<transformer entity id>",
            "smoke_detector": "<smoke detector entity id>",
            "door": "<access door entity id>",
            "water_tank": "<water tank entity id>",
            "edge_node": "<edge node entity id>",
            "filter": "<filter entity id>",
            "chiller": "<chiller entity id>",
            "pump": "<pump entity id>",
        }
    """
    generators = []
    if "ups" in asset_ids:
        generators.append(simulate_ups(tenant_id, asset_ids["ups"]))
    if "transformer" in asset_ids:
        generators.append(simulate_transformer_temp(tenant_id, asset_ids["transformer"]))
    if "smoke_detector" in asset_ids:
        generators.append(simulate_smoke(tenant_id, asset_ids["smoke_detector"]))
    if "door" in asset_ids:
        generators.append(simulate_door(tenant_id, asset_ids["door"]))
    if "water_tank" in asset_ids:
        generators.append(simulate_tank_level(tenant_id, asset_ids["water_tank"]))
    if "edge_node" in asset_ids:
        generators.append(simulate_heartbeat(tenant_id, asset_ids["edge_node"]))
    if "filter" in asset_ids:
        generators.append(simulate_filter_dp(tenant_id, asset_ids["filter"]))
    if "chiller" in asset_ids:
        generators.append(simulate_chiller_cop(tenant_id, asset_ids["chiller"], minutes=40))
    if "pump" in asset_ids:
        generators.append(simulate_vibration(tenant_id, asset_ids["pump"], minutes=45))

    yield from interleave(*generators)
