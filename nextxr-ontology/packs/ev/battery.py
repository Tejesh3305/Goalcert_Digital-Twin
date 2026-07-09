"""
ev/battery.py — a battery-pack digital-twin domain (cell level).

Twins one traction/storage pack: modules of cells with the Thevenin ECM, thermal
coupling and degradation engines from .physics. Runs a charge/discharge duty
(CC-CV on charge), tracks per-cell state for the heatmap, and monitors cell
imbalance, thermal-runaway precursors and state-of-health.
"""
from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field

from behaviors.registry import Behavior, BehaviorRegistry, Finding, Tier
from .physics import (
    battery_ecm, battery_thermal, battery_degradation, charging_dynamics, soc_ocv,
)

SIGNALS = {
    "soc":          "ev:stateOfCharge",
    "soh":          "ev:stateOfHealth",
    "cell_v_delta": "ev:cellVoltageDelta",
    "pack_voltage": "ev:packVoltage",
    "pack_current": "ev:packCurrent",
    "cell_temp":    "ev:cellTempMax",
    "cell_temp_rise": "ev:cellTempRise",
    "coolant_temp": "ev:coolantTemp",
    "charge_rate":  "ev:chargeRate",
}

UNITS = {
    SIGNALS["soc"]: "%", SIGNALS["soh"]: "%", SIGNALS["cell_v_delta"]: "V",
    SIGNALS["pack_voltage"]: "V", SIGNALS["pack_current"]: "A",
    SIGNALS["cell_temp"]: "DEG_C", SIGNALS["cell_temp_rise"]: "°C/min",
    SIGNALS["coolant_temp"]: "DEG_C", SIGNALS["charge_rate"]: "C",
}

_MODULES = 8
_CELLS_PER_MODULE = 12
_SERIES = _MODULES * _CELLS_PER_MODULE     # 96S
_CAP_AH = 120.0
_I_MAX = 360.0                              # ~3C


@dataclass
class Redlines:
    cell_v_delta_max: float = 0.05          # 50 mV (P2-010)
    cell_temp_max: float = 55.0
    cell_temp_rise_max: float = 5.0         # °C/min (P2-011)
    pack_current_max: float = 400.0
    soh_min: float = 80.0                   # % (P2-014)
    coolant_max: float = 40.0


redlines = Redlines()


@dataclass
class BatteryState:
    c_rate: float = 0.4                     # control 0..1 (duty intensity)
    soc: float = 62.0                       # %
    soh: float = 93.0                       # %
    temp: float = 30.0                      # °C worst cell
    coolant_temp: float = 22.0              # °C
    v1: float = 0.0
    v2: float = 0.0
    charging: bool = True
    imbalance: float = 0.0                  # 0..1 weak-cell divergence
    runaway: float = 0.0                    # 0..1 thermal-runaway precursor
    coolant_fault: float = 0.0              # 0..1 lost cooling
    overcurrent: float = 0.0                # 0..1
    weak_cell: int = 37                     # index of the diverging cell
    prev_temp: float = 30.0
    hours: float = 0.0
    fault: str = "none"
    fault_severity: float = 0.0
    seed: int = 19
    _rng: random.Random = field(default=None, repr=False, compare=False)

    def rng(self):
        if self._rng is None:
            self._rng = random.Random(self.seed)
        return self._rng


_FAULTS = {
    "cell_imbalance":  {"imbalance": 0.85},
    "thermal_runaway": {"runaway": 0.95, "imbalance": 0.8},
    "coolant_loss":    {"coolant_fault": 0.9},
    "overcurrent":     {"overcurrent": 0.8},
    "aged_pack":       {"_soh": 78.0},
    "fast_charge":     {"c_rate": 0.95},
}

FAULTS = list(_FAULTS.keys())


