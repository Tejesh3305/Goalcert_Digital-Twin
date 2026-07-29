"""
predict.py — subsystem health rollup and forward projection for the solar array.

Two entry points the runtime and the routes call, matching the pack contract:

    component_health(state, frame, physics) -> per-subsystem {health, status}
    predict(state, horizon_min, points, physics) -> {trajectory, rul, events, severity}

WHY THE HORIZON IS DAYS, NOT MINUTES
------------------------------------
Every other pack projects a machine forward over minutes or hours, because a turbine
bearing fails on that timescale. PV degradation does not: soiling is a matter of
weeks, PID a matter of months. Projecting an array 120 minutes ahead would show a
flat line and be useless.

So `predict` accepts a horizon in minutes for interface compatibility with the
runtime, and internally scales its default to DAYS. The mechanism that makes this
honest is that `physics._advance_degradation` works in per-day rates, so integrating
with a large dt genuinely ages the array — the projection and the live twin share one
degradation model rather than the projection having its own faster ramp.

WHAT "RUL" MEANS FOR AN ARRAY
-----------------------------
A PV array almost never stops working. It gets gradually worse, and the useful
question is not "when will it fail" but "when will it cross the threshold that
justifies an intervention". So each RUL entry here is time-to-a-decision:

    soiling      when cumulative loss exceeds the cost of a wash
    shunt decay  when R_sh reaches half its original value
    thermal      when cell temperature crosses its derate limit
    inverter     when heat-sink temperature reaches its trip point

That framing is what makes the output schedulable, and it is why the events carry an
action rather than only a date.
"""

from __future__ import annotations

import copy

from packs._core.physics import status_from_health as _status

from . import desoto as D
from .physics import SIGNALS, redlines

# The default projection horizon, in DAYS. 180 days spans the timescales that matter:
# a soiling cycle (weeks) and a PID trend (months).
DEFAULT_HORIZON_DAYS = 180.0

# Cleaning economics. A wash is only worth doing once the recovered energy pays for
# it, so the threshold is a business decision expressed in physics terms rather than
# an arbitrary percentage.
SOILING_INTERVENTION_LOSS_PCT = 4.0


def component_health(state, frame, physics) -> dict:
    """Map the array's condition onto the subsystems an engineer thinks in.

    Chosen so each maps to a DIFFERENT intervention: modules get washed, strings get
    inspected, cells get shaded or ventilated, the inverter gets serviced, sensors get
    calibrated. A health breakdown whose entries all lead to the same action is
    decoration.
    """
    strings = frame.get("_strings", []) or []
    poa = frame.get(SIGNALS["poa"], 0.0)

    # Modules: how much light is being lost to soiling.
    soiling_values = [s.get("soiling", 1.0) for s in strings] or [1.0]
    mean_soiling = sum(soiling_values) / len(soiling_values)
    # 1.0 clean -> health 1.0; 0.85 (15 % loss) -> health 0.0.
    modules = max(0.0, min(1.0, (mean_soiling - 0.85) / 0.15))

    # Strings: mismatch and outright failures.
    imbalance = frame.get(SIGNALS["imbalance"], 0.0)
    open_strings = sum(1 for s in strings if s.get("open"))
    bypassed = sum(1 for s in strings if (s.get("bypassed") or 0) > 0.01)
    string_health = max(0.0, 1.0 - imbalance / redlines.current_imbalance_pct * 0.8
                        - open_strings * 0.35 - bypassed * 0.2)

    # Cells: shunt/series degradation, read through fill factor.
    fill_factor = frame.get(SIGNALS["ff"], 0.0)
    cells = (max(0.0, min(1.0, (fill_factor - redlines.fill_factor_min)
                          / (0.79 - redlines.fill_factor_min)))
             if fill_factor > 0 else 1.0)

    # Inverter: thermal margin and conversion efficiency.
    heatsink = frame.get(SIGNALS["heatsink_temp"], 25.0)
    thermal = max(0.0, min(1.0, (redlines.heatsink_temp - heatsink) / 40.0))
    efficiency = frame.get(SIGNALS["efficiency"], 98.0)
    inverter = min(thermal, max(0.0, min(1.0, (efficiency - 90.0) / 8.0))) \
        if poa > 50.0 else thermal

    # Sensors: is the baseline trustworthy? A failed pyranometer invalidates every
    # other number here, so it is a subsystem in its own right.
    ghi = frame.get(SIGNALS["ghi"], 0.0)
    sensors = 1.0
    if ghi > 200.0 and poa > 0:
        ratio = poa / ghi
        sensors = 1.0 if 0.85 <= ratio <= 1.35 else max(
            0.0, 1.0 - abs(ratio - 1.1) * 1.6)

    components = {
        "modules": modules,
        "strings": max(0.0, min(1.0, string_health)),
        "cells": cells,
        "inverter": max(0.0, min(1.0, inverter)),
        "sensors": max(0.0, min(1.0, sensors)),
    }
    out = {name: {"health": round(value, 3), "status": _status(value)}
           for name, value in components.items()}
    overall = min(components.values()) if components else 1.0
    out["overall"] = {"health": round(overall, 3), "status": _status(overall)}
    return out


