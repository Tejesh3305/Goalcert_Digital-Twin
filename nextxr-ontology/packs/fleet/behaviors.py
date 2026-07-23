"""
behaviors.fleet — the behaviour rules that watch a tram / fleet-network twin.

Three-tier stack, same contract as the rest of the platform:

  * Tier A (physics)  — NetworkFlowResidual: achieved commercial speed vs what
                        the timetable physics predicts from demand/signalling —
                        the earliest hidden-congestion / blockage signal.
                        TractionEnergyResidual: measured traction draw vs the
                        physics prediction — catches OHL leakage / arcing and
                        regen loss before voltage or substations trip.
  * Tier C (rules)    — hard-limit monitors: OTP, headway adherence, OHL
                        undervoltage, substation overload, rail temperature
                        (buckling), bogie vibration (wheel flats), brake and
                        pantograph wear, door/signal fault counts, fleet
                        availability, accumulated network delay.

`build_fleet_registry()` returns exactly these behaviours, used by both the
live engine and the forward predictor.
"""
from __future__ import annotations

from behaviors.registry import Behavior, Tier, TelemetrySample, Finding, BehaviorRegistry
from .physics import FleetPhysics, SIGNALS, redlines


# ── Tier A: physics residuals ─────────────────────────────────────────

class NetworkFlowResidual(Behavior):
    """Predicts the commercial network speed from demand, signalling health and
    rail temperature, and fires when the achieved speed falls well short —
    i.e. the fleet is out there but the network isn't flowing. That gap means
    something physical (blockage, TSRs, junction failures, street congestion)
    before any single channel trips a hard limit."""
    behavior_id = "fleet.network_flow_residual"
    tier = Tier.A
    watches = ["fleet:networkSpeed"]
    reads = ["passenger load, signal faults, rail temperature from the same "
             "entity (via graph query)"]
    emits = "A warning Finding when the speed shortfall exceeds the threshold."

    def __init__(self, shortfall_pct: float = 0.25):
        self.shortfall = shortfall_pct
        self.phys = FleetPhysics()
        self._firing: dict[str, bool] = {}

    def evaluate(self, sample: TelemetrySample, query) -> list[Finding]:
        ent = sample.entity_id
        d = self.phys.d
        try:
            node = query.get_node(sample.tenant_id, ent) or {}
        except Exception:
            node = {}

        def gp(local, dflt):
            try:
                return float(node.get(local, dflt))
            except Exception:
                return dflt

        demand = gp("passengerLoad", d.demand * 100.0) / 100.0 * 0.9
        sig_health = max(0.0, 1.0 - gp("signalFaults", 0.0) / 12.0)
        track_temp = gp("railTemperature", d.ambient + 9.0)
        expected = self.phys.expected_speed(demand, sig_health, track_temp, 0.15)

        if expected <= 1e-6:
            return []
        shortfall = (expected - sample.value) / expected
        if shortfall <= self.shortfall:
            self._firing[ent] = False
            return []
        if self._firing.get(ent):
            return []
        self._firing[ent] = True

        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=ent,
            severity="warning",
            message=(f"Network flow shortfall — {sample.value:.1f} km/h achieved vs "
                     f"{expected:.1f} km/h predicted ({shortfall*100:.0f}% below). "
                     f"The fleet is in service but the network isn't flowing: check "
                     f"for line blockages, junction/points failures, and heat speed "
                     f"restrictions before headways collapse."),
            confidence=min(1.0, shortfall / (self.shortfall * 2)),
            evidence={"achieved_kmh": sample.value, "predicted_kmh": round(expected, 1),
                      "shortfall_pct": round(shortfall * 100, 1),
                      "signal": sample.signal})]