class EVBatteryPackPhysics:
    def __init__(self, **options):
        self.opts = options or {}

    def init_state(self) -> BatteryState:
        return BatteryState()

    def inject(self, state: BatteryState, fault: str, severity: float = 0.85) -> None:
        eff = max(0.0, min(1.0, float(severity)))
        state.fault = fault
        state.fault_severity = eff
        for attr, amount in _FAULTS.get(fault, {}).items():
            if attr == "_soh":
                state.soh = min(state.soh, amount)
            elif attr == "c_rate":
                state.c_rate = max(state.c_rate, amount * eff)
            else:
                setattr(state, attr, min(1.2, getattr(state, attr) + amount * eff))

    def clear(self, state: BatteryState) -> None:
        state.fault = "none"
        state.fault_severity = 0.0
        state.imbalance = 0.0
        state.runaway = 0.0
        state.coolant_fault = 0.0
        state.overcurrent = 0.0

    def forward(self, state: BatteryState, dt: float = 1.0) -> dict:
        rng = state.rng()
        c = max(0.0, min(1.0, state.c_rate))
        state.hours += dt / 3600.0
        state.prev_temp = state.temp

        # charge/discharge ping-pong duty
        if state.charging and state.soc >= 95.0:
            state.charging = False
        elif not state.charging and state.soc <= 25.0:
            state.charging = True

        pack_voltage_est = _SERIES * soc_ocv(state.soc / 100.0)
        if state.charging:
            cd = charging_dynamics(state.soc / 100.0, state.temp, pack_voltage_est,
                                   i_max_a=_I_MAX * (0.4 + 0.6 * c), p_max_kw=150.0,
                                   connector_temp_c=state.coolant_temp + 8.0)
            charge_a = cd["power_kw"] * 1000.0 / max(1.0, pack_voltage_est)
            ecm_current = -charge_a                      # charging (ECM: + = discharge)
            state.soc = min(100.0, state.soc + charge_a * dt / (_CAP_AH * 3600.0) * 100.0)
        else:
            discharge_a = _I_MAX * (0.25 + 0.6 * c)
            ecm_current = discharge_a
            state.soc = max(15.0, state.soc - discharge_a * dt / (_CAP_AH * 3600.0) * 100.0)
        ecm_current *= (1.0 + 0.6 * state.overcurrent)
        pack_current = abs(ecm_current)

        # ── ECM (mean cell) — sub-mΩ resistances for a large-format (120 Ah) cell ──
        ecm = battery_ecm(state.soc / 100.0, ecm_current, temp_c=state.temp,
                          v1=state.v1, v2=state.v2, r0=0.0008, r1=0.0006, c1=25000.0,
                          r2=0.0004, c2=60000.0, dt=dt)
        state.v1, state.v2 = ecm["v1"], ecm["v2"]
        pack_voltage = _SERIES * ecm["terminal_voltage"]
        cell_loss = ecm["ohmic_loss_w"]

        # ── thermal ──
        state.coolant_temp = 20.0 + 12.0 * state.coolant_fault + 0.02 * pack_current
        th = battery_thermal(state.temp, cell_loss * (1.0 + 2.0 * state.runaway),
                             coolant_in_c=state.coolant_temp,
                             flow_lpm=10.0 * (1.0 - state.coolant_fault), dt=dt)
        state.temp = max(10.0, th["temperature"] + 8.0 * state.runaway * dt / 60.0)
        temp_rise = (state.temp - state.prev_temp) / max(1e-6, dt) * 60.0 + 6.0 * state.runaway

        # ── degradation ──
        deg = battery_degradation(state.temp, state.soc / 100.0, pack_current / _CAP_AH,
                                  dt_h=dt / 3600.0, soh=state.soh)
        state.soh = deg["soh"]

        # ── imbalance ──
        cell_v_delta = 0.008 + 0.09 * state.imbalance + 0.02 * state.runaway \
            + 0.0004 * max(0.0, state.temp - 40.0)

        def j(v, frac):
            return v * (1.0 + rng.uniform(-frac, frac))

        return {
            SIGNALS["soc"]:          round(state.soc, 1),
            SIGNALS["soh"]:          round(state.soh, 2),
            SIGNALS["cell_v_delta"]: round(max(0.0, j(cell_v_delta, 0.03)), 4),
            SIGNALS["pack_voltage"]: round(max(0.0, j(pack_voltage, 0.002)), 1),
            SIGNALS["pack_current"]: round(j(pack_current, 0.01), 1),
            SIGNALS["cell_temp"]:    round(j(state.temp, 0.008), 1),
            SIGNALS["cell_temp_rise"]: round(max(0.0, temp_rise), 2),
            SIGNALS["coolant_temp"]: round(state.coolant_temp, 1),
            SIGNALS["charge_rate"]:  round(pack_current / _CAP_AH, 2),
        }

    def residuals(self, frame: dict) -> dict:
        return {SIGNALS["cell_v_delta"]: frame.get(SIGNALS["cell_v_delta"], 0.01)}

    def health_index(self, frame: dict) -> float:
        if not frame:
            return 1.0

        def hi(v, nominal, limit):
            return max(0.0, min(1.0, (limit - v) / (limit - nominal)))

        def lo(v, nominal, limit):
            return max(0.0, min(1.0, (v - limit) / (nominal - limit)))

        margins = [
            hi(frame.get(SIGNALS["cell_v_delta"], 0.01), 0.01, redlines.cell_v_delta_max),
            hi(frame.get(SIGNALS["cell_temp"], 30.0), 30.0, redlines.cell_temp_max),
            hi(frame.get(SIGNALS["cell_temp_rise"], 1.0), 1.0, redlines.cell_temp_rise_max),
            hi(frame.get(SIGNALS["pack_current"], 240.0), 240.0, redlines.pack_current_max),
            lo(frame.get(SIGNALS["soh"], 93.0), 93.0, redlines.soh_min),
            hi(frame.get(SIGNALS["coolant_temp"], 25.0), 25.0, redlines.coolant_max),
        ]
        return round(min(margins), 3)

    # ── live cell heatmap (P2-021) ──
    def network_state(self, state: BatteryState) -> dict:
        rng = random.Random(state.seed + 13)
        mean_temp = state.temp
        mean_v = soc_ocv(state.soc / 100.0)
        cells = []
        for m in range(_MODULES):
            for k in range(_CELLS_PER_MODULE):
                idx = m * _CELLS_PER_MODULE + k
                weak = idx == state.weak_cell
                dv = (rng.uniform(-0.006, 0.006)
                      + (0.09 * state.imbalance + 0.02 * state.runaway if weak else 0.0))
                dt_ = (rng.uniform(-1.5, 1.5)
                       + (10.0 * state.runaway + 4.0 * state.imbalance if weak else 0.0))
                v = round(mean_v + dv, 3)
                t = round(mean_temp + dt_, 1)
                soh_c = round(state.soh + rng.uniform(-1.5, 0.5) - (6.0 if weak and state.imbalance > 0.5 else 0.0), 1)
                status = ("critical" if (weak and (state.runaway > 0.4 or state.imbalance > 0.6))
                          else "warning" if abs(dv) > 0.03 or t > 45 else "ok")
                cells.append({"id": f"M{m + 1}-C{k + 1}", "module": m + 1, "row": m, "col": k,
                              "soc": round(state.soc + rng.uniform(-2, 2), 1), "soh": soh_c,
                              "voltage": v, "temp": t, "status": status})
        modules = [{"id": f"M{m + 1}", "index": m,
                    "avg_temp": round(sum(c["temp"] for c in cells if c["module"] == m + 1) / _CELLS_PER_MODULE, 1)}
                   for m in range(_MODULES)]
        return {"cells": cells, "modules": modules, "rows": _MODULES, "cols": _CELLS_PER_MODULE,
                "pack": {"soc": round(state.soc, 1), "soh": round(state.soh, 1),
                         "series": _SERIES, "capacity_ah": _CAP_AH}, "fault": state.fault}


