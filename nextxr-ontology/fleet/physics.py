"""
fleet/physics.py — a tram fleet-network forward model with a live spatial map.

Same interface contract as the other domains, plus one extra method the machine
runtime exposes as GET /twins/{tenant}/network:

    network_state(state) -> geometry + live vehicle positions + per-route status

The network is a small fixed tram map (stops, routes, depots, substations) with a
fleet of vehicles that advance along their route polylines each tick. 22 aggregate
KPIs/telemetry signals are derived from the service level, fleet wear, weather heat
and injected faults. Injecting `ohl_damage` blocks a route (vehicles on it hold and
the route status flips to "blocked") — the demo's headline network behaviour.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

SIGNALS = {
    "otp":           "fleet:otp",
    "headway":       "fleet:headwayAdherence",
    "avg_speed":     "fleet:networkSpeed",
    "fleet_avail":   "fleet:fleetAvailability",
    "in_service":    "fleet:tramsInService",
    "pax_load":      "fleet:passengerLoad",
    "dwell":         "fleet:dwellTime",
    "energy":        "fleet:tractionPower",
    "regen":         "fleet:regenShare",
    "ohl_v":         "fleet:overheadVoltage",
    "sub_load":      "fleet:substationLoad",
    "track_temp":    "fleet:railTemperature",
    "switch_faults": "fleet:switchFaults",
    "signal_faults": "fleet:signalFaults",
    "door_faults":   "fleet:doorFaults",
    "brake_wear":    "fleet:brakeWear",
    "panto_wear":    "fleet:pantographWear",
    "traction_temp": "fleet:tractionMotorTemp",
    "vib":           "fleet:bogieVibration",
    "delay":         "fleet:networkDelay",
    "incidents":     "fleet:activeIncidents",
    "hvac_load":     "fleet:hvacLoad",
}

UNITS = {
    SIGNALS["otp"]: "%", SIGNALS["headway"]: "%", SIGNALS["avg_speed"]: "km/h",
    SIGNALS["fleet_avail"]: "%", SIGNALS["in_service"]: "", SIGNALS["pax_load"]: "%",
    SIGNALS["dwell"]: "s", SIGNALS["energy"]: "MW", SIGNALS["regen"]: "%",
    SIGNALS["ohl_v"]: "V", SIGNALS["sub_load"]: "%", SIGNALS["track_temp"]: "DEG_C",
    SIGNALS["switch_faults"]: "", SIGNALS["signal_faults"]: "", SIGNALS["door_faults"]: "",
    SIGNALS["brake_wear"]: "%", SIGNALS["panto_wear"]: "%", SIGNALS["traction_temp"]: "DEG_C",
    SIGNALS["vib"]: "G", SIGNALS["delay"]: "min", SIGNALS["incidents"]: "", SIGNALS["hvac_load"]: "%",
}


@dataclass
class Redlines:
    otp_min: float = 80.0
    headway_min: float = 75.0
    ohl_v_min: float = 650.0
    sub_load_max: float = 90.0
    track_temp_max: float = 50.0
    vib_max: float = 0.8
    brake_wear_max: float = 80.0
    panto_wear_max: float = 80.0
    door_faults_max: float = 5.0
    signal_faults_max: float = 4.0
    fleet_avail_min: float = 85.0
    delay_max: float = 8.0


redlines = Redlines()


# ── Network geometry (a compact tram map) ───────────────────────────
_NODES = [
    {"id": "n1", "name": "Central", "x": 50, "y": 50},
    {"id": "n2", "name": "Harbour", "x": 18, "y": 42},
    {"id": "n3", "name": "University", "x": 50, "y": 16},
    {"id": "n4", "name": "Stadium", "x": 82, "y": 42},
    {"id": "n5", "name": "Market", "x": 34, "y": 68},
    {"id": "n6", "name": "Airport", "x": 80, "y": 80},
    {"id": "n7", "name": "Riverside", "x": 14, "y": 66},
    {"id": "n8", "name": "Hillcrest", "x": 86, "y": 14},
]
_ROUTES = [
    {"id": "R1", "name": "Line 1 · Harbour–Hillcrest", "color": "#e11d48",
     "path": ["n2", "n1", "n3", "n8"], "loop": False},
    {"id": "R2", "name": "Line 2 · Riverside–Airport", "color": "#2563eb",
     "path": ["n7", "n5", "n1", "n4", "n6"], "loop": False},
    {"id": "R3", "name": "Line 3 · City Loop", "color": "#16a34a",
     "path": ["n5", "n1", "n4", "n8", "n3", "n5"], "loop": True},
]
_DEPOTS = [{"id": "d1", "name": "North Depot", "x": 50, "y": 88}]
_SUBSTATIONS = [
    {"id": "s1", "name": "Substation A", "x": 38, "y": 46},
    {"id": "s2", "name": "Substation B", "x": 68, "y": 52},
]
_KM_PER_UNIT = 0.16   # map units -> km


def _build_net() -> dict:
    idx = {n["id"]: n for n in _NODES}
    routes = []
    fleet = []
    total_km = 0.0
    for ri, r in enumerate(_ROUTES):
        pts = [[idx[p]["x"], idx[p]["y"]] for p in r["path"]]
        seglen = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
        length_km = round(sum(seglen) * _KM_PER_UNIT, 2)
        total_km += length_km
        routes.append({**r, "points": pts, "seglen": seglen,
                       "length": sum(seglen), "length_km": length_km})
        n_veh = 4 if not r["loop"] else 5
        for k in range(n_veh):
            fleet.append({"id": f"T{ri+1}{k+1:02d}", "route": r["id"],
                          "phase": k / n_veh, "dir": 1})
    return {"nodes": _NODES, "routes": routes, "depots": _DEPOTS,
            "substations": _SUBSTATIONS, "fleet": fleet,
            "route_km": round(total_km, 1), "fleet_size": len(fleet)}


def _point_on_route(route: dict, phase: float) -> list:
    """[x,y] at fractional distance `phase` (0..1) along the route polyline."""
    pts, seglen, total = route["points"], route["seglen"], route["length"]
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


@dataclass
class FleetState:
    service_level: float = 0.8      # control, 0..1 (share of timetable running)
    brake_wear: float = 8.0         # %
    panto_wear: float = 6.0         # %
    heat: float = 0.15              # 0..1 weather/thermal load
    sub_extra: float = 0.0          # 0..1 extra substation loading
    signal_faults: float = 0.0
    switch_faults: float = 0.0
    door_faults: float = 0.0
    surge: float = 0.0              # 0..1 passenger surge
    blocked: set = field(default_factory=set)   # blocked route ids
    phases: dict = field(default_factory=dict)  # vehicle_id -> phase
    hours: float = 0.0
    fault: str = "none"
    fault_severity: float = 0.0
    seed: int = 5
    _rng: random.Random = field(default=None, repr=False, compare=False)

    def rng(self):
        if self._rng is None:
            self._rng = random.Random(self.seed)
        return self._rng


_FAULTS = {
    "ohl_damage":          {"_block": 1, "sub_extra": 0.3},
    "signalling_outage":   {"signal_faults": 4, "_delay": True},
    "heatwave":            {"heat": 0.7},
    "substation_overload": {"sub_extra": 0.6},
    "brake_wear":          {"brake_wear": 55},
    "panto_wear":          {"panto_wear": 55},
    "door_fault":          {"door_faults": 4},
    "event_surge":         {"surge": 0.8},
}

FAULTS = list(_FAULTS.keys())


class FleetPhysics:
    def __init__(self, **options):
        self.opts = options or {}
        self.net = _build_net()
        self._route_by_id = {r["id"]: r for r in self.net["routes"]}

    def init_state(self) -> FleetState:
        st = FleetState()
        st.phases = {v["id"]: v["phase"] for v in self.net["fleet"]}
        return st

    def inject(self, state: FleetState, fault: str, severity: float = 0.85) -> None:
        eff = max(0.0, min(1.0, float(severity)))
        state.fault = fault
        state.fault_severity = eff
        spec = _FAULTS.get(fault, {})
        for attr, amount in spec.items():
            if attr == "_block":
                # block the busiest non-blocked route
                for r in self.net["routes"]:
                    if r["id"] not in state.blocked:
                        state.blocked.add(r["id"])
                        break
            elif attr == "_delay":
                pass
            elif attr in ("heat", "sub_extra", "surge"):
                setattr(state, attr, min(1.5, getattr(state, attr) + amount * eff))
            else:
                setattr(state, attr, min(120.0, getattr(state, attr) + amount * eff))

    def clear(self, state: FleetState) -> None:
        state.fault = "none"
        state.fault_severity = 0.0
        state.blocked.clear()
        state.heat = 0.15
        state.sub_extra = 0.0
        state.surge = 0.0
        state.signal_faults = 0.0
        state.door_faults = 0.0

    def forward(self, state: FleetState, dt: float = 1.0) -> dict:
        rng = state.rng()
        S = max(0.0, min(1.0, state.service_level))
        state.hours += dt / 3600.0

        # Slow wear accrual.
        state.brake_wear = min(100.0, state.brake_wear + dt * (0.0015 + 0.003 * S))
        state.panto_wear = min(100.0, state.panto_wear + dt * (0.0012 + 0.003 * S))

        blocked = state.blocked
        n_blocked = len(blocked)
        heat = state.heat

        # Advance vehicles (blocked routes hold position).
        avg_speed_kmh = 24.0 * (0.6 + 0.4 * S) * (1.0 - 0.12 * n_blocked) * (1.0 - 0.2 * heat)
        for r in self.net["routes"]:
            if r["id"] in blocked or r["length"] <= 0:
                continue
            # phase advance ∝ speed * dt / route length
            dphase = (avg_speed_kmh / 3.6) * dt / (r["length"] / _KM_PER_UNIT * 1000.0)
            for v in self.net["fleet"]:
                if v["route"] == r["id"]:
                    state.phases[v["id"]] = (state.phases.get(v["id"], 0.0) + dphase) % 1.0

        fleet_size = self.net["fleet_size"]
        in_service = round(fleet_size * (0.6 + 0.4 * S) * (1.0 - 0.15 * n_blocked))
        pax_load = min(100.0, 42.0 + 40.0 * S + 45.0 * state.surge)
        dwell = 22.0 + 0.12 * pax_load + 3.0 * state.door_faults
        delay = (1.2 + 3.5 * n_blocked + 0.8 * state.signal_faults
                 + 0.04 * max(0.0, pax_load - 70.0) + 2.0 * state.surge)
        otp = max(0.0, 96.0 - 3.0 * delay - 4.0 * n_blocked - 1.5 * state.signal_faults)
        headway = max(0.0, 95.0 - 2.2 * delay - 3.0 * n_blocked - 3.0 * state.surge)
        fleet_avail = max(0.0, 97.0 - state.brake_wear * 0.12 - state.panto_wear * 0.1
                          - state.door_faults * 1.2)

        sub_load = min(120.0, 44.0 + 34.0 * S + 45.0 * state.sub_extra + 12.0 * heat)
        ohl_v = 750.0 - 120.0 * state.sub_extra - 0.6 * max(0.0, sub_load - 80.0) - 60.0 * n_blocked
        track_temp = 22.0 + 26.0 * heat + 0.05 * pax_load
        traction_temp = 52.0 + 22.0 * S + 30.0 * heat
        vib = 0.2 + 0.006 * state.brake_wear + 0.15 * heat
        energy = in_service * (0.28 + 0.12 * S) * (1.0 + 0.1 * heat)
        regen = max(0.0, 34.0 - 20.0 * heat - 8.0 * state.sub_extra)
        hvac_load = min(100.0, 38.0 + 55.0 * heat + 0.1 * pax_load)
        incidents = n_blocked + (1 if state.signal_faults >= 3 else 0) + (1 if state.door_faults >= 3 else 0)

        def j(v, frac):
            return v * (1.0 + rng.uniform(-frac, frac))

        return {
            SIGNALS["otp"]:           round(j(otp, 0.01), 1),
            SIGNALS["headway"]:       round(j(headway, 0.01), 1),
            SIGNALS["avg_speed"]:     round(max(0.0, j(avg_speed_kmh, 0.02)), 1),
            SIGNALS["fleet_avail"]:   round(fleet_avail, 1),
            SIGNALS["in_service"]:    int(max(0, in_service)),
            SIGNALS["pax_load"]:      round(j(pax_load, 0.02), 1),
            SIGNALS["dwell"]:         round(j(dwell, 0.02), 1),
            SIGNALS["energy"]:        round(max(0.0, j(energy, 0.03)), 2),
            SIGNALS["regen"]:         round(max(0.0, j(regen, 0.03)), 1),
            SIGNALS["ohl_v"]:         round(max(0.0, j(ohl_v, 0.006)), 0),
            SIGNALS["sub_load"]:      round(j(sub_load, 0.02), 1),
            SIGNALS["track_temp"]:    round(j(track_temp, 0.01), 1),
            SIGNALS["switch_faults"]: int(state.switch_faults),
            SIGNALS["signal_faults"]: int(state.signal_faults),
            SIGNALS["door_faults"]:   int(state.door_faults),
            SIGNALS["brake_wear"]:    round(state.brake_wear, 1),
            SIGNALS["panto_wear"]:    round(state.panto_wear, 1),
            SIGNALS["traction_temp"]: round(j(traction_temp, 0.01), 1),
            SIGNALS["vib"]:           round(max(0.0, j(vib, 0.03)), 3),
            SIGNALS["delay"]:         round(max(0.0, j(delay, 0.03)), 2),
            SIGNALS["incidents"]:     int(incidents),
            SIGNALS["hvac_load"]:     round(j(hvac_load, 0.02), 1),
        }

    def residuals(self, frame: dict) -> dict:
        return {
            SIGNALS["otp"]: frame.get(SIGNALS["otp"], 96.0) - 96.0,
            SIGNALS["ohl_v"]: frame.get(SIGNALS["ohl_v"], 750.0) - 750.0,
        }

    def health_index(self, frame: dict) -> float:
        if not frame:
            return 1.0

        def hi(v, nominal, limit):
            return max(0.0, min(1.0, (limit - v) / (limit - nominal)))

        def lo(v, nominal, limit):
            return max(0.0, min(1.0, (v - limit) / (nominal - limit)))

        # Nominal references are the normal operating points at typical service,
        # so a healthy network sits near 1.0 and only decays toward its redlines.
        margins = [
            lo(frame.get(SIGNALS["otp"], 92.0), 92.0, redlines.otp_min),
            lo(frame.get(SIGNALS["headway"], 92.0), 92.0, redlines.headway_min),
            lo(frame.get(SIGNALS["ohl_v"], 750.0), 750.0, redlines.ohl_v_min),
            lo(frame.get(SIGNALS["fleet_avail"], 95.0), 95.0, redlines.fleet_avail_min),
            hi(frame.get(SIGNALS["sub_load"], 75.0), 75.0, redlines.sub_load_max),
            hi(frame.get(SIGNALS["track_temp"], 30.0), 30.0, redlines.track_temp_max),
            hi(frame.get(SIGNALS["vib"], 0.28), 0.28, redlines.vib_max),
            hi(frame.get(SIGNALS["delay"], 1.5), 1.5, redlines.delay_max),
        ]
        return round(min(margins), 3)

    # ── the live network map ──
    def network_state(self, state: FleetState) -> dict:
        vehicles = []
        route_status = {}
        for r in self.net["routes"]:
            blocked = r["id"] in state.blocked
            route_status[r["id"]] = "blocked" if blocked else "ok"
        for v in self.net["fleet"]:
            r = self._route_by_id[v["route"]]
            phase = state.phases.get(v["id"], 0.0)
            x, y = _point_on_route(r, phase)
            blocked = v["route"] in state.blocked
            vehicles.append({
                "id": v["id"], "route": v["route"], "x": x, "y": y,
                "status": "held" if blocked else "moving",
            })
            if not blocked and route_status[v["route"]] == "ok" and state.fault_severity > 0.4:
                route_status[v["route"]] = "degraded"
        return {
            "nodes": self.net["nodes"],
            "routes": [{"id": r["id"], "name": r["name"], "color": r["color"],
                        "points": r["points"], "length_km": r["length_km"],
                        "loop": r["loop"], "status": route_status[r["id"]]}
                       for r in self.net["routes"]],
            "depots": self.net["depots"],
            "substations": self.net["substations"],
            "vehicles": vehicles,
            "blocked": sorted(state.blocked),
            "route_km": self.net["route_km"],
            "fleet_size": self.net["fleet_size"],
        }
