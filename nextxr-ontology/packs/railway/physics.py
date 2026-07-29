"""
railway/physics.py — an urban metro (MRT) network forward model with a live map.

Two layers live here:

  1. Component physics engines (faithful first-principles models), each usable in
     isolation and unit-testable:
        traction_power_flow()     — 750 V DC third-rail Kirchhoff power flow
        train_dynamics()          — Davis resistance + adhesion + ETCS brake curve
        station_hvac_load()       — psychrometric sensible/latent station cooling
        escalator_motor_model()   — escalator motor current / torque / thermal rise
        rail_thermal_expansion()  — CWR rail stress vs neutral temperature
        wheel_rail_contact()      — Hertzian contact stress + Nadal derailment Q/P
        passenger_los_model()     — platform Level of Service (Fruin/HCM) + dwell

  2. RailwayPhysics — the network twin. A small fixed metro map (3 lines, 9
     stations with interchanges, depots, traction substations) with trains that
     advance along their line polylines each tick. The component engines above
     drive ~25 aggregate network KPIs. `network_state(state)` returns the geometry
     plus live train positions and per-station live KPIs (the frontend map).

Same interface contract as the other machine domains (init_state / forward /
inject / residuals / health_index) plus network_state.
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
    # operations
    "otp":                  "rail:onTimePerformance",
    "headway":              "rail:headwayAdherence",
    "avg_speed":            "rail:networkSpeed",
    "train_avail":          "rail:trainAvailability",
    "in_service":           "rail:trainsInService",
    "load_factor":          "rail:loadFactor",
    "dwell":                "rail:dwellTime",
    "delay":                "rail:networkDelay",
    "incidents":            "rail:activeIncidents",
    # traction power
    "traction_power":       "rail:tractionPower",
    "regen":                "rail:regenShare",
    "third_rail_v":         "rail:thirdRailVoltage",
    "sub_load":             "rail:substationLoad",
    "traction_temp":        "rail:tractionMotorTemp",
    # permanent way
    "rail_temp":            "rail:railTemperature",
    "rail_stress":          "rail:railStress",
    "wheel_qp":             "rail:derailmentQuotient",
    "bogie_vib":            "rail:bogieVibration",
    # signalling
    "track_circuit_faults": "rail:trackCircuitFaults",
    "signal_faults":        "rail:signalFaults",
    # station services
    "psd_faults":           "rail:psdFaults",
    "escalator_temp":       "rail:escalatorMotorTemp",
    "hvac_load":            "rail:stationHvacLoad",
    "hvac_dp":              "rail:platformHvacDifferential",
    "station_los":          "rail:platformLOS",
}

UNITS = {
    SIGNALS["otp"]: "%", SIGNALS["headway"]: "%", SIGNALS["avg_speed"]: "km/h",
    SIGNALS["train_avail"]: "%", SIGNALS["in_service"]: "", SIGNALS["load_factor"]: "%",
    SIGNALS["dwell"]: "s", SIGNALS["delay"]: "min", SIGNALS["incidents"]: "",
    SIGNALS["traction_power"]: "MW", SIGNALS["regen"]: "%", SIGNALS["third_rail_v"]: "V",
    SIGNALS["sub_load"]: "%", SIGNALS["traction_temp"]: "DEG_C",
    SIGNALS["rail_temp"]: "DEG_C", SIGNALS["rail_stress"]: "MPa",
    SIGNALS["wheel_qp"]: "", SIGNALS["bogie_vib"]: "mm/s",
    SIGNALS["track_circuit_faults"]: "", SIGNALS["signal_faults"]: "",
    SIGNALS["psd_faults"]: "", SIGNALS["escalator_temp"]: "DEG_C",
    SIGNALS["hvac_load"]: "%", SIGNALS["hvac_dp"]: "Pa", SIGNALS["station_los"]: "LOS",
}


@dataclass
class Redlines:
    otp_min: float = 90.0
    headway_min: float = 85.0
    train_avail_min: float = 90.0
    third_rail_max: float = 900.0        # DC overvoltage ceiling (P0)
    third_rail_min: float = 500.0        # undervoltage floor
    sub_load_max: float = 90.0
    rail_temp_max: float = 55.0
    rail_stress_max: float = 75.0        # MPa compressive (CWR buckle margin)
    wheel_qp_max: float = 0.8            # Nadal flange-climb limit
    bogie_vib_max: float = 7.1           # mm/s RMS (ISO 10816-3 zone C/D)
    traction_temp_max: float = 155.0     # insulation class F
    psd_faults_max: float = 5.0
    escalator_temp_max: float = 120.0
    hvac_dp_min: float = 10.0            # Pa — below → filter block / fan fail
    station_los_max: float = 5.0         # LOS E (index 5) → crush loading
    track_circuit_faults_max: float = 3.0
    signal_faults_max: float = 4.0
    delay_max: float = 5.0


redlines = Redlines()


# ════════════════════════════════════════════════════════════════════
#  1.  COMPONENT PHYSICS ENGINES  (first-principles, unit-testable)
# ════════════════════════════════════════════════════════════════════

def traction_power_flow(trains_in_section: float, train_kw: float = 1000.0,
                        feed_voltage: float = 750.0, feeder_ohm: float = 0.006,
                        regen_kw: float = 0.0, receptive_fraction: float = 1.0,
                        substation_rating_kw: float = 6000.0,
                        rheostat_ceiling_v: float = 1000.0, absorb_frac: float = 0.10) -> dict:
    """750 V DC third-rail power flow via a lumped Kirchhoff feeder.

    Each in-section train draws `train_kw`; the resulting current sags the rail
    voltage across the conductor-rail resistance (V = V_feed − I·R). Regenerative
    braking power that no other train can absorb (the non-receptive fraction) has
    nowhere to dissipate, so it RAISES the DC-link voltage toward the on-board
    braking-rheostat ceiling, which clamps it. The rise saturates with the surplus
    fraction of substation rating (half-rise at `absorb_frac`) — a receptivity
    collapse drives the rail toward the ceiling rather than through an arbitrary
    gain factor.
    """
    demand_kw = max(0.0, trains_in_section) * train_kw
    demand_a = demand_kw * 1000.0 / feed_voltage
    v_sag = demand_a * feeder_ohm
    surplus_kw = max(0.0, regen_kw) * (1.0 - max(0.0, min(1.0, receptive_fraction)))
    surplus_frac = surplus_kw / max(1.0, substation_rating_kw)
    rise = (rheostat_ceiling_v - feed_voltage) * (surplus_frac / (surplus_frac + absorb_frac))
    voltage = min(rheostat_ceiling_v, feed_voltage - v_sag + rise)
    sub_load = min(160.0, 100.0 * (demand_kw + surplus_kw) / substation_rating_kw)
    return {"voltage": voltage, "current_a": demand_a,
            "substation_load": sub_load, "demand_kw": demand_kw}


def train_dynamics(speed_kmh: float, gradient_permille: float = 0.0,
                   mass_t: float = 200.0, notch: float = 1.0,
                   davis_a: float = 1.5, davis_b: float = 0.02, davis_c: float = 0.00045,
                   max_tractive_kn: float = 200.0, adhesion: float = 0.33,
                   service_decel: float = 1.1, jerk_limit: float = 0.75) -> dict:
    """Longitudinal train dynamics.

    Running resistance uses the Davis formula R = A + B·v + C·v² (N/tonne, v in
    km/h) with a gradient correction (9.81·‰ N/tonne). Tractive effort at the
    commanded `notch` is capped by wheel–rail adhesion (µ·m·g). The service brake
    distance follows an ETCS-style curve: a jerk-limited deceleration onset
    (bounded da/dt) followed by constant service deceleration.
    """
    vk = max(0.0, speed_kmh)
    v_ms = vk / 3.6
    r_specific = davis_a + davis_b * vk + davis_c * vk * vk       # N/tonne
    grade_force = 9.81 * gradient_permille                        # N/tonne
    resistance_kn = (r_specific + grade_force) * mass_t / 1000.0
    adhesion_kn = adhesion * mass_t * 9.81                        # µ·m·g
    tractive_kn = min(max(0.0, notch) * max_tractive_kn, adhesion_kn)
    net_kn = tractive_kn - resistance_kn
    accel = net_kn * 1000.0 / (mass_t * 1000.0)                   # m/s²
    # ETCS service brake curve (jerk-limited onset then constant service decel).
    t_jerk = service_decel / max(1e-6, jerk_limit)
    d_jerk = max(0.0, v_ms * t_jerk - (jerk_limit * t_jerk ** 3) / 6.0)
    v_after = max(0.0, v_ms - 0.5 * jerk_limit * t_jerk ** 2)
    brake_distance = d_jerk + v_after ** 2 / (2.0 * service_decel)
    return {"resistance_kn": resistance_kn, "tractive_effort_kn": tractive_kn,
            "adhesion_limit_kn": adhesion_kn, "acceleration": accel,
            "brake_distance_m": brake_distance}


def station_hvac_load(pax: float, t_out_c: float = 32.0, rh_out: float = 0.7,
                      t_set_c: float = 25.0, door_open_fraction: float = 0.15,
                      envelope_ua_kw_per_k: float = 6.0, filter_blockage: float = 0.0,
                      fan_ok: bool = True, rated_kw: float = 900.0,
                      base_dp_pa: float = 26.0) -> dict:
    """Station ACMV cooling load from psychrometric first principles.

    Sensible + latent load = occupant gains (≈75 W sensible / 55 W latent per
    metro passenger) + envelope conduction (UA·ΔT) + latent infiltration of humid
    outdoor air through open PSDs/entrances. The supply/return pressure
    differential is the health signal: it collapses toward zero as the filter
    blocks or the supply fan fails (P1-013).
    """
    dt = max(0.0, t_out_c - t_set_c)
    sensible_kw = pax * 0.075 + envelope_ua_kw_per_k * dt
    latent_kw = pax * 0.055 + door_open_fraction * rh_out * dt * 0.8
    total_kw = sensible_kw + latent_kw
    load_pct = min(140.0, 100.0 * total_kw / rated_kw)
    dp_pa = base_dp_pa * (1.0 if fan_ok else 0.0) * (1.0 - min(1.0, max(0.0, filter_blockage)))
    return {"sensible_kw": sensible_kw, "latent_kw": latent_kw,
            "total_kw": total_kw, "load_pct": load_pct, "supply_return_dp_pa": dp_pa}


def escalator_motor_model(passengers_on_step: float, step_speed_ms: float = 0.65,
                          t_amb_c: float = 30.0, rise_height_m: float = 6.0,
                          incline_deg: float = 30.0, rated_kw: float = 15.0,
                          efficiency: float = 0.72) -> dict:
    """Escalator drive-motor model.

    Mechanical lifting power = (passenger mass)·g·(vertical component of step
    speed). Shaft power adds drive losses and a no-load term; motor current and
    torque follow. Winding temperature rises with load and ambient — the thermal
    limit flag trips an overload (P1-006).
    """
    pax_mass = max(0.0, passengers_on_step) * 75.0
    vertical_speed = step_speed_ms * math.sin(math.radians(incline_deg))
    lift_w = pax_mass * 9.81 * vertical_speed
    shaft_w = lift_w / efficiency + 0.12 * rated_kw * 1000.0
    load_pct = 100.0 * shaft_w / (rated_kw * 1000.0)
    current_a = shaft_w / (math.sqrt(3) * 400.0 * 0.85)          # 400 V 3-ph, pf 0.85
    torque_nm = shaft_w / (2 * math.pi * max(0.3, step_speed_ms / 0.4))
    temp_c = t_amb_c + 0.45 * load_pct
    return {"shaft_kw": shaft_w / 1000.0, "current_a": current_a,
            "torque_nm": torque_nm, "load_pct": load_pct,
            "motor_temp_c": temp_c, "thermal_trip": temp_c > 120.0}


def rail_thermal_expansion(rail_temp_c: float, neutral_temp_c: float = 30.0,
                           e_mpa: float = 210000.0, alpha: float = 1.15e-5,
                           area_mm2: float = 7670.0,
                           buckle_stress_mpa: float = 90.0) -> dict:
    """Continuously-welded-rail (CWR) thermal stress.

    CWR cannot expand, so a temperature excursion from the stress-free neutral
    temperature is reacted as axial stress σ = E·α·ΔT (compressive when hot,
    tensile when cold). High compressive stress at elevated rail temperature is
    the track-buckling precursor (P1-007).
    """
    dt = rail_temp_c - neutral_temp_c
    stress_mpa = e_mpa * alpha * dt                              # +compressive hot
    axial_force_kn = stress_mpa * area_mm2 / 1000.0
    return {"delta_t": dt, "stress_mpa": stress_mpa,
            "axial_force_kn": axial_force_kn,
            "buckle_risk": stress_mpa >= buckle_stress_mpa}


def wheel_rail_contact(axle_load_kn: float = 160.0, speed_kmh: float = 60.0,
                       lateral_kn: float | None = None, wheel_radius_m: float = 0.43,
                       e_gpa: float = 210.0, poisson: float = 0.3,
                       flange_angle_deg: float = 70.0, friction: float = 0.36,
                       wheel_flat_mm: float = 0.0) -> dict:
    """Wheel–rail contact mechanics.

    Peak Hertzian contact pressure for the wheel-on-rail point contact, a
    T-gamma-style wear index, and the derailment quotient Q/P (lateral/vertical
    force ratio) compared against the Nadal flange-climb limit. A wheel flat adds
    a speed-dependent dynamic impact factor that inflates both Q and Q/P (P1-008).
    """
    wheel_load_n = axle_load_kn * 1000.0 / 2.0
    e_star = e_gpa * 1e9 / (2.0 * (1.0 - poisson ** 2))
    p0_pa = (6.0 * wheel_load_n * e_star ** 2 / (math.pi ** 3 * wheel_radius_m ** 2)) ** (1.0 / 3.0)
    contact_stress_mpa = p0_pa / 1e6
    impact = 1.0 + wheel_flat_mm * 0.35 + speed_kmh * 0.002       # dynamic factor
    b = math.radians(flange_angle_deg)
    nadal = (math.tan(b) - friction) / (1.0 + friction * math.tan(b))
    # Q/P from a lateral/vertical force balance, measured against the Nadal
    # flange-climb limit. The vertical reference is the static wheel load (the
    # basis of the Nadal criterion); the lateral guiding force grows with
    # curving/hunting speed and is amplified by the wheel-flat dynamic impact.
    if lateral_kn is not None:
        lateral_n = lateral_kn * 1000.0
    else:
        lateral_n = (wheel_load_n * (0.12 + 0.0015 * speed_kmh) * impact
                     + wheel_flat_mm * 0.04 * wheel_load_n)
    qp = lateral_n / max(1.0, wheel_load_n)
    nadal_utilisation = qp / nadal if nadal > 0 else 0.0   # ≥1 ⇒ flange climb
    wear_index = contact_stress_mpa * (lateral_n / 1000.0) * speed_kmh * 1e-4
    return {"contact_stress_mpa": contact_stress_mpa, "derailment_quotient": qp,
            "nadal_limit": nadal, "nadal_utilisation": nadal_utilisation,
            "wear_index": wear_index, "dynamic_factor": impact}


_LOS_BANDS = [(0.30, "A", 1), (0.50, "B", 2), (0.72, "C", 3),
              (1.00, "D", 4), (1.40, "E", 5)]


def passenger_los_model(pax_count: float, platform_area_m2: float = 420.0,
                        crush_density: float = 4.0) -> dict:
    """Platform Level of Service (Fruin / HCM pedestrian bands).

    Maps waiting passenger density (persons/m²) to LOS A–F and estimates the
    dwell-time extension crush loading adds through boarding friction (P1-009).
    """
    density = max(0.0, pax_count) / max(1.0, platform_area_m2)
    los, los_n = "F", 6
    for thr, letter, n in _LOS_BANDS:
        if density <= thr:
            los, los_n = letter, n
            break
    crush = min(1.0, density / crush_density)
    dwell_extension_s = 8.0 * crush + 4.0 * max(0, los_n - 3)
    return {"density": round(density, 3), "los": los, "los_index": los_n,
            "dwell_extension_s": dwell_extension_s, "crush": crush}


# ════════════════════════════════════════════════════════════════════
#  2.  NETWORK GEOMETRY  (a compact 3-line metro with interchanges)
# ════════════════════════════════════════════════════════════════════
_STATIONS = [
    {"id": "NGT", "name": "Northgate",    "x": 50, "y": 12, "area": 430, "weight": 0.9},
    {"id": "RIV", "name": "Riverside",    "x": 50, "y": 30, "area": 480, "weight": 1.1},
    {"id": "CEN", "name": "Central",      "x": 50, "y": 50, "area": 620, "weight": 1.6},
    {"id": "MKT", "name": "Market",       "x": 50, "y": 70, "area": 500, "weight": 1.2},
    {"id": "HBF", "name": "Harbourfront", "x": 50, "y": 88, "area": 460, "weight": 1.0},
    {"id": "WPK", "name": "Westpark",     "x": 14, "y": 50, "area": 410, "weight": 0.8},
    {"id": "UNI", "name": "University",   "x": 30, "y": 50, "area": 470, "weight": 1.1},
    {"id": "EXPO", "name": "Expo",        "x": 70, "y": 50, "area": 520, "weight": 1.2},
    {"id": "APT", "name": "Airport",      "x": 88, "y": 50, "area": 560, "weight": 1.3},
]
_LINES = [
    {"id": "L1", "name": "Line 1 · North–South", "color": "#e11d48",
     "path": ["NGT", "RIV", "CEN", "MKT", "HBF"], "loop": False, "trains": 4},
    {"id": "L2", "name": "Line 2 · East–West", "color": "#2563eb",
     "path": ["WPK", "UNI", "CEN", "EXPO", "APT"], "loop": False, "trains": 4},
    {"id": "L3", "name": "Line 3 · Circle", "color": "#16a34a",
     "path": ["RIV", "EXPO", "MKT", "UNI", "RIV"], "loop": True, "trains": 4},
]
_DEPOTS = [
    {"id": "D1", "name": "Northgate Depot", "x": 50, "y": 3, "berths": 8},
    {"id": "D2", "name": "Airport Depot",   "x": 96, "y": 50, "berths": 6},
]
_SUBSTATIONS = [
    {"id": "SSA", "name": "TSS Riverside", "x": 44, "y": 22},
    {"id": "SSB", "name": "TSS Market",    "x": 44, "y": 62},
    {"id": "SSC", "name": "TSS University", "x": 30, "y": 44},
    {"id": "SSD", "name": "TSS Expo",      "x": 70, "y": 44},
]
_KM_PER_UNIT = 0.30   # map units → km


def _build_net() -> dict:
    idx = {s["id"]: s for s in _STATIONS}
    lines, fleet, total_km = [], [], 0.0
    for _li, ln in enumerate(_LINES):
        pts = [[idx[p]["x"], idx[p]["y"]] for p in ln["path"]]
        seglen = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
        length_km = round(sum(seglen) * _KM_PER_UNIT, 2)
        total_km += length_km
        lines.append({**ln, "points": pts, "seglen": seglen,
                      "length": sum(seglen), "length_km": length_km})
        for k in range(ln["trains"]):
            fleet.append({"id": f"{ln['id']}-{k + 1:02d}", "line": ln["id"],
                          "phase": k / ln["trains"]})
    # interchange = station appearing on >1 line
    on_lines = {}
    for ln in _LINES:
        for sid in set(ln["path"]):
            on_lines.setdefault(sid, []).append(ln["id"])
    for s in _STATIONS:
        s["lines"] = on_lines.get(s["id"], [])
        s["interchange"] = len(s["lines"]) > 1
    return {"stations": _STATIONS, "lines": lines, "depots": _DEPOTS,
            "substations": _SUBSTATIONS, "fleet": fleet,
            "route_km": round(total_km, 1), "fleet_size": len(fleet)}


def _point_on_line(line: dict, phase: float) -> list:
    """[x,y] at fractional distance `phase` (0..1) along the line polyline."""
    pts, seglen, total = line["points"], line["seglen"], line["length"]
    if total <= 0:
        return list(pts[0])
    target = (phase % 1.0) * total
    acc = 0.0
    for i, sl in enumerate(seglen):
        if acc + sl >= target:
            f = (target - acc) / sl if sl else 0.0
            ax, ay = pts[i]
            bx, by = pts[i + 1]
            return [round(ax + (bx - ax) * f, 2), round(ay + (by - ay) * f, 2)]
        acc += sl
    return list(pts[-1])


# ════════════════════════════════════════════════════════════════════
#  3.  NETWORK TWIN
# ════════════════════════════════════════════════════════════════════
@dataclass
class RailwayState:
    service_level: float = 0.82          # control 0..1 (share of timetable running)
    heat: float = 0.15                   # 0..1 weather/thermal load
    surge: float = 0.0                   # 0..1 passenger surge
    brake_wear: float = 9.0              # %
    shoe_wear: float = 7.0               # % collector-shoe wear
    wheel_flat: float = 0.0              # mm
    regen_surplus: float = 0.0           # 0..1 non-receptive regen fraction
    filter_blockage: float = 0.0         # 0..1 ACMV filter blockage
    fan_ok: bool = True
    escalator_overload: float = 0.0      # 0..1 extra escalator step load
    signal_faults: float = 0.0
    track_circuit_faults: float = 0.0
    psd_faults: float = 0.0
    blocked: set = field(default_factory=set)     # blocked line ids
    fault_target: str = ""               # station/line the active fault localises to
    phases: dict = field(default_factory=dict)    # train_id -> phase
    hours: float = 0.0
    fault: str = "none"
    fault_severity: float = 0.0
    seed: int = 7
    _rng: random.Random = field(default=None, repr=False, compare=False)

    def rng(self):
        if self._rng is None:
            self._rng = random.Random(self.seed)
        return self._rng


# fault key -> (state mutations, localises-to)
_FAULTS = {
    "traction_overvoltage": {"regen_surplus": 0.85, "_no_receptive": True},
    "third_rail_fault":     {"_block": 1},
    "signalling_outage":    {"signal_faults": 4, "_block_soft": True},
    "track_circuit_fault":  {"track_circuit_faults": 3, "_target_station": True},
    "psd_fault":            {"psd_faults": 4, "_target_station": True},
    "escalator_fault":      {"escalator_overload": 0.8, "_target_station": True},
    "hvac_filter_block":    {"filter_blockage": 0.85, "_target_station": True},
    "rail_buckle":          {"heat": 0.95},
    "wheel_flat":           {"wheel_flat": 2.6},
    "passenger_surge":      {"surge": 0.85},
    "heatwave":             {"heat": 0.75},
}

FAULTS = list(_FAULTS.keys())


class RailwayPhysics:
    def __init__(self, **options):
        self.opts = options or {}
        self.net = _build_net()
        self._line_by_id = {ln["id"]: ln for ln in self.net["lines"]}

    def init_state(self) -> RailwayState:
        st = RailwayState()
        st.phases = {v["id"]: v["phase"] for v in self.net["fleet"]}
        return st

    # ── fault injection / clear ──
    def inject(self, state: RailwayState, fault: str, severity: float = 0.85) -> None:
        eff = max(0.0, min(1.0, float(severity)))
        state.fault = fault
        state.fault_severity = eff
        spec = _FAULTS.get(fault, {})
        rng = state.rng()
        for attr, amount in spec.items():
            if attr == "_block":
                for ln in self.net["lines"]:
                    if ln["id"] not in state.blocked:
                        state.blocked.add(ln["id"])
                        state.fault_target = ln["id"]
                        break
            elif attr == "_block_soft":
                # signalling degrades a line's throughput without fully blocking it
                for ln in self.net["lines"]:
                    if ln["id"] not in state.blocked:
                        state.fault_target = ln["id"]
                        break
            elif attr == "_no_receptive":
                state.regen_surplus = max(state.regen_surplus, 0.85 * eff)
            elif attr == "_target_station":
                stations = self.net["stations"]
                state.fault_target = stations[rng.randrange(len(stations))]["id"]
            elif attr in ("heat", "surge", "regen_surplus", "filter_blockage",
                          "escalator_overload"):
                setattr(state, attr, min(1.2, getattr(state, attr) + amount * eff))
            elif attr == "wheel_flat":
                state.wheel_flat = min(6.0, state.wheel_flat + amount * eff)
            else:
                setattr(state, attr, min(30.0, getattr(state, attr) + amount * eff))
        if fault == "hvac_filter_block" and eff > 0.9:
            state.fan_ok = False

    def clear(self, state: RailwayState) -> None:
        state.fault = "none"
        state.fault_severity = 0.0
        state.blocked.clear()
        state.fault_target = ""
        state.heat = 0.15
        state.surge = 0.0
        state.regen_surplus = 0.0
        state.filter_blockage = 0.0
        state.fan_ok = True
        state.escalator_overload = 0.0
        state.signal_faults = 0.0
        state.track_circuit_faults = 0.0
        state.psd_faults = 0.0

    # ── the forward step ──
    def forward(self, state: RailwayState, dt: float = 1.0) -> dict:
        rng = state.rng()
        S = max(0.0, min(1.0, state.service_level))
        state.hours += dt / 3600.0

        # slow wear accrual
        state.brake_wear = min(100.0, state.brake_wear + dt * (0.0012 + 0.0026 * S))
        state.shoe_wear = min(100.0, state.shoe_wear + dt * (0.0010 + 0.0022 * S))
        if state.wheel_flat > 0:
            state.wheel_flat = min(6.0, state.wheel_flat + dt * 3e-5 * S)

        blocked = state.blocked
        n_blocked = len(blocked)
        soft = 1 if (state.signal_faults >= 3 and state.fault_target and not n_blocked) else 0
        heat = state.heat
        surge = state.surge

        # ── advance trains (blocked lines hold position) ──
        avg_speed = 42.0 * (0.6 + 0.4 * S) * (1.0 - 0.12 * n_blocked) \
            * (1.0 - 0.15 * heat) * (1.0 - 0.10 * soft)
        for ln in self.net["lines"]:
            if ln["id"] in blocked or ln["length"] <= 0:
                continue
            dphase = (avg_speed / 3.6) * dt / (ln["length"] / _KM_PER_UNIT * 1000.0)
            for v in self.net["fleet"]:
                if v["line"] == ln["id"]:
                    state.phases[v["id"]] = (state.phases.get(v["id"], 0.0) + dphase) % 1.0

        fleet_size = self.net["fleet_size"]
        in_service = round(fleet_size * (0.7 + 0.3 * S) * (1.0 - 0.15 * n_blocked))
        active_sections = max(1, len(self.net["substations"]) - n_blocked)

        # ── traction power (component engine) ──
        load_factor = min(120.0, 45.0 + 42.0 * S + 55.0 * surge)
        section_train_kw = 700.0 + 450.0 * S      # avg per-train draw in a section
        tp = traction_power_flow(
            trains_in_section=max(1.0, in_service / active_sections),
            train_kw=section_train_kw, regen_kw=in_service * 230.0,
            receptive_fraction=1.0 - min(1.0, state.regen_surplus))
        third_rail_v = tp["voltage"]
        sub_load = min(140.0, tp["substation_load"] * (1.0 + 0.25 * n_blocked) + 10.0 * heat)
        traction_power = tp["demand_kw"] * active_sections / 1000.0 * (1.0 + 0.08 * heat)  # MW
        regen = 0.0 if state.regen_surplus > 0.5 else max(0.0, 34.0 - 18.0 * heat - 8.0 * (sub_load > 85))

        # ── permanent way ──
        rail_temp = 22.0 + 42.0 * heat + 0.05 * load_factor
        rt = rail_thermal_expansion(rail_temp)
        rail_stress = rt["stress_mpa"]
        wr = wheel_rail_contact(axle_load_kn=140.0 + 40.0 * (load_factor / 100.0),
                                speed_kmh=avg_speed, wheel_flat_mm=state.wheel_flat)
        wheel_qp = wr["derailment_quotient"]
        bogie_vib = 2.0 + 1.9 * state.wheel_flat + 0.02 * avg_speed \
            + 0.010 * state.brake_wear + 1.2 * heat
        traction_temp = 58.0 + 34.0 * S + 26.0 * heat + 8.0 * (sub_load > 90)

        # ── station services (component engines, network-worst) ──
        peak_pax = (120.0 + 110.0 * S + 260.0 * surge) * 1.4   # busiest platform
        hv = station_hvac_load(pax=peak_pax, t_out_c=28.0 + 12.0 * heat,
                               filter_blockage=state.filter_blockage, fan_ok=state.fan_ok)
        hvac_load = hv["load_pct"]
        hvac_dp = hv["supply_return_dp_pa"]
        esc = escalator_motor_model(
            passengers_on_step=18.0 + 26.0 * S + 40.0 * surge + 40.0 * state.escalator_overload,
            t_amb_c=28.0 + 10.0 * heat)
        escalator_temp = esc["motor_temp_c"]
        los = passenger_los_model(peak_pax)
        station_los = los["los_index"]

        # ── signalling ──
        signal_faults = state.signal_faults
        track_circuit_faults = state.track_circuit_faults
        psd_faults = state.psd_faults

        # ── operations rollup ──
        dwell = 24.0 + 0.10 * load_factor + los["dwell_extension_s"] + 2.6 * psd_faults
        delay = (0.7 + 3.6 * n_blocked + 0.7 * signal_faults + 0.5 * track_circuit_faults
                 + 0.04 * max(0.0, load_factor - 75.0) + 2.0 * surge + 0.8 * soft)
        otp = max(0.0, 99.0 - 3.0 * delay - 4.0 * n_blocked - 1.1 * signal_faults)
        headway = max(0.0, 98.0 - 2.2 * delay - 3.0 * n_blocked - 3.0 * surge)
        train_avail = max(0.0, 99.0 - 0.12 * state.brake_wear - 0.9 * psd_faults
                          - 1.5 * (state.wheel_flat > 1.0))
        incidents = (n_blocked + int(signal_faults >= 3) + int(track_circuit_faults >= 2)
                     + int(third_rail_v >= redlines.third_rail_max)
                     + int(wheel_qp >= 0.7) + int(rail_stress >= redlines.rail_stress_max))

        def j(v, frac):
            return jitter(rng, v, frac)

        return {
            SIGNALS["otp"]:                  round(j(otp, 0.008), 1),
            SIGNALS["headway"]:              round(j(headway, 0.008), 1),
            SIGNALS["avg_speed"]:            round(max(0.0, j(avg_speed, 0.02)), 1),
            SIGNALS["train_avail"]:          round(train_avail, 1),
            SIGNALS["in_service"]:           int(max(0, in_service)),
            SIGNALS["load_factor"]:          round(j(load_factor, 0.02), 1),
            SIGNALS["dwell"]:                round(j(dwell, 0.02), 1),
            SIGNALS["delay"]:                round(max(0.0, j(delay, 0.03)), 2),
            SIGNALS["incidents"]:            int(incidents),
            SIGNALS["traction_power"]:       round(max(0.0, j(traction_power, 0.03)), 2),
            SIGNALS["regen"]:                round(max(0.0, regen), 1),
            SIGNALS["third_rail_v"]:         round(max(0.0, j(third_rail_v, 0.004)), 0),
            SIGNALS["sub_load"]:             round(j(sub_load, 0.02), 1),
            SIGNALS["traction_temp"]:        round(j(traction_temp, 0.01), 1),
            SIGNALS["rail_temp"]:            round(j(rail_temp, 0.01), 1),
            SIGNALS["rail_stress"]:          round(max(0.0, j(rail_stress, 0.01)), 1),
            SIGNALS["wheel_qp"]:             round(max(0.0, j(wheel_qp, 0.02)), 3),
            SIGNALS["bogie_vib"]:            round(max(0.0, j(bogie_vib, 0.03)), 2),
            SIGNALS["track_circuit_faults"]: int(track_circuit_faults),
            SIGNALS["signal_faults"]:        int(signal_faults),
            SIGNALS["psd_faults"]:           int(psd_faults),
            SIGNALS["escalator_temp"]:       round(j(escalator_temp, 0.01), 1),
            SIGNALS["hvac_load"]:            round(j(hvac_load, 0.02), 1),
            SIGNALS["hvac_dp"]:              round(max(0.0, hvac_dp), 1),
            SIGNALS["station_los"]:          int(station_los),
        }

    def residuals(self, frame: dict) -> dict:
        return {
            SIGNALS["third_rail_v"]: frame.get(SIGNALS["third_rail_v"], 750.0) - 750.0,
            SIGNALS["otp"]: frame.get(SIGNALS["otp"], 98.0) - 98.0,
        }

    def health_index(self, frame: dict) -> float:
        if not frame:
            return 1.0

        def hi(v, nominal, limit):   # lower is healthier
            return max(0.0, min(1.0, (limit - v) / (limit - nominal)))

        def lo(v, nominal, limit):   # higher is healthier
            return max(0.0, min(1.0, (v - limit) / (nominal - limit)))

        def band(v, plo, phi, hlo, hhi):   # two-sided with a healthy plateau
            if v < plo:
                return max(0.0, min(1.0, (v - hlo) / (plo - hlo)))
            if v > phi:
                return max(0.0, min(1.0, (hhi - v) / (hhi - phi)))
            return 1.0

        margins = [
            lo(frame.get(SIGNALS["otp"], 96.0), 96.0, redlines.otp_min),
            lo(frame.get(SIGNALS["headway"], 96.0), 96.0, redlines.headway_min),
            lo(frame.get(SIGNALS["train_avail"], 97.0), 97.0, redlines.train_avail_min),
            band(frame.get(SIGNALS["third_rail_v"], 750.0), 700.0, 820.0,
                 redlines.third_rail_min, redlines.third_rail_max),
            hi(frame.get(SIGNALS["sub_load"], 60.0), 60.0, redlines.sub_load_max),
            hi(frame.get(SIGNALS["rail_temp"], 32.0), 32.0, redlines.rail_temp_max),
            hi(frame.get(SIGNALS["rail_stress"], 20.0), 20.0, redlines.rail_stress_max),
            hi(frame.get(SIGNALS["wheel_qp"], 0.28), 0.28, redlines.wheel_qp_max),
            hi(frame.get(SIGNALS["bogie_vib"], 3.0), 3.0, redlines.bogie_vib_max),
            hi(frame.get(SIGNALS["traction_temp"], 90.0), 90.0, redlines.traction_temp_max),
            lo(frame.get(SIGNALS["hvac_dp"], 26.0), 26.0, redlines.hvac_dp_min),
            hi(frame.get(SIGNALS["station_los"], 3), 3, redlines.station_los_max),
            hi(frame.get(SIGNALS["delay"], 1.0), 1.0, redlines.delay_max),
        ]
        return round(min(margins), 3)

    # ── the live network map + per-station KPIs ──
    def _station_kpis(self, state: RailwayState) -> list:
        rng = random.Random(state.seed + 91)
        S = max(0.0, min(1.0, state.service_level))
        out = []
        for s in self.net["stations"]:
            base = (200.0 + 180.0 * S) * s["weight"]
            targeted = state.fault_target == s["id"]
            surge_local = state.surge * (1.6 if targeted else 1.0) + (0.5 if targeted and state.fault == "passenger_surge" else 0.0)
            pax = base * (0.7 + 0.3 * S) * (1.0 + 1.1 * surge_local) + rng.uniform(-20, 20)
            los = passenger_los_model(pax, platform_area_m2=s["area"])
            psd = "critical" if targeted and state.fault == "psd_fault" else "ok"
            esc = "critical" if targeted and state.fault == "escalator_fault" else "ok"
            hvac = ("critical" if targeted and state.fault == "hvac_filter_block"
                    else "warning" if state.heat > 0.6 else "ok")
            tc = "critical" if targeted and state.fault == "track_circuit_fault" else "ok"
            worst = "ok"
            for st_ in (psd, esc, hvac, tc):
                if st_ == "critical":
                    worst = "critical"
                elif st_ == "warning" and worst == "ok":
                    worst = "warning"
            if los["los_index"] >= 5 and worst == "ok":
                worst = "warning"
            out.append({
                "id": s["id"], "name": s["name"], "x": s["x"], "y": s["y"],
                "lines": s["lines"], "interchange": s["interchange"],
                "pax": int(max(0, pax)), "los": los["los"], "los_index": los["los_index"],
                "psd": psd, "escalator": esc, "hvac": hvac, "track_circuit": tc,
                "status": worst,
            })
        return out

    def _depot_board(self, state: RailwayState) -> list:
        rng = random.Random(state.seed + 41)
        S = max(0.0, min(1.0, state.service_level))
        depots = []
        tno = 1
        for d in self.net["depots"]:
            berths = []
            for b in range(d["berths"]):
                r = rng.random()
                # more trains out in service when service level is high
                if r < 0.55 + 0.25 * S:
                    status, occ = "in_service", None
                elif r < 0.8:
                    status = "maintenance"
                    occ = f"RS-{tno:03d}"
                    tno += 1
                else:
                    status = "stabled"
                    occ = f"RS-{tno:03d}"
                    tno += 1
                eta = None
                if status == "maintenance":
                    eta = round(1.0 + rng.uniform(0, 6.0) + 8.0 * state.brake_wear / 100.0, 1)
                berths.append({"berth": f"{d['id']}-{b + 1:02d}", "status": status,
                               "unit": occ, "ready_in_h": eta})
            avail = sum(1 for x in berths if x["status"] != "maintenance")
            depots.append({"id": d["id"], "name": d["name"], "x": d["x"], "y": d["y"],
                           "berth_count": d["berths"], "available": avail,
                           "in_maintenance": sum(1 for x in berths if x["status"] == "maintenance"),
                           "berths": berths})
        return depots

    def network_state(self, state: RailwayState) -> dict:
        line_status = {}
        for ln in self.net["lines"]:
            if ln["id"] in state.blocked:
                line_status[ln["id"]] = "blocked"
            elif state.fault_target == ln["id"] and state.fault_severity > 0.3:
                line_status[ln["id"]] = "degraded"
            else:
                line_status[ln["id"]] = "ok"
        trains = []
        for v in self.net["fleet"]:
            ln = self._line_by_id[v["line"]]
            phase = state.phases.get(v["id"], 0.0)
            x, y = _point_on_line(ln, phase)
            held = v["line"] in state.blocked
            load = min(120, int(48 + 40 * state.service_level + 55 * state.surge + (state.seed + hash(v["id"])) % 12))
            trains.append({"id": v["id"], "line": v["line"], "x": x, "y": y,
                           "load": load, "status": "held" if held else "moving"})
        return {
            "stations": self._station_kpis(state),
            "lines": [{"id": ln["id"], "name": ln["name"], "color": ln["color"],
                       "points": ln["points"], "path": ln["path"],
                       "length_km": ln["length_km"], "loop": ln["loop"],
                       "status": line_status[ln["id"]]} for ln in self.net["lines"]],
            "depots": self._depot_board(state),
            "substations": self.net["substations"],
            "trains": trains,
            "blocked": sorted(state.blocked),
            "route_km": self.net["route_km"],
            "fleet_size": self.net["fleet_size"],
        }
