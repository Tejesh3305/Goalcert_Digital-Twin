"""
railway/trainset.py — a rolling-stock (train-set) digital-twin domain.

Where RailwayPhysics twins the whole network, this twins ONE train set at the
vehicle level: traction, running gear (bogies/wheelsets), braking, doors,
auxiliaries and saloon HVAC. It reuses the component engines train_dynamics()
and wheel_rail_contact() from .physics so the vehicle physics is the same
first-principles model the network aggregates.

Same interface contract as the other machine domains (no network_state).
"""
from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field

from behaviors.registry import Behavior, BehaviorRegistry, Finding, Tier
from packs._core.physics import (
    clamp, jitter, first_order_lag, margin_hi, margin_lo, worst_health,
    status_from_health,
)
from .physics import (
    train_dynamics, wheel_rail_contact, SIGNALS as NET_SIGNALS,
)

# Thermal time constants (s): the traction motor is a large thermal mass; the
# brake discs a smaller one — both ramp toward their steady value, never snap.
_TAU_MOTOR = 180.0
_TAU_BRAKE = 90.0

SIGNALS = {
    "speed":         "rail:trainSpeed",
    "tractive":      "rail:tractiveEffort",
    "line_current":  "rail:lineCurrent",
    "motor_current": "rail:motorCurrent",
    "motor_temp":    "rail:tractionMotorTemp",
    "bogie_vib":     "rail:bogieVibration",
    "wheel_qp":      "rail:derailmentQuotient",
    "wheel_flat":    "rail:wheelFlat",
    "brake_temp":    "rail:brakeTemperature",
    "brake_wear":    "rail:brakeWear",
    "door_faults":   "rail:doorFaults",
    "car_hvac":      "rail:carHvacTemp",
    "aux_v":         "rail:auxVoltage",
    "regen":         "rail:regenShare",
}

UNITS = {
    SIGNALS["speed"]: "km/h", SIGNALS["tractive"]: "kN", SIGNALS["line_current"]: "A",
    SIGNALS["motor_current"]: "A", SIGNALS["motor_temp"]: "DEG_C", SIGNALS["bogie_vib"]: "mm/s",
    SIGNALS["wheel_qp"]: "", SIGNALS["wheel_flat"]: "mm", SIGNALS["brake_temp"]: "DEG_C",
    SIGNALS["brake_wear"]: "%", SIGNALS["door_faults"]: "", SIGNALS["car_hvac"]: "DEG_C",
    SIGNALS["aux_v"]: "V", SIGNALS["regen"]: "%",
}


@dataclass
class Redlines:
    motor_temp_max: float = 155.0
    bogie_vib_max: float = 7.1
    wheel_qp_max: float = 0.8
    wheel_flat_max: float = 3.0
    brake_temp_max: float = 350.0
    brake_wear_max: float = 85.0
    door_faults_max: float = 3.0
    car_hvac_max: float = 28.0
    aux_v_min: float = 90.0
    line_current_max: float = 3500.0


redlines = Redlines()


@dataclass
class TrainsetState:
    throttle: float = 0.55           # control 0..1 (power/brake notch demand)
    mass_t: float = 210.0
    brake_wear: float = 12.0
    wheel_flat: float = 0.0
    heat: float = 0.2
    door_faults: float = 0.0
    motor_derate: float = 0.0        # 0..1 cooling loss
    earth_fault: float = 0.0         # 0..1 insulation/earth fault → overcurrent
    hvac_fault: float = 0.0          # 0..1 saloon HVAC loss
    aux_fault: float = 0.0           # 0..1 auxiliary converter fault
    motor_temp: float = 0.0          # °C — winding thermal state (0 ⇒ snap first tick)
    brake_temp: float = 0.0          # °C — brake-disc thermal state
    hours: float = 0.0
    fault: str = "none"
    fault_severity: float = 0.0
    seed: int = 11
    _rng: random.Random = field(default=None, repr=False, compare=False)

    def rng(self):
        if self._rng is None:
            self._rng = random.Random(self.seed)
        return self._rng


