"""
it.py — generative models for compute / ICT, and rotating equipment helpers.

Server demonstrates the full coupling cycle and the realism you asked for:
  * draws power as a NONLINEAR function of CPU load (not a fixed amount),
  * dumps ~all that power into its Zone as heat (ctx.space, read by ZoneModel via
    `contained`), and
  * THERMAL-THROTTLES: if the room gets hot (AHU fault upstream), CPU load and
    therefore power are capped — a visible chiller→AHU→zone→server feedback.

  P = P_idle + (P_max − P_idle) · load^1.4        (typical server power curve)
  Q_heat ≈ P · 1000  (W)                          (≈100% of electrical → heat)
"""

from __future__ import annotations

import math
from dynamics.model import DynamicsModel, EntityState, EntityContext

CFP = "https://ontology.nextxr.io/v3/cfp#"
SIG_PWR = CFP + "activePower"          # kW
SIG_TEMP = CFP + "temperature"         # CPU/inlet temp °C
SIG_HEAT = CFP + "heatOutputW"         # W dumped to the zone (read by ZoneModel)
SIG_CPU = CFP + "cpuLoad"              # fraction 0..1
SIG_HB = CFP + "heartbeat"             # 1 alive / 0 missed


class ServerModel(DynamicsModel):
    archetype = "ComputeLoad"
    models = [CFP + "Server", CFP + "EdgeNode"]
    produces = [SIG_PWR, SIG_TEMP, SIG_HEAT, SIG_CPU, SIG_HB]
    consumes = ["SPATIAL ambient (its room/zone)"]

    def init_state(self, ctx):
        return EntityState(status="running",
                           internal={}, signals={SIG_PWR: 0.0})

    def step(self, ctx, state):
        p_idle = ctx.fnum("idlePowerKW", 0.2)
        p_max = ctx.fnum("maxPowerKW", 0.8)

        # workload: diurnal base + noise
        h = (ctx.t / 3600.0) % 24.0
        base = 0.3 + 0.4 * max(0.0, math.sin((h - 6) / 12 * math.pi))
        load = min(1.0, max(0.05, base + ctx.rng.gauss(0, 0.05)))

        # inlet temp from the room/zone it sits in
        inlet = ctx.space.signals.get(SIG_TEMP, 22.0) if ctx.space else 22.0

        # THERMAL THROTTLING: above throttle temp, cap load linearly to 0 by trip temp
        t_throttle = ctx.fnum("throttleTempC", 30.0)
        t_trip = ctx.fnum("tripTempC", 40.0)
        if inlet > t_throttle:
            cap = max(0.0, 1.0 - (inlet - t_throttle) / max(t_trip - t_throttle, 1.0))
            load = min(load, cap)
        if inlet >= t_trip:
            state.status = "fault"; load = 0.0
        elif inlet > t_throttle:
            state.status = "degraded"
        else:
            state.status = "running"

        # nonlinear power curve + PSU ripple
        power = p_idle + (p_max - p_idle) * (load ** 1.4)
        power *= (1 + ctx.rng.gauss(0, 0.02))
        heat_w = power * 1000.0 * ctx.fnum("heatFraction", 0.98)
        cpu_temp = inlet + 15.0 * load + ctx.rng.gauss(0, 0.3)

        # heartbeat: rare dropout
        hb = 0.0 if (state.status == "fault" or
                     ctx.rng.random() < ctx.fnum("dropoutRatePerHr", 0.0) * ctx.dt / 3600.0) else 1.0

        state.signals = {SIG_PWR: round(power, 3), SIG_TEMP: round(cpu_temp, 1),
                         SIG_HEAT: round(heat_w, 1), SIG_CPU: round(load, 3),
                         SIG_HB: hb}
        return state
