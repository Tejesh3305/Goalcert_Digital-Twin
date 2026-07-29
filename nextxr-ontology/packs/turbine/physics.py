"""
turbine/physics.py — a gas-turbine engine forward model, authored from scratch.

Mirrors the interface the demo's engine.py expects from a domain physics module:

    SIGNALS   : short-key -> signal string (the TelemetrySample.signal keys)
    UNITS     : signal string -> unit label
    redlines  : hard limits used by diagnostics + prediction
    <Physics> : a class with
                  init_state()            -> a fresh state object
                  forward(state, dt)      -> {signal: value} frame, advances state
                  inject(state, fault, s) -> perturb the state with a named fault
                  residuals(frame)        -> {signal: residual} (Tier-A physics)
                  health_index(frame)     -> 0..1 overall condition

The model is deterministic in its trends (degradation accumulators) with a light
stochastic jitter so the live feed looks real. It is NOT a certified engine model
— it is a plausible, monotonic-degradation twin that makes the findings, health,
and RUL surfaces behave correctly for the demo.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from packs._core.physics import (
    clamp,
    first_order_lag,
    jitter,
    margin_hi,
    margin_lo,
    worst_health,
)

# ── Signal catalogue ────────────────────────────────────────────────
SIGNALS = {
    "egt":       "turbine:egt",         # exhaust gas temperature
    "n1":        "turbine:n1",          # fan / LP-shaft speed
    "n2":        "turbine:n2",          # core / HP-shaft speed
    "fuel":      "turbine:fuelFlow",
    "vib":       "turbine:vibration",
    "epr":       "turbine:epr",         # engine pressure ratio
    "oil_temp":  "turbine:oilTemp",
    "oil_press": "turbine:oilPressure",
}

UNITS = {
    SIGNALS["egt"]: "DEG_C",
    SIGNALS["n1"]: "RPM",
    SIGNALS["n2"]: "RPM",
    SIGNALS["fuel"]: "KG-PER-HR",
    SIGNALS["vib"]: "G",
    SIGNALS["epr"]: "",
    SIGNALS["oil_temp"]: "DEG_C",
    SIGNALS["oil_press"]: "PSI",
}


@dataclass
class Redlines:
    egt: float = 950.0           # C — over-temp
    vib: float = 1.15            # g — excessive vibration
    oil_temp: float = 140.0      # C — oil over-temp
    oil_press_min: float = 25.0  # PSI — loss of oil pressure
    n1_max: float = 5200.0       # RPM
    n2_max: float = 15200.0      # RPM


redlines = Redlines()

# Reference (healthy, full-power) design points the model relaxes toward.
_N1_100 = 5000.0
_N2_100 = 14600.0
_EGT_AMBIENT = 15.0
_EGT_SPAN = 820.0     # ΔT from idle→full at health
_FUEL_100 = 2600.0    # kg/h at full power
_EPR_100 = 1.62

# Thermal time constants (s): EGT responds within tens of seconds to a fuel/power
# change; the oil system is a larger thermal mass and lags further behind.
_TAU_EGT = 22.0
_TAU_OIL = 90.0


@dataclass
class TurbineState:
    throttle: float = 0.62               # control input, 0..1
    # Degradation accumulators — 0 = pristine, grow monotonically with wear/fault.
    compressor_fouling: float = 0.05     # depresses EPR, raises EGT & fuel
    bearing_wear: float = 0.04           # raises vibration & oil temp
    oil_leak: float = 0.0                # bleeds oil pressure
    combustor_distress: float = 0.03     # raises EGT hot-streak
    # Thermal states carried between ticks so they can lag (0 = uninitialised;
    # the first forward() snaps them to their steady value).
    egt: float = 0.0
    oil_temp: float = 0.0
    hours: float = 0.0                   # accumulated running hours (sim)
    fault: str = "none"
    fault_severity: float = 0.0
    seed: int = 7

    _rng: random.Random = field(default=None, repr=False, compare=False)

    def rng(self) -> random.Random:
        if self._rng is None:
            self._rng = random.Random(self.seed)
        return self._rng


# Named faults and how each nudges the degradation accumulators / control.
_FAULTS = {
    "compressor_fouling": {"compressor_fouling": 0.55},
    "bearing_wear":       {"bearing_wear": 0.55},
    "oil_leak":           {"oil_leak": 0.6},
    "hot_section":        {"combustor_distress": 0.6},
    "combustor_distress": {"combustor_distress": 0.6},
    "overspeed":          {"_throttle": 0.35},
    "fod":                {"bearing_wear": 0.3, "compressor_fouling": 0.35},
}

FAULTS = list(_FAULTS.keys())


class TurbinePhysics:
    """Gas-turbine forward model. One instance per live twin."""

    def __init__(self, **options):
        self.opts = options or {}

    # ── lifecycle ──
    def init_state(self) -> TurbineState:
        return TurbineState()

    # ── fault injection (used by simulate + non-destructive project) ──
    def inject(self, state: TurbineState, fault: str, severity: float = 0.85) -> None:
        eff = max(0.0, min(1.0, float(severity)))
        state.fault = fault
        state.fault_severity = eff
        for attr, amount in _FAULTS.get(fault, {}).items():
            if attr == "_throttle":
                state.throttle = min(1.05, state.throttle + amount * eff)
            else:
                setattr(state, attr, min(1.5, getattr(state, attr) + amount * eff))

    # ── one integration step ──
    def forward(self, state: TurbineState, dt: float = 1.0) -> dict:
        rng = state.rng()
        thr = clamp(state.throttle, 0.0, 1.05)
        state.hours += dt / 3600.0

        # Stress- and fault-driven wear. forward() and predict() call the SAME
        # law, so the projected RUL trajectory matches the live twin's.
        self._degrade(state, dt)

        foul = state.compressor_fouling
        wear = state.bearing_wear
        comb = state.combustor_distress

        # Shaft speeds track throttle; fouling makes the core work harder (N2↑).
        n1 = _N1_100 * (0.32 + 0.68 * thr) * (1.0 - 0.01 * foul)
        n2 = _N2_100 * (0.55 + 0.45 * thr) * (1.0 + 0.03 * foul)

        # EGT and oil temperature are THERMAL states with real inertia: they lag
        # their steady value with a time constant instead of jumping with throttle.
        # Steady EGT rises with power, fouling (poor compression) and combustor
        # distress; steady oil temp with load and bearing friction.
        egt_ss = (_EGT_AMBIENT + _EGT_SPAN * (0.25 + 0.75 * thr)
                  + 140.0 * foul + 180.0 * comb)
        oil_ss = 78.0 + 42.0 * thr + 55.0 * wear
        state.egt = egt_ss if state.egt <= 0.0 else first_order_lag(state.egt, egt_ss, _TAU_EGT, dt)
        state.oil_temp = oil_ss if state.oil_temp <= 0.0 else first_order_lag(state.oil_temp, oil_ss, _TAU_OIL, dt)
        egt = state.egt
        oil_temp = state.oil_temp

        # Fuel flow ~ power, penalised by fouling (lower efficiency).
        fuel = _FUEL_100 * (0.18 + 0.82 * thr) * (1.0 + 0.12 * foul)

        # EPR falls as the compressor fouls.
        epr = _EPR_100 * (0.55 + 0.45 * thr) * (1.0 - 0.16 * foul)

        # Vibration grows sharply with bearing wear (and a touch with speed).
        vib = 0.16 + 0.10 * thr + 0.95 * wear * wear + 0.05 * comb

        # Oil pressure bleeds with leaks.
        oil_press = 62.0 * (0.4 + 0.6 * thr) - 40.0 * state.oil_leak

        frame = {
            SIGNALS["egt"]:       round(jitter(rng, egt, 0.006), 1),
            SIGNALS["n1"]:        round(jitter(rng, n1, 0.004), 0),
            SIGNALS["n2"]:        round(jitter(rng, n2, 0.004), 0),
            SIGNALS["fuel"]:      round(jitter(rng, fuel, 0.01), 1),
            SIGNALS["vib"]:       round(max(0.0, jitter(rng, vib, 0.03)), 3),
            SIGNALS["epr"]:       round(jitter(rng, epr, 0.005), 3),
            SIGNALS["oil_temp"]:  round(jitter(rng, oil_temp, 0.006), 1),
            SIGNALS["oil_press"]: round(max(0.0, jitter(rng, oil_press, 0.01)), 1),
        }
        return frame

    def _degrade(self, state: TurbineState, dt: float) -> None:
        """Advance the degradation accumulators. Base wear is slow — an untouched
        engine barely ages within a demo — while an active fault multiplies the
        rate so a projection reaches a redline within the forecast horizon. Called
        from forward() every tick, so the live twin and predict() age on the SAME
        curve and the RUL trajectory reflects what the twin will actually do."""
        thr = clamp(state.throttle, 0.0, 1.05)
        sev = state.fault_severity if state.fault != "none" else 0.0
        load = 0.5 + thr
        mult = 1.0 + 8.0 * sev
        state.bearing_wear = min(1.6, state.bearing_wear
                                 + dt * load * mult * (2.0e-6 + 1.2e-5 * state.bearing_wear))
        state.compressor_fouling = min(1.6, state.compressor_fouling
                                       + dt * mult * (1.5e-6 + 6.0e-6 * state.compressor_fouling))
        state.combustor_distress = min(1.6, state.combustor_distress
                                       + dt * mult * (1.0e-6 + 5.0e-6 * state.combustor_distress))
        state.oil_leak = min(1.2, state.oil_leak
                             + dt * mult * (5.0e-7 + 8.0e-6 * state.oil_leak))

    # ── Tier-A physics residuals (expected vs modelled ideal) ──
    def residuals(self, frame: dict) -> dict:
        """Deviation of each signal from its clean-engine expectation at the
        implied throttle. Large residuals are what a Tier-A behaviour flags."""
        egt = frame.get(SIGNALS["egt"], 0.0)
        epr = frame.get(SIGNALS["epr"], _EPR_100)
        # Infer throttle from EPR (clean relation), then predict a clean EGT.
        thr = max(0.0, min(1.05, (epr / _EPR_100 - 0.55) / 0.45)) if epr else 0.6
        egt_clean = _EGT_AMBIENT + _EGT_SPAN * (0.25 + 0.75 * thr)
        return {
            SIGNALS["egt"]: egt - egt_clean,
            SIGNALS["vib"]: frame.get(SIGNALS["vib"], 0.0) - (0.16 + 0.10 * thr),
            SIGNALS["oil_press"]: frame.get(SIGNALS["oil_press"], 62.0)
                                  - 62.0 * (0.4 + 0.6 * thr),
        }

    # ── overall health 0..1 (1 = perfect) ──
    def health_index(self, frame: dict) -> float:
        """Worst subsystem margin. Each signal maps to 1.0 at its nominal
        operating point and 0.0 at its redline, so a healthy engine sits near
        1.0 and only collapses as a signal approaches its limit."""
        if not frame:
            return 1.0
        return round(worst_health([
            margin_hi(frame.get(SIGNALS["egt"], 0.0), 660.0, redlines.egt),
            margin_hi(frame.get(SIGNALS["vib"], 0.0), 0.28, redlines.vib),
            margin_hi(frame.get(SIGNALS["oil_temp"], 0.0), 112.0, redlines.oil_temp),
            margin_lo(frame.get(SIGNALS["oil_press"], 48.0), 48.0, redlines.oil_press_min),
        ]), 3)
