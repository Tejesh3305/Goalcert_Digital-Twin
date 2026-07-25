"""
hospital/predict.py — subsystem health rollup + forward prediction / RUL for the
hospital campus. Same entry points and shapes as the other machine domains.
"""
from __future__ import annotations

import copy

from .physics import SIGNALS, redlines


from packs._core.physics import status_from_health as _status


def component_health(state, frame, physics) -> dict:
    or_p = frame.get(SIGNALS["or_pressure"], 15.0)
    iso_p = frame.get(SIGNALS["iso_pressure"], -8.0)
    ach = frame.get(SIGNALS["air_changes"], 22.0)
    f0 = frame.get(SIGNALS["autoclave_f0"], 18.0)
    o2 = frame.get(SIGNALS["o2_pressure"], 405.0)
    reserve = frame.get(SIGNALS["gas_reserve"], 90.0)
    blood = frame.get(SIGNALS["bloodbank_temp"], 4.0)
    infr = frame.get(SIGNALS["infection_risk"], 0.4)
    runtime = frame.get(SIGNALS["ups_runtime"], 80.0)
    gen = frame.get(SIGNALS["gen_ready"], 1.0)
    powl = frame.get(SIGNALS["power_load"], 60.0)
    hot = frame.get(SIGNALS["hot_water"], 55.0)
    cold = frame.get(SIGNALS["cold_water"], 15.0)
    legs = frame.get(SIGNALS["dead_legs"], 0)
    wait = frame.get(SIGNALS["ed_wait"], 90.0)
    occ = frame.get(SIGNALS["bed_occ"], 70.0)

    comp = {
        "clinical_environment": max(0.0, min(1.0,
            (or_p - redlines.or_pressure_min) / (15.0 - redlines.or_pressure_min) * 0.4
            + (ach - redlines.air_changes_min) / (22.0 - redlines.air_changes_min) * 0.3
            + (f0 - redlines.autoclave_f0_min) / (18.0 - redlines.autoclave_f0_min) * 0.3)),
        "medical_gas": max(0.0, min(1.0,
            (o2 - redlines.o2_pressure_min) / (405.0 - redlines.o2_pressure_min) * 0.6
            + (reserve - redlines.gas_reserve_min) / (90.0 - redlines.gas_reserve_min) * 0.4)),
        "cold_chain": max(0.0, 1.0 - max(0.0, blood - 4.0) / (redlines.bloodbank_max - 4.0)),
        "power_resilience": max(0.0, min(1.0,
            (runtime - redlines.ups_runtime_min) / (80.0 - redlines.ups_runtime_min) * 0.6
            + gen * 0.4 - max(0.0, powl - redlines.power_load_max) / 30.0)),
        "water_safety": max(0.0, min(1.0,
            (hot - redlines.hot_water_min) / (55.0 - redlines.hot_water_min) * 0.4
            + (redlines.cold_water_max - cold) / (redlines.cold_water_max - 15.0) * 0.3
            + (1.0 - min(1.0, legs / (redlines.dead_legs_max + 1.0))) * 0.3)),
        "patient_flow": max(0.0, min(1.0,
            1.0 - max(0.0, wait - 90.0) / (redlines.ed_wait_max - 90.0) * 0.6
            - max(0.0, occ - 70.0) / (redlines.bed_occ_max - 70.0) * 0.4
            - max(0.0, infr - 1.0) / redlines.infection_risk_max * 0.3)),
    }
    out = {k: {"health": round(v, 3), "status": _status(v)} for k, v in comp.items()}
    overall = min(comp.values()) if comp else 1.0
    out["overall"] = {"health": round(overall, 3), "status": _status(overall)}
    return out


_GUARDS = [
    ("OR pressure", SIGNALS["or_pressure"], redlines.or_pressure_min, "below", "clinical_environment"),
    ("air changes", SIGNALS["air_changes"], redlines.air_changes_min, "below", "clinical_environment"),
    ("autoclave F0", SIGNALS["autoclave_f0"], redlines.autoclave_f0_min, "below", "clinical_environment"),
    ("O2 pressure", SIGNALS["o2_pressure"], redlines.o2_pressure_min, "below", "medical_gas"),
    ("medical gas reserve", SIGNALS["gas_reserve"], redlines.gas_reserve_min, "below", "medical_gas"),
    ("blood-bank temperature", SIGNALS["bloodbank_temp"], redlines.bloodbank_max, "above", "cold_chain"),
    ("UPS runtime", SIGNALS["ups_runtime"], redlines.ups_runtime_min, "below", "power_resilience"),
    ("critical power load", SIGNALS["power_load"], redlines.power_load_max, "above", "power_resilience"),
    ("hot-water return", SIGNALS["hot_water"], redlines.hot_water_min, "below", "water_safety"),
    ("dead-legs at risk", SIGNALS["dead_legs"], redlines.dead_legs_max, "above", "water_safety"),
    ("infection probability", SIGNALS["infection_risk"], redlines.infection_risk_max, "above", "patient_flow"),
    ("ED wait time", SIGNALS["ed_wait"], redlines.ed_wait_max, "above", "patient_flow"),
]


def predict(state, horizon_min: float = 120.0, points: int = 120, physics=None) -> dict:
    if physics is None:
        from .physics import HospitalCampusPhysics
        physics = HospitalCampusPhysics()

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
            SIGNALS["or_pressure"]: frame[SIGNALS["or_pressure"]],
            SIGNALS["o2_pressure"]: frame[SIGNALS["o2_pressure"]],
            SIGNALS["ed_wait"]: frame[SIGNALS["ed_wait"]],
            SIGNALS["ups_runtime"]: frame[SIGNALS["ups_runtime"]],
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