_FAULTS = {
    "motor_overtemp": {"motor_derate": 0.8},
    "wheel_flat":     {"wheel_flat": 2.6},
    "brake_fade":     {"brake_wear": 80},
    "door_fault":     {"door_faults": 4},
    "earth_fault":    {"earth_fault": 0.8},
    "hvac_fault":     {"hvac_fault": 0.85},
    "aux_converter":  {"aux_fault": 0.8},
    "heatwave":       {"heat": 0.8},
}

FAULTS = list(_FAULTS.keys())


class TrainsetPhysics:
    def __init__(self, **options):
        self.opts = options or {}

    def init_state(self) -> TrainsetState:
        return TrainsetState()

    def inject(self, state: TrainsetState, fault: str, severity: float = 0.85) -> None:
        eff = max(0.0, min(1.0, float(severity)))
        state.fault = fault
        state.fault_severity = eff
        for attr, amount in _FAULTS.get(fault, {}).items():
            if attr in ("motor_derate", "earth_fault", "hvac_fault", "aux_fault", "heat"):
                setattr(state, attr, min(1.2, getattr(state, attr) + amount * eff))
            elif attr == "wheel_flat":
                state.wheel_flat = min(6.0, state.wheel_flat + amount * eff)
            else:
                setattr(state, attr, min(120.0, getattr(state, attr) + amount * eff))

    def clear(self, state: TrainsetState) -> None:
        state.fault = "none"
        state.fault_severity = 0.0
        state.heat = 0.2
        state.motor_derate = 0.0
        state.earth_fault = 0.0
        state.hvac_fault = 0.0
        state.aux_fault = 0.0
        state.door_faults = 0.0

    def forward(self, state: TrainsetState, dt: float = 1.0) -> dict:
        rng = state.rng()
        notch = clamp(state.throttle)
        state.hours += dt / 3600.0
        # Wear advances here (not in predict) and accelerates under an active
        # fault, so the live twin and predict() age on the same curve.
        _wear_mult = 1.0 + 2.0 * (state.fault_severity if state.fault != "none" else 0.0)
        state.brake_wear = min(100.0, state.brake_wear + dt * _wear_mult * (0.0015 + 0.004 * notch))
        if state.wheel_flat > 0:
            state.wheel_flat = min(6.0, state.wheel_flat + dt * _wear_mult * 4e-5)

        # duty cycle: a nominal line speed from the commanded notch
        speed = 25.0 + 55.0 * notch
        td = train_dynamics(speed_kmh=speed, gradient_permille=6.0 * notch,
                            mass_t=state.mass_t, notch=notch)
        tractive = td["tractive_effort_kn"]

        # traction electrical: P = TE * v ; current = P / V
        power_kw = tractive * (speed / 3.6)
        line_v = 750.0
        base_current = power_kw * 1000.0 / line_v
        line_current = base_current * (1.0 + 1.4 * state.earth_fault)   # earth fault → overcurrent
        motor_current = line_current / 4.0                              # 4 motored axles

        # Traction-motor winding temperature: a thermal STATE with real inertia
        # (I²R-driven steady value, cooled by ventilation) that ramps toward its
        # steady value rather than snapping with the notch.
        cooling = 1.0 - 0.7 * state.motor_derate
        motor_ss = 55.0 + 65.0 * notch + 28.0 * state.heat \
            + 45.0 * state.motor_derate + 20.0 * state.earth_fault
        motor_ss = 40.0 + (motor_ss - 40.0) / max(0.3, cooling)
        state.motor_temp = motor_ss if state.motor_temp <= 0.0 else first_order_lag(
            state.motor_temp, motor_ss, _TAU_MOTOR, dt)
        motor_temp = state.motor_temp

        wr = wheel_rail_contact(axle_load_kn=160.0,
                                speed_kmh=speed, wheel_flat_mm=state.wheel_flat)
        wheel_qp = wr["derailment_quotient"]
        bogie_vib = 2.0 + 1.9 * state.wheel_flat + 0.02 * speed + 0.012 * state.brake_wear + 1.0 * state.heat

        # Brake-disc temperature: friction braking dissipates kinetic energy at a
        # rate that rises smoothly as the notch drops below coasting while moving
        # (∝ v²), replacing the old discontinuous — and self-contradictory —
        # 180·(notch<0.25)·(speed>40) step. Also a thermal state (convective cooling).
        brake_frac = max(0.0, 0.35 - notch) / 0.35
        brake_ss = (45.0 + 0.9 * state.brake_wear
                    + brake_frac * (speed / 80.0) ** 2 * 300.0 + 40.0 * state.heat)
        state.brake_temp = brake_ss if state.brake_temp <= 0.0 else first_order_lag(
            state.brake_temp, brake_ss, _TAU_BRAKE, dt)
        brake_temp = state.brake_temp
        car_hvac = 22.0 + 6.0 * state.hvac_fault + 3.0 * state.heat + 0.01 * (40.0 * notch)
        aux_v = 110.0 - 30.0 * state.aux_fault - 3.0 * state.heat
        regen = max(0.0, 32.0 - 18.0 * state.heat) * (1.0 - state.earth_fault)

        def j(v, frac):
            return jitter(rng, v, frac)

        return {
            SIGNALS["speed"]:         round(max(0.0, j(speed, 0.02)), 1),
            SIGNALS["tractive"]:      round(max(0.0, j(tractive, 0.02)), 1),
            SIGNALS["line_current"]:  round(max(0.0, j(line_current, 0.02)), 0),
            SIGNALS["motor_current"]: round(max(0.0, j(motor_current, 0.02)), 0),
            SIGNALS["motor_temp"]:    round(j(motor_temp, 0.01), 1),
            SIGNALS["bogie_vib"]:     round(max(0.0, j(bogie_vib, 0.03)), 2),
            SIGNALS["wheel_qp"]:      round(max(0.0, j(wheel_qp, 0.02)), 3),
            SIGNALS["wheel_flat"]:    round(state.wheel_flat, 2),
            SIGNALS["brake_temp"]:    round(j(brake_temp, 0.02), 1),
            SIGNALS["brake_wear"]:    round(state.brake_wear, 1),
            SIGNALS["door_faults"]:   int(state.door_faults),
            SIGNALS["car_hvac"]:      round(j(car_hvac, 0.01), 1),
            SIGNALS["aux_v"]:         round(j(aux_v, 0.006), 1),
            SIGNALS["regen"]:         round(max(0.0, regen), 1),
        }

    def residuals(self, frame: dict) -> dict:
        return {SIGNALS["aux_v"]: frame.get(SIGNALS["aux_v"], 110.0) - 110.0}

    def health_index(self, frame: dict) -> float:
        if not frame:
            return 1.0
        return round(worst_health([
            margin_hi(frame.get(SIGNALS["motor_temp"], 90.0), 90.0, redlines.motor_temp_max),
            margin_hi(frame.get(SIGNALS["bogie_vib"], 3.4), 3.4, redlines.bogie_vib_max),
            margin_hi(frame.get(SIGNALS["wheel_qp"], 0.28), 0.28, redlines.wheel_qp_max),
            margin_hi(frame.get(SIGNALS["brake_temp"], 70.0), 70.0, redlines.brake_temp_max),
            margin_hi(frame.get(SIGNALS["brake_wear"], 15.0), 15.0, redlines.brake_wear_max),
            margin_lo(frame.get(SIGNALS["aux_v"], 110.0), 110.0, redlines.aux_v_min),
            margin_hi(frame.get(SIGNALS["line_current"], 2200.0), 2200.0, redlines.line_current_max),
        ]), 3)


