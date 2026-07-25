"""
ev/predict.py — subsystem health rollup + forward prediction / RUL for the EV
charging-network twin. Same entry points and shapes as the other machine domains.
"""
from __future__ import annotations

import copy

from .physics import SIGNALS, redlines


from packs._core.physics import status_from_health as _status


def component_health(state, frame, physics) -> dict:
    tload = frame.get(SIGNALS["transformer_load"], 65.0)
    hotspot = frame.get(SIGNALS["transformer_hotspot"], 60.0)
    aging = frame.get(SIGNALS["transformer_aging"], 0.2)
    volt = frame.get(SIGNALS["grid_voltage"], 100.0)
    freq = frame.get(SIGNALS["grid_frequency"], 50.0)
    conn = frame.get(SIGNALS["connector_temp"], 34.0)
    avail = frame.get(SIGNALS["available_chargers"], 100.0)
    faults = frame.get(SIGNALS["charger_faults"], 0.0)

    comp = {
        "grid": max(0.0, min(1.0, (volt - redlines.grid_voltage_min) / (100.0 - redlines.grid_voltage_min) * 0.7
                    + (1.0 - min(1.0, abs(freq - 50.0) / 0.5)) * 0.3)),
        "transformer": max(0.0, min(1.0, 1.0 - max(0.0, tload - 65.0) / (redlines.transformer_load_max - 65.0) * 0.5
                           - max(0.0, hotspot - 60.0) / (redlines.transformer_hotspot_max - 60.0) * 0.3
                           - max(0.0, aging - 0.2) / redlines.transformer_aging_max * 0.2)),
        "chargers": max(0.0, min(1.0, (avail - redlines.available_chargers_min) / (100.0 - redlines.available_chargers_min) * 0.6
                        + (1.0 - min(1.0, faults / (redlines.charger_faults_max + 1.0))) * 0.4
                        - max(0.0, conn - redlines.connector_temp_max) / 30.0)),
        "power_quality": max(0.0, min(1.0, 1.0 - max(0.0, conn - 34.0) / (redlines.connector_temp_max - 34.0) * 0.5
                             - max(0.0, 100.0 - volt) / (100.0 - redlines.grid_voltage_min) * 0.5)),
    }
    out = {k: {"health": round(v, 3), "status": _status(v)} for k, v in comp.items()}
    overall = min(comp.values()) if comp else 1.0
    out["overall"] = {"health": round(overall, 3), "status": _status(overall)}
    return out


_GUARDS = [
    ("transformer load", SIGNALS["transformer_load"], redlines.transformer_load_max, "above", "transformer"),
    ("transformer hot-spot", SIGNALS["transformer_hotspot"], redlines.transformer_hotspot_max, "above", "transformer"),
    ("transformer aging", SIGNALS["transformer_aging"], redlines.transformer_aging_max, "above", "transformer"),
    ("grid voltage", SIGNALS["grid_voltage"], redlines.grid_voltage_min, "below", "grid"),
    ("connector temperature", SIGNALS["connector_temp"], redlines.connector_temp_max, "above", "chargers"),
    ("available chargers", SIGNALS["available_chargers"], redlines.available_chargers_min, "below", "chargers"),
]


def predict(state, horizon_min: float = 120.0, points: int = 120, physics=None) -> dict:
    if physics is None:
        from .physics import EVChargingNetworkPhysics
        physics = EVChargingNetworkPhysics()

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
            SIGNALS["transformer_load"]: frame[SIGNALS["transformer_load"]],
            SIGNALS["transformer_hotspot"]: frame[SIGNALS["transformer_hotspot"]],
            SIGNALS["grid_voltage"]: frame[SIGNALS["grid_voltage"]],
            SIGNALS["network_load"]: frame[SIGNALS["network_load"]],
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