class TractionEnergyResidual(Behavior):
    """Predicts network traction draw from trams in service, speed, HVAC and
    regen share; fires when measured power runs well above it — energy is being
    burned that isn't producing service (OHL arcing, resistive losses, dragging
    brakes) before the electrical protection acts."""
    behavior_id = "fleet.traction_energy_residual"
    tier = Tier.A
    watches = ["fleet:tractionPower"]
    reads = ["trams in service, network speed, HVAC load, regen share from the "
             "same entity (via graph query)"]
    emits = "A warning Finding when traction draw exceeds prediction by the threshold."

    def __init__(self, excess_pct: float = 0.20):
        self.excess = excess_pct
        self.phys = FleetPhysics()
        self._firing: dict[str, bool] = {}

    def evaluate(self, sample: TelemetrySample, query) -> list[Finding]:
        ent = sample.entity_id
        d = self.phys.d
        try:
            node = query.get_node(sample.tenant_id, ent) or {}
        except Exception:
            node = {}

        def gp(local, dflt):
            try:
                return float(node.get(local, dflt))
            except Exception:
                return dflt

        in_service = int(gp("tramsInService", 0))
        if in_service <= 0:
            return []
        expected = self.phys.expected_power_mw(
            in_service, gp("networkSpeed", d.base_speed),
            gp("hvacLoad", 40.0), gp("regenShare", d.regen))
        if expected <= 1e-6:
            return []
        excess = (sample.value - expected) / expected
        if excess <= self.excess:
            self._firing[ent] = False
            return []
        if self._firing.get(ent):
            return []
        self._firing[ent] = True

        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=ent,
            severity="warning",
            message=(f"Traction energy anomaly — drawing {sample.value:.2f} MW vs "
                     f"{expected:.2f} MW predicted ({excess*100:.0f}% above). Power "
                     f"is being burned without producing service: check overhead-line "
                     f"contact/arcing, dragging brakes and regen availability before "
                     f"the substations trip."),
            confidence=min(1.0, excess / (self.excess * 2)),
            evidence={"measured_mw": sample.value, "predicted_mw": round(expected, 2),
                      "excess_pct": round(excess * 100, 1), "signal": sample.signal})]


# ── Tier C: hard-limit threshold monitors ────────────────────────────

class _Threshold(Behavior):
    """Shared one-shot threshold machinery (fires once on crossing, clears when
    the signal returns in-band)."""
    tier = Tier.C

    def __init__(self, limit: float):
        self.limit = limit
        self._state: dict[str, dict] = {}

    def _over(self, value: float) -> bool:
        raise NotImplementedError

    def _finding(self, sample: TelemetrySample) -> Finding:
        raise NotImplementedError

    def evaluate(self, sample: TelemetrySample, query) -> list[Finding]:
        st = self._state.setdefault(sample.entity_id, {"fired": False})
        if not self._over(sample.value):
            st["fired"] = False
            return []
        if st["fired"]:
            return []
        st["fired"] = True
        return [self._finding(sample)]


def _mk_rule(bid, sig_key, direction, default_limit, severity, unit, text):
    """Factory for the Tier-C monitors — one line each below instead of ten
    near-identical classes."""

    class _Rule(_Threshold):
        behavior_id = bid
        watches = [SIGNALS[sig_key]]
        reads = [f"{SIGNALS[sig_key]} in {unit}"]
        emits = f"A {severity} Finding when the value goes {direction} the limit."

        def __init__(self, limit: float = None):
            super().__init__(limit if limit is not None else default_limit())

        def _over(self, value):
            return value > self.limit if direction == "above" else value < self.limit

        def _finding(self, sample):
            return Finding(
                behavior_id=bid, tier=Tier.C, flags=sample.entity_id,
                severity=severity,
                message=text.format(v=sample.value, lim=self.limit),
                confidence=1.0,
                evidence={"value": sample.value, "unit": unit,
                          "limit": self.limit, "signal": sample.signal})

    _Rule.__name__ = bid.replace(".", "_")
    return _Rule


OTPLowRule = _mk_rule(
    "fleet.otp_low", "otp", "below", lambda: redlines.otp_min, "critical", "%",
    "On-time performance collapsed — {v:.0f}% (minimum {lim:.0f}%). The service "
    "is no longer deliverable to timetable: hold-and-space via control, deploy "
    "spare cars, and clear the underlying blockage or junction failures.")

HeadwayLowRule = _mk_rule(
    "fleet.headway_low", "headway", "below", lambda: redlines.headway_min,
    "warning", "%",
    "Headway adherence low — {v:.0f}% (minimum {lim:.0f}%). Trams are bunching: "
    "gaps double passenger waits and loads. Regulate at timing points and check "
    "for slow zones and door-fault dwell blowouts.")

OHLVoltageLowRule = _mk_rule(
    "fleet.ohl_undervoltage", "ohl_v", "below", lambda: redlines.ohl_v_min,
    "critical", "V",
    "Overhead-line undervoltage — {v:.0f} V (minimum {lim:.0f} V). Traction "
    "packages will start dropping out on the affected sections. Check substation "
    "output, section feeds, and pantograph/OHL arcing losses.")

SubstationOverloadRule = _mk_rule(
    "fleet.substation_overload", "sub_load", "above", lambda: redlines.sub_load_max,
    "critical", "%",
    "Substation overload — {v:.0f}% of capacity (limit {lim:.0f}%). A protection "
    "trip would de-energise whole sections mid-service. Shed HVAC load, thin the "
    "service through the affected feed, and restore any derated rectifier.")