# ── health rollup + prediction ──
_status = status_from_health


def component_health(state, frame, physics) -> dict:
    mtemp = frame.get(SIGNALS["motor_temp"], 90.0)
    mcur = frame.get(SIGNALS["line_current"], 700.0)
    vib = frame.get(SIGNALS["bogie_vib"], 2.2)
    qp = frame.get(SIGNALS["wheel_qp"], 0.28)
    btemp = frame.get(SIGNALS["brake_temp"], 70.0)
    bwear = frame.get(SIGNALS["brake_wear"], 15.0)
    doors = frame.get(SIGNALS["door_faults"], 0.0)
    hvac = frame.get(SIGNALS["car_hvac"], 22.0)
    aux = frame.get(SIGNALS["aux_v"], 110.0)

    comp = {
        "traction": max(0.0, min(1.0, 1.0 - max(0.0, mtemp - 120.0) / 60.0
                        - max(0.0, mcur - 2600.0) / 900.0)),
        "running_gear": max(0.0, 1.0 - (vib / redlines.bogie_vib_max) * 0.45
                            - max(0.0, qp - 0.4) / 0.5),
        "braking": max(0.0, min(1.0, 1.0 - max(0.0, btemp - 200.0) / 200.0
                       - bwear / (redlines.brake_wear_max + 15.0))),
        "doors": max(0.0, 1.0 - doors / (redlines.door_faults_max + 1.0)),
        "auxiliaries": max(0.0, min(1.0, (aux - redlines.aux_v_min) / (110.0 - redlines.aux_v_min))),
        "hvac": max(0.0, min(1.0, 1.0 - max(0.0, hvac - 24.0) / 8.0)),
    }
    out = {k: {"health": round(v, 3), "status": _status(v)} for k, v in comp.items()}
    overall = min(comp.values()) if comp else 1.0
    out["overall"] = {"health": round(overall, 3), "status": _status(overall)}
    return out