def predict(state, horizon_min: float = None, points: int = 90,
            physics=None) -> dict:
    """Project the array forward and report trajectory, RUL and scheduled events.

    Integrates the twin's OWN degradation model, at daily steps, so the projection
    matches how the live array actually ages. Environment is held at a representative
    clear-noon condition rather than simulated day by day: the question this answers
    is "how does the ARRAY change", and letting weather vary would bury that signal
    in diurnal noise. The trajectory is therefore a same-conditions comparison —
    which is exactly what a degradation forecast should be.
    """
    if physics is None:
        from .physics import SolarPhysics
        physics = SolarPhysics()

    horizon_days = (horizon_min / 1440.0) if horizon_min else DEFAULT_HORIZON_DAYS
    horizon_days = max(1.0, horizon_days)
    steps = max(2, int(points))
    dt_days = horizon_days / steps
    dt_seconds = dt_days * 86400.0

    projected = copy.deepcopy(state)
    projected._rng = None
    # Freeze the environment at clear-sky noon so the trajectory isolates
    # degradation. `measured_env` prevents the diurnal simulator overwriting it.
    projected.measured_env = True
    projected.poa = 950.0
    projected.ghi = 880.0
    projected.ambient_temp = 30.0
    projected.wind_speed = 2.0
    projected.module_temp = D.module_temp_faiman(30.0, 950.0, 2.0)
    projected.cell_temp = D.cell_temp_from_module(projected.module_temp, 950.0)

    baseline_rsh = _mean_rsh(physics, projected)
    trajectory, events, rul = [], [], {}

    for step in range(steps):
        day = round(step * dt_days, 2)
        frame = physics.forward(projected, dt=dt_seconds)
        # forward() re-derives cell temperature from the frozen environment, so the
        # thermal state stays fixed and only wear advances.

        health = physics.health_index(frame)
        row = {
            "t": day,
            "days": day,
            "health": health,
            SIGNALS["dc_power"]: frame.get(SIGNALS["dc_power"]),
            SIGNALS["pr"]: frame.get(SIGNALS["pr"]),
            SIGNALS["delta_p_pct"]: frame.get(SIGNALS["delta_p_pct"]),
            SIGNALS["ff"]: frame.get(SIGNALS["ff"]),
            SIGNALS["imbalance"]: frame.get(SIGNALS["imbalance"]),
        }
        rsh = frame.get(SIGNALS["rsh"])
        if rsh is not None:
            row[SIGNALS["rsh"]] = rsh
        trajectory.append(row)

        # Soiling crossing the cleaning-economics threshold.
        if "modules" not in rul:
            deficit = -(frame.get(SIGNALS["delta_p_pct"]) or 0.0)
            if deficit >= SOILING_INTERVENTION_LOSS_PCT:
                rul["modules"] = day
                events.append({
                    "t": day, "days": day, "component": "modules",
                    "signal": SIGNALS["delta_p_pct"],
                    "action": "cleaning",
                    "priority": "low",
                    "message": (f"Soiling loss reaches "
                                f"{SOILING_INTERVENTION_LOSS_PCT:.0f}% at ~day "
                                f"{day:.0f} — schedule a wash"),
                })

        # Shunt decay reaching half its baseline (the §4.2 PID threshold).
        if "cells" not in rul and baseline_rsh and rsh:
            if rsh <= baseline_rsh * 0.5:
                rul["cells"] = day
                events.append({
                    "t": day, "days": day, "component": "cells",
                    "signal": SIGNALS["rsh"],
                    "action": "engineering_diagnostics",
                    "priority": "scheduled",
                    "message": (f"Shunt resistance reaches half its baseline "
                                f"({baseline_rsh:.0f} -> {rsh:.0f} ohm) at ~day "
                                f"{day:.0f} — PID/delamination investigation"),
                })

        # Guarded hard limits.
        for component, signal, limit, direction, label, action in (
            ("inverter", SIGNALS["heatsink_temp"], redlines.heatsink_temp, "above",
             "Heat-sink temperature", "inverter_service"),
            ("cells", SIGNALS["cell_temp"], redlines.cell_temp, "above",
             "Cell temperature", "ventilation_review"),
            ("strings", SIGNALS["imbalance"], redlines.current_imbalance_pct,
             "above", "String current imbalance", "string_inspection"),
        ):
            value = frame.get(signal)
            if value is None or component in rul:
                continue
            crossed = value >= limit if direction == "above" else value <= limit
            if crossed:
                rul[component] = day
                events.append({
                    "t": day, "days": day, "component": component,
                    "signal": signal, "action": action,
                    "priority": "medium",
                    "message": (f"{label} reaches its limit ({value:.1f}) at ~day "
                                f"{day:.0f}"),
                })

    rul_list = sorted(
        ({"component": component, "days": days,
          "months": round(days / 30.44, 1), "minutes": round(days * 1440.0, 1)}
         for component, days in rul.items()),
        key=lambda row: row["days"])

    # Severity from how SOON the first intervention is due, in months rather than as
    # a fraction of the horizon: "a wash due in three weeks" means the same thing
    # whether the caller asked for 90 days or 365.
    if not rul_list:
        severity = "nominal"
    elif rul_list[0]["months"] <= 1.0:
        severity = "critical"
    elif rul_list[0]["months"] <= 3.0:
        severity = "warning"
    else:
        severity = "nominal"

    return {
        "trajectory": trajectory,
        "rul": rul_list,
        "events": events,
        "severity": severity,
        "horizon_min": horizon_days * 1440.0,
        "horizon_days": horizon_days,
        "basis": ("physics — the twin's own De Soto model and degradation "
                  "accumulators integrated forward at a fixed clear-noon condition, "
                  "so the trajectory isolates array degradation from weather"),
    }


def _mean_rsh(physics, state) -> float:
    """Mean normalised shunt resistance right now — the projection's baseline."""
    values = []
    for s in state.strings:
        operating = physics.string_operating_point(state, s)
        if operating.get("r_sh_ref"):
            values.append(operating["r_sh_ref"])
    return sum(values) / len(values) if values else 0.0
