"""
predict.py — the prediction engine for the fleet-network twin.

Same contract as edm/predict.py and turbine/predict.py:

  1. component_health(state, frame, physics) — 0..1 health + status word per
     subsystem (rolling stock / traction power / track & points / signalling /
     operations) from the present state and telemetry frame.

  2. predict(start_state, horizon) — project the network's CURRENT trajectory
     forward (continuing any active fault/degradation, else natural wear) and
     report the trajectory, now-vs-horizon health, the predicted detection
     timeline, and time-to-limit / RUL per failure mode.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone, timedelta

from behaviors.registry import TelemetrySample
from .behaviors import build_fleet_registry
from .physics import FleetPhysics, FleetState, SIGNALS, UNITS, redlines


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _status(h: float) -> str:
    return ("good" if h >= 0.8 else "fair" if h >= 0.6
            else "degraded" if h >= 0.4 else "critical")


def component_health(state: FleetState, frame: dict,
                     physics: FleetPhysics) -> dict:
    """Per-subsystem health (0..1) with a status word."""
    d, lim = physics.d, physics.lim
    otp = frame.get(SIGNALS["otp"], d.otp)
    headway = frame.get(SIGNALS["headway"], d.headway)
    ohl_v = frame.get(SIGNALS["ohl_v"], d.ohl_v)
    sub_load = frame.get(SIGNALS["sub_load"], 50.0)
    track_temp = frame.get(SIGNALS["track_temp"], d.ambient + 9.0)
    vib = frame.get(SIGNALS["vib"], 0.3)
    brake = frame.get(SIGNALS["brake_wear"], 20.0)
    panto = frame.get(SIGNALS["panto_wear"], 18.0)
    doors = frame.get(SIGNALS["door_faults"], 0)
    avail = frame.get(SIGNALS["fleet_avail"], 95.0)
    delay = frame.get(SIGNALS["delay"], 0.0)
    switches = frame.get(SIGNALS["switch_faults"], 0)
    signals_f = frame.get(SIGNALS["signal_faults"], 0)

    brake_m = _clamp(1 - brake / lim.brake_wear_max)
    panto_m = _clamp(1 - panto / lim.panto_wear_max)
    door_m = _clamp(1 - doors / lim.door_faults_max)
    avail_m = _clamp((avail - lim.fleet_avail_min) / (100.0 - lim.fleet_avail_min))
    rolling_stock = _clamp(0.30 * brake_m + 0.25 * panto_m
                           + 0.20 * door_m + 0.25 * avail_m)

    volt_m = _clamp((ohl_v - lim.ohl_v_min) / (d.ohl_v - lim.ohl_v_min))
    sub_m = _clamp(1 - max(0.0, sub_load - 70.0) / (lim.sub_load_max - 70.0))
    power = _clamp(0.55 * volt_m + 0.45 * sub_m)

    temp_m = _clamp(1 - max(0.0, track_temp - 38.0) / (lim.track_temp_max - 38.0))
    vib_m = _clamp(1 - vib / lim.vib_max)
    switch_m = _clamp(1 - switches / 10.0)
    track = _clamp(0.40 * temp_m + 0.30 * vib_m + 0.30 * switch_m)

    signalling = _clamp(1 - signals_f / (lim.signal_faults_max * 1.6))

    otp_m = _clamp((otp - lim.otp_min) / (d.otp - lim.otp_min))
    hw_m = _clamp((headway - lim.headway_min) / (d.headway - lim.headway_min))
    delay_m = _clamp(1 - delay / lim.delay_max)
    operations = _clamp(0.40 * otp_m + 0.35 * hw_m + 0.25 * delay_m)

    overall = physics.health_index(frame)

    out = {}
    for name, h in (("rolling_stock", rolling_stock), ("power", power),
                    ("track", track), ("signalling", signalling),
                    ("operations", operations), ("overall", overall)):
        out[name] = {"health": round(h, 3), "status": _status(h)}
    return out


# Failure-mode limits tracked for time-to-limit / RUL.
_LIMITS = [
    ("substation_trip", SIGNALS["sub_load"], "above",
     lambda: redlines.sub_load_max, "substation trip (overload)"),
    ("traction_undervoltage", SIGNALS["ohl_v"], "below",
     lambda: redlines.ohl_v_min, "overhead-line undervoltage"),
    ("track_buckling", SIGNALS["track_temp"], "above",
     lambda: redlines.track_temp_max, "rail-buckling temperature"),
    ("brake_limit", SIGNALS["brake_wear"], "above",
     lambda: redlines.brake_wear_max, "fleet brake-wear limit"),
    ("pantograph_limit", SIGNALS["panto_wear"], "above",
     lambda: redlines.panto_wear_max, "pantograph wear limit"),
    ("service_collapse", SIGNALS["otp"], "below",
     lambda: redlines.otp_min, "on-time-performance collapse"),
    ("network_disruption", SIGNALS["delay"], "above",
     lambda: redlines.delay_max, "network-wide delay threshold"),
]


def predict(start_state: FleetState, horizon_min: float = 120.0,
            points: int = 120, physics: FleetPhysics | None = None) -> dict:
    """Project the present trajectory forward `horizon_min` minutes."""
    physics = physics or FleetPhysics()
    state = copy.deepcopy(start_state)

    horizon_s = horizon_min * 60.0
    step_s = max(1.0, horizon_s / points)
    steps = int(points)

    q_node: dict = {}

    class _Q:
        def get_node(self, t, n): return dict(q_node)
        def get_property(self, t, n, k, default=None): return q_node.get(k, default)
        def list_by_label(self, t, l, limit=100): return []
        def get_findings(self, t, e=None): return []

    q = _Q()
    registry = build_fleet_registry()
    base = datetime.now(timezone.utc)

    trajectory: list[dict] = []
    events: list[dict] = []
    fired: set[str] = set()
    ttl: dict[str, float | None] = {k[0]: None for k in _LIMITS}
    health_now = None

    for i in range(steps + 1):
        t_min = round(i * step_s / 60.0, 2)
        frame = physics.forward(state, dt=step_s)
        ch = component_health(state, frame, physics)
        if health_now is None:
            health_now = ch

        q_node.clear()
        q_node.update({k.split("#")[-1].split(":")[-1]: v for k, v in frame.items()})
        for sig, val in frame.items():
            s = TelemetrySample(signal=sig, entity_id="predict", value=float(val),
                                unit=UNITS.get(sig, ""),
                                timestamp=base + timedelta(seconds=i * step_s),
                                tenant_id="predict")
            for f in registry.evaluate(s, q):
                if f.behavior_id not in fired:
                    fired.add(f.behavior_id)
                    events.append({"t_min": t_min, "behavior_id": f.behavior_id,
                                   "severity": f.severity, "message": f.message})

        for key, sig, direction, lim_fn, _label in _LIMITS:
            if ttl[key] is None:
                v = frame.get(sig)
                lv = lim_fn()
                if v is not None and ((direction == "above" and v >= lv) or
                                      (direction == "below" and v <= lv)):
                    ttl[key] = t_min

        trajectory.append({
            "t_min": t_min,
            "otp": frame[SIGNALS["otp"]],
            "headway": frame[SIGNALS["headway"]],
            "avg_speed": frame[SIGNALS["avg_speed"]],
            "delay": frame[SIGNALS["delay"]],
            "ohl_v": frame[SIGNALS["ohl_v"]],
            "sub_load": frame[SIGNALS["sub_load"]],
            "track_temp": frame[SIGNALS["track_temp"]],
            "brake_wear": frame[SIGNALS["brake_wear"]],
            "vib": frame[SIGNALS["vib"]],
            "energy": frame[SIGNALS["energy"]],
            "health": ch["overall"]["health"],
            "rolling_stock_h": ch["rolling_stock"]["health"],
            "power_h": ch["power"]["health"],
            "track_h": ch["track"]["health"],
            "signalling_h": ch["signalling"]["health"],
            "operations_h": ch["operations"]["health"],
        })

    if len(trajectory) > 100:
        k = len(trajectory) // 100
        trajectory = trajectory[::k] + [trajectory[-1]]

    last = trajectory[-1]
    health_horizon = {
        "rolling_stock": {"health": last["rolling_stock_h"], "status": _status(last["rolling_stock_h"])},
        "power": {"health": last["power_h"], "status": _status(last["power_h"])},
        "track": {"health": last["track_h"], "status": _status(last["track_h"])},
        "signalling": {"health": last["signalling_h"], "status": _status(last["signalling_h"])},
        "operations": {"health": last["operations_h"], "status": _status(last["operations_h"])},
        "overall": {"health": last["health"], "status": _status(last["health"])},
    }

    rul = []
    label_map = {k[0]: k[4] for k in _LIMITS}
    for key, t in ttl.items():
        rul.append({"mode": label_map[key], "time_to_limit_min": t,
                    "within_horizon": t is not None})

    return {
        "horizon_min": horizon_min,
        "fault_active": start_state.fault if start_state.fault != "none" else None,
        "trajectory": trajectory,
        "events": events,
        "component_health_now": health_now,
        "component_health_horizon": health_horizon,
        "rul": rul,
        "severity": ("critical" if any(e["severity"] == "critical" for e in events)
                     else "warning" if events else "nominal"),
    }
