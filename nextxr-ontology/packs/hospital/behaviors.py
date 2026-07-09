"""
hospital/behaviors.py — the 3-tier behaviour registry for the hospital campus.

Edge-latched, cross-signal-aware, same contract as the other domains. Includes
the six clinical-safety named rules (P3-011 … P3-016):

  hospital.or_pressure_diff       OR positive pressure < 8 Pa → JCI action + AHU check
  hospital.medical_gas_low        O2 outlet < 350 kPa → P0 Facilities + clinical alarm
  hospital.cold_chain_excursion   blood bank > 6 °C (>15 min) → pharmacist quarantine
  hospital.ups_battery_low        UPS runtime < 15 min → load shed + test generator
  hospital.generator_fail_to_start standby genset not up in 10 s → Code Utility
  hospital.legionella_threshold   hot return < 50 °C or cold supply > 20 °C (HTM 04-01)
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
                      "signal": sample.signal, "unit": self._unit})]


# ── P3-011 · OR pressure differential ──
class ORPressureDifferential(Behavior):
    """Tier-A: operating-theatre positive pressure below the safe differential —
    contamination ingress risk. JCI-compliant immediate action + AHU fault check."""
    behavior_id = "hospital.or_pressure_diff"
    tier = Tier.A
    watches = [SIGNALS["or_pressure"]]
    reads = ["OR positive pressure"]
    emits = "OR pressure differential low"

    def __init__(self, floor: float = None):
        self._floor = floor if floor is not None else redlines.or_pressure_min
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        if not self._latch.rising(sample.entity_id, sample.value <= self._floor):
            return []
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
            severity="critical",
            message=f"OR positive pressure {sample.value:.1f} Pa ≤ {self._floor:.0f} Pa — "
                    f"JCI immediate action: suspend elective surgery, check AHU/supply fan and door seals.",
            confidence=1.0,
            evidence={"or_pressure": sample.value, "floor": self._floor,
                      "action": "jci_immediate_ahu_check", "signal": sample.signal})]


# ── P3-012 · medical gas low pressure (P0) ──
class MedicalGasLowPressure(Behavior):
    """Tier-A: O2 pipeline outlet pressure below the HTM 02-01 low-pressure
    setpoint. P0 escalation to Facilities Engineering + clinical alarm."""
    behavior_id = "hospital.medical_gas_low"
    tier = Tier.A
    watches = [SIGNALS["o2_pressure"]]
    reads = ["O2 outlet pressure"]
    emits = "Medical gas low pressure (P0)"

    def __init__(self, floor: float = None):
        self._floor = floor if floor is not None else redlines.o2_pressure_min
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        if not self._latch.rising(sample.entity_id, sample.value <= self._floor):
            return []
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
            severity="critical",
            message=f"P0 — medical O2 outlet {sample.value:.0f} kPa ≤ {self._floor:.0f} kPa. "
                    f"Escalate to Facilities Engineering; raise clinical area alarm; switch to reserve bank.",
            confidence=1.0,
            evidence={"o2_pressure": sample.value, "floor": self._floor, "priority": "P0",
                      "action": "facilities_escalation", "signal": sample.signal})]


# ── P3-013 · cold chain excursion ──
class ColdChainExcursion(Behavior):
    """Tier-C: blood-bank temperature above 6 °C for a sustained period — logs an
    excursion event and triggers pharmacist quarantine review per SOP."""
    behavior_id = "hospital.cold_chain_excursion"
    tier = Tier.C
    watches = [SIGNALS["bloodbank_temp"]]
    reads = ["blood-bank temperature (sustained)"]
    emits = "Cold-chain excursion"

    def __init__(self, limit: float = None, sustain: int = 4):
        self._limit = limit if limit is not None else redlines.bloodbank_max
        self._sustain = sustain
        self._count: dict[str, int] = defaultdict(int)
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        over = sample.value > self._limit
        self._count[sample.entity_id] = self._count[sample.entity_id] + 1 if over else 0
        breach = self._count[sample.entity_id] >= self._sustain
        if not self._latch.rising(sample.entity_id, breach):
            return []
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
            severity="critical",
            message=f"Cold-chain excursion: blood bank {sample.value:.1f} °C > {self._limit:.0f} °C "
                    f"sustained — quarantine stock and trigger pharmacist review per SOP.",
            confidence=0.95,
            evidence={"temperature": sample.value, "limit": self._limit,
                      "action": "pharmacist_quarantine", "signal": sample.signal})]


# ── P3-014 · UPS battery low ──
class UPSBatteryLow(Behavior):
    """Tier-C: UPS runtime below 15 minutes on the critical load — recommend
    non-critical load shed and alert Facilities to test generator readiness."""
    behavior_id = "hospital.ups_battery_low"
    tier = Tier.C
    watches = [SIGNALS["ups_runtime"]]
    reads = ["UPS runtime remaining"]
    emits = "UPS battery low"

    def __init__(self, floor: float = None):
        self._floor = floor if floor is not None else redlines.ups_runtime_min
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        if not self._latch.rising(sample.entity_id, sample.value <= self._floor):
            return []
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
            severity="warning",
            message=f"UPS runtime {sample.value:.0f} min ≤ {self._floor:.0f} min — shed non-critical "
                    f"load and alert Facilities to confirm generator readiness.",
            confidence=0.9,
            evidence={"runtime_min": sample.value, "floor": self._floor,
                      "action": "load_shed_test_generator", "signal": sample.signal})]


# ── P3-015 · generator fail-to-start ──
class GeneratorFailToStart(Behavior):
    """Tier-A: the AMF signal fired (mains lost) but the standby generator did not
    reach rated speed within 10 s — Code Utility."""
    behavior_id = "hospital.generator_fail_to_start"
    tier = Tier.A
    watches = [SIGNALS["gen_ready"]]
    reads = ["generator ready state"]
    emits = "Generator fail-to-start"

    def __init__(self):
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        # gen_ready drops to 0 only when mains is lost AND the genset has not started.
        if not self._latch.rising(sample.entity_id, sample.value < 0.5):
            return []
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
            severity="critical",
            message="Generator FAIL-TO-START — AMF relay fired but the standby set did not reach "
                    "rated speed within 10 s. Declare Code Utility; the UPS is now the only reserve.",
            confidence=1.0,
            evidence={"gen_ready": sample.value, "action": "code_utility", "signal": sample.signal})]


# ── P3-016 · Legionella temperature threshold ──
class LegionellaThreshold(Behavior):
    """Tier-C: hot-water return below 50 °C or cold-water supply above 20 °C
    (outside the HTM 04-01 control band). Watches the hot return and reads the cold
    supply sibling so one rule covers both bounds."""
    behavior_id = "hospital.legionella_threshold"
    tier = Tier.C
    watches = [SIGNALS["hot_water"]]
    reads = ["hot-water return", "cold-water supply (sibling)"]
    emits = "Legionella control-band breach"

    def __init__(self, hot_floor: float = None, cold_ceiling: float = None):
        self._hot = hot_floor if hot_floor is not None else redlines.hot_water_min
        self._cold = cold_ceiling if cold_ceiling is not None else redlines.cold_water_max
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        cold = query.get_property(sample.tenant_id, sample.entity_id, "coldWaterSupplyTemp", 15.0)
        try:
            cold = float(cold)
        except (TypeError, ValueError):
            cold = 15.0
        hot_bad = sample.value < self._hot
        cold_bad = cold > self._cold
        if not self._latch.rising(sample.entity_id, hot_bad or cold_bad):
            return []
        why = []
        if hot_bad:
            why.append(f"hot return {sample.value:.1f} °C < {self._hot:.0f} °C")
        if cold_bad:
            why.append(f"cold supply {cold:.1f} °C > {self._cold:.0f} °C")
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
            severity="warning",
            message=f"Legionella control-band breach ({'; '.join(why)}) — HTM 04-01. "
                    f"Schedule thermal flush of dead-legs and review TMV/circulation.",
            confidence=0.85,
            evidence={"hot_water": sample.value, "cold_water": cold,
                      "action": "thermal_flush", "signal": sample.signal})]


# ── Tier-B: ED wait-time trend ──
class EDWaitTrend(Behavior):
    """Tier-B: ED wait time drifting above its learned baseline (flow congestion)."""
    behavior_id = "hospital.ed_wait_zscore"
    tier = Tier.B
    watches = [SIGNALS["ed_wait"]]
    reads = ["ED wait-time history"]
    emits = "ED wait time statistically abnormal"

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
            detail = {"mean": round(mean, 1), "z": round(z, 2)}
        h.append(sample.value)
        if not self._latch.rising(sample.entity_id, abnormal):
            return []
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
            severity="warning",
            message=f"ED wait {sample.value:.0f} min is {detail.get('z', '?')}σ above baseline "
                    f"{detail.get('mean', '?')} min — flow congestion building.",
            confidence=0.8,
            evidence={"value": sample.value, "signal": sample.signal, **detail})]


def build_hospital_registry() -> BehaviorRegistry:
    reg = BehaviorRegistry()
    # Named clinical-safety rules (P3-011 … P3-016)
    reg.register(ORPressureDifferential())
    reg.register(MedicalGasLowPressure())
    reg.register(ColdChainExcursion())
    reg.register(UPSBatteryLow())
    reg.register(GeneratorFailToStart())
    reg.register(LegionellaThreshold())
    # Trend
    reg.register(EDWaitTrend())
    # Hard limits for the remaining aggregate KPIs
    reg.register(_HardLimit("hospital.iso_pressure", SIGNALS["iso_pressure"], redlines.iso_pressure_max,
                            "above", "Isolation room pressure", " Pa"))
    reg.register(_HardLimit("hospital.air_changes_low", SIGNALS["air_changes"], redlines.air_changes_min,
                            "below", "Air changes per hour", " ACH", severity="warning"))
    reg.register(_HardLimit("hospital.autoclave_f0_low", SIGNALS["autoclave_f0"], redlines.autoclave_f0_min,
                            "below", "Autoclave F0", " min"))
    reg.register(_HardLimit("hospital.n2o_low", SIGNALS["n2o_pressure"], redlines.n2o_pressure_min,
                            "below", "N2O pressure", " kPa"))
    reg.register(_HardLimit("hospital.gas_reserve_low", SIGNALS["gas_reserve"], redlines.gas_reserve_min,
                            "below", "Medical gas reserve", "%", severity="warning"))
    reg.register(_HardLimit("hospital.infection_high", SIGNALS["infection_risk"], redlines.infection_risk_max,
                            "above", "Infection probability", "%"))
    reg.register(_HardLimit("hospital.ups_soc_low", SIGNALS["ups_soc"], redlines.ups_soc_min,
                            "below", "UPS state of charge", "%", severity="warning"))
    reg.register(_HardLimit("hospital.power_high", SIGNALS["power_load"], redlines.power_load_max,
                            "above", "Critical power load", "%"))
    reg.register(_HardLimit("hospital.dead_legs_high", SIGNALS["dead_legs"], redlines.dead_legs_max,
                            "above", "Legionella dead-legs", "", severity="warning"))
    reg.register(_HardLimit("hospital.ed_wait_high", SIGNALS["ed_wait"], redlines.ed_wait_max,
                            "above", "ED wait time", " min", severity="warning"))
    reg.register(_HardLimit("hospital.bed_occ_high", SIGNALS["bed_occ"], redlines.bed_occ_max,
                            "above", "Bed occupancy", "%", severity="warning"))
    return reg