_GUARDS = [
    ("traction motor temperature", SIGNALS["motor_temp"], redlines.motor_temp_max, "above", "traction"),
    ("line overcurrent", SIGNALS["line_current"], redlines.line_current_max, "above", "traction"),
    ("bogie vibration", SIGNALS["bogie_vib"], redlines.bogie_vib_max, "above", "running_gear"),
    ("derailment quotient", SIGNALS["wheel_qp"], redlines.wheel_qp_max, "above", "running_gear"),
    ("brake temperature", SIGNALS["brake_temp"], redlines.brake_temp_max, "above", "braking"),
    ("brake wear", SIGNALS["brake_wear"], redlines.brake_wear_max, "above", "braking"),
    ("door faults", SIGNALS["door_faults"], redlines.door_faults_max, "above", "doors"),
    ("auxiliary voltage", SIGNALS["aux_v"], redlines.aux_v_min, "below", "auxiliaries"),
]


def predict(state, horizon_min: float = 120.0, points: int = 120, physics=None) -> dict:
    if physics is None:
        physics = TrainsetPhysics()
    st = copy.deepcopy(state)
    st._rng = None
    dt_min = horizon_min / max(1, points)
    dt_s = dt_min * 60.0
    trajectory, events, rul = [], [], {}
    for i in range(points):
        t_min = round(i * dt_min, 2)
        # No separate ramp: physics.forward advances wear itself (accelerated by
        # an active fault), so the projection follows the twin's own dynamics.
        frame = physics.forward(st, dt=dt_s)
        trajectory.append({
            "t": t_min,
            SIGNALS["motor_temp"]: frame[SIGNALS["motor_temp"]],
            SIGNALS["bogie_vib"]: frame[SIGNALS["bogie_vib"]],
            SIGNALS["brake_wear"]: frame[SIGNALS["brake_wear"]],
            SIGNALS["aux_v"]: frame[SIGNALS["aux_v"]],
            "health": round(physics.health_index(frame), 3),
        })
        for label, sig, lim, direction, subsystem in _GUARDS:
            v = frame.get(sig)
            if v is None:
                continue
            crossed = v >= lim if direction == "above" else v <= lim
            if crossed and subsystem not in rul:
                rul[subsystem] = t_min
                events.append({"t": t_min, "signal": sig, "component": subsystem,
                               "message": f"{label} reaches limit ({v:.1f}) at ~{t_min:.0f} min"})
    rul_list = sorted(({"component": k, "minutes": v, "hours": round(v / 60.0, 2)}
                       for k, v in rul.items()), key=lambda r: r["minutes"])
    if not rul_list:
        severity = "nominal"
    elif rul_list[0]["minutes"] <= horizon_min * 0.34:
        severity = "critical"
    else:
        severity = "warning"
    return {"trajectory": trajectory, "rul": rul_list, "events": events,
            "severity": severity, "horizon_min": horizon_min}


# ── behaviours ──
class _Latch:
    def __init__(self):
        self._on = {}

    def rising(self, key, cond):
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

    def evaluate(self, sample, query):
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


