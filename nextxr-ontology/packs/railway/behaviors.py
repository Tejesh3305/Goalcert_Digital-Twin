"""
railway/behaviors.py — the 3-tier behaviour registry for the metro network.

Edge-latched, cross-signal-aware, same contract as the other domains. Includes
the five safety-critical named rules of the railway pack (P1-010 … P1-014):

  railway.overvoltage           third-rail > 900 V DC → P0, substation load shed
  railway.bogie_vibration       RMS accel > 7.1 mm/s (ISO 10816-3) → maintenance
  railway.door_cycle_failure    ≥3 consecutive failed PSD cycles → escalate
  railway.hvac_pressure_diff    platform HVAC ΔP < 10 Pa → filter block / fan fail
  railway.track_circuit_timeout occupancy not cleared within dwell+30 s → hold
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
    """Generic Tier-C threshold rule (edge-latched)."""
    tier = Tier.C

    def __init__(self, behavior_id, signal, limit, direction, label, unit,
                 severity="critical"):
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


# ── P1-010 · traction overvoltage (P0) ──
class RailOvervoltage(Behavior):
    """Tier-A: third-rail voltage above the 900 V DC ceiling — regenerative energy
    with no receptive load. P0 alert; recommend substation load-shed."""
    behavior_id = "railway.overvoltage"
    tier = Tier.A
    watches = [SIGNALS["third_rail_v"]]
    reads = ["third-rail voltage"]
    emits = "Traction overvoltage (P0)"

    def __init__(self, limit: float = None):
        self._limit = limit if limit is not None else redlines.third_rail_max
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        if not self._latch.rising(sample.entity_id, sample.value >= self._limit):
            return []
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
            severity="critical",
            message=f"P0 — traction OVERVOLTAGE {sample.value:.0f} V ≥ {self._limit:.0f} V DC. "
                    f"Regen energy not receptive; recommend substation load-shed / "
                    f"open the affected feeder.",
            confidence=1.0,
            evidence={"third_rail_v": sample.value, "limit": self._limit,
                      "priority": "P0", "action": "substation_load_shed",
                      "signal": sample.signal},
        )]


# ── P1-011 · bogie vibration anomaly (ISO 10816-3) ──
class BogieVibration(Behavior):
    """Tier-C: bogie RMS vibration above the ISO 10816-3 limit (7.1 mm/s) — wheel
    flat / bearing degradation. Raises a maintenance recommendation."""
    behavior_id = "railway.bogie_vibration"
    tier = Tier.C
    watches = [SIGNALS["bogie_vib"]]
    reads = ["bogie vibration RMS"]
    emits = "Bogie vibration anomaly"

    def __init__(self, limit: float = None):
        self._limit = limit if limit is not None else redlines.bogie_vib_max
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        if not self._latch.rising(sample.entity_id, sample.value >= self._limit):
            return []
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
            severity="warning",
            message=f"Bogie vibration {sample.value:.1f} mm/s ≥ {self._limit:.1f} mm/s "
                    f"(ISO 10816-3) — inspect wheelsets/bearings; schedule wheel reprofiling.",
            confidence=0.9,
            evidence={"vibration": sample.value, "limit": self._limit,
                      "standard": "ISO 10816-3", "action": "wheel_reprofiling",
                      "signal": sample.signal},
        )]


# ── P1-012 · PSD door-cycle failure escalation ──
class DoorCycleFailure(Behavior):
    """Tier-C: tracks failed platform-screen-door cycles and escalates to the
    Station Controller after 3 consecutive failures."""
    behavior_id = "railway.door_cycle_failure"
    tier = Tier.C
    watches = [SIGNALS["psd_faults"]]
    reads = ["consecutive PSD cycle failures"]
    emits = "PSD door-cycle failure escalation"

    def __init__(self, threshold: int = 3):
        self._threshold = threshold
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        failures = int(sample.value)
        # Escalate once the consecutive-failure count reaches the threshold.
        if not self._latch.rising(sample.entity_id, failures >= self._threshold):
            return []
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
            severity="critical",
            message=f"{failures} consecutive PSD door-cycle failures ≥ {self._threshold} — "
                    f"ESCALATED to Station Controller; isolate the affected door and hold berth.",
            confidence=0.95,
            evidence={"consecutive_failures": failures, "threshold": self._threshold,
                      "escalation": "station_controller", "signal": sample.signal},
        )]


# ── P1-013 · platform HVAC pressure differential ──
class HvacPressureDifferential(Behavior):
    """Tier-A: platform HVAC supply/return differential below 10 Pa — filter
    blockage or supply-fan failure."""
    behavior_id = "railway.hvac_pressure_diff"
    tier = Tier.A
    watches = [SIGNALS["hvac_dp"]]
    reads = ["platform HVAC supply/return differential"]
    emits = "HVAC differential collapse"

    def __init__(self, floor: float = None):
        self._floor = floor if floor is not None else redlines.hvac_dp_min
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        if not self._latch.rising(sample.entity_id, sample.value <= self._floor):
            return []
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
            severity="warning",
            message=f"Platform HVAC differential {sample.value:.1f} Pa ≤ {self._floor:.0f} Pa — "
                    f"filter blockage or supply-fan failure; inspect filter bank / fan.",
            confidence=0.85,
            evidence={"differential_pa": sample.value, "floor": self._floor,
                      "action": "inspect_filter_fan", "signal": sample.signal},
        )]


# ── P1-014 · track-circuit occupancy timeout ──
class TrackCircuitTimeout(Behavior):
    """Tier-C: a track circuit failed to clear within the expected dwell window
    (+30 s margin) — triggers a signalling safety hold. Reads the co-located dwell
    signal to state the expected clear window."""
    behavior_id = "railway.track_circuit_timeout"
    tier = Tier.C
    watches = [SIGNALS["track_circuit_faults"]]
    reads = ["track-circuit faults", "dwell time (sibling signal)"]
    emits = "Track-circuit occupancy timeout"

    def __init__(self, limit: float = None):
        self._limit = limit if limit is not None else 1.0
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        if not self._latch.rising(sample.entity_id, sample.value >= self._limit):
            return []
        dwell = query.get_property(sample.tenant_id, sample.entity_id, "dwellTime", 30.0)
        try:
            window = float(dwell) + 30.0
        except (TypeError, ValueError):
            window = 60.0
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
            severity="critical",
            message=f"Track-circuit failed to clear within dwell+30 s (~{window:.0f} s window) — "
                    f"signalling SAFETY HOLD applied; dispatch signalling technician.",
            confidence=0.95,
            evidence={"faults": int(sample.value), "clear_window_s": round(window, 1),
                      "action": "signal_safety_hold", "signal": sample.signal},
        )]


# ── Tier-A: third-rail undervoltage residual ──
class ThirdRailSag(Behavior):
    """Tier-A: third-rail voltage sagging well below its 750 V nominal — traction
    supply weakness (substation loading or feeder loss)."""
    behavior_id = "railway.thirdrail_sag"
    tier = Tier.A
    watches = [SIGNALS["third_rail_v"]]
    reads = ["third-rail voltage"]
    emits = "Third-rail voltage sag"

    def __init__(self, margin: float = 150.0, nominal: float = 750.0):
        self._margin, self._nominal = margin, nominal
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        residual = self._nominal - sample.value
        if not self._latch.rising(sample.entity_id, residual > self._margin):
            return []
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
            severity="warning",
            message=f"Third-rail voltage sagging {residual:.0f} V below nominal — "
                    f"traction supply weakness (check substation load / feeder).",
            confidence=0.8,
            evidence={"third_rail_v": sample.value, "nominal": self._nominal,
                      "residual": round(residual, 1), "signal": sample.signal},
        )]


# ── Tier-B: network delay trend ──
class DelayTrend(Behavior):
    """Tier-B: network delay drifting above its learned baseline."""
    behavior_id = "railway.delay_zscore"
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
            message=f"Network delay {sample.value:.1f} min is {detail.get('z', '?')}σ "
                    f"above baseline {detail.get('mean', '?')} min — service degrading.",
            confidence=0.8,
            evidence={"value": sample.value, "signal": sample.signal, **detail},
        )]


def build_railway_registry() -> BehaviorRegistry:
    reg = BehaviorRegistry()
    # Named safety-critical rules (P1-010 … P1-014)
    reg.register(RailOvervoltage())
    reg.register(BogieVibration())
    reg.register(DoorCycleFailure())
    reg.register(HvacPressureDifferential())
    reg.register(TrackCircuitTimeout())
    # Physics + trend
    reg.register(ThirdRailSag())
    reg.register(DelayTrend())
    # Hard limits for the remaining aggregate KPIs
    reg.register(_HardLimit("railway.otp_low", SIGNALS["otp"], redlines.otp_min,
                            "below", "On-time performance", "%", severity="warning"))
    reg.register(_HardLimit("railway.headway_low", SIGNALS["headway"], redlines.headway_min,
                            "below", "Headway adherence", "%", severity="warning"))
    reg.register(_HardLimit("railway.avail_low", SIGNALS["train_avail"], redlines.train_avail_min,
                            "below", "Train availability", "%", severity="warning"))
    reg.register(_HardLimit("railway.sub_high", SIGNALS["sub_load"], redlines.sub_load_max,
                            "above", "Substation load", "%"))
    reg.register(_HardLimit("railway.rail_hot", SIGNALS["rail_temp"], redlines.rail_temp_max,
                            "above", "Rail temperature", "°C", severity="warning"))
    reg.register(_HardLimit("railway.rail_buckle", SIGNALS["rail_stress"], redlines.rail_stress_max,
                            "above", "CWR rail stress", " MPa"))
    reg.register(_HardLimit("railway.derailment_qp", SIGNALS["wheel_qp"], redlines.wheel_qp_max,
                            "above", "Derailment quotient Q/P", ""))
    reg.register(_HardLimit("railway.motor_hot", SIGNALS["traction_temp"], redlines.traction_temp_max,
                            "above", "Traction motor temperature", "°C"))
    reg.register(_HardLimit("railway.escalator_hot", SIGNALS["escalator_temp"], redlines.escalator_temp_max,
                            "above", "Escalator motor temperature", "°C", severity="warning"))
    reg.register(_HardLimit("railway.los_crush", SIGNALS["station_los"], redlines.station_los_max,
                            "above", "Platform level of service", " LOS", severity="warning"))
    reg.register(_HardLimit("railway.delay_high", SIGNALS["delay"], redlines.delay_max,
                            "above", "Network delay", " min"))
    return reg