# ── health rollup + prediction ──
def _status(h):
    return "critical" if h < 0.4 else "warning" if h < 0.72 else "ok"


def component_health(state, frame, physics) -> dict:
    delta = frame.get(SIGNALS["cell_v_delta"], 0.01)
    temp = frame.get(SIGNALS["cell_temp"], 30.0)
    rise = frame.get(SIGNALS["cell_temp_rise"], 1.0)
    soh = frame.get(SIGNALS["soh"], 93.0)
    cur = frame.get(SIGNALS["pack_current"], 150.0)
    coolant = frame.get(SIGNALS["coolant_temp"], 22.0)
    comp = {
        "cells_balance": max(0.0, 1.0 - max(0.0, delta - 0.01) / (redlines.cell_v_delta_max - 0.01)),
        "thermal": max(0.0, min(1.0, 1.0 - max(0.0, temp - 30.0) / (redlines.cell_temp_max - 30.0)
                       - max(0.0, coolant - 25.0) / (redlines.coolant_max - 25.0) * 0.5)),
        "safety": max(0.0, 1.0 - max(0.0, rise - 1.0) / (redlines.cell_temp_rise_max - 1.0)),
        "capacity": max(0.0, min(1.0, (soh - redlines.soh_min) / (93.0 - redlines.soh_min))),
        "power": max(0.0, min(1.0, 1.0 - max(0.0, cur - 240.0) / (redlines.pack_current_max - 240.0))),
    }
    out = {k: {"health": round(v, 3), "status": _status(v)} for k, v in comp.items()}
    overall = min(comp.values()) if comp else 1.0
    out["overall"] = {"health": round(overall, 3), "status": _status(overall)}
    return out


