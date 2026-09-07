"""
imaging.py — the hospital IMAGING-SUITE machine twin ("hospital-imaging").

Where the hospital-campus SPEC models a whole campus as aggregate KPIs, this
domain models the single-storey imaging suite rendered by the frontend's
walkable floor plan (frontend/src/panels/hospitalFloorPlan/) at EQUIPMENT
level: every fault targets one physical machine that exists in that scene, so
the 3-D view can light up the exact unit that failed.

Equipment modelled (ids match `eq` tags in the scene's assetPlacements.js):

    MRI-1            3T MRI (cryocooler loop, helium inventory)
    CT-1             CT scanner (X-ray tube thermal, detector calibration)
    AHU-N1/N2/EQ/S1/S2  air handling units (filter ΔP, airflow, fan bearing)
    AC-C1…AC-C5      consultant-room split ACs (refrigerant charge)
    UPS-EQ/UPS-S     UPS strings (SoC, battery capacity → runtime)
    SWG-EQ/SWG-S     switchboards (phase imbalance, supply source)
    PUMP-1           CHW pump (flow, vibration/seal)
    BLR-1            calorifier (hot-water supply temperature / legionella)
    GAS-MRI/GAS-CT   medical O2 line panels (HTM 02-01 pressure, reserve)
    MON-CANN/MON-CT  patient monitors (battery)

Only genuine machines carry faults — beds, wheelchairs, furniture and other
non-functional items are deliberately absent from this model.

Physics per unit is a first-order plant (packs/_core first_order_lag) driven
by scan workload, with fault parameters perturbing the physically meaningful
quantity (a clogged filter raises ΔP and starves airflow; a failed battery
string collapses runtime while SoC stays high; an O2 leak drains the reserve
and sags line pressure per the manifold curve). The suite-level signal frame
exposes the ALARM channel of each class (the worst unit), which is what a BMS
would trend; per-unit values are served by `network_state()`.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from behaviors.registry import Behavior, BehaviorRegistry, Finding, Tier

from packs._core.physics import (
    clamp,
    first_order_lag,
    jitter,
    margin_hi,
    margin_lo,
    run_rul,
    status_from_health,
    worst_health,
)

# ── signal catalogue ────────────────────────────────────────────────────────
SIGNALS = {
    "mri_coolant":  "hsi:mriCoolantTemp",     # °C  magnet coldhead loop
    "mri_helium":   "hsi:mriHeliumLevel",     # %
    "ct_tube":      "hsi:ctTubeTemp",         # °C  tube-oil temperature
    "ct_drift":     "hsi:ctDetectorDrift",    # HU  calibration offset
    "ahu_dp":       "hsi:ahuFilterDP",        # Pa  worst AHU filter ΔP
    "ahu_flow":     "hsi:ahuAirflow",         # %   worst AHU airflow of design
    "ahu_vib":      "hsi:ahuFanVibration",    # mm/s worst AHU fan bearing
    "room_temp":    "hsi:consultRoomTemp",    # °C  worst consult-room temperature
    "o2_pressure":  "hsi:o2LinePressure",     # kPa worst O2 panel line pressure
    "o2_reserve":   "hsi:o2Reserve",          # %   manifold reserve
    "ups_soc":      "hsi:upsSoC",             # %   worst UPS state of charge
    "ups_runtime":  "hsi:upsRuntime",         # min worst UPS backup runtime
    "mains":        "hsi:mainsSupply",        # 1/0 utility present
    "imbalance":    "hsi:phaseImbalance",     # %   worst switchboard imbalance
    "hot_water":    "hsi:hotWaterSupply",     # °C  calorifier flow temperature
    "pump_flow":    "hsi:pumpFlow",           # %   CHW pump flow of rated
    "pump_vib":     "hsi:pumpVibration",      # mm/s pump bearing/seal vibration
    "mon_battery":  "hsi:monitorBattery",     # %   worst patient-monitor battery
    "scan_util":    "hsi:scanUtilisation",    # %   imaging workload (control)
    "power_load":   "hsi:criticalPowerLoad",  # %   essential-bus loading
}

UNITS = {
    SIGNALS["mri_coolant"]: "°C", SIGNALS["mri_helium"]: "%",
    SIGNALS["ct_tube"]: "°C", SIGNALS["ct_drift"]: "HU",
    SIGNALS["ahu_dp"]: "Pa", SIGNALS["ahu_flow"]: "%", SIGNALS["ahu_vib"]: "mm/s",
    SIGNALS["room_temp"]: "°C",
    SIGNALS["o2_pressure"]: "kPa", SIGNALS["o2_reserve"]: "%",
    SIGNALS["ups_soc"]: "%", SIGNALS["ups_runtime"]: "min",
    SIGNALS["mains"]: "", SIGNALS["imbalance"]: "%",
    SIGNALS["hot_water"]: "°C",
    SIGNALS["pump_flow"]: "%", SIGNALS["pump_vib"]: "mm/s",
    SIGNALS["mon_battery"]: "%", SIGNALS["scan_util"]: "%",
    SIGNALS["power_load"]: "%",
}


@dataclass
class Redlines:
    mri_coolant_max: float = 28.0     # °C — cryocooler capacity ceiling
    mri_helium_min: float = 20.0      # %  — quench-risk floor (warn at 30)
    ct_tube_max: float = 70.0         # °C — tube-oil alarm
    ct_drift_max: float = 8.0         # HU — QA recalibration limit
    ahu_dp_max: float = 300.0         # Pa — filter change point
    ahu_flow_min: float = 65.0        # %  — hygienic airflow floor
    ahu_vib_max: float = 7.1          # mm/s — ISO 10816 zone C/D boundary
    room_temp_max: float = 28.0       # °C — clinical-room comfort ceiling
    o2_pressure_min: float = 340.0    # kPa — HTM 02-01 low-line alarm
    o2_reserve_min: float = 30.0      # %
    ups_runtime_min: float = 15.0     # min — MRI quench-protect ride-through
    ups_soc_min: float = 25.0         # %
    imbalance_max: float = 12.0       # %  — phase imbalance trip advisory
    hot_water_min: float = 45.0       # °C — legionella control band floor
    pump_flow_min: float = 55.0       # %
    pump_vib_max: float = 7.1         # mm/s
    mon_battery_min: float = 15.0     # %


redlines = Redlines()

# ── equipment register (ids match the floor-plan scene) ─────────────────────
EQUIPMENT = [
    {"id": "MRI-1",    "kind": "mri",            "label": "MRI 1 — 3T Scanner",     "room": "mri1",     "subsystem": "imaging"},
    {"id": "CT-1",     "kind": "ctscanner",      "label": "CT Scanner",             "room": "ct",       "subsystem": "imaging"},
    {"id": "AHU-N1",   "kind": "ahu",            "label": "AHU 1 (North Plant)",    "room": "plantN",   "subsystem": "hvac"},
    {"id": "AHU-N2",   "kind": "ahu",            "label": "AHU 2 (North Plant)",    "room": "plantN",   "subsystem": "hvac"},
    {"id": "AHU-EQ",   "kind": "ahu",            "label": "MRI Equipment AHU",      "room": "mriEquip", "subsystem": "hvac"},
    {"id": "AHU-S1",   "kind": "ahu",            "label": "AHU 3 (South Plant)",    "room": "plantS",   "subsystem": "hvac"},
    {"id": "AHU-S2",   "kind": "ahu",            "label": "AHU 4 (South Plant)",    "room": "plantS",   "subsystem": "hvac"},
    {"id": "AC-C1",    "kind": "splitac",        "label": "Consultant 1 AC",        "room": "c1",       "subsystem": "hvac"},
    {"id": "AC-C2",    "kind": "splitac",        "label": "Consultant 2 AC",        "room": "c2",       "subsystem": "hvac"},
    {"id": "AC-C3",    "kind": "splitac",        "label": "Consultant 3 AC",        "room": "c3",       "subsystem": "hvac"},
    {"id": "AC-C4",    "kind": "splitac",        "label": "Consultant 4 AC",        "room": "c4",       "subsystem": "hvac"},
    {"id": "AC-C5",    "kind": "splitac",        "label": "Consultant 5 AC",        "room": "c5",       "subsystem": "hvac"},
    {"id": "UPS-EQ",   "kind": "ups",            "label": "MRI UPS",                "room": "mriEquip", "subsystem": "power"},
    {"id": "UPS-S",    "kind": "ups",            "label": "Site UPS",               "room": "plantS",   "subsystem": "power"},
    {"id": "SWG-EQ",   "kind": "switchgear",     "label": "MRI Switchboard",        "room": "mriEquip", "subsystem": "power"},
    {"id": "SWG-S",    "kind": "switchgear",     "label": "Main Switchboard",       "room": "plantS",   "subsystem": "power"},
    {"id": "PUMP-1",   "kind": "pump",           "label": "CHW Pump",               "room": "plantN",   "subsystem": "hydronics"},
    {"id": "BLR-1",    "kind": "boiler",         "label": "Calorifier",             "room": "plantN",   "subsystem": "hydronics"},
    {"id": "GAS-MRI",  "kind": "gas",            "label": "O2 Line Panel — MRI",    "room": "mri1",     "subsystem": "medgas"},
    {"id": "GAS-CT",   "kind": "gas",            "label": "O2 Line Panel — CT",     "room": "ct",       "subsystem": "medgas"},
]

EQUIPMENT_BY_ID = {e["id"]: e for e in EQUIPMENT}

# Patient monitors are legitimate powered devices; keep them last so the list
# above stays grouped by plant. (Separate append keeps the diff readable.)
EQUIPMENT += [
    {"id": "MON-CANN", "kind": "patientmonitor", "label": "Monitor — Cannulation", "room": "cann", "subsystem": "devices"},
    {"id": "MON-CT",   "kind": "patientmonitor", "label": "Monitor — CT",          "room": "ct",   "subsystem": "devices"},
]
EQUIPMENT_BY_ID = {e["id"]: e for e in EQUIPMENT}

# ── fault catalogue — every fault targets one machine above ─────────────────
_FAULTS: dict[str, dict] = {
    "mri_chiller_fault": {
        "target": "MRI-1", "label": "MRI cryocooler / chilled-water fault",
        "description": "Chilled-water flow to the MRI coldhead collapses; magnet "
                       "coolant temperature climbs toward the 28 °C ceiling and "
                       "helium boil-off accelerates (quench precursor).",
        "signal": SIGNALS["mri_coolant"],
    },
    "mri_helium_leak": {
        "target": "MRI-1", "label": "MRI helium boil-off / leak",
        "description": "Cryostat helium inventory falls; below 30 % is a warning, "
                       "below 20 % scanning must stop pending a helium fill.",
        "signal": SIGNALS["mri_helium"],
    },
    "ct_tube_overtemp": {
        "target": "CT-1", "label": "CT tube cooling failure",
        "description": "Tube heat-exchanger degraded; anode/oil temperature rises "
                       "with scan duty toward the 70 °C interlock.",
        "signal": SIGNALS["ct_tube"],
    },
    "ct_detector_drift": {
        "target": "CT-1", "label": "CT detector calibration drift",
        "description": "Detector gain drifts; HU offset grows past the ±8 HU QA "
                       "limit — images bias until recalibration.",
        "signal": SIGNALS["ct_drift"],
    },
    "ahu_filter_clogged": {
        "target": "AHU-N1", "label": "AHU filter blocked",
        "description": "Final filter loads up; ΔP climbs past 300 Pa and supply "
                       "airflow starves the consult wing.",
        "signal": SIGNALS["ahu_dp"],
    },
    "ahu_fan_bearing": {
        "target": "AHU-S1", "label": "AHU supply-fan bearing wear",
        "description": "Fan bearing degrades; vibration climbs through ISO 10816 "
                       "zone C into D (7.1 mm/s) with airflow loss.",
        "signal": SIGNALS["ahu_vib"],
    },
    "splitac_refrigerant_leak": {
        "target": "AC-C3", "label": "Split-AC refrigerant leak",
        "description": "Consultant-room split AC loses charge; cooling capacity "
                       "collapses and the room drifts above 28 °C.",
        "signal": SIGNALS["room_temp"],
    },
    "medgas_o2_leak": {
        "target": "GAS-MRI", "label": "Medical O2 line leak",
        "description": "Pipeline leak downstream of the manifold; line pressure "
                       "sags below the HTM 02-01 340 kPa alarm and reserve drains.",
        "signal": SIGNALS["o2_pressure"],
    },
    "ups_battery_degraded": {
        "target": "UPS-EQ", "label": "UPS battery string failure",
        "description": "A battery string drops out; SoC reads normal but backup "
                       "runtime collapses below the 15 min MRI ride-through.",
        "signal": SIGNALS["ups_runtime"],
    },
    "mains_failure": {
        "target": "SWG-S", "label": "Utility mains failure",
        "description": "Incoming supply lost; the suite rides on UPS — SoC and "
                       "runtime run down until the mains returns.",
        "signal": SIGNALS["mains"],
    },
    "switchgear_phase_imbalance": {
        "target": "SWG-S", "label": "Switchboard phase imbalance",
        "description": "Loose connection / single-phase overload; current "
                       "imbalance climbs past 12 % (overheating risk).",
        "signal": SIGNALS["imbalance"],
    },
    "boiler_lockout": {
        "target": "BLR-1", "label": "Calorifier burner lockout",
        "description": "Burner locks out; hot-water flow temperature decays below "
                       "the 45 °C legionella control band.",
        "signal": SIGNALS["hot_water"],
    },
    "pump_seal_leak": {
        "target": "PUMP-1", "label": "CHW pump seal / cavitation",
        "description": "Mechanical seal passes; the pump cavitates — flow falls "
                       "and vibration rises.",
        "signal": SIGNALS["pump_flow"],
    },
    "monitor_battery_fault": {
        "target": "MON-CT", "label": "Patient-monitor battery fault",
        "description": "Monitor battery cell failure; charge drains toward the "
                       "15 % clinical alarm.",
        "signal": SIGNALS["mon_battery"],
    },
}

FAULTS = list(_FAULTS.keys())
FAULT_INFO = _FAULTS


# ── state ───────────────────────────────────────────────────────────────────
def _init_units() -> dict:
    u: dict[str, dict] = {}
    for e in EQUIPMENT:
        k = e["kind"]
        if k == "mri":
            u[e["id"]] = {"coolant": 19.0, "helium": 98.0, "chiller_eff": 1.0, "he_leak": 0.0}
        elif k == "ctscanner":
            u[e["id"]] = {"tube": 46.0, "cool_eff": 1.0, "drift": 1.2, "drift_rate": 0.0}
        elif k == "ahu":
            u[e["id"]] = {"dp": 95.0, "flow": 98.0, "vib": 2.2, "clog": 0.0, "bearing": 0.0}
        elif k == "splitac":
            u[e["id"]] = {"room": 22.4, "cap": 1.0}
        elif k == "ups":
            u[e["id"]] = {"soc": 100.0, "cap": 1.0}
        elif k == "switchgear":
            u[e["id"]] = {"imbalance": 2.0, "fault": 0.0}
        elif k == "pump":
            u[e["id"]] = {"flow": 97.0, "vib": 2.4, "seal": 0.0}
        elif k == "boiler":
            u[e["id"]] = {"temp": 58.0, "lockout": 0.0}
        elif k == "gas":
            u[e["id"]] = {"pressure": 415.0, "reserve": 92.0, "leak": 0.0}
        elif k == "patientmonitor":
            u[e["id"]] = {"batt": 95.0, "batt_fault": 0.0}
    return u


@dataclass
class ImagingState:
    scan_load: float = 0.55          # control 0..1 — imaging workload
    units: dict = field(default_factory=_init_units)
    mains_ok: bool = True
    hours: float = 0.0
    fault: str = "none"
    fault_severity: float = 0.0
    fault_target: str = ""
    seed: int = 21
    _rng: random.Random = field(default=None, repr=False, compare=False)

    def rng(self):
        if self._rng is None:
            self._rng = random.Random(self.seed)
        return self._rng


# ── physics ─────────────────────────────────────────────────────────────────
class ImagingSuitePhysics:
    """Per-equipment first-order plant models for the imaging suite."""

    def __init__(self, **options):
        self.opts = options or {}

    def init_state(self) -> ImagingState:
        return ImagingState()

    # ── fault injection / clear ──
    def inject(self, state: ImagingState, fault: str, severity: float = 0.85) -> None:
        info = _FAULTS.get(fault)
        if info is None:
            return
        eff = clamp(float(severity))
        state.fault = fault
        state.fault_severity = eff
        state.fault_target = info["target"]
        u = state.units.get(info["target"], {})
        if fault == "mri_chiller_fault":
            u["chiller_eff"] = min(u.get("chiller_eff", 1.0), 1.0 - 0.8 * eff)
        elif fault == "mri_helium_leak":
            u["he_leak"] = max(u.get("he_leak", 0.0), eff)
        elif fault == "ct_tube_overtemp":
            u["cool_eff"] = min(u.get("cool_eff", 1.0), 1.0 - 0.85 * eff)
        elif fault == "ct_detector_drift":
            u["drift_rate"] = max(u.get("drift_rate", 0.0), 0.035 * eff)
        elif fault == "ahu_filter_clogged":
            u["clog"] = max(u.get("clog", 0.0), eff)
        elif fault == "ahu_fan_bearing":
            u["bearing"] = max(u.get("bearing", 0.0), eff)
        elif fault == "splitac_refrigerant_leak":
            u["cap"] = min(u.get("cap", 1.0), 1.0 - 0.9 * eff)
        elif fault == "medgas_o2_leak":
            u["leak"] = max(u.get("leak", 0.0), eff)
        elif fault == "ups_battery_degraded":
            u["cap"] = min(u.get("cap", 1.0), 1.0 - 0.75 * eff)
        elif fault == "mains_failure":
            state.mains_ok = False
        elif fault == "switchgear_phase_imbalance":
            u["fault"] = max(u.get("fault", 0.0), eff)
        elif fault == "boiler_lockout":
            u["lockout"] = max(u.get("lockout", 0.0), eff)
        elif fault == "pump_seal_leak":
            u["seal"] = max(u.get("seal", 0.0), eff)
        elif fault == "monitor_battery_fault":
            u["batt_fault"] = max(u.get("batt_fault", 0.0), eff)

    def clear(self, state: ImagingState) -> None:
        """Repair complete: restore every unit to its healthy baseline."""
        state.fault = "none"
        state.fault_severity = 0.0
        state.fault_target = ""
        state.mains_ok = True
        state.units = _init_units()

    # ── forward step ──
    def forward(self, state: ImagingState, dt: float = 1.0) -> dict:
        rng = state.rng()
        S = clamp(state.scan_load)
        state.hours += dt / 3600.0
        U = state.units

        for e in EQUIPMENT:
            u = U[e["id"]]
            k = e["kind"]
            if k == "mri":
                target = 18.0 + 3.5 * S + 14.5 * (1.0 - u["chiller_eff"])
                u["coolant"] = first_order_lag(u["coolant"], target, tau=75.0, dt=dt)
                boiloff = 0.0004 + 0.45 * u["he_leak"] \
                    + (0.02 if u["coolant"] > redlines.mri_coolant_max else 0.0)
                u["helium"] = max(0.0, u["helium"] - boiloff * dt)
            elif k == "ctscanner":
                duty = max(S, 0.35)
                target = 38.0 + 24.0 * duty + 55.0 * (1.0 - u["cool_eff"]) * duty
                u["tube"] = first_order_lag(u["tube"], target, tau=55.0, dt=dt)
                u["drift"] = min(15.0, u["drift"] + u["drift_rate"] * dt)
            elif k == "ahu":
                u["dp"] = first_order_lag(u["dp"], 88.0 + 18.0 * S + 275.0 * u["clog"], tau=40.0, dt=dt)
                u["flow"] = first_order_lag(u["flow"], 100.0 - 32.0 * u["clog"] - 14.0 * u["bearing"], tau=35.0, dt=dt)
                u["vib"] = first_order_lag(u["vib"], 2.2 + 7.6 * u["bearing"], tau=25.0, dt=dt)
            elif k == "splitac":
                target = 21.8 + 1.4 * S + 7.0 * (1.0 - u["cap"])
                u["room"] = first_order_lag(u["room"], target, tau=110.0, dt=dt)
            elif k == "ups":
                if state.mains_ok:
                    u["soc"] = min(100.0, u["soc"] + 0.03 * dt)
                else:
                    u["soc"] = max(0.0, u["soc"] - 0.055 * dt)   # ~3.3 %/min on battery
            elif k == "switchgear":
                u["imbalance"] = first_order_lag(u["imbalance"], 2.0 + 12.5 * u["fault"], tau=18.0, dt=dt)
            elif k == "pump":
                u["flow"] = first_order_lag(u["flow"], 97.0 - 45.0 * u["seal"], tau=30.0, dt=dt)
                u["vib"] = first_order_lag(u["vib"], 2.4 + 6.8 * u["seal"], tau=25.0, dt=dt)
            elif k == "boiler":
                target = 24.0 if u["lockout"] > 0.3 else 58.0 - 3.0 * S
                u["temp"] = first_order_lag(u["temp"], target, tau=140.0, dt=dt)
            elif k == "gas":
                u["reserve"] = max(0.0, u["reserve"] - (0.0009 + 0.05 * u["leak"]) * dt)
                p_target = 415.0 - 22.0 * S - 95.0 * u["leak"] \
                    - max(0.0, 30.0 - u["reserve"]) * 2.5
                u["pressure"] = first_order_lag(u["pressure"], p_target, tau=22.0, dt=dt)
            elif k == "patientmonitor":
                if u["batt_fault"] > 0.2:
                    u["batt"] = max(0.0, u["batt"] - 0.35 * u["batt_fault"] * dt)
                else:
                    u["batt"] = min(95.0, u["batt"] + 0.05 * dt)

        # ── suite-level frame: the alarm channel of each class (worst unit) ──
        ahus = [U[e["id"]] for e in EQUIPMENT if e["kind"] == "ahu"]
        acs = [U[e["id"]] for e in EQUIPMENT if e["kind"] == "splitac"]
        upss = [U[e["id"]] for e in EQUIPMENT if e["kind"] == "ups"]
        swgs = [U[e["id"]] for e in EQUIPMENT if e["kind"] == "switchgear"]
        gass = [U[e["id"]] for e in EQUIPMENT if e["kind"] == "gas"]
        mons = [U[e["id"]] for e in EQUIPMENT if e["kind"] == "patientmonitor"]
        mri = U["MRI-1"]
        ct = U["CT-1"]
        pump = U["PUMP-1"]
        blr = U["BLR-1"]

        worst_ups = min(upss, key=lambda u: u["soc"] * u["cap"])
        runtime = 45.0 * worst_ups["cap"] * (worst_ups["soc"] / 100.0)
        power_load = 46.0 + 30.0 * S + (10.0 if not state.mains_ok else 0.0)

        j = lambda v, f=0.006: jitter(rng, v, f)  # noqa: E731 — small sensor noise
        frame = {
            SIGNALS["mri_coolant"]: round(j(mri["coolant"]), 2),
            SIGNALS["mri_helium"]: round(mri["helium"], 2),
            SIGNALS["ct_tube"]: round(j(ct["tube"]), 2),
            SIGNALS["ct_drift"]: round(ct["drift"], 2),
            SIGNALS["ahu_dp"]: round(j(max(a["dp"] for a in ahus)), 1),
            SIGNALS["ahu_flow"]: round(j(min(a["flow"] for a in ahus)), 1),
            SIGNALS["ahu_vib"]: round(j(max(a["vib"] for a in ahus)), 2),
            SIGNALS["room_temp"]: round(j(max(a["room"] for a in acs)), 2),
            SIGNALS["o2_pressure"]: round(j(min(g["pressure"] for g in gass)), 1),
            SIGNALS["o2_reserve"]: round(min(g["reserve"] for g in gass), 1),
            SIGNALS["ups_soc"]: round(worst_ups["soc"], 1),
            SIGNALS["ups_runtime"]: round(runtime, 1),
            SIGNALS["mains"]: 1.0 if state.mains_ok else 0.0,
            SIGNALS["imbalance"]: round(j(max(s["imbalance"] for s in swgs)), 2),
            SIGNALS["hot_water"]: round(j(blr["temp"]), 1),
            SIGNALS["pump_flow"]: round(j(pump["flow"]), 1),
            SIGNALS["pump_vib"]: round(j(pump["vib"]), 2),
            SIGNALS["mon_battery"]: round(min(m["batt"] for m in mons), 1),
            SIGNALS["scan_util"]: round(S * 100.0, 1),
            SIGNALS["power_load"]: round(j(power_load), 1),
        }
        return frame

    # ── health ──
    def _unit_health(self, state: ImagingState, eq: dict) -> float:
        u = state.units[eq["id"]]
        k = eq["kind"]
        if k == "mri":
            h = min(margin_hi(u["coolant"], 23.0, 30.0), margin_lo(u["helium"], 60.0, 18.0))
        elif k == "ctscanner":
            h = min(margin_hi(u["tube"], 60.0, 73.0), margin_hi(u["drift"], 4.0, 9.0))
        elif k == "ahu":
            h = min(margin_hi(u["dp"], 180.0, 320.0), margin_hi(u["vib"], 4.0, 7.6),
                    margin_lo(u["flow"], 85.0, 60.0))
        elif k == "splitac":
            h = margin_hi(u["room"], 25.0, 28.6)
        elif k == "ups":
            rt = 45.0 * u["cap"] * (u["soc"] / 100.0)
            h = min(margin_lo(rt, 28.0, 11.0), margin_lo(u["soc"], 60.0, 18.0))
        elif k == "switchgear":
            h = margin_hi(u["imbalance"], 6.0, 13.0)
            if not state.mains_ok:
                h = min(h, 0.5)
        elif k == "pump":
            h = min(margin_lo(u["flow"], 82.0, 50.0), margin_hi(u["vib"], 4.0, 7.6))
        elif k == "boiler":
            h = margin_lo(u["temp"], 52.0, 42.0)
        elif k == "gas":
            h = min(margin_lo(u["pressure"], 392.0, 332.0), margin_lo(u["reserve"], 55.0, 24.0))
        elif k == "patientmonitor":
            h = margin_lo(u["batt"], 50.0, 12.0)
        else:
            h = 1.0
        # An actively-faulted unit shows distress immediately, before the plant
        # dynamics have fully evolved — the alarm relay closes at once.
        if state.fault != "none" and state.fault_target == eq["id"]:
            h = min(h, 1.0 - 0.5 * state.fault_severity)
        return clamp(h)

    def health_index(self, frame: dict) -> float:
        r = redlines
        return worst_health([
            margin_hi(frame.get(SIGNALS["mri_coolant"], 19.0), 23.0, r.mri_coolant_max + 2.0),
            margin_lo(frame.get(SIGNALS["mri_helium"], 98.0), 60.0, r.mri_helium_min - 2.0),
            margin_hi(frame.get(SIGNALS["ct_tube"], 46.0), 60.0, r.ct_tube_max + 3.0),
            margin_hi(frame.get(SIGNALS["ct_drift"], 1.2), 4.0, r.ct_drift_max + 1.0),
            margin_hi(frame.get(SIGNALS["ahu_dp"], 95.0), 180.0, r.ahu_dp_max + 20.0),
            margin_hi(frame.get(SIGNALS["ahu_vib"], 2.2), 4.0, r.ahu_vib_max + 0.5),
            margin_hi(frame.get(SIGNALS["room_temp"], 22.4), 25.0, r.room_temp_max + 0.6),
            margin_lo(frame.get(SIGNALS["o2_pressure"], 415.0), 392.0, r.o2_pressure_min - 8.0),
            margin_lo(frame.get(SIGNALS["ups_runtime"], 45.0), 28.0, r.ups_runtime_min - 4.0),
            margin_hi(frame.get(SIGNALS["imbalance"], 2.0), 6.0, r.imbalance_max + 1.0),
            margin_lo(frame.get(SIGNALS["hot_water"], 58.0), 52.0, r.hot_water_min - 3.0),
            margin_lo(frame.get(SIGNALS["pump_flow"], 97.0), 82.0, r.pump_flow_min - 5.0),
            margin_lo(frame.get(SIGNALS["mon_battery"], 95.0), 50.0, r.mon_battery_min - 3.0),
        ])

    def residuals(self, frame: dict) -> dict:
        return {
            "coolant_margin": redlines.mri_coolant_max - frame.get(SIGNALS["mri_coolant"], 19.0),
            "o2_margin": frame.get(SIGNALS["o2_pressure"], 415.0) - redlines.o2_pressure_min,
            "runtime_margin": frame.get(SIGNALS["ups_runtime"], 45.0) - redlines.ups_runtime_min,
        }

    # ── per-equipment map for the 3-D floor plan ──
    def network_state(self, state: ImagingState) -> dict:
        out = []
        for e in EQUIPMENT:
            u = state.units[e["id"]]
            h = self._unit_health(state, e)
            k = e["kind"]
            if k == "mri":
                metrics = [("Coolant", round(u["coolant"], 1), "°C"), ("Helium", round(u["helium"], 1), "%")]
            elif k == "ctscanner":
                metrics = [("Tube", round(u["tube"], 1), "°C"), ("Drift", round(u["drift"], 1), "HU")]
            elif k == "ahu":
                metrics = [("Filter ΔP", round(u["dp"], 0), "Pa"), ("Airflow", round(u["flow"], 0), "%"),
                           ("Vibration", round(u["vib"], 1), "mm/s")]
            elif k == "splitac":
                metrics = [("Room", round(u["room"], 1), "°C")]
            elif k == "ups":
                metrics = [("SoC", round(u["soc"], 0), "%"),
                           ("Runtime", round(45.0 * u["cap"] * u["soc"] / 100.0, 0), "min")]
            elif k == "switchgear":
                metrics = [("Imbalance", round(u["imbalance"], 1), "%"),
                           ("Source", "mains" if state.mains_ok else "UPS", "")]
            elif k == "pump":
                metrics = [("Flow", round(u["flow"], 0), "%"), ("Vibration", round(u["vib"], 1), "mm/s")]
            elif k == "boiler":
                metrics = [("Flow temp", round(u["temp"], 1), "°C")]
            elif k == "gas":
                metrics = [("Pressure", round(u["pressure"], 0), "kPa"), ("Reserve", round(u["reserve"], 0), "%")]
            elif k == "patientmonitor":
                metrics = [("Battery", round(u["batt"], 0), "%")]
            else:
                metrics = []
            active = state.fault if (state.fault != "none" and state.fault_target == e["id"]) else None
            out.append({
                "id": e["id"], "kind": k, "label": e["label"], "room": e["room"],
                "subsystem": e["subsystem"], "health": round(h, 3),
                "status": status_from_health(h),
                "fault": active,
                "fault_label": _FAULTS[active]["label"] if active else None,
                "metrics": [{"label": m[0], "value": m[1], "unit": m[2]} for m in metrics],
            })
        return {
            "equipment": out,
            "fault": state.fault, "fault_target": state.fault_target,
            "fault_info": _FAULTS.get(state.fault),
            "mains_ok": state.mains_ok,
        }


# ── behaviours ──────────────────────────────────────────────────────────────
class _Limit(Behavior):
    """Edge-latched hard limit with a domain message and selectable severity."""
    tier = Tier.C

    def __init__(self, behavior_id, signal, limit, direction, label, unit,
                 severity="critical", action=""):
        self.behavior_id = behavior_id
        self.watches = [signal]
        self.reads = [f"{label} vs limit"]
        self.emits = f"{label} out of limits"
        self._limit, self._dir, self._label = limit, direction, label
        self._unit, self._sev, self._action = unit, severity, action
        self._on: dict[str, bool] = {}

    def evaluate(self, sample, query) -> list:
        breach = (sample.value >= self._limit if self._dir == "above"
                  else sample.value <= self._limit)
        prev = self._on.get(sample.entity_id, False)
        self._on[sample.entity_id] = breach
        if not (breach and not prev):
            return []
        rel = "≥" if self._dir == "above" else "≤"
        msg = (f"{self._label}: {sample.value:.1f} {self._unit} {rel} "
               f"{self._limit:.0f} {self._unit}")
        if self._action:
            msg += f" — {self._action}"
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
            severity=self._sev, message=msg, confidence=1.0,
            evidence={"value": sample.value, "limit": self._limit,
                      "signal": sample.signal, "unit": self._unit})]


def build_imaging_registry() -> BehaviorRegistry:
    r = redlines
    reg = BehaviorRegistry()
    rules = [
        ("hsi.mri_coolant_high", SIGNALS["mri_coolant"], r.mri_coolant_max, "above",
         "MRI magnet coolant", "°C", "critical",
         "cryocooler/CHW fault — suspend scanning, check chilled-water flow (quench risk)"),
        ("hsi.mri_helium_warn", SIGNALS["mri_helium"], 30.0, "below",
         "MRI helium level", "%", "warning",
         "schedule helium fill; investigate boil-off rate"),
        ("hsi.mri_helium_low", SIGNALS["mri_helium"], r.mri_helium_min, "below",
         "MRI helium level", "%", "critical",
         "stop scanning — helium fill required before further use"),
        ("hsi.ct_tube_hot", SIGNALS["ct_tube"], r.ct_tube_max, "above",
         "CT tube temperature", "°C", "critical",
         "pause scan queue; check tube heat exchanger and coolant pump"),
        ("hsi.ct_detector_drift", SIGNALS["ct_drift"], r.ct_drift_max, "above",
         "CT detector drift", "HU", "warning",
         "run air calibration; QA phantom before next patient"),
        ("hsi.ahu_filter_dp", SIGNALS["ahu_dp"], r.ahu_dp_max, "above",
         "AHU filter ΔP", "Pa", "warning",
         "replace final filter; verify design airflow after change"),
        ("hsi.ahu_airflow_low", SIGNALS["ahu_flow"], r.ahu_flow_min, "below",
         "AHU supply airflow", "%", "critical",
         "hygienic airflow floor breached in served rooms"),
        ("hsi.ahu_vibration", SIGNALS["ahu_vib"], r.ahu_vib_max, "above",
         "AHU fan vibration", "mm/s", "critical",
         "ISO 10816 zone D — take fan offline, inspect bearing"),
        ("hsi.room_overtemp", SIGNALS["room_temp"], r.room_temp_max, "above",
         "Consult-room temperature", "°C", "warning",
         "split-AC underperforming — check refrigerant charge and condensate"),
        ("hsi.medgas_low", SIGNALS["o2_pressure"], r.o2_pressure_min, "below",
         "Medical O2 line pressure", "kPa", "critical",
         "P0 — HTM 02-01 low-line alarm: isolate leak, switch to reserve bank"),
        ("hsi.gas_reserve_low", SIGNALS["o2_reserve"], r.o2_reserve_min, "below",
         "Medical O2 reserve", "%", "warning",
         "order cylinder bank changeover"),
        ("hsi.ups_runtime_low", SIGNALS["ups_runtime"], r.ups_runtime_min, "below",
         "UPS backup runtime", "min", "critical",
         "below MRI ride-through — test/replace battery string"),
        ("hsi.ups_on_battery", SIGNALS["mains"], 0.5, "below",
         "Utility mains supply", "", "critical",
         "site on UPS — start generator / restore incoming supply"),
        ("hsi.phase_imbalance", SIGNALS["imbalance"], r.imbalance_max, "above",
         "Switchboard phase imbalance", "%", "critical",
         "thermographic inspection; check connections and single-phase loads"),
        ("hsi.hot_water_low", SIGNALS["hot_water"], r.hot_water_min, "below",
         "Hot-water flow temperature", "°C", "warning",
         "legionella control band breached — reset calorifier burner"),
        ("hsi.pump_flow_low", SIGNALS["pump_flow"], r.pump_flow_min, "below",
         "CHW pump flow", "%", "critical",
         "seal/cavitation — imaging cooling at risk; changeover to standby"),
        ("hsi.pump_vibration", SIGNALS["pump_vib"], r.pump_vib_max, "above",
         "CHW pump vibration", "mm/s", "warning",
         "inspect mechanical seal and impeller"),
        ("hsi.monitor_battery", SIGNALS["mon_battery"], r.mon_battery_min, "below",
         "Patient-monitor battery", "%", "critical",
         "swap monitor / replace battery before transport use"),
    ]
    for rid, sig, lim, direction, label, unit, sev, action in rules:
        reg.register(_Limit(rid, sig, lim, direction, label, unit, sev, action))
    return reg


# ── health rollup + prediction ──────────────────────────────────────────────
_SUBSYSTEMS = [
    ("imaging", "Imaging (MRI + CT)"),
    ("hvac", "HVAC (AHUs + Split ACs)"),
    ("medgas", "Medical Gas"),
    ("power", "Power (UPS + Switchboards)"),
    ("hydronics", "Hydronics (Pump + Calorifier)"),
    ("devices", "Clinical Devices"),
]


def component_health(state, frame, physics) -> dict:
    by_sub: dict[str, list[float]] = {}
    for e in EQUIPMENT:
        by_sub.setdefault(e["subsystem"], []).append(physics._unit_health(state, e))
    out = {}
    for key, _ in _SUBSYSTEMS:
        h = min(by_sub.get(key, [1.0]))
        out[key] = {"health": round(h, 3), "status": status_from_health(h)}
    overall = min((v["health"] for v in out.values()), default=1.0)
    out["overall"] = {"health": round(overall, 3), "status": status_from_health(overall)}
    return out


_GUARDS = [
    ("MRI coolant", SIGNALS["mri_coolant"], redlines.mri_coolant_max, "above", "imaging"),
    ("MRI helium", SIGNALS["mri_helium"], redlines.mri_helium_min, "below", "imaging"),
    ("CT tube temperature", SIGNALS["ct_tube"], redlines.ct_tube_max, "above", "imaging"),
    ("AHU filter ΔP", SIGNALS["ahu_dp"], redlines.ahu_dp_max, "above", "hvac"),
    ("AHU vibration", SIGNALS["ahu_vib"], redlines.ahu_vib_max, "above", "hvac"),
    ("consult-room temperature", SIGNALS["room_temp"], redlines.room_temp_max, "above", "hvac"),
    ("O2 line pressure", SIGNALS["o2_pressure"], redlines.o2_pressure_min, "below", "medgas"),
    ("UPS runtime", SIGNALS["ups_runtime"], redlines.ups_runtime_min, "below", "power"),
    ("phase imbalance", SIGNALS["imbalance"], redlines.imbalance_max, "above", "power"),
    ("hot-water temperature", SIGNALS["hot_water"], redlines.hot_water_min, "below", "hydronics"),
    ("pump flow", SIGNALS["pump_flow"], redlines.pump_flow_min, "below", "hydronics"),
    ("monitor battery", SIGNALS["mon_battery"], redlines.mon_battery_min, "below", "devices"),
]


def predict(state, horizon_min: float = 120.0, points: int = 120, physics=None) -> dict:
    if physics is None:
        physics = ImagingSuitePhysics()
    return run_rul(state, physics, _GUARDS, horizon_min=horizon_min, points=points,
                   traj_signals=[SIGNALS["mri_coolant"], SIGNALS["o2_pressure"],
                                 SIGNALS["ahu_dp"], SIGNALS["ups_runtime"]])


# ── SPEC ────────────────────────────────────────────────────────────────────
SPEC = {
    "key": "hospital-imaging",
    "label": "Hospital Imaging Suite",
    "class_iri": "https://ontology.nextxr.io/v3/hospital#Hospital",
    "control": "scan_load",
    "physics": ImagingSuitePhysics,
    "predict": predict,
    "component_health": component_health,
    "build_registry": build_imaging_registry,
    "signals": SIGNALS,
    "units": UNITS,
    "faults": FAULTS,
    "fault_info": {k: {"label": v["label"], "target": v["target"],
                       "description": v["description"], "signal": v["signal"]}
                   for k, v in _FAULTS.items()},
    "equipment": EQUIPMENT,
    "sensors": {
        SIGNALS["mri_coolant"]: ("MRI Magnet Coolant", "°C"),
        SIGNALS["mri_helium"]: ("MRI Helium Level", "%"),
        SIGNALS["ct_tube"]: ("CT Tube Temperature", "°C"),
        SIGNALS["ct_drift"]: ("CT Detector Drift", "HU"),
        SIGNALS["ahu_dp"]: ("AHU Filter ΔP (worst)", "Pa"),
        SIGNALS["ahu_flow"]: ("AHU Airflow (worst)", "%"),
        SIGNALS["ahu_vib"]: ("AHU Fan Vibration (worst)", "mm/s"),
        SIGNALS["room_temp"]: ("Consult Room Temp (worst)", "°C"),
        SIGNALS["o2_pressure"]: ("O2 Line Pressure", "kPa"),
        SIGNALS["o2_reserve"]: ("O2 Reserve", "%"),
        SIGNALS["ups_soc"]: ("UPS State of Charge", "%"),
        SIGNALS["ups_runtime"]: ("UPS Backup Runtime", "min"),
        SIGNALS["mains"]: ("Utility Mains", ""),
        SIGNALS["imbalance"]: ("Phase Imbalance (worst)", "%"),
        SIGNALS["hot_water"]: ("Hot Water Flow Temp", "°C"),
        SIGNALS["pump_flow"]: ("CHW Pump Flow", "%"),
        SIGNALS["pump_vib"]: ("CHW Pump Vibration", "mm/s"),
        SIGNALS["mon_battery"]: ("Monitor Battery (worst)", "%"),
        SIGNALS["scan_util"]: ("Scan Utilisation", "%"),
        SIGNALS["power_load"]: ("Critical Power Load", "%"),
    },
    "subsystems": _SUBSYSTEMS,
    "checks": {
        SIGNALS["mri_coolant"]: (redlines.mri_coolant_max, "above"),
        SIGNALS["mri_helium"]: (redlines.mri_helium_min, "below"),
        SIGNALS["ct_tube"]: (redlines.ct_tube_max, "above"),
        SIGNALS["ct_drift"]: (redlines.ct_drift_max, "above"),
        SIGNALS["ahu_dp"]: (redlines.ahu_dp_max, "above"),
        SIGNALS["ahu_flow"]: (redlines.ahu_flow_min, "below"),
        SIGNALS["ahu_vib"]: (redlines.ahu_vib_max, "above"),
        SIGNALS["room_temp"]: (redlines.room_temp_max, "above"),
        SIGNALS["o2_pressure"]: (redlines.o2_pressure_min, "below"),
        SIGNALS["o2_reserve"]: (redlines.o2_reserve_min, "below"),
        SIGNALS["ups_runtime"]: (redlines.ups_runtime_min, "below"),
        SIGNALS["imbalance"]: (redlines.imbalance_max, "above"),
        SIGNALS["hot_water"]: (redlines.hot_water_min, "below"),
        SIGNALS["pump_flow"]: (redlines.pump_flow_min, "below"),
        SIGNALS["pump_vib"]: (redlines.pump_vib_max, "above"),
        SIGNALS["mon_battery"]: (redlines.mon_battery_min, "below"),
    },
}
