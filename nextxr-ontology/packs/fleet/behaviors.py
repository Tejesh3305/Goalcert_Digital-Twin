"""
fleet/behaviors.py — the 3-tier behaviour registry for the tram fleet network.
Same edge-latched, cross-signal-aware contract as the other domains.
"""
from __future__ import annotations

from collections import defaultdict, deque

from behaviors.registry import Behavior, BehaviorRegistry, Finding, Tier
from .physics import SIGNALS, redlines


class _Latch:
    def __init__(self):
        self._on: dict[str, bool] = {}

    def rising(self, key: str, cond: bool) -> bool:
        prev = self._on.get(key, False)
        self._on[key] = cond
        return cond and not prev


class _HardLimit(Behavior):
    tier = Tier.C

    def __init__(self, behavior_id, signal, limit, direction, label, unit, severity="critical"):
        self.behavior_id = behavior_id
        self.watches = [signal]
        self.reads = [f"{label} vs limit"]
        self.emits = f"{label} out of limits"
        self._limit, self._dir, self._label = limit, direction, label
        self._unit, self._sev = unit, severity
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        breach = (sample.value >= self._limit if self._dir == "above"
                  else sample.value <= self._limit)
        if not self._latch.rising(sample.entity_id, breach):
            return []
        rel = "≥" if self._dir == "above" else "≤"
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
            severity=self._sev,
            message=f"{self._label} out of limits: {sample.value:.1f}{self._unit} "
                    f"{rel} {self._limit:.0f}{self._unit}",
            confidence=1.0,
            evidence={"value": sample.value, "limit": self._limit,
                      "signal": sample.signal, "unit": self._unit},
        )]


class FleetOHLResidual(Behavior):
    """Tier-A: overhead-line voltage sagging below its nominal 750 V — traction
    power-supply weakness (substation loading or OHL damage)."""
    behavior_id = "fleet.ohl_sag"
    tier = Tier.A
    watches = [SIGNALS["ohl_v"]]
    reads = ["overhead voltage"]
    emits = "Overhead-line voltage sag"

    def __init__(self, margin: float = 60.0):
        self._margin = margin
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        residual = 750.0 - sample.value
        if not self._latch.rising(sample.entity_id, residual > self._margin):
            return []
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
            severity="warning",
            message=f"Overhead voltage sagging {residual:.0f}V below nominal — "
                    f"traction supply weakness (check substations / OHL)",
            confidence=0.82,
            evidence={"ohl_v": sample.value, "nominal": 750.0,
                      "residual": round(residual, 1), "signal": sample.signal},
        )]


class FleetDelayTrend(Behavior):
    """Tier-B: network delay drifting above its learned baseline."""
    behavior_id = "fleet.delay_zscore"
    tier = Tier.B
    watches = [SIGNALS["delay"]]
    reads = ["network delay history"]
    emits = "Network delay statistically abnormal"

    def __init__(self, warmup: int = 16, z: float = 3.0, window: int = 60):
        self._warmup, self._z = warmup, z
        self._hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=window))
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        h = self._hist[sample.entity_id]
        abnormal, detail = False, {}
        if len(h) >= self._warmup:
            mean = sum(h) / len(h)
            std = (sum((x - mean) ** 2 for x in h) / len(h)) ** 0.5 or 1e-6
            z = (sample.value - mean) / std
            abnormal = z >= self._z
            detail = {"mean": round(mean, 2), "z": round(z, 2)}
        h.append(sample.value)
        if not self._latch.rising(sample.entity_id, abnormal):
            return []
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
            severity="warning",
            message=f"Network delay {sample.value:.1f} min is {detail.get('z','?')}σ "
                    f"above baseline {detail.get('mean','?')} min — service degrading",
            confidence=0.8,
            evidence={"value": sample.value, "signal": sample.signal, **detail},
        )]


def build_fleet_registry() -> BehaviorRegistry:
    reg = BehaviorRegistry()
    reg.register(_HardLimit("fleet.otp_low", SIGNALS["otp"], redlines.otp_min,
                            "below", "On-time performance", "%"))
    reg.register(_HardLimit("fleet.headway_low", SIGNALS["headway"], redlines.headway_min,
                            "below", "Headway adherence", "%", severity="warning"))
    reg.register(_HardLimit("fleet.ohl_low", SIGNALS["ohl_v"], redlines.ohl_v_min,
                            "below", "Overhead voltage", " V"))
    reg.register(_HardLimit("fleet.sub_high", SIGNALS["sub_load"], redlines.sub_load_max,
                            "above", "Substation load", "%"))
    reg.register(_HardLimit("fleet.track_hot", SIGNALS["track_temp"], redlines.track_temp_max,
                            "above", "Rail temperature", "°C"))
    reg.register(_HardLimit("fleet.vib_high", SIGNALS["vib"], redlines.vib_max,
                            "above", "Bogie vibration", "g"))
    reg.register(_HardLimit("fleet.brake_worn", SIGNALS["brake_wear"], redlines.brake_wear_max,
                            "above", "Brake wear", "%"))
    reg.register(_HardLimit("fleet.panto_worn", SIGNALS["panto_wear"], redlines.panto_wear_max,
                            "above", "Pantograph wear", "%"))
    reg.register(_HardLimit("fleet.delay_high", SIGNALS["delay"], redlines.delay_max,
                            "above", "Network delay", " min"))
    reg.register(_HardLimit("fleet.avail_low", SIGNALS["fleet_avail"], redlines.fleet_avail_min,
                            "below", "Fleet availability", "%"))
    reg.register(FleetOHLResidual())
    reg.register(FleetDelayTrend())
    return reg
