"""
ev/physics.py — EV component physics engines + a charging-network forward model.

Component engines (faithful, unit-testable) — shared with the battery-pack twin:
    battery_ecm()            — 2RC Thevenin equivalent circuit + SoC-OCV table,
                               temperature-dependent parameters
    battery_thermal()        — I²R ohmic heating coupled to a lumped thermal node
                               with liquid coolant
    battery_degradation()    — calendar ageing (Arrhenius) + cycle ageing (SEI
                               growth) → SoH fade
    charging_dynamics()      — CC-CV charging with cold preconditioning + DC
                               fast-charge power derating (OCPP setpoint)
    grid_transformer_aging() — IEC 60076-7 hot-spot temperature + insulation
                               loss-of-life
    ev_range_prediction()    — Wh/km consumption (aero/roll/grade/HVAC) corrected
                               for real-time SoH
    pmsm_motor()             — d-q axis PMSM torque/speed/efficiency + winding
                               temperature + iron loss

EVChargingNetworkPhysics — the charging-network twin: stations, chargers, grid
connection, transformer, solar and V2G driving ~20 KPIs. `network_state` returns
the geo map, the grid load curve and the V2G trading view.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

# ────────────────────────────────────────────────────────────────────
#  Signals (charging-network domain)
# ────────────────────────────────────────────────────────────────────
SIGNALS = {
    "network_load":       "ev:networkLoad",
    "transformer_load":   "ev:transformerLoad",
    "active_sessions":    "ev:activeSessions",
    "available_chargers": "ev:availableChargers",
    "avg_charge_power":   "ev:avgChargePower",
    "grid_voltage":       "ev:gridVoltage",
    "grid_frequency":     "ev:gridFrequency",
    "transformer_hotspot": "ev:transformerHotspot",
    "transformer_aging":  "ev:transformerAging",
    "connector_temp":     "ev:connectorTemp",
    "charger_faults":     "ev:chargerFaults",
    "energy_delivered":   "ev:energyDelivered",
    "solar_output":       "ev:solarOutput",
    "solar_irradiance":   "ev:solarIrradiance",
    "self_consumption":   "ev:selfConsumption",
    "v2g_export":         "ev:v2gExport",
    "spot_price":         "ev:spotPrice",
    "v2g_revenue":        "ev:v2gRevenue",
    "fleet_soc":          "ev:stateOfCharge",
    "fleet_soh":          "ev:stateOfHealth",
}

UNITS = {
    SIGNALS["network_load"]: "kW", SIGNALS["transformer_load"]: "%",
    SIGNALS["active_sessions"]: "", SIGNALS["available_chargers"]: "%",
    SIGNALS["avg_charge_power"]: "kW", SIGNALS["grid_voltage"]: "%",
    SIGNALS["grid_frequency"]: "Hz", SIGNALS["transformer_hotspot"]: "DEG_C",
    SIGNALS["transformer_aging"]: "x", SIGNALS["connector_temp"]: "DEG_C",
    SIGNALS["charger_faults"]: "", SIGNALS["energy_delivered"]: "kWh",
    SIGNALS["solar_output"]: "kW", SIGNALS["solar_irradiance"]: "W/m2",
    SIGNALS["self_consumption"]: "%", SIGNALS["v2g_export"]: "kW",
    SIGNALS["spot_price"]: "$/MWh", SIGNALS["v2g_revenue"]: "$/h",
    SIGNALS["fleet_soc"]: "%", SIGNALS["fleet_soh"]: "%",
}


@dataclass
class Redlines:
    transformer_load_max: float = 100.0    # % rated (trip)
    transformer_hotspot_max: float = 110.0  # °C (IEC insulation)
    transformer_aging_max: float = 4.0      # x nominal
    grid_voltage_min: float = 90.0          # % nominal
    grid_freq_min: float = 49.5             # Hz
    grid_freq_max: float = 50.5             # Hz
    connector_temp_max: float = 60.0        # °C
    charger_faults_max: float = 3.0
    available_chargers_min: float = 40.0    # %


redlines = Redlines()


# ════════════════════════════════════════════════════════════════════
#  1.  COMPONENT PHYSICS ENGINES
# ════════════════════════════════════════════════════════════════════
# SoC → open-circuit voltage per cell (Li-ion NMC, 3.0–4.2 V), from cell data.
_OCV_TABLE = [(0.0, 3.20), (0.05, 3.45), (0.1, 3.55), (0.2, 3.63), (0.3, 3.68),
              (0.4, 3.73), (0.5, 3.79), (0.6, 3.86), (0.7, 3.94), (0.8, 4.03),
              (0.9, 4.12), (1.0, 4.20)]


def soc_ocv(soc: float) -> float:
    """Interpolate cell OCV from the SoC-OCV lookup table (SoC in 0..1)."""
    s = max(0.0, min(1.0, soc))
    for i in range(1, len(_OCV_TABLE)):
        s0, v0 = _OCV_TABLE[i - 1]
        s1, v1 = _OCV_TABLE[i]
        if s <= s1:
            f = (s - s0) / (s1 - s0) if s1 > s0 else 0.0
            return v0 + (v1 - v0) * f
    return _OCV_TABLE[-1][1]


def battery_ecm(soc: float, current_a: float, temp_c: float = 25.0,
                v1: float = 0.0, v2: float = 0.0, r0: float = 0.018,
                r1: float = 0.012, c1: float = 2000.0, r2: float = 0.008,
                c2: float = 6000.0, dt: float = 1.0) -> dict:
    """2RC Thevenin equivalent-circuit cell model.

    Terminal voltage = OCV(SoC) − I·R0 − V1 − V2, with two RC branches capturing
    the fast/slow polarisation transients and Arrhenius temperature-dependent
    resistances (higher when cold). `current_a` > 0 is discharge. Returns the new
    RC states so the caller can integrate over time (P2-003).
    """
    tfac = math.exp(1800.0 * (1.0 / (temp_c + 273.15) - 1.0 / 298.15))
    tfac = max(0.6, min(4.0, tfac))
    R0, R1, R2 = r0 * tfac, r1 * tfac, r2 * tfac
    v1n = v1 + dt * (current_a / c1 - v1 / (R1 * c1))
    v2n = v2 + dt * (current_a / c2 - v2 / (R2 * c2))
    ocv = soc_ocv(soc)
    terminal = ocv - current_a * R0 - v1n - v2n
    ohmic_loss_w = current_a * current_a * R0 + v1n * current_a + v2n * current_a
    return {"terminal_voltage": terminal, "ocv": ocv, "v1": v1n, "v2": v2n,
            "r0": R0, "ohmic_loss_w": max(0.0, ohmic_loss_w)}


def battery_thermal(temp_c: float, ohmic_loss_w: float, coolant_in_c: float = 20.0,
                    flow_lpm: float = 10.0, thermal_mass_j_per_k: float = 9000.0,
                    ua_base: float = 8.0, dt: float = 1.0) -> dict:
    """Lumped-node battery thermal model.

    Cell/module temperature rises with ECM ohmic loss and is removed by a liquid
    coolant loop whose effectiveness scales with flow rate: dT/dt = (Q_gen −
    UA(flow)·(T − T_coolant)) / (m·cp) (P2-004).
    """
    ua = ua_base * (0.4 + 0.6 * min(1.0, flow_lpm / 12.0))
    q_cool = ua * (temp_c - coolant_in_c)
    dtemp = (ohmic_loss_w - q_cool) / max(1.0, thermal_mass_j_per_k) * dt
    return {"temperature": temp_c + dtemp, "q_gen_w": ohmic_loss_w,
            "q_cool_w": q_cool, "dT_dt": dtemp / max(1e-6, dt) * 60.0}  # °C/min


def battery_degradation(temp_c: float, soc: float, c_rate: float, dt_h: float = 1.0,
                        soh: float = 100.0) -> dict:
    """Battery state-of-health fade: calendar + cycle ageing.

    Calendar ageing follows an Arrhenius law (accelerated by heat and high SoC);
    cycle ageing models SEI-layer growth per equivalent full cycle (accelerated by
    C-rate and heat). Returns the SoH decrement over dt_h (P2-005).
    """
    arr = math.exp(-24000.0 / 8.314 * (1.0 / (temp_c + 273.15) - 1.0 / 298.15))
    cal_fade = 0.0016 * arr * (0.4 + 0.6 * soc) * dt_h
    cyc_fade = 0.0009 * c_rate * (1.0 + 0.03 * max(0.0, temp_c - 35.0)) * dt_h
    return {"soh": max(0.0, soh - cal_fade - cyc_fade),
            "calendar_fade": cal_fade, "cycle_fade": cyc_fade}


def charging_dynamics(soc: float, temp_c: float, pack_voltage: float,
                      i_max_a: float = 250.0, p_max_kw: float = 150.0,
                      connector_temp_c: float = 30.0, cv_soc: float = 0.8) -> dict:
    """CC-CV charging with cold preconditioning + DC fast-charge derating.

    Constant current below the CV knee, then a taper as the pack approaches full.
    Cold cells are current-limited (preconditioning); the DC fast charger derates
    its power as the connector heats. The delivered power is the OCPP setpoint the
    charger reports back (P2-006).
    """
    cold_derate = 0.25 if temp_c < 5.0 else 0.55 if temp_c < 12.0 else 1.0
    if soc < cv_soc:
        i_cmd = i_max_a * cold_derate
        mode = "CC"
    else:
        i_cmd = i_max_a * cold_derate * max(0.08, 1.0 - (soc - cv_soc) / (1.0 - cv_soc) * 0.9)
        mode = "CV"
    temp_derate = 1.0 if connector_temp_c < 50.0 else max(0.25, 1.0 - (connector_temp_c - 50.0) / 40.0)
    power_kw = min(p_max_kw, i_cmd * pack_voltage / 1000.0) * temp_derate
    i_actual = power_kw * 1000.0 / max(1.0, pack_voltage)
    return {"current_a": i_actual, "power_kw": power_kw, "mode": mode,
            "cold_derate": cold_derate, "temp_derate": temp_derate}


def grid_transformer_aging(load_pct: float, ambient_c: float = 25.0, dt_h: float = 1.0,
                           total_rise_k: float = 78.0, exponent: float = 1.6) -> dict:
    """Distribution-transformer hot-spot + insulation loss-of-life (IEC 60076-7).

    Hot-spot temperature = ambient + total_rise·(load fraction)^y. The relative
    ageing rate doubles every 6 °C above the 98 °C reference (V = 2^((θh−98)/6));
    loss-of-life over the interval = ageing rate · dt (P2-007).
    """
    k = max(0.0, load_pct / 100.0)
    hotspot = ambient_c + total_rise_k * k ** exponent
    aging_rate = 2.0 ** ((hotspot - 98.0) / 6.0)
    return {"hotspot_c": hotspot, "aging_rate": aging_rate,
            "loss_of_life_h": aging_rate * dt_h}


def ev_range_prediction(soc: float, soh: float, capacity_kwh: float = 75.0,
                        speed_kmh: float = 100.0, wind_kmh: float = 0.0,
                        grade_pct: float = 0.0, hvac_kw: float = 1.5,
                        mass_kg: float = 2000.0, cd_a: float = 0.64,
                        crr: float = 0.011, aux_kw: float = 0.3,
                        drivetrain_eff: float = 0.88) -> dict:
    """Real-time range estimate from a Wh/km consumption model.

    Sums aerodynamic (½ρ·CdA·v_rel²·v), rolling (Crr·m·g·v), grade (m·g·sinθ·v) and
    HVAC/aux power, divided by speed to get Wh/km, then multiplies usable energy
    (capacity·SoC·SoH) to give the range (P2-008).
    """
    v = max(0.1, speed_kmh / 3.6)
    v_rel = max(0.0, (speed_kmh + wind_kmh) / 3.6)
    p_aero = 0.5 * 1.2 * cd_a * v_rel * v_rel * v
    p_roll = crr * mass_kg * 9.81 * v
    p_grade = mass_kg * 9.81 * (grade_pct / 100.0) * v
    p_drive = max(0.0, p_aero + p_roll + p_grade) / drivetrain_eff + (hvac_kw + aux_kw) * 1000.0
    wh_per_km = p_drive / v / 3.6
    usable_kwh = capacity_kwh * (soc / 100.0) * (soh / 100.0)
    range_km = usable_kwh * 1000.0 / max(1.0, wh_per_km)
    return {"wh_per_km": wh_per_km, "range_km": range_km, "usable_kwh": usable_kwh}


def pmsm_motor(torque_nm: float, speed_rpm: float, winding_temp_c: float = 60.0,
               pole_pairs: int = 4, flux_wb: float = 0.15, rs_ohm: float = 0.02,
               k_iron: float = 0.6) -> dict:
    """Permanent-magnet synchronous motor (d-q axis, id=0 control).

    Torque τ = 1.5·p·λ·iq gives the quadrature current; copper loss = 1.5·Rs·iq²
    (temperature-corrected), iron loss ∝ ω^1.6, and efficiency = P_mech/P_elec.
    Winding temperature bounds continuous operation (P2-009).
    """
    omega = speed_rpm * 2.0 * math.pi / 60.0
    iq = torque_nm / (1.5 * pole_pairs * flux_wb)
    rs = rs_ohm * (1.0 + 0.004 * (winding_temp_c - 25.0))
    p_cu = 1.5 * rs * iq * iq
    p_fe = k_iron * (omega / 100.0) ** 1.6
    p_mech = max(0.0, torque_nm * omega)
    p_elec = p_mech + p_cu + p_fe
    eff = 100.0 * p_mech / p_elec if p_elec > 1.0 else 0.0
    return {"current_q_a": iq, "copper_loss_w": p_cu, "iron_loss_w": p_fe,
            "elec_power_kw": p_elec / 1000.0, "efficiency": eff}


# ════════════════════════════════════════════════════════════════════
#  2.  CHARGING-NETWORK GEOMETRY
# ════════════════════════════════════════════════════════════════════
_STATIONS = [
    {"id": "S1", "name": "City Hub", "x": 30, "y": 34, "chargers": 8, "kw": 150},
    {"id": "S2", "name": "Airport", "x": 72, "y": 26, "chargers": 12, "kw": 350},
    {"id": "S3", "name": "Riverside Mall", "x": 48, "y": 58, "chargers": 6, "kw": 75},
    {"id": "S4", "name": "Bus Depot", "x": 20, "y": 72, "chargers": 10, "kw": 60},
    {"id": "S5", "name": "Highway Rapid", "x": 84, "y": 64, "chargers": 6, "kw": 350},
    {"id": "S6", "name": "Suburb Park", "x": 58, "y": 16, "chargers": 4, "kw": 50},
]
_TRANSFORMER_RATED_KW = 2500.0
_SOLAR_RATED_KW = 800.0


def _total_chargers():
    return sum(s["chargers"] for s in _STATIONS)


# ════════════════════════════════════════════════════════════════════
#  3.  CHARGING-NETWORK TWIN
# ════════════════════════════════════════════════════════════════════
@dataclass
class EVNetworkState:
    demand_level: float = 0.55           # control 0..1 (charging demand)
    hour: float = 13.0                   # time of day (drives solar + price)
    connector_extra: float = 0.0         # 0..1 extra connector heating
    grid_sag: float = 0.0                # 0..1 grid voltage depression
    transformer_extra: float = 0.0       # 0..1 extra transformer loading/heat
    charger_faults: float = 0.0
    solar_curtail: float = 0.0           # 0..1 solar output loss
    price_spike: float = 0.0             # 0..1 spot-price event
    shed: float = 0.0                    # 0..1 load shed applied
    energy_kwh: float = 0.0
    hours: float = 0.0
    fault: str = "none"
    fault_severity: float = 0.0
    seed: int = 17
    _rng: random.Random = field(default=None, repr=False, compare=False)

    def rng(self):
        if self._rng is None:
            self._rng = random.Random(self.seed)
        return self._rng


_FAULTS = {
    "charger_overheat":     {"connector_extra": 0.8},
    "grid_weak":            {"grid_sag": 0.8},
    "grid_overload":        {"demand_level": 0.98, "solar_curtail": 1.0, "transformer_extra": 0.4},
    "transformer_overheat": {"transformer_extra": 0.9},
    "charger_fault":        {"charger_faults": 4},
    "solar_dropout":        {"solar_curtail": 1.0},
    "price_spike":          {"price_spike": 0.9},
    "demand_surge":         {"demand_level": 0.9},
}

FAULTS = list(_FAULTS.keys())


def _solar_fraction(hour: float) -> float:
    """Clear-sky irradiance fraction (0 at night, peak at solar noon)."""
    h = hour % 24.0
    if h < 6.0 or h > 19.0:
        return 0.0
    return max(0.0, math.sin(math.pi * (h - 6.0) / 13.0))


def _price_at(hour: float, spike: float) -> float:
    """A day-ahead spot price ($/MWh): overnight trough, evening peak, + spike."""
    h = hour % 24.0
    base = 55.0 + 45.0 * math.sin(math.pi * (h - 8.0) / 12.0)
    if 17.0 <= h <= 21.0:
        base += 60.0
    return max(15.0, base + spike * 220.0)


class EVChargingNetworkPhysics:
    def __init__(self, **options):
        self.opts = options or {}
        self.total_chargers = _total_chargers()

    def init_state(self) -> EVNetworkState:
        return EVNetworkState()

    def inject(self, state: EVNetworkState, fault: str, severity: float = 0.85) -> None:
        eff = max(0.0, min(1.0, float(severity)))
        state.fault = fault
        state.fault_severity = eff
        for attr, amount in _FAULTS.get(fault, {}).items():
            if attr == "demand_level":
                state.demand_level = max(state.demand_level, amount * eff)
            elif attr == "charger_faults":
                state.charger_faults = min(20.0, state.charger_faults + amount * eff)
            else:
                setattr(state, attr, min(1.2, getattr(state, attr) + amount * eff))

    def clear(self, state: EVNetworkState) -> None:
        state.fault = "none"
        state.fault_severity = 0.0
        state.connector_extra = 0.0
        state.grid_sag = 0.0
        state.transformer_extra = 0.0
        state.charger_faults = 0.0
        state.solar_curtail = 0.0
        state.price_spike = 0.0
        state.shed = 0.0

    def forward(self, state: EVNetworkState, dt: float = 1.0) -> dict:
        rng = state.rng()
        D = max(0.0, min(1.0, state.demand_level))
        state.hours += dt / 3600.0
        state.hour = (state.hour + dt / 3600.0) % 24.0

        # ── charging demand ──
        cap = _TRANSFORMER_RATED_KW
        faulted = int(state.charger_faults)
        available = max(0, self.total_chargers - faulted)
        active = min(available, round(self.total_chargers * (0.3 + 0.5 * D)))
        # effective installed demand (diversified), reduced by any load shed
        raw_load = 2600.0 * (0.22 + 0.55 * D)
        network_load = raw_load * (1.0 - 0.35 * state.shed)
        avg_power = network_load / max(1, active)

        # ── solar ──
        sun = _solar_fraction(state.hour) * (1.0 - state.solar_curtail)
        solar_kw = _SOLAR_RATED_KW * sun
        irradiance = 1000.0 * sun
        grid_import = max(0.0, network_load - solar_kw)
        self_consumption = 100.0 * min(solar_kw, network_load) / max(1.0, solar_kw) if solar_kw > 1 else 0.0

        # ── transformer (IEC 60076-7) ──
        transformer_load = min(170.0, 100.0 * (grid_import / cap) + 45.0 * state.transformer_extra)
        # grid-overload shedding keeps the transformer under its rating (updated
        # for the NEXT tick from this tick's loading)
        overloaded = transformer_load > 92.0 or state.fault == "grid_overload"
        state.shed = min(1.0, state.shed + 0.15) if overloaded else max(0.0, state.shed - 0.05)
        tr = grid_transformer_aging(transformer_load, ambient_c=25.0 + 8.0 * _solar_fraction(state.hour))
        hotspot = tr["hotspot_c"] + 50.0 * state.transformer_extra
        aging = 2.0 ** ((hotspot - 98.0) / 6.0)

        # ── grid quality ──
        grid_voltage = 100.0 - 6.0 * max(0.0, transformer_load - 80.0) / 20.0 - 15.0 * state.grid_sag
        grid_frequency = 50.0 - 0.15 * state.grid_sag - 0.05 * max(0.0, transformer_load - 90.0) / 10.0

        # ── connectors ──
        connector_temp = 28.0 + 22.0 * (avg_power / 200.0) + 40.0 * state.connector_extra
        charger_derate = 1.0 if connector_temp < 50.0 else max(0.3, 1.0 - (connector_temp - 50.0) / 40.0)

        # ── V2G ──
        price = _price_at(state.hour, state.price_spike)
        v2g_worth = price > 110.0
        v2g_export = (120.0 + 180.0 * state.price_spike) * (1.0 if v2g_worth else 0.0)
        v2g_revenue = v2g_export * price / 1000.0

        # ── fleet ──
        fleet_soc = 68.0 - 18.0 * D + 6.0 * (sun > 0.3)
        fleet_soh = 93.5

        state.energy_kwh += network_load * dt / 3600.0

        def j(v, frac):
            return v * (1.0 + rng.uniform(-frac, frac))

        return {
            SIGNALS["network_load"]:       round(max(0.0, j(network_load, 0.02)), 1),
            SIGNALS["transformer_load"]:   round(j(transformer_load, 0.01), 1),
            SIGNALS["active_sessions"]:    int(active),
            SIGNALS["available_chargers"]: round(100.0 * available / self.total_chargers, 1),
            SIGNALS["avg_charge_power"]:   round(j(avg_power, 0.02), 1),
            SIGNALS["grid_voltage"]:       round(j(grid_voltage, 0.003), 1),
            SIGNALS["grid_frequency"]:     round(j(grid_frequency, 0.0006), 2),
            SIGNALS["transformer_hotspot"]: round(j(hotspot, 0.01), 1),
            SIGNALS["transformer_aging"]:  round(aging, 2),
            SIGNALS["connector_temp"]:     round(j(connector_temp, 0.01), 1),
            SIGNALS["charger_faults"]:     int(faulted),
            SIGNALS["energy_delivered"]:   round(state.energy_kwh, 1),
            SIGNALS["solar_output"]:       round(max(0.0, j(solar_kw, 0.02)), 1),
            SIGNALS["solar_irradiance"]:   round(max(0.0, irradiance), 0),
            SIGNALS["self_consumption"]:   round(self_consumption, 1),
            SIGNALS["v2g_export"]:         round(v2g_export, 1),
            SIGNALS["spot_price"]:         round(price, 1),
            SIGNALS["v2g_revenue"]:        round(v2g_revenue, 2),
            SIGNALS["fleet_soc"]:          round(fleet_soc, 1),
            SIGNALS["fleet_soh"]:          round(fleet_soh, 1),
        }

    def residuals(self, frame: dict) -> dict:
        return {
            SIGNALS["grid_voltage"]: frame.get(SIGNALS["grid_voltage"], 100.0) - 100.0,
            SIGNALS["grid_frequency"]: frame.get(SIGNALS["grid_frequency"], 50.0) - 50.0,
        }

    def health_index(self, frame: dict) -> float:
        if not frame:
            return 1.0

        def hi(v, nominal, limit):
            return max(0.0, min(1.0, (limit - v) / (limit - nominal)))

        def lo(v, nominal, limit):
            return max(0.0, min(1.0, (v - limit) / (nominal - limit)))

        margins = [
            hi(frame.get(SIGNALS["transformer_load"], 65.0), 65.0, redlines.transformer_load_max),
            hi(frame.get(SIGNALS["transformer_hotspot"], 60.0), 60.0, redlines.transformer_hotspot_max),
            hi(frame.get(SIGNALS["transformer_aging"], 0.2), 0.2, redlines.transformer_aging_max),
            lo(frame.get(SIGNALS["grid_voltage"], 100.0), 100.0, redlines.grid_voltage_min),
            hi(frame.get(SIGNALS["connector_temp"], 34.0), 34.0, redlines.connector_temp_max),
            lo(frame.get(SIGNALS["available_chargers"], 100.0), 100.0, redlines.available_chargers_min),
        ]
        return round(min(margins), 3)

    # ── live views: geo map + grid load curve + V2G trading ──
    def _stations(self, state: EVNetworkState) -> list:
        rng = random.Random(state.seed + 23)
        D = max(0.0, min(1.0, state.demand_level))
        faulted_total = int(state.charger_faults)
        out = []
        for si, s in enumerate(_STATIONS):
            faulted = 1 if (faulted_total > 0 and si == 0) else 0
            active = min(s["chargers"] - faulted, round(s["chargers"] * (0.3 + 0.5 * D)) + rng.randint(-1, 1))
            active = max(0, active)
            avail = s["chargers"] - faulted - active
            load = active * s["kw"] * (0.35 + 0.25 * D) * (1.0 - 0.4 * state.shed)
            util = active / max(1, s["chargers"])
            status = ("critical" if faulted else "warning" if util > 0.85 else "ok")
            out.append({"id": s["id"], "name": s["name"], "x": s["x"], "y": s["y"],
                        "chargers_total": s["chargers"], "chargers_active": active,
                        "chargers_available": max(0, avail), "chargers_faulted": faulted,
                        "max_kw": s["kw"], "load_kw": round(load, 1),
                        "utilisation": round(util * 100, 0), "status": status})
        return out

    def _load_curve(self, state: EVNetworkState) -> list:
        """A 24-hour demand vs solar vs transformer-capacity profile with a
        demand-response window (P2-022)."""
        D = max(0.0, min(1.0, state.demand_level))
        curve = []
        for h in range(24):
            shape = 0.35 + 0.5 * max(0.0, math.sin(math.pi * (h - 6) / 15.0)) + (0.35 if 17 <= h <= 20 else 0.0)
            demand = _TRANSFORMER_RATED_KW * 0.7 * shape * (0.6 + 0.6 * D)
            solar = _SOLAR_RATED_KW * _solar_fraction(h)
            dr = 17 <= h <= 20 and demand > _TRANSFORMER_RATED_KW * 0.85
            curve.append({"hour": h, "demand_kw": round(demand, 0), "solar_kw": round(solar, 0),
                          "grid_kw": round(max(0, demand - solar), 0),
                          "capacity_kw": _TRANSFORMER_RATED_KW, "dr_event": dr})
        return curve

    def _v2g(self, state: EVNetworkState) -> dict:
        """Spot price curve + recommended V2G export windows (P2-023)."""
        price_curve = [{"hour": h, "price": round(_price_at(h, state.price_spike if 17 <= h <= 21 else 0.0), 1)}
                       for h in range(24)]
        windows = []
        for h in range(24):
            p = _price_at(h, state.price_spike if 17 <= h <= 21 else 0.0)
            if p > 110.0:
                export_kw = 150.0 + 200.0 * state.price_spike
                windows.append({"start": h, "end": h + 1, "price": round(p, 1),
                                "export_kw": round(export_kw, 0),
                                "revenue": round(export_kw * p / 1000.0, 2),
                                "recommended": p > 130.0})
        now_price = _price_at(state.hour, state.price_spike)
        return {"spot_price": round(now_price, 1), "price_curve": price_curve,
                "export_windows": windows,
                "recommendation": ("EXPORT — spot price above threshold" if now_price > 130.0
                                   else "HOLD — charge, price below export threshold"),
                "pending_approval": now_price > 130.0,
                "total_revenue": round(sum(w["revenue"] for w in windows), 2)}

    def network_state(self, state: EVNetworkState) -> dict:
        stations = self._stations(state)
        return {
            "stations": stations,
            "grid": {"transformer_rated_kw": _TRANSFORMER_RATED_KW,
                     "load_kw": round(sum(s["load_kw"] for s in stations), 1),
                     "solar_rated_kw": _SOLAR_RATED_KW},
            "load_curve": self._load_curve(state),
            "v2g": self._v2g(state),
            "fault": state.fault,
        }