_GUARDS = [
    ("cell voltage imbalance", SIGNALS["cell_v_delta"], redlines.cell_v_delta_max, "above", "cells_balance"),
    ("cell temperature", SIGNALS["cell_temp"], redlines.cell_temp_max, "above", "thermal"),
    ("cell temperature rise", SIGNALS["cell_temp_rise"], redlines.cell_temp_rise_max, "above", "safety"),
    ("pack current", SIGNALS["pack_current"], redlines.pack_current_max, "above", "power"),
    ("state of health", SIGNALS["soh"], redlines.soh_min, "below", "capacity"),
    ("coolant temperature", SIGNALS["coolant_temp"], redlines.coolant_max, "above", "thermal"),
]


def predict(state, horizon_min: float = 120.0, points: int = 120, physics=None) -> dict:
    if physics is None:
        physics = EVBatteryPackPhysics()
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
            SIGNALS["cell_v_delta"]: frame[SIGNALS["cell_v_delta"]],
            SIGNALS["cell_temp"]: frame[SIGNALS["cell_temp"]],
            SIGNALS["soh"]: frame[SIGNALS["soh"]],
            SIGNALS["soc"]: frame[SIGNALS["soc"]],
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
                               "message": f"{label} reaches limit ({v:.3f}) at ~{t_min:.0f} min"})
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
                        message=f"{self._label} out of limits: {sample.value:.3f}{self._unit} {rel} {self._limit}{self._unit}",
                        confidence=1.0,
                        evidence={"value": sample.value, "limit": self._limit, "signal": sample.signal})]


# ── P2-010 · cell voltage imbalance ──
class CellVoltageImbalance(Behavior):
    behavior_id = "ev.cell_voltage_imbalance"
    tier = Tier.C
    watches = [SIGNALS["cell_v_delta"]]
    reads = ["max-min cell voltage delta"]
    emits = "Cell voltage imbalance"

    def __init__(self, limit=None):
        self._limit = limit if limit is not None else redlines.cell_v_delta_max
        self._latch = _Latch()

    def evaluate(self, sample, query):
        if not self._latch.rising(sample.entity_id, sample.value >= self._limit):
            return []
        return [Finding(behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
                        severity="warning",
                        message=f"Cell voltage imbalance {sample.value * 1000:.0f} mV ≥ {self._limit * 1000:.0f} mV — "
                                f"enable passive balancing and schedule a BMS inspection.",
                        confidence=0.9,
                        evidence={"delta_v": sample.value, "limit": self._limit,
                                  "action": "passive_balancing_bms", "signal": sample.signal})]


# ── P2-011 · thermal runaway precursor (P0) ──
class ThermalRunawayPrecursor(Behavior):
    behavior_id = "ev.thermal_runaway_precursor"
    tier = Tier.A
    watches = [SIGNALS["cell_temp_rise"]]
    reads = ["cell dT/dt", "cell voltage delta (collapse flag)"]
    emits = "Thermal runaway precursor (P0)"

    def __init__(self, rise_limit=None, collapse_v=0.09):
        self._rise = rise_limit if rise_limit is not None else redlines.cell_temp_rise_max
        self._collapse = collapse_v
        self._latch = _Latch()

    def evaluate(self, sample, query):
        delta = query.get_property(sample.tenant_id, sample.entity_id, "cellVoltageDelta", 0.0)
        try:
            collapse = float(delta) >= self._collapse
        except (TypeError, ValueError):
            collapse = False
        breach = sample.value >= self._rise and collapse
        if not self._latch.rising(sample.entity_id, breach):
            return []
        return [Finding(behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
                        severity="critical",
                        message=f"P0 — THERMAL RUNAWAY precursor: dT/dt {sample.value:.1f} °C/min ≥ "
                                f"{self._rise:.0f} with cell-voltage collapse. Disconnect charger, "
                                f"isolate module, put fire suppression on standby.",
                        confidence=1.0,
                        evidence={"dT_dt": sample.value, "priority": "P0",
                                  "action": "charger_disconnect_fire_standby", "signal": sample.signal})]


