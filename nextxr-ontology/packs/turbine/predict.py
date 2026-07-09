"""
turbine/predict.py — health rollup + forward prediction / RUL for the turbine.

Two entry points the runtime + routes call:

  component_health(state, frame, physics) -> per-subsystem {health, status} + "overall"
  predict(state, horizon_min, points, physics) -> {trajectory, rul, events, severity}

`predict` integrates the degradation accumulators forward (they grow faster the
more worn they already are, plus any injected fault), pushes each step through the
real physics.forward, and reports the first time each guarded signal crosses its
redline — that crossing time is the remaining-useful-life for its subsystem.
"""
from __future__ import annotations

import copy

from .physics import SIGNALS, redlines


def _status(health: float) -> str:
    return "critical" if health < 0.4 else "warning" if health < 0.72 else "ok"


def component_health(state, frame, physics) -> dict:
    """Map the degradation accumulators + latest frame onto the 5 subsystems."""
    foul = getattr(state, "compressor_fouling", 0.0)
    wear = getattr(state, "bearing_wear", 0.0)
    comb = getattr(state, "combustor_distress", 0.0)
    leak = getattr(state, "oil_leak", 0.0)

    egt = frame.get(SIGNALS["egt"], 0.0)
    vib = frame.get(SIGNALS["vib"], 0.0)
    oilp = frame.get(SIGNALS["oil_press"], redlines.oil_press_min * 2)

    comp = {
        "compressor":  max(0.0, 1.0 - foul * 1.1),
        "combustor":   max(0.0, 1.0 - comb * 1.2 - max(0.0, egt - 780) / 600.0),
        "turbine":     max(0.0, 1.0 - (max(0.0, egt - 720) / 500.0) - comb * 0.4),
        "bearings":    max(0.0, 1.0 - (vib / redlines.vib)),
        "lubrication": max(0.0, min(1.0, oilp / (redlines.oil_press_min * 2.0)) - leak * 0.3),
    }
    out = {k: {"health": round(v, 3), "status": _status(v)} for k, v in comp.items()}
    overall = min(comp.values()) if comp else 1.0
    out["overall"] = {"health": round(overall, 3), "status": _status(overall)}
    return out


# Which signal guards which subsystem, and the direction of the limit.
_GUARDS = [
    ("egt", SIGNALS["egt"], redlines.egt, "above", "turbine"),
    ("vibration", SIGNALS["vib"], redlines.vib, "above", "bearings"),
    ("oil temperature", SIGNALS["oil_temp"], redlines.oil_temp, "above", "lubrication"),
    ("oil pressure", SIGNALS["oil_press"], redlines.oil_press_min, "below", "lubrication"),
]


def predict(state, horizon_min: float = 120.0, points: int = 120, physics=None) -> dict:
    """Project the engine forward and report trajectory + RUL + events."""
    if physics is None:
        from .physics import TurbinePhysics
        physics = TurbinePhysics()

    st = copy.deepcopy(state)
    st._rng = None  # fresh deterministic stream for the projection
    dt_min = horizon_min / max(1, points)
    dt_s = dt_min * 60.0

    trajectory = []
    events = []
    rul = {}  # subsystem -> minutes to its first crossing

    sev_now = getattr(st, "fault_severity", 0.0) or 0.0
    for i in range(points):
        t_min = round(i * dt_min, 2)
        # Degradation grows ∝ (baseline + current level) — worn parts worsen faster.
        growth = dt_s * (1.0 + 3.0 * sev_now)
        st.bearing_wear = min(1.6, st.bearing_wear + growth * (2.5e-5 + 6e-5 * st.bearing_wear))
        st.compressor_fouling = min(1.6, st.compressor_fouling + growth * (2.0e-5 + 4e-5 * st.compressor_fouling))
        st.combustor_distress = min(1.6, st.combustor_distress + growth * (1.5e-5 + 5e-5 * st.combustor_distress))
        st.oil_leak = min(1.2, st.oil_leak + growth * (1.0e-5 + 8e-5 * st.oil_leak))

        frame = physics.forward(st, dt=dt_s)
        trajectory.append({
            "t": t_min,
            SIGNALS["egt"]: frame[SIGNALS["egt"]],
            SIGNALS["vib"]: frame[SIGNALS["vib"]],
            SIGNALS["oil_temp"]: frame[SIGNALS["oil_temp"]],
            SIGNALS["oil_press"]: frame[SIGNALS["oil_press"]],
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
