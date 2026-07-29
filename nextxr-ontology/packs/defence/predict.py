"""
packs/defence/predict.py — subsystem health rollup + forward prediction / RUL for
the military-base twin. Same entry points and shapes as the other machine domains.
"""
from __future__ import annotations

import copy

from packs._core.physics import status_from_health as _status

from .physics import SIGNALS, redlines


def component_health(state, frame, physics) -> dict:
    cov = frame.get(SIGNALS["radar_coverage"], 90.0)
    jam = frame.get(SIGNALS["jamming_level"], 0.0)
    comms = frame.get(SIGNALS["comms_availability"], 98.0)
    margin = frame.get(SIGNALS["cookoff_margin"], 30.0)
    fuel = frame.get(SIGNALS["fuel_level"], 82.0)
    nbc = frame.get(SIGNALS["nbc_reading"], 0.0)
    breaches = frame.get(SIGNALS["perimeter_breaches"], 0)
    avail = frame.get(SIGNALS["aircraft_avail"], 90.0)
    readiness = frame.get(SIGNALS["mission_readiness"], 95.0)

    comp = {
        "force_protection": max(0.0, min(1.0, 1.0 - breaches / 3.0 - nbc / redlines.nbc_max * 0.5)),
        "isr": max(0.0, min(1.0, (cov - redlines.radar_coverage_min) / (90.0 - redlines.radar_coverage_min) * 0.6
                   + (1.0 - min(1.0, jam / redlines.jamming_max)) * 0.4)),
        "logistics": max(0.0, min(1.0, (fuel - redlines.fuel_min) / (82.0 - redlines.fuel_min) * 0.5
                         + (margin - redlines.cookoff_margin_min) / (30.0 - redlines.cookoff_margin_min) * 0.5)),
        "air_ops": max(0.0, min(1.0, avail / 92.0)),
        "c4isr": max(0.0, min(1.0, (comms - redlines.comms_min) / (98.0 - redlines.comms_min) * 0.5
                     + (readiness - redlines.mission_readiness_min) / (95.0 - redlines.mission_readiness_min) * 0.5)),
    }
    out = {k: {"health": round(v, 3), "status": _status(v)} for k, v in comp.items()}
    overall = min(comp.values()) if comp else 1.0
    out["overall"] = {"health": round(overall, 3), "status": _status(overall)}
    return out


_GUARDS = [
    ("radar coverage", SIGNALS["radar_coverage"], redlines.radar_coverage_min, "below", "isr"),
    ("comms availability", SIGNALS["comms_availability"], redlines.comms_min, "below", "c4isr"),
    ("cook-off margin", SIGNALS["cookoff_margin"], redlines.cookoff_margin_min, "below", "logistics"),
    ("fuel level", SIGNALS["fuel_level"], redlines.fuel_min, "below", "logistics"),
    ("NBC contamination", SIGNALS["nbc_reading"], redlines.nbc_max, "above", "force_protection"),
    ("mission readiness", SIGNALS["mission_readiness"], redlines.mission_readiness_min, "below", "c4isr"),
    ("flight hours to service", SIGNALS["flight_hours"], redlines.flight_hours_min, "below", "air_ops"),
]


def predict(state, horizon_min: float = 120.0, points: int = 120, physics=None) -> dict:
    if physics is None:
        from .physics import DefenceBasePhysics
        physics = DefenceBasePhysics()

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
            SIGNALS["radar_coverage"]: frame[SIGNALS["radar_coverage"]],
            SIGNALS["fuel_level"]: frame[SIGNALS["fuel_level"]],
            SIGNALS["mission_readiness"]: frame[SIGNALS["mission_readiness"]],
            SIGNALS["flight_hours"]: frame[SIGNALS["flight_hours"]],
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
