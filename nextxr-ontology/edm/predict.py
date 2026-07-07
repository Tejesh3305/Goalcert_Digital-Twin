"""
edm/predict.py — health rollup + forward prediction / RUL for the wire-EDM twin.
Same two entry points and shapes as turbine/predict.py.
"""
from __future__ import annotations

import copy

from edm.physics import SIGNALS, redlines


def _status(h: float) -> str:
    return "critical" if h < 0.4 else "warning" if h < 0.72 else "ok"


def component_health(state, frame, physics) -> dict:
    clog = getattr(state, "filter_clog", 0.0)
    resin = getattr(state, "resin_depletion", 0.0)
    gw = getattr(state, "guide_wear", 0.0)
    chill = getattr(state, "chiller_health", 1.0)
    deb = getattr(state, "debris", 0.0)

    short_rate = frame.get(SIGNALS["short_rate"], 0.0)
    die_temp = frame.get(SIGNALS["die_temp"], 22.0)
    die_cond = frame.get(SIGNALS["die_cond"], 8.0)
    wire_wear = frame.get(SIGNALS["wire_wear"], 0.0)
    break_risk = frame.get(SIGNALS["break_risk"], 0.0)

    comp = {
        "generator":   max(0.0, 1.0 - (short_rate / redlines.short_rate) - 0.4 * deb),
        "dielectric":  max(0.0, 1.0 - 0.6 * clog - 0.5 * resin - (1.0 - chill) * 0.5
                             - max(0.0, die_temp - 26) / 14.0 - max(0.0, die_cond - 12) / 26.0),
        "wire_system": max(0.0, 1.0 - (break_risk / redlines.break_risk) - wire_wear / 220.0),
        "guides_axes": max(0.0, 1.0 - gw * 1.2 - 0.3 * (break_risk / redlines.break_risk)),
    }
    out = {k: {"health": round(v, 3), "status": _status(v)} for k, v in comp.items()}
    overall = min(comp.values()) if comp else 1.0
    out["overall"] = {"health": round(overall, 3), "status": _status(overall)}
    return out


_GUARDS = [
    ("short-circuit rate", SIGNALS["short_rate"], redlines.short_rate, "above", "generator"),
    ("wire-break risk", SIGNALS["break_risk"], redlines.break_risk, "above", "wire_system"),
    ("dielectric temperature", SIGNALS["die_temp"], redlines.die_temp, "above", "dielectric"),
    ("dielectric conductivity", SIGNALS["die_cond"], redlines.die_cond, "above", "dielectric"),
    ("dielectric pressure", SIGNALS["die_press"], redlines.die_press_min, "below", "dielectric"),
    ("wire tension", SIGNALS["wire_tension"], redlines.wire_tension_min, "below", "wire_system"),
    ("gap voltage", SIGNALS["gap_v"], redlines.gap_v_min, "below", "generator"),
]


def predict(state, horizon_min: float = 120.0, points: int = 120, physics=None) -> dict:
    if physics is None:
        from edm.physics import EDMPhysics
        physics = EDMPhysics()

    st = copy.deepcopy(state)
    st._rng = None
    dt_min = horizon_min / max(1, points)
    dt_s = dt_min * 60.0

    trajectory, events, rul = [], [], {}
    sev = getattr(st, "fault_severity", 0.0) or 0.0
    for i in range(points):
        t_min = round(i * dt_min, 2)
        g = dt_s * (1.0 + 3.0 * sev)
        st.filter_clog = min(1.6, st.filter_clog + g * (1.5e-5 + 5e-5 * st.filter_clog))
        st.resin_depletion = min(1.6, st.resin_depletion + g * (1.2e-5 + 5e-5 * st.resin_depletion))
        st.guide_wear = min(1.6, st.guide_wear + g * (1.0e-5 + 5e-5 * st.guide_wear))
        st.debris = min(1.6, st.debris + g * (1.5e-5 + 6e-5 * st.debris))
        st.chiller_health = max(0.0, st.chiller_health - g * 1.0e-5 * (0.5 + sev))

        frame = physics.forward(st, dt=dt_s)
        trajectory.append({
            "t": t_min,
            SIGNALS["short_rate"]: frame[SIGNALS["short_rate"]],
            SIGNALS["break_risk"]: frame[SIGNALS["break_risk"]],
            SIGNALS["die_temp"]: frame[SIGNALS["die_temp"]],
            SIGNALS["gap_v"]: frame[SIGNALS["gap_v"]],
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