def build_trainset_registry() -> BehaviorRegistry:
    reg = BehaviorRegistry()
    reg.register(_HardLimit("trainset.motor_hot", SIGNALS["motor_temp"], redlines.motor_temp_max,
                            "above", "Traction motor temperature", "°C"))
    reg.register(_HardLimit("trainset.overcurrent", SIGNALS["line_current"], redlines.line_current_max,
                            "above", "Line current", " A"))
    reg.register(_HardLimit("trainset.bogie_vibration", SIGNALS["bogie_vib"], redlines.bogie_vib_max,
                            "above", "Bogie vibration (ISO 10816-3)", " mm/s", severity="warning"))
    reg.register(_HardLimit("trainset.derailment_qp", SIGNALS["wheel_qp"], redlines.wheel_qp_max,
                            "above", "Derailment quotient Q/P", ""))
    reg.register(_HardLimit("trainset.brake_hot", SIGNALS["brake_temp"], redlines.brake_temp_max,
                            "above", "Brake temperature", "°C", severity="warning"))
    reg.register(_HardLimit("trainset.brake_worn", SIGNALS["brake_wear"], redlines.brake_wear_max,
                            "above", "Brake wear", "%"))
    reg.register(_HardLimit("trainset.door_fault", SIGNALS["door_faults"], redlines.door_faults_max,
                            "above", "Door faults", ""))
    reg.register(_HardLimit("trainset.aux_undervolt", SIGNALS["aux_v"], redlines.aux_v_min,
                            "below", "Auxiliary voltage", " V"))
    return reg


SPEC = {
    "key": "railway-trainset",
    "label": "Rolling Stock (Train Set)",
    "class_iri": "https://ontology.nextxr.io/v3/railway#RollingStock",
    "control": "throttle",
    "physics": TrainsetPhysics,
    "predict": predict,
    "component_health": component_health,
    "build_registry": build_trainset_registry,
    "signals": SIGNALS,
    "units": UNITS,
    "faults": FAULTS,
    "sensors": {
        SIGNALS["speed"]: ("Train Speed", "km/h"),
        SIGNALS["tractive"]: ("Tractive Effort", "kN"),
        SIGNALS["line_current"]: ("Line Current", "A"),
        SIGNALS["motor_current"]: ("Motor Current", "A"),
        SIGNALS["motor_temp"]: ("Traction Motor Temp", "°C"),
        SIGNALS["bogie_vib"]: ("Bogie Vibration", "mm/s"),
        SIGNALS["wheel_qp"]: ("Derailment Q/P", ""),
        SIGNALS["wheel_flat"]: ("Wheel Flat", "mm"),
        SIGNALS["brake_temp"]: ("Brake Temperature", "°C"),
        SIGNALS["brake_wear"]: ("Brake Wear", "%"),
        SIGNALS["door_faults"]: ("Door Faults", ""),
        SIGNALS["car_hvac"]: ("Saloon HVAC Temp", "°C"),
        SIGNALS["aux_v"]: ("Auxiliary Voltage", "V"),
        SIGNALS["regen"]: ("Regen Share", "%"),
    },
    "subsystems": [
        ("traction", "Traction"),
        ("running_gear", "Running Gear (Bogies)"),
        ("braking", "Braking"),
        ("doors", "Doors & TCMS"),
        ("auxiliaries", "Auxiliaries"),
        ("hvac", "Saloon HVAC"),
    ],
    "checks": {
        SIGNALS["motor_temp"]: (redlines.motor_temp_max, "above"),
        SIGNALS["bogie_vib"]: (redlines.bogie_vib_max, "above"),
        SIGNALS["wheel_qp"]: (redlines.wheel_qp_max, "above"),
        SIGNALS["brake_temp"]: (redlines.brake_temp_max, "above"),
        SIGNALS["brake_wear"]: (redlines.brake_wear_max, "above"),
        SIGNALS["line_current"]: (redlines.line_current_max, "above"),
        SIGNALS["aux_v"]: (redlines.aux_v_min, "below"),
        SIGNALS["door_faults"]: (redlines.door_faults_max, "above"),
    },
}

__all__ = ["TrainsetPhysics", "SIGNALS", "UNITS", "redlines", "FAULTS",
           "component_health", "predict", "build_trainset_registry", "SPEC"]