# ── P2-014 · SoH threshold ──
class SoHThreshold(Behavior):
    behavior_id = "ev.soh_threshold"
    tier = Tier.C
    watches = [SIGNALS["soh"]]
    reads = ["pack state of health"]
    emits = "Battery second-life threshold"

    def __init__(self, floor=None):
        self._floor = floor if floor is not None else redlines.soh_min
        self._latch = _Latch()

    def evaluate(self, sample, query):
        if not self._latch.rising(sample.entity_id, sample.value <= self._floor):
            return []
        return [Finding(behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
                        severity="warning",
                        message=f"Pack SoH {sample.value:.1f}% ≤ {self._floor:.0f}% — flag for second-life "
                                f"assessment and initiate a GoalCert battery-removal training event.",
                        confidence=0.95,
                        evidence={"soh": sample.value, "floor": self._floor,
                                  "action": "second_life_goalcert", "signal": sample.signal})]


def build_battery_registry() -> BehaviorRegistry:
    reg = BehaviorRegistry()
    reg.register(CellVoltageImbalance())
    reg.register(ThermalRunawayPrecursor())
    reg.register(SoHThreshold())
    reg.register(_HardLimit("ev.cell_hot", SIGNALS["cell_temp"], redlines.cell_temp_max,
                            "above", "Cell temperature", "°C"))
    reg.register(_HardLimit("ev.overcurrent", SIGNALS["pack_current"], redlines.pack_current_max,
                            "above", "Pack current", "A"))
    reg.register(_HardLimit("ev.coolant_hot", SIGNALS["coolant_temp"], redlines.coolant_max,
                            "above", "Coolant temperature", "°C", severity="warning"))
    return reg


SPEC = {
    "key": "ev-battery-pack",
    "label": "EV Battery Pack",
    "class_iri": "https://ontology.nextxr.io/v3/ev#BatteryPack",
    "control": "c_rate",
    "physics": EVBatteryPackPhysics,
    "predict": predict,
    "component_health": component_health,
    "build_registry": build_battery_registry,
    "signals": SIGNALS,
    "units": UNITS,
    "faults": FAULTS,
    "sensors": {
        SIGNALS["soc"]: ("State of Charge", "%"),
        SIGNALS["soh"]: ("State of Health", "%"),
        SIGNALS["cell_v_delta"]: ("Cell Voltage Delta", "V"),
        SIGNALS["pack_voltage"]: ("Pack Voltage", "V"),
        SIGNALS["pack_current"]: ("Pack Current", "A"),
        SIGNALS["cell_temp"]: ("Max Cell Temp", "°C"),
        SIGNALS["cell_temp_rise"]: ("Cell Temp Rise", "°C/min"),
        SIGNALS["coolant_temp"]: ("Coolant Temp", "°C"),
        SIGNALS["charge_rate"]: ("Charge Rate", "C"),
    },
    "subsystems": [
        ("cells_balance", "Cell Balancing"),
        ("thermal", "Thermal"),
        ("safety", "Safety"),
        ("capacity", "Capacity (SoH)"),
        ("power", "Power"),
    ],
    "checks": {
        SIGNALS["cell_v_delta"]: (redlines.cell_v_delta_max, "above"),
        SIGNALS["cell_temp"]: (redlines.cell_temp_max, "above"),
        SIGNALS["cell_temp_rise"]: (redlines.cell_temp_rise_max, "above"),
        SIGNALS["pack_current"]: (redlines.pack_current_max, "above"),
        SIGNALS["soh"]: (redlines.soh_min, "below"),
        SIGNALS["coolant_temp"]: (redlines.coolant_max, "above"),
    },
}

__all__ = ["EVBatteryPackPhysics", "SIGNALS", "UNITS", "redlines", "FAULTS",
           "component_health", "predict", "build_battery_registry", "SPEC"]
