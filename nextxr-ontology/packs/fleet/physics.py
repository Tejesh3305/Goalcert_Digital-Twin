"""
physics.py — tram / light-rail fleet-network digital-twin physics.

A first-principles-flavoured model of a whole tram network: rolling stock
(traction, brakes, doors, pantographs, HVAC, bogies), traction power (overhead
line + substations), track & points, signalling, and service operations
(headway, dwell, on-time performance, passenger demand).

Used exactly like the EDM / turbine twins:

  1. EXPECTED / RESIDUAL — given the service level and demand, compute what the
     network speed and traction power *should* be. The residual (measured -
     expected) is the earliest congestion / blockage / power-loss signal: if
     trams are on the road but the realised network speed falls well short of
     what the timetable predicts, something physical changed (blockage, TSRs,
     signal degradation) before any single hard limit trips.

  2. FORWARD SIM — given a single "service_level" command (fraction of the
     timetable being operated, 0..1) plus degradation/fault state, produce a
     physically consistent network telemetry frame at 1 Hz — including live
     per-vehicle positions for the network map (network_state()).

Pure stdlib. Signal keys are canonical CURIEs (fleet:*).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .network import get_network

FLEET = "fleet:"

SIGNALS = {
    "otp":           "fleet:onTimePerformance",   # % services on time
    "headway":       "fleet:headwayAdherence",    # % headways within band
    "avg_speed":     "fleet:networkSpeed",        # km/h commercial speed
    "fleet_avail":   "fleet:fleetAvailability",   # % of fleet serviceable
    "in_service":    "fleet:tramsInService",      # vehicles on the road
    "pax_load":      "fleet:passengerLoad",       # % average load factor
    "dwell":         "fleet:avgDwellTime",        # s average stop dwell
    "energy":        "fleet:tractionPower",       # MW network traction draw
    "regen":         "fleet:regenShare",          # % braking energy recovered
    "ohl_v":         "fleet:overheadVoltage",     # V DC at worst section
    "sub_load":      "fleet:substationLoad",      # % worst substation load
    "track_temp":    "fleet:railTemperature",     # C worst rail temp
    "switch_faults": "fleet:switchFaults",        # count degraded points
    "signal_faults": "fleet:signalFaults",        # count active signal faults
    "door_faults":   "fleet:doorFaults",          # count vehicles w/ door faults
    "brake_wear":    "fleet:brakeWear",           # % fleet-average pad wear
    "panto_wear":    "fleet:pantographWear",      # % avg carbon-strip wear
    "traction_temp": "fleet:tractionMotorTemp",   # C fleet-avg motor temp
    "vib":           "fleet:bogieVibration",      # g avg bogie vibration
    "delay":         "fleet:networkDelay",        # min total accumulated delay
    "incidents":     "fleet:activeIncidents",     # count open incidents
    "hvac_load":     "fleet:hvacLoad",            # % saloon HVAC load
}

UNITS = {
    "fleet:onTimePerformance": "PERCENT",
    "fleet:headwayAdherence": "PERCENT",
    "fleet:networkSpeed": "KM-PER-HR",
    "fleet:fleetAvailability": "PERCENT",
    "fleet:tramsInService": "COUNT",
    "fleet:passengerLoad": "PERCENT",
    "fleet:avgDwellTime": "SEC",
    "fleet:tractionPower": "MegaW",
    "fleet:regenShare": "PERCENT",
    "fleet:overheadVoltage": "VOLT",
    "fleet:substationLoad": "PERCENT",
    "fleet:railTemperature": "DEG_C",
    "fleet:switchFaults": "COUNT",
    "fleet:signalFaults": "COUNT",
    "fleet:doorFaults": "COUNT",
    "fleet:brakeWear": "PERCENT",
    "fleet:pantographWear": "PERCENT",
    "fleet:tractionMotorTemp": "DEG_C",
    "fleet:bogieVibration": "G",
    "fleet:networkDelay": "MIN",
    "fleet:activeIncidents": "COUNT",
    "fleet:hvacLoad": "PERCENT",
}


@dataclass
class DesignPoint:
    """Nominal (healthy, standard-timetable) operating point of the network."""
    base_speed: float = 16.0       # km/h — commercial speed incl. stops
    base_dwell: float = 24.0       # s    — average stop dwell
    ohl_v: float = 600.0           # V DC — nominal overhead voltage
    per_tram_kw: float = 95.0      # kW   — average draw per in-service tram
    regen: float = 22.0            # %    — regenerated braking energy share
    otp: float = 88.0              # %    — healthy on-time performance
    headway: float = 92.0          # %    — healthy headway adherence
    demand: float = 0.55           # 0..1 — typical inter-peak passenger demand
    ambient: float = 22.0          # C    — typical Melbourne day
    service_frac: float = 0.78     # fraction of fleet required at full timetable
    hvac_setpoint: float = 22.0    # C saloon setpoint


@dataclass
class Redlines:
    otp_min: float = 65.0          # %  — below: service collapse
    headway_min: float = 70.0      # %  — below: bunching / gaps
    ohl_v_min: float = 520.0       # V  — below: traction undervoltage trips
    sub_load_max: float = 95.0     # %  — above: substation trip risk
    track_temp_max: float = 47.0   # C  — above: rail-buckling risk (TSRs forced)
    vib_max: float = 1.20          # g  — above: wheel-flat / track-defect alarm
    brake_wear_max: float = 70.0   # %  — above: fleet braking margin gone
    panto_wear_max: float = 75.0   # %  — above: OHL de-wirement / arcing risk
    door_faults_max: float = 10.0  # count — above: boarding times blow out
    signal_faults_max: float = 5.0 # count — above: junctions on manual working
    fleet_avail_min: float = 78.0  # %  — below: can't cover the timetable
    delay_max: float = 120.0       # min — above: network-wide disruption
    traction_temp_max: float = 105.0  # C — above: traction derate


redlines = Redlines()


@dataclass
class FleetState:
    """Slowly-varying network state the forward sim integrates."""
    service_level: float = 0.85      # 0..1 fraction of timetable operated (control)
    demand: float = 0.55             # 0..1 passenger demand (scenario input)
    ambient: float = 22.0            # C   ambient temperature (scenario input)
    ohl_health: float = 1.0          # 1=ok 0=failed overhead-line condition
    sub_derate: float = 0.0          # 0..1 substation capacity lost
    track_wear: float = 0.08         # 0..1 track surface degradation
    switch_health: float = 1.0       # 1=ok points/switches condition
    signal_health: float = 1.0       # 1=ok signalling condition
    brake_wear: float = 0.22         # 0..1 fleet-average brake pad wear
    panto_wear: float = 0.18         # 0..1 fleet-average pantograph wear
    door_health: float = 1.0         # 1=ok door systems condition
    hvac_health: float = 1.0         # 1=ok saloon HVAC condition
    wheel_flats: float = 0.05        # 0..1 wheel-flat prevalence
    delay_min: float = 0.0           # accumulated network delay (recovers)
    run_hours: float = 0.0
    fault: str = "none"
    fault_severity: float = 0.0
    extras: dict = field(default_factory=dict)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


class FleetPhysics:
    """Stateless network relations (expected/residual) + a stateful forward sim
    with live per-vehicle positions for the map."""

    def __init__(self, design: DesignPoint | None = None,
                 limits: Redlines | None = None, seed: int = 0,
                 network=None):
        self.d = design or DesignPoint()
        self.lim = limits or redlines
        self.rng = random.Random(seed)
        self.net = get_network(network)          # normalised network spec
        self._vehicles: list[dict] = []          # live map entities
        self._sub_cap_mw = sum(s.get("capacity_mw", 4.0)
                               for s in self.net.get("substations", [])) or 40.0

    # ── 1. Service relations (expected behaviour) ────────────────────

    def scheduled_trams(self, service_level: float) -> int:
        """Vehicles the timetable wants on the road at a service level."""
        return int(round(self.net["fleet_size"] * self.d.service_frac
                         * _clamp(service_level)))

    def expected_speed(self, demand: float, signal_health: float,
                       track_temp: float, congestion: float) -> float:
        """Commercial network speed (km/h) the timetable physics predicts.
        Falls with passenger demand (dwell), degraded signalling (junction
        throughput), heat TSRs, and street congestion."""
        v = self.d.base_speed
        v *= (1.0 - 0.22 * _clamp(demand) ** 1.5)          # dwell + boarding drag
        v *= (0.55 + 0.45 * _clamp(signal_health))         # junction throughput
        if track_temp > self.lim.track_temp_max - 5.0:     # heat TSRs bite early
            v *= max(0.45, 1.0 - (track_temp - (self.lim.track_temp_max - 5.0)) * 0.06)
        v *= (1.0 - 0.35 * _clamp(congestion))
        return max(3.0, v)

    def expected_power_mw(self, in_service: int, avg_speed: float,
                          hvac_load: float, regen_share: float) -> float:
        """Network traction draw (MW): per-tram base draw scaled by how hard
        the fleet is working (speed vs design) plus HVAC, minus regeneration."""
        drive = self.d.per_tram_kw * (0.55 + 0.45 * avg_speed / self.d.base_speed)
        hvac = 12.0 * (hvac_load / 100.0)                  # kW per tram
        gross = in_service * (drive + hvac) / 1000.0
        return max(0.0, gross * (1.0 - 0.01 * regen_share))

    def expected_dwell(self, demand: float, door_faults: int) -> float:
        """Average stop dwell (s): boarding load grows super-linearly with
        demand; door faults force manual release cycles."""
        return (self.d.base_dwell * (1.0 + 0.85 * _clamp(demand) ** 2)
                + 1.8 * max(0, door_faults))

    # ── 2. Residuals + health ─────────────────────────────────────────

    def residuals(self, measured: dict) -> dict:
        """measured -> {signal: measured-expected} for predictable signals."""
        g = lambda k, dflt: float(measured.get(k, dflt))  # noqa: E731
        out: dict[str, float] = {}
        demand = g(SIGNALS["pax_load"], self.d.demand * 100.0) / 100.0
        track_temp = g(SIGNALS["track_temp"], self.d.ambient + 8.0)
        sig_faults = g(SIGNALS["signal_faults"], 0.0)
        sig_health = _clamp(1.0 - sig_faults / 12.0)
        if SIGNALS["avg_speed"] in measured:
            out[SIGNALS["avg_speed"]] = measured[SIGNALS["avg_speed"]] - \
                self.expected_speed(demand, sig_health, track_temp, 0.15)
        if SIGNALS["energy"] in measured and SIGNALS["in_service"] in measured:
            out[SIGNALS["energy"]] = measured[SIGNALS["energy"]] - \
                self.expected_power_mw(int(measured[SIGNALS["in_service"]]),
                                       g(SIGNALS["avg_speed"], self.d.base_speed),
                                       g(SIGNALS["hvac_load"], 40.0),
                                       g(SIGNALS["regen"], self.d.regen))
        return out

    def health_index(self, measured: dict) -> float:
        """0..1 network health from proximity to the key limits."""
        score = 1.0
        otp = measured.get(SIGNALS["otp"])
        if otp is not None:
            score -= _clamp((self.d.otp - otp) / (self.d.otp - self.lim.otp_min)) * 0.25
        hw = measured.get(SIGNALS["headway"])
        if hw is not None:
            score -= _clamp((self.d.headway - hw) /
                            (self.d.headway - self.lim.headway_min)) * 0.15
        v = measured.get(SIGNALS["ohl_v"])
        if v is not None:
            score -= _clamp((self.d.ohl_v - v) / (self.d.ohl_v - self.lim.ohl_v_min)) * 0.15
        sub = measured.get(SIGNALS["sub_load"])
        if sub is not None:
            score -= _clamp(max(0.0, sub - 70.0) / (self.lim.sub_load_max - 70.0)) * 0.10
        tt = measured.get(SIGNALS["track_temp"])
        if tt is not None:
            score -= _clamp(max(0.0, tt - 38.0) / (self.lim.track_temp_max - 38.0)) * 0.10
        av = measured.get(SIGNALS["fleet_avail"])
        if av is not None:
            score -= _clamp((92.0 - av) / (92.0 - self.lim.fleet_avail_min)) * 0.15
        dl = measured.get(SIGNALS["delay"])
        if dl is not None:
            score -= _clamp(dl / self.lim.delay_max) * 0.10
        return _clamp(score)

    # ── 3. Forward simulation ─────────────────────────────────────────

    def init_state(self, service_level: float = 0.85) -> FleetState:
        st = FleetState(service_level=service_level)
        self._seed_vehicles(st)
        return st

    FAULTS = (
        "ohl_damage", "substation_overload", "track_buckling", "switch_failure",
        "signal_failure", "brake_degradation", "pantograph_wear",
        "door_system_fault", "wheel_flats", "demand_surge",
    )

    def inject(self, state: FleetState, fault: str, severity: float = 0.6) -> None:
        """Arm a fault and seed its immediate signature so the live feed shows
        it at once, then keep progressing (same contract as EDM/turbine)."""
        state.fault = fault
        s = state.fault_severity = _clamp(severity)
        if fault == "ohl_damage":
            state.ohl_health = min(state.ohl_health, 1.0 - 0.45 * s)
            state.extras["fault_route"] = self._pick_route()
        elif fault == "substation_overload":
            state.sub_derate = max(state.sub_derate, 0.35 * s)
        elif fault == "track_buckling":
            state.ambient = max(state.ambient, 30.0 + 10.0 * s)
            state.track_wear = _clamp(state.track_wear + 0.20 * s)
            state.extras["fault_route"] = self._pick_route()
        elif fault == "switch_failure":
            state.switch_health = min(state.switch_health, 1.0 - 0.55 * s)
            state.extras["fault_route"] = self._pick_route()
        elif fault == "signal_failure":
            state.signal_health = min(state.signal_health, 1.0 - 0.60 * s)
        elif fault == "brake_degradation":
            state.brake_wear = _clamp(max(state.brake_wear, 0.45 * s + 0.2))
        elif fault == "pantograph_wear":
            state.panto_wear = _clamp(max(state.panto_wear, 0.45 * s + 0.2))
        elif fault == "door_system_fault":
            state.door_health = min(state.door_health, 1.0 - 0.55 * s)
        elif fault == "wheel_flats":
            state.wheel_flats = _clamp(max(state.wheel_flats, 0.50 * s))
        elif fault == "demand_surge":
            state.demand = _clamp(max(state.demand, 0.55 + 0.45 * s))

    def _pick_route(self) -> str:
        r = self.rng.choice(self.net["routes"])
        return r["id"]

    def forward(self, state: FleetState, dt: float = 1.0, noise: bool = True) -> dict:
        """Advance the network `dt` seconds; return a consistent telemetry frame."""
        d, lim = self.d, self.lim
        dt_h = dt / 3600.0
        sl = _clamp(state.service_level)

        # ── slow baseline degradation ──
        state.run_hours += dt_h
        state.brake_wear = _clamp(state.brake_wear + (0.4 + 0.6 * sl) * 0.004 * dt_h)
        state.panto_wear = _clamp(state.panto_wear + (0.4 + 0.6 * sl) * 0.003 * dt_h)
        state.track_wear = _clamp(state.track_wear + 0.001 * dt_h)
        state.wheel_flats = _clamp(state.wheel_flats + 0.0015 * dt_h)

        # ── injected faults progress, scaled by severity ──
        sev, f = state.fault_severity, state.fault
        if f == "ohl_damage":
            state.ohl_health = _clamp(state.ohl_health - 1.6 * sev * dt_h)
        elif f == "substation_overload":
            state.sub_derate = _clamp(state.sub_derate + 1.4 * sev * dt_h)
        elif f == "track_buckling":
            state.ambient = min(46.0, state.ambient + 6.0 * sev * dt_h)
        elif f == "switch_failure":
            state.switch_health = _clamp(state.switch_health - 1.8 * sev * dt_h)
        elif f == "signal_failure":
            state.signal_health = _clamp(state.signal_health - 2.0 * sev * dt_h)
        elif f == "brake_degradation":
            state.brake_wear = _clamp(state.brake_wear + 1.6 * sev * dt_h)
        elif f == "pantograph_wear":
            state.panto_wear = _clamp(state.panto_wear + 1.8 * sev * dt_h)
        elif f == "door_system_fault":
            state.door_health = _clamp(state.door_health - 1.6 * sev * dt_h)
        elif f == "wheel_flats":
            state.wheel_flats = _clamp(state.wheel_flats + 1.5 * sev * dt_h)
        elif f == "demand_surge":
            state.demand = _clamp(state.demand + 0.8 * sev * dt_h)

        # ── discrete fault counts from subsystem condition ──
        switch_faults = int(round((1.0 - state.switch_health) * 14))
        signal_faults = int(round((1.0 - state.signal_health) * 12))
        door_faults = int(round((1.0 - state.door_health) * 22
                                + state.brake_wear * 2))

        # ── fleet availability: consumables + door/brake defects hold cars in ──
        avail = _clamp(1.0
                       - 0.35 * max(0.0, state.brake_wear - 0.5)
                       - 0.30 * max(0.0, state.panto_wear - 0.5)
                       - 0.012 * door_faults
                       - 0.05 * state.wheel_flats)
        fleet_avail = avail * 100.0

        scheduled = self.scheduled_trams(sl)
        in_service = min(scheduled, int(self.net["fleet_size"] * avail))

        # ── track & environment ──
        # rail temp runs above ambient in sun + traffic heating
        track_temp = state.ambient + 9.0 + 3.0 * sl
        tsr = track_temp > lim.track_temp_max - 5.0        # heat speed restrictions

        # ── operations: dwell, congestion, speed ──
        dwell = self.expected_dwell(state.demand, door_faults)
        # congestion proxy: demand + degraded switches funnelling traffic
        congestion = _clamp(0.10 + 0.25 * state.demand
                            + 0.30 * (1.0 - state.switch_health)
                            + 0.15 * (1.0 - state.ohl_health))
        avg_speed = self.expected_speed(state.demand, state.signal_health,
                                        track_temp, congestion)
        # crowding: demand vs seats actually on the road
        supply = max(0.05, in_service / max(1, self.scheduled_trams(1.0)))
        pax_load = _clamp(state.demand / supply * 0.62, 0.0, 1.35) * 100.0

        # ── headway + OTP: bunching from faults, crowding and slow running ──
        headway = d.headway
        headway -= 22.0 * (1.0 - state.signal_health)
        headway -= 16.0 * (1.0 - state.switch_health)
        headway -= 10.0 * max(0.0, (dwell - d.base_dwell) / d.base_dwell)
        headway -= 8.0 * max(0.0, 1.0 - avg_speed / d.base_speed)
        headway = max(20.0, headway)

        otp = d.otp
        otp -= 0.9 * (d.headway - headway)
        otp -= 12.0 * (1.0 - state.ohl_health)
        otp -= (8.0 if tsr else 0.0)
        otp -= 0.25 * max(0.0, pax_load - 80.0)
        otp = max(15.0, otp)

        # ── delay accumulates with disruption, recovers when healthy ──
        disrupt = (1.0 - headway / d.headway) + (1.0 - otp / d.otp)
        state.delay_min = max(0.0, state.delay_min
                              + (disrupt * 90.0 - 25.0) * dt_h * 60.0 / 60.0)
        # (gain ~90 min/h fully disrupted; recovery ~25 min/h when clean)

        # ── traction power, substations, OHL ──
        hvac_need = _clamp(abs(state.ambient - d.hvac_setpoint) / 14.0) \
            * state.hvac_health
        hvac_load = 25.0 + 65.0 * hvac_need
        regen = d.regen * (1.0 - 0.5 * state.brake_wear)   # worn pads = friction share up
        energy = self.expected_power_mw(in_service, avg_speed, hvac_load, regen)
        energy *= (1.0 + 0.25 * (1.0 - state.ohl_health))  # arcing / resistive losses
        sub_cap = self._sub_cap_mw * (1.0 - state.sub_derate)
        sub_load = _clamp(energy / max(1e-3, sub_cap), 0.0, 1.4) * 100.0
        # worst-section voltage sags with load and OHL condition
        ohl_v = d.ohl_v * (1.0 - 0.10 * _clamp(sub_load / 100.0) ** 2) \
            * (0.90 + 0.10 * state.ohl_health)
        ohl_v -= 25.0 * state.panto_wear                   # arcing contact loss

        # ── rolling stock condition aggregates ──
        traction_temp = 55.0 + 40.0 * _clamp(energy / max(1e-3, self._sub_cap_mw)) \
            + 12.0 * _clamp(pax_load / 100.0) + max(0.0, state.ambient - 25.0)
        vib = 0.28 + 0.9 * state.wheel_flats + 0.5 * state.track_wear
        incidents = int(switch_faults > 3) + int(signal_faults > 2) \
            + int(state.ohl_health < 0.7) + int(track_temp >= lim.track_temp_max) \
            + int(state.delay_min > 60.0)

        # advance the live vehicle positions for the map
        self._advance_vehicles(state, dt, avg_speed, in_service)

        if noise:
            otp += self.rng.gauss(0, 0.5)
            headway += self.rng.gauss(0, 0.6)
            avg_speed += self.rng.gauss(0, 0.15)
            energy = max(0.0, energy + self.rng.gauss(0, 0.12))
            ohl_v += self.rng.gauss(0, 2.0)
            track_temp += self.rng.gauss(0, 0.2)
            traction_temp += self.rng.gauss(0, 0.8)
            vib = max(0.05, vib + self.rng.gauss(0, 0.02))
            pax_load = max(0.0, pax_load + self.rng.gauss(0, 1.2))
            dwell = max(8.0, dwell + self.rng.gauss(0, 0.6))

        return {
            SIGNALS["otp"]: round(_clamp(otp, 0, 100), 1),
            SIGNALS["headway"]: round(_clamp(headway, 0, 100), 1),
            SIGNALS["avg_speed"]: round(avg_speed, 2),
            SIGNALS["fleet_avail"]: round(fleet_avail, 1),
            SIGNALS["in_service"]: in_service,
            SIGNALS["pax_load"]: round(pax_load, 1),
            SIGNALS["dwell"]: round(dwell, 1),
            SIGNALS["energy"]: round(energy, 2),
            SIGNALS["regen"]: round(regen, 1),
            SIGNALS["ohl_v"]: round(ohl_v, 1),
            SIGNALS["sub_load"]: round(sub_load, 1),
            SIGNALS["track_temp"]: round(track_temp, 1),
            SIGNALS["switch_faults"]: switch_faults,
            SIGNALS["signal_faults"]: signal_faults,
            SIGNALS["door_faults"]: door_faults,
            SIGNALS["brake_wear"]: round(state.brake_wear * 100.0, 1),
            SIGNALS["panto_wear"]: round(state.panto_wear * 100.0, 1),
            SIGNALS["traction_temp"]: round(traction_temp, 1),
            SIGNALS["vib"]: round(vib, 3),
            SIGNALS["delay"]: round(state.delay_min, 1),
            SIGNALS["incidents"]: incidents,
            SIGNALS["hvac_load"]: round(hvac_load, 1),
        }

    # ── 4. Live vehicles for the network map ─────────────────────────

    def _seed_vehicles(self, state: FleetState) -> None:
        """Distribute an initial fleet across routes, weighted by route length.
        W8 heritage cars only ever serve the City Circle (route 35)."""
        routes = self.net["routes"]
        classes: list[str] = []
        for fc in self.net["fleet"]:
            classes += [fc["class"]] * int(fc.get("count", 0))
        self.rng.shuffle(classes)
        target = self.scheduled_trams(state.service_level)
        weights = [r["length_km"] for r in routes]
        total_w = sum(weights) or 1.0
        vehicles = []
        n = 0
        for r, w in zip(routes, weights):
            count = max(1, int(round(target * w / total_w)))
            for k in range(count):
                if r["id"] == "35":
                    cls = "W8"
                else:
                    cls = next((c for c in classes if c != "W8"), "GEN")
                    if cls in classes:
                        classes.remove(cls)
                n += 1
                vehicles.append({
                    "id": f"T{n:03d}", "cls": cls, "route": r["id"],
                    "progress": (k + 0.5) / count,       # spread along the line
                    "dir": 1 if k % 2 == 0 else -1,
                    "status": "ok", "speed": 0.0,
                })
        self._vehicles = vehicles

    def _advance_vehicles(self, state: FleetState, dt: float,
                          avg_speed: float, in_service: int) -> None:
        """Move each tram along its route polyline at the network speed
        (animated faster than real time so the map visibly lives)."""
        if not self._vehicles:
            self._seed_vehicles(state)
        routes = {r["id"]: r for r in self.net["routes"]}
        fault_route = state.extras.get("fault_route")
        anim = 18.0                                   # animation speed-up factor
        for i, v in enumerate(self._vehicles):
            r = routes.get(v["route"])
            if not r:
                continue
            active = i < max(4, in_service)
            blocked = (fault_route == v["route"]
                       and state.fault in ("ohl_damage", "track_buckling",
                                           "switch_failure")
                       and state.fault_severity > 0.3)
            speed = 0.0 if (not active or blocked) else avg_speed
            v["speed"] = round(speed, 1)
            v["status"] = ("stopped" if blocked else
                           "idle" if not active else
                           "warn" if avg_speed < self.d.base_speed * 0.6 else "ok")
            if speed <= 0.0:
                continue
            frac_per_s = (speed / 3600.0) / max(0.5, r["length_km"]) * anim
            p = v["progress"] + v["dir"] * frac_per_s * dt
            if r.get("loop"):
                v["progress"] = p % 1.0
            else:
                if p >= 1.0:
                    p, v["dir"] = 1.0, -1
                elif p <= 0.0:
                    p, v["dir"] = 0.0, 1
                v["progress"] = p

    def network_state(self, state: FleetState) -> dict:
        """The live map payload: network geometry refs + vehicles + per-route
        status (blocked / degraded / ok) for the frontend network view."""
        fault_route = state.extras.get("fault_route")
        route_status = {}
        for r in self.net["routes"]:
            st = "ok"
            if fault_route == r["id"] and state.fault != "none" \
                    and state.fault_severity > 0.2:
                st = "blocked" if state.fault in (
                    "ohl_damage", "track_buckling", "switch_failure") else "degraded"
            elif state.signal_health < 0.75 or state.switch_health < 0.75:
                st = "degraded"
            route_status[r["id"]] = st
        return {
            "network_id": self.net["id"],
            "name": self.net["name"],
            "fault": state.fault if state.fault != "none" else None,
            "fault_route": fault_route,
            "route_status": route_status,
            "vehicles": [dict(v) for v in self._vehicles],
            # Static network geometry so the map can draw routes / depots /
            # substations without a second call (the frontend caches it once).
            "geometry": {
                "routes": [
                    {"id": r["id"], "name": r.get("name"), "color": r.get("color"),
                     "points": r.get("points", []), "loop": r.get("loop", False)}
                    for r in self.net["routes"]
                ],
                "depots": self.net.get("depots", []),
                "substations": self.net.get("substations", []),
                "nodes": self.net.get("nodes", []),
            },
        }
