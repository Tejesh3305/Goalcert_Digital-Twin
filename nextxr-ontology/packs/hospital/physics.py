"""
hospital/physics.py — a hospital-campus forward model with live clinical views.

Two layers:

  1. Component physics engines (faithful, unit-testable):
        hospital_hvac_pressure()      — multi-zone pressure cascade (OR +15 Pa,
                                        isolation −8 Pa, corridor buffer) + AHU
                                        failure rerouting
        medical_gas_hydraulics()      — O2/N2O pipeline drop (Hagen-Poiseuille) +
                                        manifold bank switching (HTM 02-01)
        sterilisation_f0()            — autoclave F0 lethality (trapezoidal)
        cold_chain_thermal()          — fridge/freezer excursion (Newton cooling +
                                        door events + compressor duty)
        infection_spread_wellsriley() — quanta concentration + infection probability
        power_resilience()            — UPS discharge curve + generator auto-start
                                        (NFPA 110 10-second rule)
        legionella_thermal()          — hot/cold water control band + dead-legs
        patient_flow_queuing()        — ED/bed demand via Little's Law (L = λ·W)

  2. HospitalCampusPhysics — the campus twin. Departments, beds, medical-gas
     manifolds/zones, water, power and patient flow drive ~22 aggregate KPIs.
     `network_state(state)` returns the bed board, OR schedule, patient-flow
     funnel, infection-map zones and the medical-gas schematic (the frontend).

Same machine-twin interface (init_state / forward / inject / residuals /
health_index) plus network_state.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from packs._core.physics import jitter

# ────────────────────────────────────────────────────────────────────
#  Signals
# ────────────────────────────────────────────────────────────────────
SIGNALS = {
    # clinical environment
    "or_pressure":    "hsp:orPressure",
    "iso_pressure":   "hsp:isoPressure",
    "air_changes":    "hsp:airChanges",
    "autoclave_f0":   "hsp:autoclaveF0",
    # medical gas
    "o2_pressure":    "hsp:medGasO2Pressure",
    "n2o_pressure":   "hsp:medGasN2OPressure",
    "gas_reserve":    "hsp:medGasReserve",
    # cold chain
    "bloodbank_temp": "hsp:bloodBankTemp",
    # infection control
    "infection_risk": "hsp:infectionRisk",
    "quanta":         "hsp:quantaConcentration",
    # power resilience
    "ups_soc":        "hsp:upsStateOfCharge",
    "ups_runtime":    "hsp:upsRuntime",
    "gen_ready":      "hsp:generatorReady",
    "power_load":     "hsp:criticalPowerLoad",
    # water safety
    "hot_water":      "hsp:hotWaterReturnTemp",
    "cold_water":     "hsp:coldWaterSupplyTemp",
    "dead_legs":      "hsp:legionellaDeadLegs",
    # patient flow
    "ed_arrivals":    "hsp:edArrivalRate",
    "ed_wait":        "hsp:edWaitTime",
    "patients":       "hsp:patientsInSystem",
    "bed_occ":        "hsp:bedOccupancy",
    "or_util":        "hsp:orUtilisation",
}

UNITS = {
    SIGNALS["or_pressure"]: "Pa", SIGNALS["iso_pressure"]: "Pa", SIGNALS["air_changes"]: "ACH",
    SIGNALS["autoclave_f0"]: "min", SIGNALS["o2_pressure"]: "kPa", SIGNALS["n2o_pressure"]: "kPa",
    SIGNALS["gas_reserve"]: "%", SIGNALS["bloodbank_temp"]: "DEG_C", SIGNALS["infection_risk"]: "%",
    SIGNALS["quanta"]: "q/m3", SIGNALS["ups_soc"]: "%", SIGNALS["ups_runtime"]: "min",
    SIGNALS["gen_ready"]: "", SIGNALS["power_load"]: "%", SIGNALS["hot_water"]: "DEG_C",
    SIGNALS["cold_water"]: "DEG_C", SIGNALS["dead_legs"]: "", SIGNALS["ed_arrivals"]: "/h",
    SIGNALS["ed_wait"]: "min", SIGNALS["patients"]: "", SIGNALS["bed_occ"]: "%", SIGNALS["or_util"]: "%",
}


@dataclass
class Redlines:
    or_pressure_min: float = 8.0        # Pa (JCI immediate action below)
    iso_pressure_max: float = -2.5      # Pa — isolation must stay ≤ this (negative)
    air_changes_min: float = 15.0       # ACH (OR minimum)
    autoclave_f0_min: float = 15.0      # min lethality for sterility assurance
    o2_pressure_min: float = 350.0      # kPa (HTM 02-01)
    n2o_pressure_min: float = 320.0     # kPa
    gas_reserve_min: float = 25.0       # %
    bloodbank_max: float = 6.0          # °C
    infection_risk_max: float = 5.0     # % probability
    ups_soc_min: float = 30.0           # %
    ups_runtime_min: float = 15.0       # min
    power_load_max: float = 90.0        # % critical bus
    hot_water_min: float = 50.0         # °C (HTM 04-01 hot return)
    cold_water_max: float = 20.0        # °C (HTM 04-01 cold supply)
    dead_legs_max: float = 2.0
    ed_wait_max: float = 240.0          # min (4-hour target)
    bed_occ_max: float = 92.0           # %


redlines = Redlines()


# ════════════════════════════════════════════════════════════════════
#  1.  COMPONENT PHYSICS ENGINES
# ════════════════════════════════════════════════════════════════════

def hospital_hvac_pressure(ahu_capacity: float = 1.0, or_setpoint: float = 15.0,
                           iso_setpoint: float = -8.0, corridor: float = 0.0,
                           door_open: float = 0.0, ach_design: float = 22.0) -> dict:
    """Multi-zone pressure cascade for a clinical suite.

    The operating theatre is held positive (≈+15 Pa) and the isolation room
    negative (≈−8 Pa) relative to a corridor buffer (0 Pa), so air flows OR →
    corridor → isolation and never the reverse. Zone pressures and air-change rate
    scale with available AHU capacity; an AHU failure reroutes/loses supply and
    the cascade collapses toward the corridor (P3-003).
    """
    cap = max(0.0, min(1.2, ahu_capacity))
    or_p = or_setpoint * cap * (1.0 - 0.5 * door_open)
    iso_p = iso_setpoint * cap * (1.0 - 0.4 * door_open)
    ach = ach_design * cap
    cascade_intact = (or_p > corridor + 4.0) and (iso_p < corridor - 2.5)
    return {"or_pressure": or_p, "iso_pressure": iso_p, "corridor_pressure": corridor,
            "air_changes": ach, "cascade_intact": cascade_intact}


def medical_gas_hydraulics(demand_lpm: float = 200.0, pipe_len_m: float = 120.0,
                           pipe_dia_mm: float = 28.0, manifold_pressure_kpa: float = 410.0,
                           reserve_pct: float = 90.0, leak: float = 0.0,
                           viscosity: float = 1.9e-5) -> dict:
    """Medical-gas pipeline pressure drop with manifold bank switching.

    Laminar pipe loss follows Hagen-Poiseuille ΔP = 128·μ·L·Q/(π·d⁴). The manifold
    regulator holds outlet pressure near nominal while either cylinder bank has
    reserve, switching banks as one empties; once reserve is nearly gone (or a leak
    develops) the outlet falls below the HTM 02-01 low-pressure setpoint (P3-004).
    """
    q = max(0.0, demand_lpm) / 60000.0          # m³/s
    d = pipe_dia_mm / 1000.0
    dp_pa = 128.0 * viscosity * pipe_len_m * q / (math.pi * d ** 4)
    dp_kpa = dp_pa / 1000.0
    bank_factor = 1.0 if reserve_pct > 15.0 else max(0.15, reserve_pct / 15.0)
    outlet = (manifold_pressure_kpa * bank_factor - dp_kpa) * (1.0 - 0.65 * max(0.0, min(1.0, leak)))
    return {"outlet_pressure_kpa": outlet, "pressure_drop_kpa": dp_kpa,
            "bank_switch": reserve_pct <= 25.0, "low_pressure": outlet < 350.0}


def sterilisation_f0(temp_profile, z_value: float = 10.0, ref_temp: float = 121.1,
                     dt_min: float = 1.0) -> dict:
    """Autoclave cycle lethality (F0) by trapezoidal integration of the lethal
    rate L = 10^((T−121.1)/z) over the measured temperature profile (P3-005). F0 is
    the equivalent minutes at 121.1 °C; ≥15 min gives a sterility assurance level."""
    if not temp_profile:
        return {"f0": 0.0, "sterile": False, "peak_rate": 0.0}
    rates = [10.0 ** ((t - ref_temp) / z_value) for t in temp_profile]
    f0 = 0.0
    for i in range(1, len(rates)):
        f0 += 0.5 * (rates[i - 1] + rates[i]) * dt_min
    return {"f0": f0, "sterile": f0 >= 15.0, "peak_rate": max(rates)}


def cold_chain_thermal(temp_c: float, ambient_c: float = 22.0, setpoint_c: float = 4.0,
                       ua: float = 0.02, cool_k_per_min: float = 0.6,
                       door_open: bool = False, compressor_duty: float = 1.0,
                       dt_min: float = 1.0) -> dict:
    """Refrigerated-store temperature by Newton's law of cooling.

    Ambient conduction warms the box (UA·ΔT), door-open events add a heat pulse,
    and the compressor cools while duty allows; a degraded compressor duty starves
    cooling and the box drifts over-temperature (P3-006).
    """
    warm = ua * (ambient_c - temp_c) * dt_min
    door = 3.0 * dt_min if door_open else 0.0
    cooling = cool_k_per_min * max(0.0, compressor_duty) * (1.0 if temp_c > setpoint_c else 0.2) * dt_min
    new_temp = temp_c + warm + door - cooling
    return {"temperature": new_temp, "warm_rate": warm, "excursion": new_temp > 6.0}


def infection_spread_wellsriley(infectors: float = 1.0, quanta_per_hour: float = 48.0,
                                breathing_m3ph: float = 0.48, ventilation_ach: float = 6.0,
                                volume_m3: float = 120.0, exposure_h: float = 1.0,
                                occupants: int = 12) -> dict:
    """Airborne infection risk in a shared airspace (Wells-Riley).

    Steady-state quanta concentration = I·q/Q where Q = ACH·volume (fresh-air
    supply, m³/h). The probability a susceptible is infected over the exposure is
    P = 1 − exp(−concentration·breathing·time). Lower ventilation (an AHU fault)
    raises both concentration and probability (P3-007).
    """
    q_supply = max(1.0, ventilation_ach * volume_m3)
    quanta_conc = infectors * quanta_per_hour / q_supply
    inhaled = quanta_conc * breathing_m3ph * exposure_h
    p_inf = 1.0 - math.exp(-inhaled)
    return {"quanta_concentration": quanta_conc, "infection_probability": p_inf * 100.0,
            "expected_cases": p_inf * max(0, occupants - infectors)}


def power_resilience(mains_ok: bool, ups_soc_pct: float, critical_load_kw: float = 150.0,
                     ups_capacity_kwh: float = 200.0, gen_start_delay_s: float = 8.0,
                     gen_ready: bool = True, elapsed_outage_s: float = 0.0) -> dict:
    """UPS discharge + standby-generator auto-start.

    On mains loss the UPS carries the critical load; runtime = (SoC·capacity)/load.
    The standby generator should reach rated speed within the NFPA 110 10-second
    window — modelled by `gen_start_delay_s` and `gen_ready`; if it does not, the
    UPS keeps draining (P3-008).
    """
    energy_kwh = max(0.0, ups_soc_pct) / 100.0 * ups_capacity_kwh
    runtime_min = energy_kwh / max(1.0, critical_load_kw) * 60.0
    gen_running = (not mains_ok) and gen_ready and elapsed_outage_s >= gen_start_delay_s
    on_battery = (not mains_ok) and not gen_running
    nfpa110_ok = mains_ok or gen_running or elapsed_outage_s < 10.0
    return {"ups_runtime_min": runtime_min, "generator_running": gen_running,
            "on_battery": on_battery, "nfpa110_ok": nfpa110_ok}


def legionella_thermal(hot_return_c: float = 55.0, cold_supply_c: float = 15.0,
                       flush_overdue: int = 0) -> dict:
    """Water-system Legionella-control temperatures (HTM 04-01).

    The control band keeps hot-water return ≥50 °C and cold-water supply ≤20 °C.
    Temperatures inside the 20–45 °C growth range, plus overdue thermal flushes,
    raise the number of dead-legs at risk of colonisation (P3-009).
    """
    risk_hot = hot_return_c < 50.0
    risk_cold = cold_supply_c > 20.0
    dead_legs = int(flush_overdue) + (3 if risk_hot else 0) + (3 if risk_cold else 0)
    return {"dead_legs_at_risk": dead_legs, "hot_ok": not risk_hot,
            "cold_ok": not risk_cold, "colonisation_risk": risk_hot or risk_cold}


def patient_flow_queuing(arrival_rate_ph: float = 8.0, avg_los_h: float = 3.5,
                         capacity: int = 70, servers: int = 6,
                         service_rate_ph: float = 2.0, base_wait_min: float = 25.0) -> dict:
    """ED / bed demand via Little's Law (L = λ·W).

    The number in the system L equals the arrival rate λ times the average time in
    system W (the LOS) — this drives bed occupancy (L/capacity). The ED *queue*
    wait is separate: it inflates from a base wait as server utilisation
    ρ = λ/(c·μ) approaches 1 (M/M/c congestion) (P3-010).
    """
    L = arrival_rate_ph * avg_los_h
    rho = arrival_rate_ph / max(1e-6, servers * service_rate_ph)
    if rho < 1.0:
        wait_min = base_wait_min * (1.0 + max(0.0, rho - 0.5) * 6.0)
    else:
        wait_min = base_wait_min * 12.0
    occupancy = min(130.0, 100.0 * L / max(1, capacity))
    return {"in_system": L, "utilisation": rho, "wait_min": wait_min,
            "occupancy_pct": occupancy, "bottleneck": rho > 0.85}


# ════════════════════════════════════════════════════════════════════
#  2.  CAMPUS GEOMETRY  (departments, beds, gas topology)
# ════════════════════════════════════════════════════════════════════
_ZONES = [
    {"id": "OR1", "name": "Theatre 1", "dept": "Theatres", "x": 22, "y": 22, "kind": "or"},
    {"id": "OR2", "name": "Theatre 2", "dept": "Theatres", "x": 38, "y": 22, "kind": "or"},
    {"id": "ICU", "name": "ICU", "dept": "ICU", "x": 60, "y": 22, "kind": "icu"},
    {"id": "ED", "name": "Emergency", "dept": "Emergency", "x": 22, "y": 50, "kind": "ed"},
    {"id": "RAD", "name": "Radiology", "dept": "Radiology", "x": 42, "y": 50, "kind": "rad"},
    {"id": "PHARM", "name": "Pharmacy", "dept": "Pharmacy", "x": 62, "y": 50, "kind": "pharm"},
    {"id": "LAB", "name": "Laboratory", "dept": "Laboratory", "x": 82, "y": 50, "kind": "lab"},
    {"id": "WA", "name": "Ward A", "dept": "Ward", "x": 28, "y": 80, "kind": "ward"},
    {"id": "WB", "name": "Ward B", "dept": "Ward", "x": 54, "y": 80, "kind": "ward"},
    {"id": "ISO", "name": "Isolation", "dept": "Ward", "x": 80, "y": 80, "kind": "iso"},
]
_WARDS = [("WA", "Ward A", 12), ("WB", "Ward B", 12), ("ICU", "ICU", 8)]
_THEATRES = [("OR1", "Theatre 1"), ("OR2", "Theatre 2")]
_GAS_MANIFOLDS = [("O2", "O2 Manifold", "O2"), ("N2O", "N2O Manifold", "N2O"),
                  ("AIR", "Med-Air Plant", "MedAir")]
_GAS_ZONES = [("Z-OR", "Theatres", "O2"), ("Z-ICU", "ICU", "O2"),
              ("Z-ED", "Emergency", "O2"), ("Z-WARD", "Wards", "O2")]
_FLOW_STAGES = ["Triage", "Assessment", "Treatment", "Admission", "Discharge"]


# ════════════════════════════════════════════════════════════════════
#  3.  CAMPUS TWIN
# ════════════════════════════════════════════════════════════════════
@dataclass
class HospitalState:
    patient_load: float = 0.55           # control 0..1 (campus demand level)
    ahu_capacity: float = 1.0            # OR/clinical AHU available fraction
    door_open: float = 0.0
    o2_reserve: float = 90.0             # %
    o2_leak: float = 0.0                 # 0..1
    bloodbank_temp: float = 4.0          # °C
    coldchain_fault: float = 0.0         # 0..1 lost compressor duty
    mains_ok: bool = True
    outage_s: float = 0.0
    ups_soc: float = 100.0               # %
    gen_ready: bool = True
    gen_fault: float = 0.0               # 0..1
    hot_water: float = 55.0              # °C
    cold_water: float = 15.0             # °C
    flush_overdue: int = 0
    infectors: float = 0.0
    autoclave_derate: float = 0.0        # 0..1 lost sterilisation temperature
    fault_target: str = ""               # zone/dept the active fault localises to
    hours: float = 0.0
    fault: str = "none"
    fault_severity: float = 0.0
    seed: int = 13
    _rng: random.Random = field(default=None, repr=False, compare=False)

    def rng(self):
        if self._rng is None:
            self._rng = random.Random(self.seed)
        return self._rng


_FAULTS = {
    "or_ahu_fault":      {"ahu_capacity": 0.45, "_target": "OR1"},
    "medical_gas_leak":  {"o2_leak": 0.85, "o2_reserve": 35.0, "_target": "OR1"},
    "cold_chain_fault":  {"coldchain_fault": 0.9, "_target": "PHARM"},
    "mains_failure":     {"_mains_off": True},
    "generator_fail":    {"_mains_off": True, "gen_fault": 1.0},
    "legionella_risk":   {"hot_water": -12.0, "cold_water": 9.0},
    "infection_outbreak": {"infectors": 3.0, "_target": "ISO"},
    "autoclave_fault":   {"autoclave_derate": 0.9},
    "ed_surge":          {"patient_load": 0.9},
}

FAULTS = list(_FAULTS.keys())


class HospitalCampusPhysics:
    def __init__(self, **options):
        self.opts = options or {}

    def init_state(self) -> HospitalState:
        return HospitalState()

    # ── fault injection / clear ──
    def inject(self, state: HospitalState, fault: str, severity: float = 0.85) -> None:
        eff = max(0.0, min(1.0, float(severity)))
        state.fault = fault
        state.fault_severity = eff
        spec = _FAULTS.get(fault, {})
        for attr, amount in spec.items():
            if attr == "_mains_off":
                state.mains_ok = False
                state.outage_s = 0.0
            elif attr == "_target":
                state.fault_target = amount
            elif attr == "ahu_capacity":
                state.ahu_capacity = min(state.ahu_capacity, 1.0 - (1.0 - amount) * eff)
            elif attr == "o2_reserve":
                state.o2_reserve = min(state.o2_reserve, amount)
            elif attr == "hot_water":
                state.hot_water += amount * eff
            elif attr == "cold_water":
                state.cold_water += amount * eff
            elif attr == "patient_load":
                state.patient_load = max(state.patient_load, amount * eff)
            elif attr in ("o2_leak", "coldchain_fault", "gen_fault", "autoclave_derate"):
                setattr(state, attr, min(1.2, getattr(state, attr) + amount * eff))
            elif attr == "infectors":
                state.infectors = max(state.infectors, amount * eff)

    def clear(self, state: HospitalState) -> None:
        state.fault = "none"
        state.fault_severity = 0.0
        state.fault_target = ""
        state.ahu_capacity = 1.0
        state.door_open = 0.0
        state.o2_leak = 0.0
        state.o2_reserve = 90.0
        state.coldchain_fault = 0.0
        state.mains_ok = True
        state.outage_s = 0.0
        state.gen_fault = 0.0
        state.hot_water = 55.0
        state.cold_water = 15.0
        state.infectors = 0.0
        state.autoclave_derate = 0.0

    # ── the forward step ──
    def forward(self, state: HospitalState, dt: float = 1.0) -> dict:
        rng = state.rng()
        P = max(0.0, min(1.0, state.patient_load))
        state.hours += dt / 3600.0
        dt_min = dt / 60.0

        # ── clinical environment (HVAC cascade + sterilisation) ──
        hvac = hospital_hvac_pressure(ahu_capacity=state.ahu_capacity, door_open=state.door_open)
        or_pressure = hvac["or_pressure"]
        iso_pressure = hvac["iso_pressure"]
        air_changes = hvac["air_changes"]
        hold_temp = 122.6 - 8.0 * state.autoclave_derate
        f0 = sterilisation_f0([hold_temp] * 15)["f0"]

        # ── medical gas ──
        demand = 150.0 + 220.0 * P
        state.o2_reserve = max(0.0, state.o2_reserve - dt / 3600.0 * (2.0 + 30.0 * state.o2_leak))
        mg = medical_gas_hydraulics(demand_lpm=demand, reserve_pct=state.o2_reserve, leak=state.o2_leak)
        o2_pressure = mg["outlet_pressure_kpa"]
        n2o = medical_gas_hydraulics(demand_lpm=60.0 + 40.0 * P, manifold_pressure_kpa=380.0,
                                     reserve_pct=state.o2_reserve, leak=state.o2_leak * 0.4)
        n2o_pressure = n2o["outlet_pressure_kpa"]

        # ── cold chain ──
        cc = cold_chain_thermal(state.bloodbank_temp, compressor_duty=1.0 - state.coldchain_fault,
                                door_open=(rng.random() < 0.02), dt_min=dt_min)
        state.bloodbank_temp = max(1.0, min(20.0, cc["temperature"]))

        # ── infection control (ward airspace) ──
        ward_ach = max(2.0, 6.0 * state.ahu_capacity)
        inf = infection_spread_wellsriley(infectors=state.infectors, ventilation_ach=ward_ach,
                                          occupants=int(10 + 8 * P))
        infection_risk = inf["infection_probability"]
        quanta = inf["quanta_concentration"]

        # ── power resilience ──
        critical_load_kw = 120.0 + 90.0 * P
        if state.mains_ok:
            state.outage_s = 0.0
            state.ups_soc = min(100.0, state.ups_soc + 0.15 * dt)
        else:
            state.outage_s += dt
        pr = power_resilience(state.mains_ok, state.ups_soc, critical_load_kw=critical_load_kw,
                              gen_ready=(state.gen_fault < 0.5), elapsed_outage_s=state.outage_s)
        if pr["on_battery"]:
            drain = critical_load_kw / 200.0 * (dt / 3600.0) * 100.0
            state.ups_soc = max(0.0, state.ups_soc - drain)
        ups_runtime = state.ups_soc / 100.0 * 200.0 / max(1.0, critical_load_kw) * 60.0
        gen_ready_sig = 0.0 if (not state.mains_ok and not pr["generator_running"]) else 1.0
        power_load = min(130.0, 100.0 * critical_load_kw / 250.0 + (12.0 if pr["on_battery"] else 0.0))

        # ── water safety ──
        leg = legionella_thermal(state.hot_water, state.cold_water, flush_overdue=state.flush_overdue)
        dead_legs = leg["dead_legs_at_risk"]

        # ── patient flow (Little's Law) ──
        arrivals = 4.0 + 12.0 * P
        pf = patient_flow_queuing(arrival_rate_ph=arrivals, avg_los_h=3.2 + 1.5 * P)
        ed_wait = pf["wait_min"]
        patients = pf["in_system"]
        bed_occ = pf["occupancy_pct"]
        or_util = min(100.0, 55.0 + 32.0 * P)

        def j(v, frac):
            return jitter(rng, v, frac)

        return {
            SIGNALS["or_pressure"]:    round(j(or_pressure, 0.02), 1),
            SIGNALS["iso_pressure"]:   round(j(iso_pressure, 0.02), 1),
            SIGNALS["air_changes"]:    round(max(0.0, j(air_changes, 0.02)), 1),
            SIGNALS["autoclave_f0"]:   round(max(0.0, f0), 1),
            SIGNALS["o2_pressure"]:    round(max(0.0, j(o2_pressure, 0.004)), 0),
            SIGNALS["n2o_pressure"]:   round(max(0.0, j(n2o_pressure, 0.004)), 0),
            SIGNALS["gas_reserve"]:    round(state.o2_reserve, 1),
            SIGNALS["bloodbank_temp"]: round(j(state.bloodbank_temp, 0.01), 2),
            SIGNALS["infection_risk"]: round(max(0.0, infection_risk), 2),
            SIGNALS["quanta"]:         round(max(0.0, quanta), 4),
            SIGNALS["ups_soc"]:        round(state.ups_soc, 1),
            SIGNALS["ups_runtime"]:    round(min(999.0, ups_runtime), 1),
            SIGNALS["gen_ready"]:      gen_ready_sig,
            SIGNALS["power_load"]:     round(j(power_load, 0.01), 1),
            SIGNALS["hot_water"]:      round(j(state.hot_water, 0.005), 1),
            SIGNALS["cold_water"]:     round(j(state.cold_water, 0.005), 1),
            SIGNALS["dead_legs"]:      int(dead_legs),
            SIGNALS["ed_arrivals"]:    round(j(arrivals, 0.02), 1),
            SIGNALS["ed_wait"]:        round(max(0.0, j(ed_wait, 0.02)), 1),
            SIGNALS["patients"]:       round(patients, 1),
            SIGNALS["bed_occ"]:        round(j(bed_occ, 0.01), 1),
            SIGNALS["or_util"]:        round(j(or_util, 0.02), 1),
        }

    def residuals(self, frame: dict) -> dict:
        return {
            SIGNALS["or_pressure"]: frame.get(SIGNALS["or_pressure"], 15.0) - 15.0,
            SIGNALS["o2_pressure"]: frame.get(SIGNALS["o2_pressure"], 410.0) - 410.0,
        }

    def health_index(self, frame: dict) -> float:
        if not frame:
            return 1.0

        def hi(v, nominal, limit):
            return max(0.0, min(1.0, (limit - v) / (limit - nominal)))

        def lo(v, nominal, limit):
            return max(0.0, min(1.0, (v - limit) / (nominal - limit)))

        margins = [
            lo(frame.get(SIGNALS["or_pressure"], 15.0), 15.0, redlines.or_pressure_min),
            hi(frame.get(SIGNALS["iso_pressure"], -8.0), -8.0, redlines.iso_pressure_max),
            lo(frame.get(SIGNALS["air_changes"], 22.0), 22.0, redlines.air_changes_min),
            lo(frame.get(SIGNALS["autoclave_f0"], 18.0), 18.0, redlines.autoclave_f0_min),
            lo(frame.get(SIGNALS["o2_pressure"], 405.0), 405.0, redlines.o2_pressure_min),
            lo(frame.get(SIGNALS["gas_reserve"], 90.0), 90.0, redlines.gas_reserve_min),
            hi(frame.get(SIGNALS["bloodbank_temp"], 4.0), 4.0, redlines.bloodbank_max),
            hi(frame.get(SIGNALS["infection_risk"], 0.4), 0.4, redlines.infection_risk_max),
            lo(frame.get(SIGNALS["ups_runtime"], 70.0), 70.0, redlines.ups_runtime_min),
            frame.get(SIGNALS["gen_ready"], 1.0),
            lo(frame.get(SIGNALS["hot_water"], 55.0), 55.0, redlines.hot_water_min),
            hi(frame.get(SIGNALS["cold_water"], 15.0), 15.0, redlines.cold_water_max),
            hi(frame.get(SIGNALS["dead_legs"], 0), 0, redlines.dead_legs_max),
            hi(frame.get(SIGNALS["ed_wait"], 90.0), 90.0, redlines.ed_wait_max),
            hi(frame.get(SIGNALS["bed_occ"], 70.0), 70.0, redlines.bed_occ_max),
        ]
        return round(min(margins), 3)

    # ── live clinical views (the 5 hospital frontends) ──
    def _beds(self, state: HospitalState) -> list:
        rng = random.Random(state.seed + 71)
        occ = min(0.99, 0.55 + 0.4 * state.patient_load)
        beds = []
        for wid, wname, n in _WARDS:
            targeted = state.fault_target == wid or (wid == "ICU" and state.fault_target == "ISO")
            for i in range(n):
                r = rng.random()
                if r < occ:
                    status, patient = "occupied", f"P-{rng.randint(1000, 9999)}"
                elif r < occ + 0.12:
                    status, patient = "cleaning", None
                elif targeted and r < occ + 0.2:
                    status, patient = "blocked", None
                else:
                    status, patient = "available", None
                beds.append({"id": f"{wid}-{i + 1:02d}", "ward": wname, "bed": i + 1,
                             "status": status, "patient": patient,
                             "los_h": round(rng.uniform(4, 96), 1) if status == "occupied" else None})
        return beds

    def _or_schedule(self, state: HospitalState) -> list:
        rng = random.Random(state.seed + 51)
        out = []
        for tid, tname in _THEATRES:
            cases = []
            slot = 8.0  # 08:00
            booked_total, actual_total = 0.0, 0.0
            for c in range(rng.randint(3, 4)):
                dur = round(rng.uniform(1.0, 2.5), 1)
                turnover = round(rng.uniform(0.3, 0.8), 1)
                overrun = rng.uniform(-0.2, 0.6) + (0.6 if state.fault_target == tid else 0.0)
                actual = max(0.4, round(dur + overrun, 1))
                cases.append({"id": f"{tid}-C{c + 1}", "booked_start": f"{int(slot):02d}:{int((slot % 1) * 60):02d}",
                              "duration_h": dur, "actual_h": actual, "turnover_h": turnover,
                              "status": "in_progress" if c == 1 else "booked" if c > 1 else "done"})
                booked_total += dur + turnover
                actual_total += actual + turnover
                slot += actual + turnover
            util = min(100.0, 100.0 * actual_total / 10.0)   # over a 10h list
            predicted = min(100.0, util + rng.uniform(-6, 8))
            out.append({"theatre": tname, "id": tid, "utilisation": round(util, 0),
                        "predicted": round(predicted, 0), "cases": cases})
        return out

    def _patient_flow(self, state: HospitalState, frame: dict) -> dict:
        P = state.patient_load
        arrivals = frame.get(SIGNALS["ed_arrivals"], 8.0)
        base = int(20 + 60 * P)
        # a decreasing funnel; the bottleneck stage swells when demand is high
        counts = [base, int(base * 0.78), int(base * 0.6), int(base * 0.42), int(base * 0.3)]
        waits = [8, 25, 40 + 60 * P, 55 + 90 * P, 20]
        bottleneck = "Treatment" if P > 0.7 else "Assessment" if P > 0.5 else "Triage"
        return {
            "arrival_rate": round(arrivals, 1),
            "in_system": frame.get(SIGNALS["patients"]),
            "avg_wait_min": frame.get(SIGNALS["ed_wait"]),
            "bottleneck": bottleneck,
            "stages": [{"name": _FLOW_STAGES[i], "count": counts[i], "wait_min": round(waits[i], 0)}
                       for i in range(len(_FLOW_STAGES))],
        }

    def _zones(self, state: HospitalState, frame: dict) -> list:
        rng = random.Random(state.seed + 31)
        or_p = frame.get(SIGNALS["or_pressure"], 15.0)
        iso_p = frame.get(SIGNALS["iso_pressure"], -8.0)
        base_risk = frame.get(SIGNALS["infection_risk"], 0.4)
        out = []
        for z in _ZONES:
            targeted = state.fault_target == z["id"]
            if z["kind"] == "or":
                pressure, ach = or_p, frame.get(SIGNALS["air_changes"], 22.0)
            elif z["kind"] == "iso":
                pressure, ach = iso_p, max(6.0, frame.get(SIGNALS["air_changes"], 22.0) * 0.5)
            else:
                pressure, ach = 0.0 + rng.uniform(-1, 1), 6.0
            risk = base_risk * (2.4 if (z["kind"] in ("iso", "ward") or targeted) else 0.5) \
                + (rng.uniform(0, 0.4)) + (3.0 if targeted and state.fault == "infection_outbreak" else 0.0)
            if z["kind"] == "or" and or_p < redlines.or_pressure_min:
                status = "critical"
            elif risk >= redlines.infection_risk_max:
                status = "critical"
            elif risk >= redlines.infection_risk_max * 0.6:
                status = "warning"
            else:
                status = "ok"
            out.append({"id": z["id"], "name": z["name"], "dept": z["dept"],
                        "x": z["x"], "y": z["y"], "kind": z["kind"],
                        "infection_prob": round(risk, 2), "pressure": round(pressure, 1),
                        "ach": round(ach, 1), "status": status,
                        "recommend_closure": risk >= redlines.infection_risk_max})
        return out

    def _medical_gas(self, state: HospitalState, frame: dict) -> dict:
        o2 = frame.get(SIGNALS["o2_pressure"], 405.0)
        n2o = frame.get(SIGNALS["n2o_pressure"], 375.0)
        reserve = frame.get(SIGNALS["gas_reserve"], 90.0)

        def gstatus(p, floor):
            return "critical" if p < floor else "warning" if p < floor * 1.08 else "ok"

        manifolds = []
        for mid, mname, gas in _GAS_MANIFOLDS:
            p = o2 if gas == "O2" else n2o if gas == "N2O" else 480.0
            floor = redlines.o2_pressure_min if gas == "O2" else redlines.n2o_pressure_min if gas == "N2O" else 400.0
            manifolds.append({"id": mid, "name": mname, "gas": gas, "pressure_kpa": round(p, 0),
                              "reserve_pct": round(reserve if gas != "MedAir" else 100.0, 0),
                              "status": gstatus(p, floor)})
        zones = []
        for zid, zname, gas in _GAS_ZONES:
            targeted = state.fault_target in ("OR1", "OR2") and zid == "Z-OR"
            p = (o2 if gas == "O2" else n2o) - (25.0 if targeted else 0.0)
            zones.append({"id": zid, "name": zname, "gas": gas, "pressure_kpa": round(p, 0),
                          "status": gstatus(p, redlines.o2_pressure_min)})
        return {"manifolds": manifolds, "zones": zones,
                "links": [["O2", z[0]] for z in _GAS_ZONES]}

    def network_state(self, state: HospitalState) -> dict:
        frame = self.forward_readonly(state)
        return {
            "beds": self._beds(state),
            "or_schedule": self._or_schedule(state),
            "patient_flow": self._patient_flow(state, frame),
            "zones": self._zones(state, frame),
            "medical_gas": self._medical_gas(state, frame),
            "fault": state.fault,
            "fault_target": state.fault_target,
        }

    def forward_readonly(self, state: HospitalState) -> dict:
        """A non-mutating snapshot frame for the view builders (avoids advancing
        the persistent state twice per tick)."""
        import copy
        st = copy.deepcopy(state)
        st._rng = random.Random(state.seed + 999)
        return self.forward(st, dt=0.0001)
