"""
packs/defence/physics.py — defence component physics engines + a military-base
(C4ISR) forward model with a live tactical picture.

Component engines (faithful, unit-testable) — shared with the warship twin:
    gas_turbine_brayton()        — Brayton-cycle compressor/combustor/turbine
                                   temperatures, efficiency and power + degradation
    radar_propagation()          — radar horizon, range equation, coverage arc,
                                   terrain-masked blind sectors, jamming burn-through
    ship_stability()             — righting lever (GZ), freeboard and induced list
                                   under progressive flooding
    nbc_contamination_spread()   — Gaussian plume (Pasquill-Gifford) concentration
                                   + downwind hazard range
    structural_fatigue()         — Miner's-rule cumulative damage + Paris-law crack
                                   growth + fatigue life remaining

DefenceBasePhysics — the base twin: perimeter/force-protection, radar & comms,
ammunition & fuel storage, NBC and an air fleet, driving ~18 KPIs. `network_state`
returns the NATO APP-6 tactical map and the mission board.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from packs._core.physics import jitter, margin_hi, margin_lo, worst_health

# ────────────────────────────────────────────────────────────────────
#  Signals (base / C4ISR domain)
# ────────────────────────────────────────────────────────────────────
SIGNALS = {
    "threat_level":       "def:threatLevel",
    "perimeter_breaches": "def:perimeterBreaches",
    "tracked_objects":    "def:trackedObjects",
    "radar_coverage":     "def:radarCoverage",
    "radar_range":        "def:radarRange",
    "jamming_level":      "def:jammingLevel",
    "comms_availability": "def:commsAvailability",
    "ammo_temp":          "def:ammoTemperature",
    "ammo_humidity":      "def:ammoHumidity",
    "cookoff_margin":     "def:cookoffMargin",
    "fuel_level":         "def:fuelLevel",
    "nbc_reading":        "def:nbcReading",
    "nbc_plume_range":    "def:nbcPlumeRange",
    "aircraft_avail":     "def:aircraftAvailability",
    "flight_hours":       "def:flightHoursToService",
    "sorties_ready":      "def:sortiesReady",
    "mission_readiness":  "def:missionReadiness",
    "force_protection":   "def:forceProtection",
}

UNITS = {
    SIGNALS["threat_level"]: "", SIGNALS["perimeter_breaches"]: "", SIGNALS["tracked_objects"]: "",
    SIGNALS["radar_coverage"]: "%", SIGNALS["radar_range"]: "km", SIGNALS["jamming_level"]: "%",
    SIGNALS["comms_availability"]: "%", SIGNALS["ammo_temp"]: "DEG_C", SIGNALS["ammo_humidity"]: "%",
    SIGNALS["cookoff_margin"]: "DEG_C", SIGNALS["fuel_level"]: "%", SIGNALS["nbc_reading"]: "",
    SIGNALS["nbc_plume_range"]: "km", SIGNALS["aircraft_avail"]: "%", SIGNALS["flight_hours"]: "h",
    SIGNALS["sorties_ready"]: "", SIGNALS["mission_readiness"]: "%", SIGNALS["force_protection"]: "",
}


@dataclass
class Redlines:
    radar_coverage_min: float = 70.0      # %
    comms_min: float = 80.0               # %
    ammo_temp_max: float = 52.0           # °C (cook-off approach)
    cookoff_margin_min: float = 10.0      # °C (MIL-STD-882)
    fuel_min: float = 20.0                # %
    nbc_max: float = 5.0                  # contamination index
    perimeter_breaches_max: float = 0.0   # any breach is an event
    jamming_max: float = 60.0             # %
    flight_hours_min: float = 5.0         # h to service
    mission_readiness_min: float = 60.0   # %


redlines = Redlines()


# ════════════════════════════════════════════════════════════════════
#  1.  COMPONENT PHYSICS ENGINES
# ════════════════════════════════════════════════════════════════════

def gas_turbine_brayton(pressure_ratio: float = 18.0, t_inlet_c: float = 15.0,
                        fuel_flow_kgps: float = 0.38, load: float = 0.6,
                        eff_comp: float = 0.86, eff_turb: float = 0.89, gamma: float = 1.4,
                        cp: float = 1005.0, lhv: float = 43.0e6, degradation: float = 0.0) -> dict:
    """Brayton (Joule) cycle gas turbine — naval GT / aero engine (P4-003).

    Compressor delivery temperature T2 from the isentropic ratio corrected by
    compressor efficiency; combustor exit (turbine inlet) T3 from the fuel heat
    addition; turbine exit gas temperature T4; cycle efficiency = net work / heat
    in. Degradation drops the pressure ratio and combustion efficiency.
    """
    T1 = t_inlet_c + 273.15
    pr = pressure_ratio * (1.0 - 0.12 * degradation)
    iso = pr ** ((gamma - 1.0) / gamma)
    T2 = T1 * (1.0 + (iso - 1.0) / eff_comp)
    mass_air = 22.0 * (0.4 + 0.7 * load)
    T3 = T2 + fuel_flow_kgps * lhv / (mass_air * cp) * (1.0 - 0.06 * degradation)
    # hot-section deterioration lowers the effective turbine efficiency, so less
    # enthalpy is extracted and the exhaust gas temperature rises.
    eff_turb_eff = eff_turb * (1.0 - 0.32 * degradation)
    T4 = T3 * (1.0 - eff_turb_eff * (1.0 - 1.0 / iso))
    w_turb = cp * (T3 - T4)
    w_comp = cp * (T2 - T1)
    w_net = (w_turb - w_comp) * mass_air
    q_in = fuel_flow_kgps * lhv
    eff = max(0.0, w_net / q_in) * 100.0
    return {"compressor_temp_c": T2 - 273.15, "turbine_inlet_c": T3 - 273.15,
            "egt_c": T4 - 273.15, "efficiency": eff, "power_mw": max(0.0, w_net) / 1e6}


def radar_propagation(antenna_height_m: float = 30.0, target_height_m: float = 100.0,
                      peak_power_kw: float = 1000.0, freq_ghz: float = 3.0,
                      target_rcs_m2: float = 5.0, jamming: float = 0.0,
                      terrain_masking: float = 0.0, coverage_arc_deg: float = 360.0,
                      antenna_gain: float = 3000.0, min_signal_w: float = 1e-13) -> dict:
    """Radar propagation + range equation (P4-004).

    4/3-earth radar horizon d = 4.12·(√h_ant + √h_tgt) km; free-space detection
    range from the radar range equation R = (Pt·G²·λ²·σ / ((4π)³·Smin))^¼, capped by
    the horizon. Hostile jamming forces a shorter burn-through range and, with
    terrain masking, opens blind sectors in the coverage arc.
    """
    horizon_km = 4.12 * (math.sqrt(max(0.0, antenna_height_m)) + math.sqrt(max(0.0, target_height_m)))
    lam = 0.3 / freq_ghz
    r4 = (peak_power_kw * 1000.0 * antenna_gain ** 2 * lam ** 2 * target_rcs_m2) / \
         ((4.0 * math.pi) ** 3 * min_signal_w)
    range_km = min(horizon_km, r4 ** 0.25 / 1000.0)
    eff_range = range_km * (1.0 - 0.7 * jamming)
    coverage = coverage_arc_deg / 360.0 * 100.0 * (1.0 - 0.5 * jamming) * (1.0 - 0.4 * terrain_masking)
    blind_sectors = int(terrain_masking * 6 + jamming * 4)
    return {"horizon_km": horizon_km, "range_km": eff_range, "coverage_pct": coverage,
            "blind_sectors": blind_sectors, "jamming_effectiveness": jamming * 100.0}


def ship_stability(displacement_t: float = 6000.0, gm_m: float = 1.4, heel_deg: float = 0.0,
                   flood_tonnes: float = 0.0, depth_m: float = 12.0, kb_m: float = 4.0) -> dict:
    """Transverse ship stability under progressive flooding (P4-005).

    Added-weight + free-surface flooding reduces the effective metacentric height
    GM and, when asymmetric, induces a list. The righting lever GZ = GM·sin(heel)
    (moderate-angle); draft rises and freeboard falls with the added weight. GM ≤ 0
    is a capsize condition. (Bonjean sectional areas set the added-weight scaling.)
    """
    gm_eff = gm_m - flood_tonnes / displacement_t * 9.0
    if gm_eff > 0.05:
        list_induced = math.degrees(math.atan(flood_tonnes * 2.5 / (displacement_t * gm_eff)))
    else:
        list_induced = 35.0
    heel = min(60.0, heel_deg + list_induced)
    gz = gm_eff * math.sin(math.radians(heel))
    draft = kb_m * 2.0 + flood_tonnes / displacement_t * 3.5
    freeboard = depth_m - draft
    return {"gz_m": gz, "gm_eff_m": gm_eff, "list_deg": heel, "draft_m": draft,
            "freeboard_m": freeboard, "capsize_risk": gm_eff <= 0.05 or heel > 30.0}


_PG_COEFFS = {"A": (0.22, 0.20), "B": (0.16, 0.12), "C": (0.11, 0.08),
              "D": (0.08, 0.06), "E": (0.06, 0.03), "F": (0.04, 0.016)}
_PG_HAZARD = {"A": 0.3, "B": 0.5, "C": 0.8, "D": 1.2, "E": 1.8, "F": 2.6}


def nbc_contamination_spread(release_rate_gps: float = 100.0, wind_ms: float = 3.0,
                             stability: str = "D", distance_m: float = 1000.0) -> dict:
    """NBC point-release Gaussian plume (Pasquill-Gifford) (P4-006).

    Ground-level centreline concentration C = Q / (2π·u·σy·σz), with σy, σz from the
    Pasquill-Gifford dispersion class at the downwind distance. The downwind hazard
    range (isopleth) grows with release rate and atmospheric stability and shrinks
    with wind speed.
    """
    a, b = _PG_COEFFS.get(stability, _PG_COEFFS["D"])
    x = max(1.0, distance_m)
    sy = a * x / (1.0 + 0.0001 * x) ** 0.5
    sz = b * x / (1.0 + 0.0015 * x) ** 0.5
    conc = release_rate_gps / (2.0 * math.pi * max(0.3, wind_ms) * sy * sz)
    hazard_km = min(50.0, release_rate_gps ** 0.5 * (0.5 + _PG_HAZARD.get(stability, 1.2)) / max(0.5, wind_ms))
    return {"concentration": conc * 1e6, "sigma_y": sy, "sigma_z": sz,
            "hazard_range_km": hazard_km, "plume_stability": stability}


def structural_fatigue(stress_range_mpa: float = 100.0, cycles: float = 1.0e6,
                       crack_mm: float = 1.0, sn_c: float = 3.0e14, sn_m: float = 3.0,
                       paris_c: float = 1.0e-11, paris_m: float = 3.0,
                       fracture_toughness: float = 50.0, dcycles: float = 1.0e3) -> dict:
    """Structural fatigue: Miner's rule + Paris-law crack growth (P4-007).

    Miner cumulative damage D = n/N where N = C/Sᵐ (S-N curve). The Paris law gives
    the crack-growth rate da/dN = C·(ΔK)ᵐ with ΔK = S·√(π·a); the crack advances and
    is compared against the critical size a_c = (K_IC / (S·√π))². Fatigue life
    remaining = 1 − D.
    """
    n_fail = sn_c / (stress_range_mpa ** sn_m)
    miner_damage = cycles / n_fail
    a = min(0.3, crack_mm / 1000.0)                 # cap crack size (numerical guard)
    dk = stress_range_mpa * math.sqrt(math.pi * a)
    da_dn = paris_c * (dk ** paris_m)
    crack_new = min(500.0, crack_mm + da_dn * dcycles * 1000.0)
    a_crit = (fracture_toughness / (stress_range_mpa * math.sqrt(math.pi))) ** 2 * 1000.0
    return {"miner_damage": miner_damage, "crack_mm": crack_new, "crack_growth_rate": da_dn,
            "critical_crack_mm": a_crit, "fatigue_life_pct": max(0.0, 1.0 - miner_damage) * 100.0}


# ════════════════════════════════════════════════════════════════════
#  2.  BASE GEOMETRY  (tactical picture)
# ════════════════════════════════════════════════════════════════════
_FRIENDLY = [
    {"id": "CC", "name": "Command Center", "cat": "installation", "x": 50, "y": 50, "sector": "HQ"},
    {"id": "R1", "name": "Search Radar", "cat": "installation", "x": 34, "y": 30, "sector": "N"},
    {"id": "R2", "name": "Fire-Control Radar", "cat": "installation", "x": 68, "y": 34, "sector": "E"},
    {"id": "HG", "name": "Hangar Sqn", "cat": "air", "x": 30, "y": 66, "sector": "S"},
    {"id": "AM", "name": "Ammunition Store", "cat": "installation", "x": 70, "y": 68, "sector": "S"},
    {"id": "FU", "name": "Fuel Farm", "cat": "installation", "x": 80, "y": 52, "sector": "E"},
    {"id": "GV", "name": "QRF Vehicles", "cat": "land", "x": 22, "y": 48, "sector": "W"},
]
_SECTORS = [("N", "North"), ("E", "East"), ("S", "South"), ("W", "West"), ("HQ", "Headquarters")]
_MISSIONS = [
    {"id": "M1", "name": "CAP Patrol", "phase": "Execution"},
    {"id": "M2", "name": "Convoy Escort", "phase": "Planning"},
    {"id": "M3", "name": "ISR Sortie", "phase": "Execution"},
    {"id": "M4", "name": "Perimeter Watch", "phase": "Standing"},
]


# ════════════════════════════════════════════════════════════════════
#  3.  BASE TWIN
# ════════════════════════════════════════════════════════════════════
@dataclass
class DefenceBaseState:
    readiness: float = 0.6               # control 0..1 (operational tempo)
    jamming: float = 0.0                 # 0..1
    terrain_masking: float = 0.1         # 0..1
    ammo_heat: float = 0.0               # 0..1
    nbc_release: float = 0.0             # 0..1
    fuel: float = 82.0                   # %
    intrusions: float = 0.0              # tracked objects crossing the wire
    comms_fault: float = 0.0             # 0..1
    aircraft_hours_used: float = 0.0     # 0..1 toward service
    flight_hours_remaining: float = 42.0  # h to next service (worst airframe)
    threat: float = 2.0                  # 1..5
    fault_target: str = ""               # sector the active fault localises to
    hours: float = 0.0
    fault: str = "none"
    fault_severity: float = 0.0
    seed: int = 23
    _rng: random.Random = field(default=None, repr=False, compare=False)

    def rng(self):
        if self._rng is None:
            self._rng = random.Random(self.seed)
        return self._rng


_FAULTS = {
    "perimeter_intrusion": {"intrusions": 2, "threat": 2.0, "_sector": True},
    "jamming_attack":      {"jamming": 0.85, "threat": 1.5},
    "ammo_overheat":       {"ammo_heat": 0.9},
    "nbc_release":         {"nbc_release": 0.9, "_sector": True},
    "fuel_leak":           {"fuel": -70.0},
    "comms_outage":        {"comms_fault": 0.8},
    "aircraft_grounding":  {"aircraft_hours_used": 0.95},
    "heightened_alert":    {"readiness": 0.9, "threat": 2.0},
}

FAULTS = list(_FAULTS.keys())


class DefenceBasePhysics:
    def __init__(self, **options):
        self.opts = options or {}

    def init_state(self) -> DefenceBaseState:
        return DefenceBaseState()

    def inject(self, state: DefenceBaseState, fault: str, severity: float = 0.85) -> None:
        eff = max(0.0, min(1.0, float(severity)))
        state.fault = fault
        state.fault_severity = eff
        rng = state.rng()
        for attr, amount in _FAULTS.get(fault, {}).items():
            if attr == "_sector":
                state.fault_target = _SECTORS[rng.randrange(len(_SECTORS) - 1)][0]
            elif attr == "intrusions":
                state.intrusions = min(12.0, state.intrusions + amount * eff)
            elif attr == "threat":
                state.threat = min(5.0, state.threat + amount * eff)
            elif attr == "fuel":
                state.fuel = max(0.0, state.fuel + amount * eff)
            elif attr == "readiness":
                state.readiness = max(state.readiness, amount * eff)
            elif attr == "aircraft_hours_used":
                state.aircraft_hours_used = min(1.0, amount * eff)
                state.flight_hours_remaining = 42.0 * (1.0 - state.aircraft_hours_used)
            else:
                setattr(state, attr, min(1.2, getattr(state, attr) + amount * eff))

    def clear(self, state: DefenceBaseState) -> None:
        state.fault = "none"
        state.fault_severity = 0.0
        state.fault_target = ""
        state.jamming = 0.0
        state.ammo_heat = 0.0
        state.nbc_release = 0.0
        state.intrusions = 0.0
        state.comms_fault = 0.0
        state.aircraft_hours_used = 0.0
        state.flight_hours_remaining = 42.0
        state.threat = 2.0
        state.fuel = 82.0

    def forward(self, state: DefenceBaseState, dt: float = 1.0) -> dict:
        rng = state.rng()
        R = max(0.0, min(1.0, state.readiness))
        state.hours += dt / 3600.0
        state.fuel = max(0.0, state.fuel - dt / 3600.0 * (0.4 + 1.2 * R))
        if state.aircraft_hours_used < 1.0:
            state.flight_hours_remaining = max(0.0, state.flight_hours_remaining - dt / 3600.0 * (2.0 + 6.0 * R))

        # ── radar + jamming ──
        rp = radar_propagation(jamming=state.jamming, terrain_masking=state.terrain_masking)
        radar_coverage = rp["coverage_pct"]
        radar_range = rp["range_km"]

        # ── comms ──
        comms = max(0.0, 100.0 - 40.0 * state.comms_fault - 25.0 * state.jamming)

        # ── ammunition storage ──
        ammo_temp = 24.0 + 6.0 * R + 30.0 * state.ammo_heat
        ammo_humidity = 45.0 + 20.0 * state.ammo_heat
        cookoff_margin = 60.0 - ammo_temp        # cook-off ~60 °C reference

        # ── NBC ──
        nbc = nbc_contamination_spread(release_rate_gps=120.0 * state.nbc_release,
                                       wind_ms=4.0, stability="D")
        nbc_reading = nbc["concentration"] if state.nbc_release > 0 else 0.0
        nbc_plume = nbc["hazard_range_km"] if state.nbc_release > 0 else 0.0

        # ── air fleet ──
        aircraft_avail = max(0.0, 92.0 - 55.0 * state.aircraft_hours_used)
        sorties_ready = round(6 * (0.5 + 0.5 * R) * (aircraft_avail / 92.0))

        # ── force protection + mission readiness ──
        breaches = int(state.intrusions)
        tracked = int(state.intrusions + rng.randint(0, 2) + 2 * R)
        force_protection = min(5.0, 2.0 + breaches + 0.5 * state.threat)
        mission_readiness = max(0.0, 96.0 - 3.0 * breaches - 0.2 * (100.0 - comms)
                                - 0.25 * max(0.0, 70.0 - radar_coverage)
                                - 0.3 * max(0.0, 20.0 - state.fuel)
                                - 8.0 * (state.nbc_release > 0.4) - 6.0 * (aircraft_avail < 50.0))

        def j(v, frac):
            return jitter(rng, v, frac)

        return {
            SIGNALS["threat_level"]:       round(state.threat, 1),
            SIGNALS["perimeter_breaches"]: breaches,
            SIGNALS["tracked_objects"]:    int(max(0, tracked)),
            SIGNALS["radar_coverage"]:     round(j(radar_coverage, 0.01), 1),
            SIGNALS["radar_range"]:        round(j(radar_range, 0.02), 1),
            SIGNALS["jamming_level"]:      round(state.jamming * 100.0, 1),
            SIGNALS["comms_availability"]: round(j(comms, 0.005), 1),
            SIGNALS["ammo_temp"]:          round(j(ammo_temp, 0.01), 1),
            SIGNALS["ammo_humidity"]:      round(j(ammo_humidity, 0.02), 1),
            SIGNALS["cookoff_margin"]:     round(cookoff_margin, 1),
            SIGNALS["fuel_level"]:         round(state.fuel, 1),
            SIGNALS["nbc_reading"]:        round(nbc_reading, 3),
            SIGNALS["nbc_plume_range"]:    round(nbc_plume, 2),
            SIGNALS["aircraft_avail"]:     round(aircraft_avail, 1),
            SIGNALS["flight_hours"]:       round(state.flight_hours_remaining, 1),
            SIGNALS["sorties_ready"]:      int(max(0, sorties_ready)),
            SIGNALS["mission_readiness"]:  round(j(mission_readiness, 0.005), 1),
            SIGNALS["force_protection"]:   round(force_protection, 1),
        }

    def residuals(self, frame: dict) -> dict:
        return {SIGNALS["radar_coverage"]: frame.get(SIGNALS["radar_coverage"], 90.0) - 90.0}

    def health_index(self, frame: dict) -> float:
        if not frame:
            return 1.0

        return round(worst_health([
            margin_lo(frame.get(SIGNALS["radar_coverage"], 90.0), 90.0, redlines.radar_coverage_min),
            margin_lo(frame.get(SIGNALS["comms_availability"], 98.0), 98.0, redlines.comms_min),
            margin_lo(frame.get(SIGNALS["cookoff_margin"], 30.0), 30.0, redlines.cookoff_margin_min),
            margin_lo(frame.get(SIGNALS["fuel_level"], 82.0), 82.0, redlines.fuel_min),
            margin_hi(frame.get(SIGNALS["nbc_reading"], 0.0), 0.0, redlines.nbc_max),
            margin_hi(frame.get(SIGNALS["jamming_level"], 0.0), 0.0, redlines.jamming_max),
            margin_hi(frame.get(SIGNALS["perimeter_breaches"], 0), 0, 3),
            margin_lo(frame.get(SIGNALS["mission_readiness"], 95.0), 95.0, redlines.mission_readiness_min),
            margin_lo(frame.get(SIGNALS["flight_hours"], 42.0), 42.0, redlines.flight_hours_min),
        ]), 3)

    # ── live tactical map + mission board ──
    def _assets(self, state: DefenceBaseState) -> list:
        rng = random.Random(state.seed + 61)
        out = []
        for a in _FRIENDLY:
            targeted = state.fault_target == a["sector"]
            status = "critical" if (targeted and state.fault in ("nbc_release", "perimeter_intrusion")) else \
                     "warning" if (a["id"] == "AM" and state.ammo_heat > 0.5) or \
                     (a["id"] == "FU" and state.fuel < 25) or \
                     (a["id"].startswith("R") and state.jamming > 0.4) else "ok"
            out.append({"id": a["id"], "name": a["name"], "category": a["cat"],
                        "affiliation": "friend", "x": a["x"], "y": a["y"],
                        "sector": a["sector"], "status": status})
        # hostile / unknown tracks appear on intrusion
        n_track = int(state.intrusions)
        for i in range(n_track):
            ang = rng.uniform(0, 2 * math.pi)
            r = 40 - i * 6
            out.append({"id": f"TK{i + 1}", "name": f"Track {i + 1:03d}", "category": "land",
                        "affiliation": "hostile" if i == 0 else "unknown",
                        "x": round(50 + r * math.cos(ang), 1), "y": round(50 + r * math.sin(ang), 1),
                        "sector": state.fault_target or "N", "status": "critical"})
        return out

    def _missions(self, state: DefenceBaseState, frame: dict) -> list:
        rng = random.Random(state.seed + 41)
        readiness = frame.get(SIGNALS["mission_readiness"], 95.0)
        out = []
        for m in _MISSIONS:
            if m["id"] == "M4" and int(state.intrusions) > 0:
                health, status = "red", "Active"
            elif readiness < 70 or (state.nbc_release > 0.4 and m["id"] in ("M1", "M3")):
                health = "amber"
                status = m["phase"]
            else:
                health = "green"
                status = m["phase"]
            out.append({"id": m["id"], "name": m["name"], "status": status, "health": health,
                        "phase": m["phase"],
                        "assets": rng.sample(["CAP-1", "QRF", "AWACS", "Convoy-A", "UAV-7"],
                                             k=rng.randint(1, 3))})
        return out

    def network_state(self, state: DefenceBaseState) -> dict:
        frame = self._readonly_frame(state)
        return {
            "assets": self._assets(state),
            "missions": self._missions(state, frame),
            "sectors": [{"id": sid, "name": name,
                         "status": "critical" if state.fault_target == sid else "ok"}
                        for sid, name in _SECTORS],
            "threat_level": round(state.threat, 1),
            "force_protection": frame.get(SIGNALS["force_protection"]),
            "fault": state.fault,
        }

    def _readonly_frame(self, state: DefenceBaseState) -> dict:
        import copy
        st = copy.deepcopy(state)
        st._rng = random.Random(state.seed + 777)
        return self.forward(st, dt=0.0001)
