"""
fleet/predict.py — health rollup + forward prediction / RUL for the tram network.
Same entry points and shapes as the other domains.
"""
from __future__ import annotations

import copy

from fleet.physics import SIGNALS, redlines


def _status(h: float) -> str:
    return "critical" if h < 0.4 else "warning" if h < 0.72 else "ok"


def component_health(state, frame, physics) -> dict:
    brake = frame.get(SIGNALS["brake_wear"], 0.0)
    panto = frame.get(SIGNALS["panto_wear"], 0.0)
    vib = frame.get(SIGNALS["vib"], 0.2)
    avail = frame.get(SIGNALS["fleet_avail"], 96.0)
    ohl = frame.get(SIGNALS["ohl_v"], 750.0)
    sub = frame.get(SIGNALS["sub_load"], 45.0)
    ttemp = frame.get(SIGNALS["track_temp"], 24.0)
    sig_f = frame.get(SIGNALS["signal_faults"], 0.0)
    delay = frame.get(SIGNALS["delay"], 1.0)
    otp = frame.get(SIGNALS["otp"], 95.0)
    headway = frame.get(SIGNALS["headway"], 95.0)

    comp = {
        "rolling_stock": max(0.0, 1.0 - brake / 110.0 - panto / 120.0 - (vib / redlines.vib_max) * 0.4
                             - max(0.0, 90.0 - avail) / 40.0),
        "power":         max(0.0, min(1.0, (ohl - redlines.ohl_v_min) / (750.0 - redlines.ohl_v_min))
                             - max(0.0, sub - 70.0) / 60.0),
        "track":         max(0.0, 1.0 - max(0.0, ttemp - 30.0) / 30.0 - (vib / redlines.vib_max) * 0.3),
        "signalling":    max(0.0, 1.0 - sig_f / (redlines.signal_faults_max + 1.0) - max(0.0, delay - 4.0) / 8.0),
        "operations":    max(0.0, min(1.0, (otp - redlines.otp_min) / (96.0 - redlines.otp_min))
                             * 0.6 + (headway / 100.0) * 0.4 - max(0.0, delay - 4.0) / 10.0),
    }
    out = {k: {"health": round(v, 3), "status": _status(v)} for k, v in comp.items()}
    overall = min(comp.values()) if comp else 1.0
    out["overall"] = {"health": round(overall, 3), "status": _status(overall)}
    return out


_GUARDS = [
    ("on-time performance", SIGNALS["otp"], redlines.otp_min, "below", "operations"),
    ("headway adherence", SIGNALS["headway"], redlines.headway_min, "below", "operations"),
    ("overhead voltage", SIGNALS["ohl_v"], redlines.ohl_v_min, "below", "power"),
    ("fleet availability", SIGNALS["fleet_avail"], redlines.fleet_avail_min, "below", "rolling_stock"),
    ("substation load", SIGNALS["sub_load"], redlines.sub_load_max, "above", "power"),
    ("rail temperature", SIGNALS["track_temp"], redlines.track_temp_max, "above", "track"),
    ("bogie vibration", SIGNALS["vib"], redlines.vib_max, "above", "rolling_stock"),
    ("network delay", SIGNALS["delay"], redlines.delay_max, "above", "operations"),
    ("brake wear", SIGNALS["brake_wear"], redlines.brake_wear_max, "above", "rolling_stock"),
    ("pantograph wear", SIGNALS["panto_wear"], redlines.panto_wear_max, "above", "rolling_stock"),
]


def predict(state, horizon_min: float = 120.0, points: int = 120, physics=None) -> dict:
    if physics is None:
        from fleet.physics import FleetPhysics
        physics = FleetPhysics()

    st = copy.deepcopy(state)
    st._rng = None
    st.blocked = set(state.blocked)
    dt_min = horizon_min / max(1, points)
    dt_s = dt_min * 60.0

    trajectory, events, rul = [], [], {}
    sev = getattr(st, "fault_severity", 0.0) or 0.0
    for i in range(points):
        t_min = round(i * dt_min, 2)
        g = dt_s * (1.0 + 2.0 * sev)
        st.brake_wear = min(120.0, st.brake_wear + g * (2.0e-4 + 3e-6 * st.brake_wear))
        st.panto_wear = min(120.0, st.panto_wear + g * (1.6e-4 + 3e-6 * st.panto_wear))

        frame = physics.forward(st, dt=dt_s)
        trajectory.append({
            "t": t_min,
            SIGNALS["otp"]: frame[SIGNALS["otp"]],
            SIGNALS["delay"]: frame[SIGNALS["delay"]],
            SIGNALS["brake_wear"]: frame[SIGNALS["brake_wear"]],
            SIGNALS["ohl_v"]: frame[SIGNALS["ohl_v"]],
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
