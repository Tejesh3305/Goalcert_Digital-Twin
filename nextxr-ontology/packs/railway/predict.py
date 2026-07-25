"""
railway/predict.py — subsystem health rollup + forward prediction / RUL for the
metro network. Same entry points and shapes as the other machine domains.
"""
from __future__ import annotations

import copy

from .physics import SIGNALS, redlines


from packs._core.physics import status_from_health as _status


def component_health(state, frame, physics) -> dict:
    otp = frame.get(SIGNALS["otp"], 97.0)
    headway = frame.get(SIGNALS["headway"], 96.0)
    avail = frame.get(SIGNALS["train_avail"], 97.0)
    delay = frame.get(SIGNALS["delay"], 1.0)
    third_v = frame.get(SIGNALS["third_rail_v"], 750.0)
    sub = frame.get(SIGNALS["sub_load"], 60.0)
    mtemp = frame.get(SIGNALS["traction_temp"], 90.0)
    rtemp = frame.get(SIGNALS["rail_temp"], 28.0)
    stress = frame.get(SIGNALS["rail_stress"], 20.0)
    qp = frame.get(SIGNALS["wheel_qp"], 0.28)
    vib = frame.get(SIGNALS["bogie_vib"], 2.4)
    sig_f = frame.get(SIGNALS["signal_faults"], 0.0)
    tc_f = frame.get(SIGNALS["track_circuit_faults"], 0.0)
    psd_f = frame.get(SIGNALS["psd_faults"], 0.0)
    hvac_dp = frame.get(SIGNALS["hvac_dp"], 26.0)
    los = frame.get(SIGNALS["station_los"], 2)
    esc = frame.get(SIGNALS["escalator_temp"], 40.0)

    # voltage health is two-sided around 750 V nominal
    v_lo = (third_v - redlines.third_rail_min) / (750.0 - redlines.third_rail_min)
    v_hi = (redlines.third_rail_max - third_v) / (redlines.third_rail_max - 750.0)
    v_health = max(0.0, min(1.0, min(v_lo, v_hi)))

    comp = {
        "rolling_stock": max(0.0, 1.0 - (vib / redlines.bogie_vib_max) * 0.5
                             - max(0.0, qp - 0.4) / 0.5 - max(0.0, 92.0 - avail) / 30.0),
        "traction_power": max(0.0, min(1.0, v_health
                              - max(0.0, sub - 70.0) / 60.0
                              - max(0.0, mtemp - 120.0) / 60.0)),
        "permanent_way": max(0.0, 1.0 - max(0.0, rtemp - 30.0) / 30.0
                             - max(0.0, stress - 40.0) / (redlines.rail_stress_max - 40.0) * 0.6
                             - (vib / redlines.bogie_vib_max) * 0.2),
        "signalling": max(0.0, 1.0 - sig_f / (redlines.signal_faults_max + 1.0)
                          - tc_f / (redlines.track_circuit_faults_max + 1.0)
                          - max(0.0, delay - 3.0) / 8.0),
        "stations": max(0.0, min(1.0, 1.0 - psd_f / (redlines.psd_faults_max + 1.0)
                        - max(0.0, redlines.hvac_dp_min - hvac_dp) / redlines.hvac_dp_min * 0.5
                        - max(0.0, los - 3) / 3.0 - max(0.0, esc - 90.0) / 60.0)),
        "operations": max(0.0, min(1.0, (otp - redlines.otp_min) / (98.0 - redlines.otp_min) * 0.5
                          + (headway / 100.0) * 0.5 - max(0.0, delay - 3.0) / 8.0)),
    }
    out = {k: {"health": round(v, 3), "status": _status(v)} for k, v in comp.items()}
    overall = min(comp.values()) if comp else 1.0
    out["overall"] = {"health": round(overall, 3), "status": _status(overall)}
    return out


_GUARDS = [
    ("on-time performance", SIGNALS["otp"], redlines.otp_min, "below", "operations"),
    ("headway adherence", SIGNALS["headway"], redlines.headway_min, "below", "operations"),
    ("train availability", SIGNALS["train_avail"], redlines.train_avail_min, "below", "rolling_stock"),
    ("third-rail overvoltage", SIGNALS["third_rail_v"], redlines.third_rail_max, "above", "traction_power"),
    ("substation load", SIGNALS["sub_load"], redlines.sub_load_max, "above", "traction_power"),
    ("traction motor temperature", SIGNALS["traction_temp"], redlines.traction_temp_max, "above", "traction_power"),
    ("rail temperature", SIGNALS["rail_temp"], redlines.rail_temp_max, "above", "permanent_way"),
    ("CWR rail stress", SIGNALS["rail_stress"], redlines.rail_stress_max, "above", "permanent_way"),
    ("derailment quotient", SIGNALS["wheel_qp"], redlines.wheel_qp_max, "above", "rolling_stock"),
    ("bogie vibration", SIGNALS["bogie_vib"], redlines.bogie_vib_max, "above", "rolling_stock"),
    ("network delay", SIGNALS["delay"], redlines.delay_max, "above", "operations"),
    ("platform level of service", SIGNALS["station_los"], redlines.station_los_max, "above", "stations"),
]


def predict(state, horizon_min: float = 120.0, points: int = 120, physics=None) -> dict:
    if physics is None:
        from .physics import RailwayPhysics
        physics = RailwayPhysics()

    st = copy.deepcopy(state)
    st._rng = None
    st.blocked = set(state.blocked)
    dt_min = horizon_min / max(1, points)
    dt_s = dt_min * 60.0

    trajectory, events, rul = [], [], {}
    for i in range(points):
        t_min = round(i * dt_min, 2)
        # No separate ramp: physics.forward advances wear itself (accelerated by
        # an active fault), so the projection follows the twin's own dynamics.
        frame = physics.forward(st, dt=dt_s)
        trajectory.append({
            "t": t_min,
            SIGNALS["otp"]: frame[SIGNALS["otp"]],
            SIGNALS["delay"]: frame[SIGNALS["delay"]],
            SIGNALS["third_rail_v"]: frame[SIGNALS["third_rail_v"]],
            SIGNALS["bogie_vib"]: frame[SIGNALS["bogie_vib"]],
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
