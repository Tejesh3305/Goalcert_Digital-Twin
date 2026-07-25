"""
packs/defence/warship.py — a warship digital-twin domain.

Twins a naval surface combatant: gas-turbine propulsion (Brayton), transverse
stability under progressive flooding, and hull structural fatigue. Reuses the
component engines from packs.defence.physics. `network_state` returns the damage-
control compartment cross-section.
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
from .physics import gas_turbine_brayton, ship_stability, structural_fatigue

SIGNALS = {
    "gt_power":       "def:gtPower",
    "gt_egt":         "def:gtEGT",
    "gt_comp_temp":   "def:gtCompressorTemp",
    "gt_efficiency":  "def:gtEfficiency",
    "list_angle":     "def:listAngle",
    "gz":             "def:rightingLever",
    "freeboard":      "def:freeboard",
    "flooding":       "def:floodingPercent",
    "draft":          "def:draft",
    "fatigue_damage": "def:fatigueDamage",
    "crack_length":   "def:crackLength",
    "fatigue_life":   "def:fatigueLife",
    "hull_stress":    "def:hullStress",
    "speed":          "def:shipSpeed",
}

UNITS = {
    SIGNALS["gt_power"]: "MW", SIGNALS["gt_egt"]: "DEG_C", SIGNALS["gt_comp_temp"]: "DEG_C",
    SIGNALS["gt_efficiency"]: "%", SIGNALS["list_angle"]: "deg", SIGNALS["gz"]: "m",
    SIGNALS["freeboard"]: "m", SIGNALS["flooding"]: "%", SIGNALS["draft"]: "m",
    SIGNALS["fatigue_damage"]: "", SIGNALS["crack_length"]: "mm", SIGNALS["fatigue_life"]: "%",
    SIGNALS["hull_stress"]: "MPa", SIGNALS["speed"]: "kn",
}


@dataclass
class Redlines:
    gt_egt_max: float = 650.0            # °C
    gt_efficiency_min: float = 25.0      # %
    list_angle_max: float = 15.0         # deg (Damage Control alert)
    gz_min: float = 0.2                  # m (righting lever)
    freeboard_min: float = 2.0           # m
    flooding_max: float = 25.0           # %
    fatigue_life_min: float = 20.0       # %
    crack_max: float = 25.0              # mm
    hull_stress_max: float = 220.0       # MPa


redlines = Redlines()

_COMPARTMENTS = [
    {"id": "C1", "name": "Fwd Store", "deck": "lower", "x": 6, "y": 22, "w": 15, "h": 10},
    {"id": "C2", "name": "Magazine", "deck": "lower", "x": 22, "y": 22, "w": 15, "h": 10},
    {"id": "C3", "name": "Aux Machinery", "deck": "lower", "x": 38, "y": 22, "w": 14, "h": 10},
    {"id": "C4", "name": "Main Machinery", "deck": "lower", "x": 53, "y": 22, "w": 16, "h": 10},
    {"id": "C5", "name": "Shaft Alley", "deck": "lower", "x": 70, "y": 22, "w": 14, "h": 10},
    {"id": "C6", "name": "Ops Room", "deck": "upper", "x": 22, "y": 11, "w": 16, "h": 10},
    {"id": "C7", "name": "Mess", "deck": "upper", "x": 39, "y": 11, "w": 15, "h": 10},
    {"id": "C8", "name": "Engine Uptakes", "deck": "upper", "x": 55, "y": 11, "w": 14, "h": 10},
]


@dataclass
class WarshipState:
    speed_demand: float = 0.5            # control 0..1 (propulsion setting)
    displacement_t: float = 6000.0
    gt_degradation: float = 0.0          # 0..1
    flood_tonnes: float = 0.0            # water taken on so far (integrated)
    flood_capacity_t: float = 0.0        # breached compartment volume (fill target)
    flood_tau: float = 0.0               # ingress time constant, s (0 = no breach)
    heel_bias: float = 0.0               # deg (asymmetric loading)
    hull_load: float = 0.55              # 0..1 sea-state loading
    crack_mm: float = 1.2
    cycles: float = 0.9e6
    flood_target: str = ""               # compartment id being flooded
    hours: float = 0.0
    fault: str = "none"
    fault_severity: float = 0.0
    seed: int = 29
    _rng: random.Random = field(default=None, repr=False, compare=False)

    def rng(self):
        if self._rng is None:
            self._rng = random.Random(self.seed)
        return self._rng


_FAULTS = {
    "hull_breach":    {"flood_capacity_t": 520.0, "_compartment": True},
    "asymmetric_list": {"heel_bias": 12.0, "flood_capacity_t": 220.0, "_compartment": True},
    "gt_overtemp":    {"gt_degradation": 0.85},
    "fatigue_crack":  {"crack_mm": 18.0, "hull_load": 0.9},
    "heavy_seas":     {"hull_load": 0.95},
    "flank_speed":    {"speed_demand": 0.95},
}

FAULTS = list(_FAULTS.keys())


class DefenceWarshipPhysics:
    def __init__(self, **options):
        self.opts = options or {}

    def init_state(self) -> WarshipState:
        return WarshipState()

    def inject(self, state: WarshipState, fault: str, severity: float = 0.85) -> None:
        eff = max(0.0, min(1.0, float(severity)))
        state.fault = fault
        state.fault_severity = eff
        rng = state.rng()
        for attr, amount in _FAULTS.get(fault, {}).items():
            if attr == "_compartment":
                state.flood_target = _COMPARTMENTS[rng.randrange(5)]["id"]  # a lower compartment
            elif attr == "flood_capacity_t":
                # The compartment volume is fixed; severity sets the breach SIZE,
                # i.e. how fast it floods (smaller tau = faster ingress).
                state.flood_capacity_t = getattr(state, attr) + amount
                state.flood_tau = 300.0 + 1500.0 * (1.0 - eff)
            elif attr == "speed_demand":
                state.speed_demand = max(state.speed_demand, amount * eff)
            elif attr == "crack_mm":
                state.crack_mm = max(state.crack_mm, amount * eff)
            elif attr in ("gt_degradation", "hull_load"):
                setattr(state, attr, min(1.2, getattr(state, attr) + amount * eff))
            else:
                setattr(state, attr, getattr(state, attr) + amount * eff)

    def clear(self, state: WarshipState) -> None:
        state.fault = "none"
        state.fault_severity = 0.0
        state.flood_target = ""
        state.gt_degradation = 0.0
        state.flood_tonnes = 0.0
        state.flood_capacity_t = 0.0
        state.flood_tau = 0.0
        state.heel_bias = 0.0
        state.hull_load = 0.55

    def forward(self, state: WarshipState, dt: float = 1.0) -> dict:
        rng = state.rng()
        S = clamp(state.speed_demand)
        state.hours += dt / 3600.0
        cyc_inc = dt * (60.0 + 120.0 * state.hull_load)         # wave-encounter cycles this tick
        state.cycles += cyc_inc

        # ── propulsion (Brayton) ──
        gt = gas_turbine_brayton(fuel_flow_kgps=0.22 + 0.34 * S, load=0.35 + 0.6 * S,
                                 degradation=state.gt_degradation)
        speed_kn = 6.0 + 24.0 * S

        # ── flooding (progressive) ──
        # Water ingresses toward the breached compartment's capacity with a time
        # constant set by the breach size, so flooding RISES over time instead of
        # appearing in full instantly. Ingress slows as the compartment fills and
        # the head equalises — a first-order approach captures that.
        if state.flood_tau > 0.0 and state.flood_capacity_t > 0.0:
            state.flood_tonnes = first_order_lag(state.flood_tonnes, state.flood_capacity_t,
                                                 state.flood_tau, dt)
        flooding_pct = min(100.0, state.flood_tonnes / 20.0)

        # ── stability ──
        ss = ship_stability(displacement_t=state.displacement_t, gm_m=1.4,
                            heel_deg=state.heel_bias, flood_tonnes=state.flood_tonnes)

        # ── structural fatigue ──
        hull_stress = 60.0 + 150.0 * state.hull_load + 0.3 * speed_kn
        sf = structural_fatigue(stress_range_mpa=hull_stress, cycles=state.cycles,
                                crack_mm=state.crack_mm, dcycles=cyc_inc)
        state.crack_mm = min(300.0, sf["crack_mm"])

        def j(v, frac):
            return jitter(rng, v, frac)

        return {
            SIGNALS["gt_power"]:       round(max(0.0, j(gt["power_mw"], 0.02)), 2),
            SIGNALS["gt_egt"]:         round(j(gt["egt_c"], 0.01), 1),
            SIGNALS["gt_comp_temp"]:   round(j(gt["compressor_temp_c"], 0.01), 1),
            SIGNALS["gt_efficiency"]:  round(j(gt["efficiency"], 0.01), 1),
            SIGNALS["list_angle"]:     round(j(ss["list_deg"], 0.01), 2),
            SIGNALS["gz"]:             round(ss["gz_m"], 3),
            SIGNALS["freeboard"]:      round(ss["freeboard_m"], 2),
            SIGNALS["flooding"]:       round(flooding_pct, 1),
            SIGNALS["draft"]:          round(ss["draft_m"], 2),
            SIGNALS["fatigue_damage"]: round(sf["miner_damage"], 4),
            SIGNALS["crack_length"]:   round(sf["crack_mm"], 2),
            SIGNALS["fatigue_life"]:   round(sf["fatigue_life_pct"], 1),
            SIGNALS["hull_stress"]:    round(j(hull_stress, 0.01), 1),
            SIGNALS["speed"]:          round(j(speed_kn, 0.01), 1),
        }

    def residuals(self, frame: dict) -> dict:
        return {SIGNALS["list_angle"]: frame.get(SIGNALS["list_angle"], 0.0)}

    def health_index(self, frame: dict) -> float:
        if not frame:
            return 1.0
        return round(worst_health([
            margin_hi(frame.get(SIGNALS["gt_egt"], 560.0), 560.0, redlines.gt_egt_max),
            margin_lo(frame.get(SIGNALS["gt_efficiency"], 34.0), 34.0, redlines.gt_efficiency_min),
            margin_hi(frame.get(SIGNALS["list_angle"], 1.0), 1.0, redlines.list_angle_max),
            margin_hi(frame.get(SIGNALS["flooding"], 0.0), 0.0, redlines.flooding_max),
            margin_lo(frame.get(SIGNALS["fatigue_life"], 80.0), 80.0, redlines.fatigue_life_min),
            margin_hi(frame.get(SIGNALS["crack_length"], 1.5), 1.5, redlines.crack_max),
            margin_hi(frame.get(SIGNALS["hull_stress"], 140.0), 140.0, redlines.hull_stress_max),
            margin_lo(frame.get(SIGNALS["freeboard"], 4.0), 4.0, redlines.freeboard_min),
        ]), 3)

    # ── live damage-control compartment cross-section ──
    def network_state(self, state: WarshipState) -> dict:
        flooding_pct = min(100.0, state.flood_tonnes / 20.0)
        comps = []
        for c in _COMPARTMENTS:
            targeted = state.flood_target == c["id"]
            fl = min(100.0, (flooding_pct * (1.6 if targeted else 0.15 if c["deck"] == "lower" else 0.0)))
            status = "critical" if fl > 60 else "warning" if fl > 15 else "ok"
            comps.append({**c, "flooding": round(fl, 0), "status": status})
        ss = ship_stability(displacement_t=state.displacement_t, gm_m=1.4,
                            heel_deg=state.heel_bias, flood_tonnes=state.flood_tonnes)
        return {"compartments": comps,
                "ship": {"list_deg": round(ss["list_deg"], 1), "gz_m": round(ss["gz_m"], 3),
                         "draft_m": round(ss["draft_m"], 2), "freeboard_m": round(ss["freeboard_m"], 2),
                         "capsize_risk": ss["capsize_risk"]},
                "fault": state.fault}


# ── health rollup + prediction ──
_status = status_from_health


def component_health(state, frame, physics) -> dict:
    egt = frame.get(SIGNALS["gt_egt"], 560.0)
    eff = frame.get(SIGNALS["gt_efficiency"], 34.0)
    lst = frame.get(SIGNALS["list_angle"], 1.0)
    flood = frame.get(SIGNALS["flooding"], 0.0)
    life = frame.get(SIGNALS["fatigue_life"], 80.0)
    stress = frame.get(SIGNALS["hull_stress"], 140.0)
    comp = {
        "propulsion": max(0.0, min(1.0, 1.0 - max(0.0, egt - 560.0) / (redlines.gt_egt_max - 560.0) * 0.6
                          - max(0.0, redlines.gt_efficiency_min - eff) / redlines.gt_efficiency_min * 0.4)),
        "stability": max(0.0, 1.0 - lst / redlines.list_angle_max),
        "damage_control": max(0.0, 1.0 - flood / redlines.flooding_max),
        "structure": max(0.0, min(1.0, (life - redlines.fatigue_life_min) / (80.0 - redlines.fatigue_life_min) * 0.6
                         + (1.0 - max(0.0, stress - 140.0) / (redlines.hull_stress_max - 140.0)) * 0.4)),
    }
    out = {k: {"health": round(v, 3), "status": _status(v)} for k, v in comp.items()}
    overall = min(comp.values()) if comp else 1.0
    out["overall"] = {"health": round(overall, 3), "status": _status(overall)}
    return out


_GUARDS = [
    ("turbine EGT", SIGNALS["gt_egt"], redlines.gt_egt_max, "above", "propulsion"),
    ("list angle", SIGNALS["list_angle"], redlines.list_angle_max, "above", "stability"),
    ("flooding", SIGNALS["flooding"], redlines.flooding_max, "above", "damage_control"),
    ("fatigue life", SIGNALS["fatigue_life"], redlines.fatigue_life_min, "below", "structure"),
    ("crack length", SIGNALS["crack_length"], redlines.crack_max, "above", "structure"),
    ("hull stress", SIGNALS["hull_stress"], redlines.hull_stress_max, "above", "structure"),
]


def predict(state, horizon_min: float = 120.0, points: int = 120, physics=None) -> dict:
    if physics is None:
        physics = DefenceWarshipPhysics()
    st = copy.deepcopy(state)
    st._rng = None
    dt_min = horizon_min / max(1, points)
    dt_s = dt_min * 60.0
    trajectory, events, rul = [], [], {}
    for i in range(points):
        t_min = round(i * dt_min, 2)
        frame = physics.forward(st, dt=dt_s)
        trajectory.append({
            "t": t_min,
            SIGNALS["list_angle"]: frame[SIGNALS["list_angle"]],
            SIGNALS["gt_egt"]: frame[SIGNALS["gt_egt"]],
            SIGNALS["fatigue_life"]: frame[SIGNALS["fatigue_life"]],
            SIGNALS["crack_length"]: frame[SIGNALS["crack_length"]],
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
        breach = (sample.value >= self._limit if self._dir == "above" else sample.value <= self._limit)
        if not self._latch.rising(sample.entity_id, breach):
            return []
        rel = "≥" if self._dir == "above" else "≤"
        return [Finding(behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
                        severity=self._sev,
                        message=f"{self._label} out of limits: {sample.value:.1f}{self._unit} {rel} {self._limit:.0f}{self._unit}",
                        confidence=1.0,
                        evidence={"value": sample.value, "limit": self._limit, "signal": sample.signal})]


# ── P4-010 · ship list angle ──
class ShipListAngle(Behavior):
    behavior_id = "defence.ship_list_angle"
    tier = Tier.A
    watches = [SIGNALS["list_angle"]]
    reads = ["computed list angle"]
    emits = "Ship list Damage Control alert"

    def __init__(self, limit=None):
        self._limit = limit if limit is not None else redlines.list_angle_max
        self._latch = _Latch()

    def evaluate(self, sample, query):
        if not self._latch.rising(sample.entity_id, sample.value >= self._limit):
            return []
        return [Finding(behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
                        severity="critical",
                        message=f"Ship list {sample.value:.1f}° ≥ {self._limit:.0f}° — DAMAGE CONTROL alert. "
                                f"Counter-flood / initiate ballast transfer and isolate the flooded compartment.",
                        confidence=1.0,
                        evidence={"list_deg": sample.value, "limit": self._limit,
                                  "action": "ballast_transfer", "signal": sample.signal})]


# ── structural fatigue ──
class StructuralFatigue(Behavior):
    behavior_id = "defence.structural_fatigue"
    tier = Tier.C
    watches = [SIGNALS["fatigue_life"]]
    reads = ["fatigue life remaining", "crack length (sibling)"]
    emits = "Structural fatigue limit"

    def __init__(self, floor=None):
        self._floor = floor if floor is not None else redlines.fatigue_life_min
        self._latch = _Latch()

    def evaluate(self, sample, query):
        if not self._latch.rising(sample.entity_id, sample.value <= self._floor):
            return []
        crack = query.get_property(sample.tenant_id, sample.entity_id, "crackLength", 0.0)
        return [Finding(behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
                        severity="warning",
                        message=f"Structural fatigue life {sample.value:.0f}% ≤ {self._floor:.0f}% "
                                f"(crack {crack} mm) — restrict speed/sea-state and schedule hull survey.",
                        confidence=0.9,
                        evidence={"fatigue_life": sample.value, "crack_mm": crack,
                                  "action": "hull_survey", "signal": sample.signal})]


def build_warship_registry() -> BehaviorRegistry:
    reg = BehaviorRegistry()
    reg.register(ShipListAngle())
    reg.register(StructuralFatigue())
    reg.register(_HardLimit("defence.gt_egt", SIGNALS["gt_egt"], redlines.gt_egt_max,
                            "above", "Turbine EGT", "°C"))
    reg.register(_HardLimit("defence.flooding", SIGNALS["flooding"], redlines.flooding_max,
                            "above", "Flooding", "%"))
    reg.register(_HardLimit("defence.crack", SIGNALS["crack_length"], redlines.crack_max,
                            "above", "Crack length", " mm"))
    reg.register(_HardLimit("defence.hull_stress", SIGNALS["hull_stress"], redlines.hull_stress_max,
                            "above", "Hull stress", " MPa", severity="warning"))
    reg.register(_HardLimit("defence.freeboard", SIGNALS["freeboard"], redlines.freeboard_min,
                            "below", "Freeboard", " m"))
    return reg


SPEC = {
    "key": "defence-warship",
    "label": "Warship",
    "class_iri": "https://ontology.nextxr.io/v3/defence#Vessel",
    "control": "speed_demand",
    "physics": DefenceWarshipPhysics,
    "predict": predict,
    "component_health": component_health,
    "build_registry": build_warship_registry,
    "signals": SIGNALS,
    "units": UNITS,
    "faults": FAULTS,
    "sensors": {
        SIGNALS["gt_power"]: ("GT Power", "MW"),
        SIGNALS["gt_egt"]: ("Turbine EGT", "°C"),
        SIGNALS["gt_comp_temp"]: ("Compressor Temp", "°C"),
        SIGNALS["gt_efficiency"]: ("GT Efficiency", "%"),
        SIGNALS["list_angle"]: ("List Angle", "°"),
        SIGNALS["gz"]: ("Righting Lever GZ", "m"),
        SIGNALS["freeboard"]: ("Freeboard", "m"),
        SIGNALS["flooding"]: ("Flooding", "%"),
        SIGNALS["draft"]: ("Draft", "m"),
        SIGNALS["fatigue_damage"]: ("Fatigue Damage", ""),
        SIGNALS["crack_length"]: ("Crack Length", "mm"),
        SIGNALS["fatigue_life"]: ("Fatigue Life", "%"),
        SIGNALS["hull_stress"]: ("Hull Stress", "MPa"),
        SIGNALS["speed"]: ("Ship Speed", "kn"),
    },
    "subsystems": [
        ("propulsion", "Propulsion (GT)"),
        ("stability", "Stability"),
        ("damage_control", "Damage Control"),
        ("structure", "Hull Structure"),
    ],
    "checks": {
        SIGNALS["gt_egt"]: (redlines.gt_egt_max, "above"),
        SIGNALS["list_angle"]: (redlines.list_angle_max, "above"),
        SIGNALS["flooding"]: (redlines.flooding_max, "above"),
        SIGNALS["fatigue_life"]: (redlines.fatigue_life_min, "below"),
        SIGNALS["crack_length"]: (redlines.crack_max, "above"),
        SIGNALS["hull_stress"]: (redlines.hull_stress_max, "above"),
        SIGNALS["freeboard"]: (redlines.freeboard_min, "below"),
    },
}

__all__ = ["DefenceWarshipPhysics", "SIGNALS", "UNITS", "redlines", "FAULTS",
           "component_health", "predict", "build_warship_registry", "SPEC"]