TrackTempHighRule = _mk_rule(
    "fleet.rail_temp_high", "track_temp", "above", lambda: redlines.track_temp_max,
    "critical", "C",
    "Rail temperature critical — {v:.1f} °C (limit {lim:.1f} °C). Buckling risk: "
    "impose heat speed restrictions on exposed track now and inspect known "
    "stress-prone curves before returning to line speed.")

VibrationHighRule = _mk_rule(
    "fleet.bogie_vibration_high", "vib", "above", lambda: redlines.vib_max,
    "warning", "g",
    "Bogie vibration high — {v:.2f} g (limit {lim:.2f} g). Wheel flats or track "
    "defects are hammering both the fleet and the rail. Pull the worst cars for "
    "wheel truing and schedule track-geometry inspection on the worst corridor.")

BrakeWearHighRule = _mk_rule(
    "fleet.brake_wear_high", "brake_wear", "above", lambda: redlines.brake_wear_max,
    "warning", "%",
    "Fleet brake wear high — {v:.0f}% average (limit {lim:.0f}%). Stopping "
    "margins and regen recovery both degrade; cars will start failing brake "
    "tests. Rotate the worst vehicles through pad replacement now.")

PantographWearHighRule = _mk_rule(
    "fleet.pantograph_wear_high", "panto_wear", "above", lambda: redlines.panto_wear_max,
    "warning", "%",
    "Pantograph wear high — {v:.0f}% average (limit {lim:.0f}%). Worn carbons "
    "arc and can de-wire, damaging kilometres of overhead. Replace strips at "
    "depot and inspect the OHL contact wire on the heaviest corridors.")

DoorFaultsHighRule = _mk_rule(
    "fleet.door_faults_high", "door_faults", "above", lambda: redlines.door_faults_max,
    "warning", "count",
    "Door faults high — {v:.0f} vehicles affected (limit {lim:.0f}). Dwell times "
    "blow out and cars get pulled from service. Prioritise door-system "
    "maintenance; check obstacle-detection sensors and door controllers.")

SignalFaultsHighRule = _mk_rule(
    "fleet.signal_faults_high", "signal_faults", "above",
    lambda: redlines.signal_faults_max, "critical", "count",
    "Signal faults high — {v:.0f} active (limit {lim:.0f}). Junctions fall back "
    "to manual working and throughput collapses. Dispatch signalling techs to "
    "the affected interlockings and regulate service around them.")

FleetAvailabilityLowRule = _mk_rule(
    "fleet.availability_low", "fleet_avail", "below",
    lambda: redlines.fleet_avail_min, "critical", "%",
    "Fleet availability low — {v:.0f}% (minimum {lim:.0f}%). Not enough "
    "serviceable trams to cover the timetable; gaps are now structural. Expedite "
    "depot turnarounds and cut the service level cleanly rather than randomly.")

NetworkDelayHighRule = _mk_rule(
    "fleet.network_delay_high", "delay", "above", lambda: redlines.delay_max,
    "warning", "min",
    "Accumulated network delay high — {v:.0f} min (limit {lim:.0f} min). "
    "Disruption is compounding faster than recovery. Consider short-working "
    "and stepping back crews to reset the timetable.")


def build_fleet_registry() -> BehaviorRegistry:
    """A registry of exactly the behaviours that watch fleet-network signals."""
    r = BehaviorRegistry()
    for b in (NetworkFlowResidual(), TractionEnergyResidual(),
              OTPLowRule(), HeadwayLowRule(), OHLVoltageLowRule(),
              SubstationOverloadRule(), TrackTempHighRule(), VibrationHighRule(),
              BrakeWearHighRule(), PantographWearHighRule(), DoorFaultsHighRule(),
              SignalFaultsHighRule(), FleetAvailabilityLowRule(),
              NetworkDelayHighRule()):
        try:
            r.register(b)
        except Exception:  # noqa: BLE001 — duplicate id, skip
            pass
    return r


__all__ = [
    "NetworkFlowResidual", "TractionEnergyResidual", "OTPLowRule",
    "HeadwayLowRule", "OHLVoltageLowRule", "SubstationOverloadRule",
    "TrackTempHighRule", "VibrationHighRule", "BrakeWearHighRule",
    "PantographWearHighRule", "DoorFaultsHighRule", "SignalFaultsHighRule",
    "FleetAvailabilityLowRule", "NetworkDelayHighRule", "build_fleet_registry",
]
