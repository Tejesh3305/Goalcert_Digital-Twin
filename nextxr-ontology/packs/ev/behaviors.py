"""
ev/behaviors.py — the 3-tier behaviour registry for the EV charging network.

Includes the two network-level named rules (P2-012, P2-013); the battery-level
named rules (cell imbalance, thermal-runaway precursor, SoH) live in ev/battery.py.
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
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id, severity=self._sev,
            message=f"{self._label} out of limits: {sample.value:.1f}{self._unit} {rel} {self._limit:.0f}{self._unit}",
            confidence=1.0,
            evidence={"value": sample.value, "limit": self._limit, "signal": sample.signal})]


# ── P2-012 · charger derating ──
class ChargerDerating(Behavior):
    """Tier-C: reduce charger output power when the connector overheats (>60 °C) or
    the grid voltage sags below 90% nominal. Watches connector temperature and
    reads the grid-voltage sibling so one rule covers both triggers."""
    behavior_id = "ev.charger_derating"
    tier = Tier.C
    watches = [SIGNALS["connector_temp"]]
    reads = ["connector temperature", "grid voltage (sibling)"]
    emits = "Charger power derating"

    def __init__(self, temp_limit=None, volt_floor=None):
        self._temp = temp_limit if temp_limit is not None else redlines.connector_temp_max
        self._volt = volt_floor if volt_floor is not None else redlines.grid_voltage_min
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        volt = query.get_property(sample.tenant_id, sample.entity_id, "gridVoltage", 100.0)
        try:
            volt = float(volt)
        except (TypeError, ValueError):
            volt = 100.0
        hot = sample.value >= self._temp
        weak = volt <= self._volt
        if not self._latch.rising(sample.entity_id, hot or weak):
            return []
        why = f"connector {sample.value:.0f}°C" if hot else f"grid voltage {volt:.0f}%"
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id, severity="warning",
            message=f"Charger power derating triggered ({why}) — reducing output to protect the "
                    f"connector/grid; OCPP setpoint lowered.",
            confidence=0.9,
            evidence={"connector_temp": sample.value, "grid_voltage": volt,
                      "action": "reduce_output", "signal": sample.signal})]


# ── P2-013 · grid overload ──
class GridOverload(Behavior):
    """Tier-A: total charging load approaching the transformer rated capacity —
    shed the lowest-priority chargers to prevent a transformer trip."""
    behavior_id = "ev.grid_overload"
    tier = Tier.A
    watches = [SIGNALS["transformer_load"]]
    reads = ["transformer load vs rated kVA"]
    emits = "Grid overload — load shedding"

    def __init__(self, limit: float = 90.0):
        self._limit = limit
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        if not self._latch.rising(sample.entity_id, sample.value >= self._limit):
            return []
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id, severity="critical",
            message=f"Grid overload — transformer at {sample.value:.0f}% of rated kVA ≥ {self._limit:.0f}%. "
                    f"Shedding lowest-priority chargers to prevent a transformer trip.",
            confidence=1.0,
            evidence={"transformer_load": sample.value, "limit": self._limit,
                      "action": "shed_low_priority", "signal": sample.signal})]


# ── Tier-B: network load trend ──
class LoadTrend(Behavior):
    behavior_id = "ev.load_zscore"
    tier = Tier.B
    watches = [SIGNALS["network_load"]]
    reads = ["network load history"]
    emits = "Network load statistically abnormal"

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
            detail = {"mean": round(mean, 0), "z": round(z, 2)}
        h.append(sample.value)
        if not self._latch.rising(sample.entity_id, abnormal):
            return []
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id, severity="warning",
            message=f"Network load {sample.value:.0f} kW is {detail.get('z', '?')}σ above baseline "
                    f"{detail.get('mean', '?')} kW — demand spike building.",
            confidence=0.8,
            evidence={"value": sample.value, "signal": sample.signal, **detail})]


def build_ev_registry() -> BehaviorRegistry:
    reg = BehaviorRegistry()
    reg.register(ChargerDerating())
    reg.register(GridOverload())
    reg.register(LoadTrend())
    reg.register(_HardLimit("ev.transformer_hot", SIGNALS["transformer_hotspot"], redlines.transformer_hotspot_max,
                            "above", "Transformer hot-spot", "°C"))
    reg.register(_HardLimit("ev.transformer_aging", SIGNALS["transformer_aging"], redlines.transformer_aging_max,
                            "above", "Transformer aging rate", "x"))
    reg.register(_HardLimit("ev.grid_undervolt", SIGNALS["grid_voltage"], redlines.grid_voltage_min,
                            "below", "Grid voltage", "%"))
    reg.register(_HardLimit("ev.freq_low", SIGNALS["grid_frequency"], redlines.grid_freq_min,
                            "below", "Grid frequency", " Hz", severity="warning"))
    reg.register(_HardLimit("ev.chargers_low", SIGNALS["available_chargers"], redlines.available_chargers_min,
                            "below", "Available chargers", "%", severity="warning"))
    reg.register(_HardLimit("ev.charger_faults", SIGNALS["charger_faults"], redlines.charger_faults_max,
                            "above", "Charger faults", "", severity="warning"))
    return reg
