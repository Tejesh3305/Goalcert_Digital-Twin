"""
edm/physics.py — a wire-EDM (electrical discharge machining) forward model.

Same interface contract as turbine/physics.py. Wire EDM cuts by controlled sparks
across a dielectric-flushed gap between a moving wire electrode and the workpiece.
The twin models four subsystems — discharge generator, dielectric & flushing, wire
transport, guides & axes — through 18 signals, degraded by five wear/contamination
accumulators (filter clog, resin depletion, guide wear, chiller loss, debris).

Authored from scratch; not a machine-tool-grade model, but a coherent,
monotonic-degradation twin whose findings / health / RUL behave correctly.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

SIGNALS = {
    "gap_v":        "edm:gapVoltage",
    "peak_i":       "edm:peakCurrent",
    "ton":          "edm:pulseOnTime",
    "toff":         "edm:pulseOffTime",
    "spark_freq":   "edm:sparkFrequency",
    "energy":       "edm:dischargeEnergy",
    "wire_tension": "edm:wireTension",
    "wire_feed":    "edm:wireFeedRate",
    "wire_wear":    "edm:wireWear",
    "cut_speed":    "edm:cuttingSpeed",
    "die_flow":     "edm:dielectricFlow",
    "die_press":    "edm:dielectricPressure",
    "die_temp":     "edm:dielectricTemp",
    "die_cond":     "edm:dielectricConductivity",
    "short_rate":   "edm:shortCircuitRate",
    "spark_gap":    "edm:sparkGap",
    "ra":           "edm:surfaceFinishRa",
    "break_risk":   "edm:wireBreakRisk",
}

UNITS = {
    SIGNALS["gap_v"]: "V", SIGNALS["peak_i"]: "A", SIGNALS["ton"]: "us",
    SIGNALS["toff"]: "us", SIGNALS["spark_freq"]: "kHz", SIGNALS["energy"]: "mJ",
    SIGNALS["wire_tension"]: "N", SIGNALS["wire_feed"]: "m/min",
    SIGNALS["wire_wear"]: "%", SIGNALS["cut_speed"]: "mm2/min",
    SIGNALS["die_flow"]: "L/min", SIGNALS["die_press"]: "bar",
    SIGNALS["die_temp"]: "DEG_C", SIGNALS["die_cond"]: "uS/cm",
    SIGNALS["short_rate"]: "%", SIGNALS["spark_gap"]: "um",
    SIGNALS["ra"]: "um", SIGNALS["break_risk"]: "%",
}


@dataclass
class Redlines:
    short_rate: float = 25.0       # % — instability
    break_risk: float = 60.0       # % — imminent wire break
    die_temp: float = 32.0         # C — dielectric too warm
    die_cond: float = 25.0         # uS/cm — deionisation exhausted
    die_press_min: float = 4.0     # bar — flushing too weak
    wire_tension_min: float = 8.0  # N — slack wire
    gap_v_min: float = 30.0        # V — collapsing gap (shorting)


redlines = Redlines()


@dataclass
class EDMState:
    intensity: float = 0.55        # control input, 0..1 (discharge intensity)
    filter_clog: float = 0.05      # 0..1 — flushing filter clog
    resin_depletion: float = 0.05  # 0..1 — deioniser resin exhaustion
    guide_wear: float = 0.04       # 0..1 — diamond guide wear
    chiller_health: float = 1.0    # 1..0 — dielectric chiller condition
    debris: float = 0.06           # 0..1 — swarf/debris in the gap
    wire_wear: float = 6.0         # % — spooled-wire wear indicator
    hours: float = 0.0
    fault: str = "none"
    fault_severity: float = 0.0
    seed: int = 11
    _rng: random.Random = field(default=None, repr=False, compare=False)

    def rng(self) -> random.Random:
        if self._rng is None:
            self._rng = random.Random(self.seed)
        return self._rng


_FAULTS = {
    "filter_clog":     {"filter_clog": 0.6},
    "resin_depletion": {"resin_depletion": 0.65},
    "guide_wear":      {"guide_wear": 0.6},
    "chiller_fault":   {"chiller_health": -0.6},
    "debris":          {"debris": 0.6},
    "flushing_loss":   {"filter_clog": 0.4, "debris": 0.3},
}

FAULTS = list(_FAULTS.keys())


class EDMPhysics:
    def __init__(self, **options):
        self.opts = options or {}

    def init_state(self) -> EDMState:
        return EDMState()

    def inject(self, state: EDMState, fault: str, severity: float = 0.85) -> None:
        eff = max(0.0, min(1.0, float(severity)))
        state.fault = fault
        state.fault_severity = eff
        for attr, amount in _FAULTS.get(fault, {}).items():
            cur = getattr(state, attr)
            if attr == "chiller_health":
                setattr(state, attr, max(0.0, cur + amount * eff))   # amount < 0
            else:
                setattr(state, attr, min(1.5, cur + amount * eff))

    def forward(self, state: EDMState, dt: float = 1.0) -> dict:
        rng = state.rng()
        I = max(0.0, min(1.0, state.intensity))
        state.hours += dt / 3600.0

        # Wire wears with use + worn guides; guides creep with intensity.
        state.wire_wear = min(100.0, state.wire_wear + dt * (0.0016 + 0.004 * I + 0.02 * state.guide_wear))
        state.guide_wear = min(1.5, state.guide_wear + dt * 1.2e-6 * (0.5 + I))

        clog, resin, gw, deb = state.filter_clog, state.resin_depletion, state.guide_wear, state.debris
        chill = state.chiller_health

        # Dielectric & flushing.
        die_flow = 18.0 * (1.0 - 0.6 * clog)
        die_press = 6.2 * (1.0 - 0.52 * clog)
        die_temp = 22.0 + 8.0 * I + 12.0 * (1.0 - chill) + 6.0 * clog
        die_cond = 8.0 + 20.0 * resin + 6.0 * deb

        # Gap instability: shorts rise with debris, weak flushing, high intensity.
        short_rate = 2.5 + 30.0 * deb + 12.0 * clog + 10.0 * max(0.0, I - 0.7)
        gap_v = 58.0 - 26.0 * deb - 0.5 * short_rate      # collapses as it shorts

        # Discharge generator.
        peak_i = 8.0 + 34.0 * I
        ton = 2.0 + 10.0 * I
        toff = max(2.0, 12.0 - 6.0 * I)
        spark_freq = max(5.0, 45.0 + 60.0 * I - 0.9 * short_rate)
        energy = 0.5 * peak_i * ton * 0.12

        # Wire transport & guides.
        wire_tension = 12.5 - 3.2 * gw - 0.02 * short_rate
        wire_feed = 6.0 + 6.0 * I
        break_risk = 4.0 + 0.45 * state.wire_wear + 32.0 * gw + 0.5 * short_rate

        # Process outputs.
        cut_speed = (10.0 + 42.0 * I) * (1.0 - 0.012 * short_rate) * (1.0 - 0.3 * clog)
        spark_gap = 24.0 + 14.0 * I - 6.0 * deb
        ra = 0.8 + 1.4 * I + 0.04 * short_rate

        def j(v, frac):
            return v * (1.0 + rng.uniform(-frac, frac))

        return {
            SIGNALS["gap_v"]:        round(max(0.0, j(gap_v, 0.01)), 1),
            SIGNALS["peak_i"]:       round(j(peak_i, 0.01), 1),
            SIGNALS["ton"]:          round(j(ton, 0.02), 2),
            SIGNALS["toff"]:         round(j(toff, 0.02), 2),
            SIGNALS["spark_freq"]:   round(j(spark_freq, 0.02), 1),
            SIGNALS["energy"]:       round(j(energy, 0.02), 2),
            SIGNALS["wire_tension"]: round(max(0.0, j(wire_tension, 0.01)), 2),
            SIGNALS["wire_feed"]:    round(j(wire_feed, 0.01), 2),
            SIGNALS["wire_wear"]:    round(state.wire_wear, 1),
            SIGNALS["cut_speed"]:    round(max(0.0, j(cut_speed, 0.02)), 1),
            SIGNALS["die_flow"]:     round(max(0.0, j(die_flow, 0.02)), 1),
            SIGNALS["die_press"]:    round(max(0.0, j(die_press, 0.02)), 2),
            SIGNALS["die_temp"]:     round(j(die_temp, 0.01), 1),
            SIGNALS["die_cond"]:     round(j(die_cond, 0.02), 1),
            SIGNALS["short_rate"]:   round(max(0.0, j(short_rate, 0.05)), 1),
            SIGNALS["spark_gap"]:    round(max(0.0, j(spark_gap, 0.02)), 1),
            SIGNALS["ra"]:           round(max(0.0, j(ra, 0.02)), 2),
            SIGNALS["break_risk"]:   round(max(0.0, min(100.0, j(break_risk, 0.03))), 1),
        }

    def residuals(self, frame: dict) -> dict:
        """Tier-A residuals: gap voltage below the clean value for its intensity
        (inferred from peak current) signals gap instability / shorting."""
        peak_i = frame.get(SIGNALS["peak_i"], 8.0)
        I = max(0.0, min(1.0, (peak_i - 8.0) / 34.0))
        gap_clean = 58.0
        return {
            SIGNALS["gap_v"]: frame.get(SIGNALS["gap_v"], gap_clean) - gap_clean,
            SIGNALS["die_press"]: frame.get(SIGNALS["die_press"], 6.2) - 6.2,
        }

    def health_index(self, frame: dict) -> float:
        if not frame:
            return 1.0

        def hi(v, nominal, limit):
            return max(0.0, min(1.0, (limit - v) / (limit - nominal)))

        def lo(v, nominal, limit):
            return max(0.0, min(1.0, (v - limit) / (nominal - limit)))

        margins = [
            hi(frame.get(SIGNALS["short_rate"], 0.0), 4.0, redlines.short_rate),
            hi(frame.get(SIGNALS["break_risk"], 0.0), 8.0, redlines.break_risk),
            hi(frame.get(SIGNALS["die_temp"], 0.0), 28.0, redlines.die_temp),
            hi(frame.get(SIGNALS["die_cond"], 0.0), 10.0, redlines.die_cond),
            lo(frame.get(SIGNALS["die_press"], 6.0), 6.0, redlines.die_press_min),
            lo(frame.get(SIGNALS["wire_tension"], 12.0), 12.0, redlines.wire_tension_min),
            lo(frame.get(SIGNALS["gap_v"], 55.0), 55.0, redlines.gap_v_min),
        ]
        return round(min(margins), 3)
