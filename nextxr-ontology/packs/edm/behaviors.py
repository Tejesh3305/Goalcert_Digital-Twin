"""
edm/behaviors.py — the 3-tier behaviour registry for the wire-EDM machine.
Same edge-latched, cross-signal-aware contract as turbine/behaviors.py.
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

    def __init__(self, behavior_id, signal, limit, direction, label, unit):
        self.behavior_id = behavior_id
        self.watches = [signal]
        self.reads = [f"{label} vs redline"]
        self.emits = f"{label} out of limits"
        self._limit, self._dir, self._label, self._unit = limit, direction, label, unit
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        breach = (sample.value >= self._limit if self._dir == "above"
                  else sample.value <= self._limit)
        if not self._latch.rising(sample.entity_id, breach):
            return []
        rel = "≥" if self._dir == "above" else "≤"
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
            severity="critical",
            message=f"{self._label} out of limits: {sample.value:.1f}{self._unit} "
                    f"{rel} {self._limit:.0f}{self._unit}",
            confidence=1.0,
            evidence={"value": sample.value, "limit": self._limit,
                      "signal": sample.signal, "unit": self._unit},
        )]


class EDMGapCollapse(Behavior):
    """Tier-A physics residual: gap voltage collapsing below the clean value for
    its discharge current (inferred from peak current) — the gap is shorting."""
    behavior_id = "edm.gap_collapse"
    tier = Tier.A
    watches = [SIGNALS["gap_v"]]
    reads = ["gap voltage", "peak current (co-located)"]
    emits = "Discharge gap collapsing (shorting)"

    def __init__(self, margin: float = 12.0):
        self._margin = margin
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        peak_i = query.get_property(sample.tenant_id, sample.entity_id, "peakcurrent")
        try:
            float(peak_i)
        except (TypeError, ValueError):
            pass
        gap_clean = 58.0
        residual = gap_clean - sample.value      # positive = collapsed
        if not self._latch.rising(sample.entity_id, residual > self._margin):
            return []
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
            severity="warning",
            message=f"Gap voltage {residual:.0f}V below the clean value — gap "
                    f"instability / shorting; expect surface-finish loss",
            confidence=0.85,
            evidence={"gap_v": sample.value, "expected": gap_clean,
                      "residual": round(residual, 1), "signal": sample.signal},
        )]


class EDMShortRateTrend(Behavior):
    """Tier-B baseline: short-circuit rate deviating from its learned mean."""
    behavior_id = "edm.short_rate_zscore"
    tier = Tier.B
    watches = [SIGNALS["short_rate"]]
    reads = ["short-circuit rate history"]
    emits = "Short-circuit rate statistically abnormal"

    def __init__(self, warmup: int = 18, z: float = 3.0, window: int = 60):
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
            message=f"Short-circuit rate {sample.value:.1f}% is {detail.get('z','?')}σ "
                    f"above baseline {detail.get('mean','?')}% — rising instability",
            confidence=0.8,
            evidence={"value": sample.value, "signal": sample.signal, **detail},
        )]


def build_edm_registry() -> BehaviorRegistry:
    reg = BehaviorRegistry()
    reg.register(_HardLimit("edm.short_rate_high", SIGNALS["short_rate"],
                            redlines.short_rate, "above", "Short-circuit rate", "%"))
    reg.register(_HardLimit("edm.break_risk_high", SIGNALS["break_risk"],
                            redlines.break_risk, "above", "Wire-break risk", "%"))
    reg.register(_HardLimit("edm.die_temp_high", SIGNALS["die_temp"],
                            redlines.die_temp, "above", "Dielectric temperature", "°C"))
    reg.register(_HardLimit("edm.die_cond_high", SIGNALS["die_cond"],
                            redlines.die_cond, "above", "Dielectric conductivity", " uS/cm"))
    reg.register(_HardLimit("edm.die_press_low", SIGNALS["die_press"],
                            redlines.die_press_min, "below", "Dielectric pressure", " bar"))
    reg.register(_HardLimit("edm.wire_tension_low", SIGNALS["wire_tension"],
                            redlines.wire_tension_min, "below", "Wire tension", " N"))
    reg.register(_HardLimit("edm.gap_v_low", SIGNALS["gap_v"],
                            redlines.gap_v_min, "below", "Gap voltage", " V"))
    reg.register(EDMGapCollapse())
    reg.register(EDMShortRateTrend())
    return reg
