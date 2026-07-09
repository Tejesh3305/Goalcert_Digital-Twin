"""
packs/defence/behaviors.py — the 3-tier behaviour registry for the military base.

Base-level named rules (P4-008, P4-009, P4-011); the warship rules (ship list,
structural fatigue) live in packs/defence/warship.py.
"""
from __future__ import annotations

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


# ── P4-008 · perimeter breach ──
class PerimeterBreach(Behavior):
    """Tier-A: radar/sensor fusion detects a tracked object crossing the base
    perimeter — a Force Protection alert with the sector ID."""
    behavior_id = "defence.perimeter_breach"
    tier = Tier.A
    watches = [SIGNALS["perimeter_breaches"]]
    reads = ["fused perimeter breach events", "force protection condition (sibling)"]
    emits = "Perimeter breach (Force Protection)"

    def __init__(self):
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        if not self._latch.rising(sample.entity_id, sample.value >= 1):
            return []
        fpcon = query.get_property(sample.tenant_id, sample.entity_id, "forceProtection", 3.0)
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id, severity="critical",
            message=f"PERIMETER BREACH — {int(sample.value)} tracked object(s) crossed the wire. "
                    f"Raise Force Protection (FPCON {fpcon}); vector QRF to the reporting sector.",
            confidence=1.0,
            evidence={"breaches": int(sample.value), "fpcon": fpcon,
                      "action": "force_protection_qrf", "signal": sample.signal})]


# ── P4-009 · ammunition temperature ──
class AmmunitionTemperature(Behavior):
    """Tier-A: munitions storage temperature/humidity approaching the cook-off risk
    threshold — a safety stand-down per MIL-STD-882. Watches ammo temperature and
    reads the cook-off margin sibling."""
    behavior_id = "defence.ammunition_temperature"
    tier = Tier.A
    watches = [SIGNALS["ammo_temp"]]
    reads = ["ammunition temperature", "cook-off margin (sibling)"]
    emits = "Ammunition cook-off risk"

    def __init__(self, temp_limit=None, margin_floor=None):
        self._temp = temp_limit if temp_limit is not None else redlines.ammo_temp_max
        self._margin = margin_floor if margin_floor is not None else redlines.cookoff_margin_min
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        margin = query.get_property(sample.tenant_id, sample.entity_id, "cookoffMargin", 30.0)
        try:
            margin = float(margin)
        except (TypeError, ValueError):
            margin = 30.0
        breach = sample.value >= self._temp or margin <= self._margin
        if not self._latch.rising(sample.entity_id, breach):
            return []
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id, severity="critical",
            message=f"Ammunition cook-off RISK: magazine {sample.value:.1f} °C (margin {margin:.0f} °C ≤ "
                    f"{self._margin:.0f}) — safety stand-down per MIL-STD-882; activate magazine cooling / sprinklers.",
            confidence=1.0,
            evidence={"ammo_temp": sample.value, "cookoff_margin": margin,
                      "action": "safety_standdown", "standard": "MIL-STD-882", "signal": sample.signal})]


# ── P4-011 · aircraft flight-hour maintenance ──
class AircraftFlightHourMaintenance(Behavior):
    """Tier-C: airframe engine flight-hours/cycles approaching the maintenance
    threshold per the manufacturer task card / DEF STAN 05-34."""
    behavior_id = "defence.aircraft_flighthour_maintenance"
    tier = Tier.C
    watches = [SIGNALS["flight_hours"]]
    reads = ["flight hours to next service"]
    emits = "Aircraft maintenance due"

    def __init__(self, floor=None):
        self._floor = floor if floor is not None else redlines.flight_hours_min
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        if not self._latch.rising(sample.entity_id, sample.value <= self._floor):
            return []
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id, severity="warning",
            message=f"Aircraft {sample.value:.1f} h to scheduled service ≤ {self._floor:.0f} h — raise the task "
                    f"card and ground for maintenance per DEF STAN 05-34 before the next sortie.",
            confidence=0.9,
            evidence={"hours_remaining": sample.value, "floor": self._floor,
                      "standard": "DEF STAN 05-34", "action": "schedule_maintenance", "signal": sample.signal})]


def build_defence_registry() -> BehaviorRegistry:
    reg = BehaviorRegistry()
    reg.register(PerimeterBreach())
    reg.register(AmmunitionTemperature())
    reg.register(AircraftFlightHourMaintenance())
    reg.register(_HardLimit("defence.radar_coverage_low", SIGNALS["radar_coverage"], redlines.radar_coverage_min,
                            "below", "Radar coverage", "%"))
    reg.register(_HardLimit("defence.comms_low", SIGNALS["comms_availability"], redlines.comms_min,
                            "below", "Comms availability", "%"))
    reg.register(_HardLimit("defence.jamming_high", SIGNALS["jamming_level"], redlines.jamming_max,
                            "above", "Jamming level", "%"))
    reg.register(_HardLimit("defence.fuel_low", SIGNALS["fuel_level"], redlines.fuel_min,
                            "below", "Fuel level", "%", severity="warning"))
    reg.register(_HardLimit("defence.nbc_high", SIGNALS["nbc_reading"], redlines.nbc_max,
                            "above", "NBC contamination", ""))
    reg.register(_HardLimit("defence.readiness_low", SIGNALS["mission_readiness"], redlines.mission_readiness_min,
                            "below", "Mission readiness", "%", severity="warning"))
    return reg
