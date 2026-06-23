"""
datacenter.py — dedicated model for a data-centre compute rack.

A ComputeRack is the data hall's heat + power workhorse: it draws a diurnal,
nonlinear IT load, dumps ~all of it into the hall as heat (read by the Zone via
`contained`), and THERMAL-THROTTLES when its inlet (the hall temperature) gets
hot — the visible CRAC->hall->rack feedback. It emits a DISTINCT inlet-temp
signal so a rack over-temperature monitor flags the rack itself (turning it red
in 3-D) without cross-firing on zone/CPU temperatures.

  P = P_idle + (P_max − P_idle) · load^1.3      (rack power curve, kW)
  Q_heat ≈ P · 1000 W                            (≈all electrical → heat)
"""

from __future__ import annotations

import math

from dynamics.model import DynamicsModel, EntityState, EntityContext

CFP = "https://ontology.nextxr.io/v3/cfp#"
DC = "https://ontology.nextxr.io/v3/datacenter#"

SIG_PWR = CFP + "activePower"
SIG_HEAT = CFP + "heatOutputW"
SIG_CPU = CFP + "cpuLoad"
SIG_INLET = DC + "rackInletTemp"      # °C cold-aisle inlet at the rack


class ComputeRackModel(DynamicsModel):
    archetype = "ComputeRack"
    produces = [SIG_PWR, SIG_INLET, SIG_HEAT, SIG_CPU]
    consumes = ["SPATIAL ambient (the data hall)"]

    def init_state(self, ctx):
        return EntityState(status="running", signals={SIG_PWR: 0.0})

    def step(self, ctx, state):
        p_idle = ctx.fnum("idlePowerKW", 2.0)
        p_max = ctx.fnum("maxPowerKW", 8.0)

        h = (ctx.t / 3600.0) % 24.0
        base = 0.45 + 0.35 * max(0.0, math.sin((h - 6) / 12 * math.pi))
        load = min(1.0, max(0.1, base + ctx.rng.gauss(0, 0.04)))

        inlet = ctx.space.signals.get(CFP + "temperature", 24.0) if ctx.space else 24.0
        t_throttle = ctx.fnum("throttleTempC", 27.0)
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

        power = p_idle + (p_max - p_idle) * (load ** 1.3)
        power *= (1 + ctx.rng.gauss(0, 0.02))
        state.signals = {
            SIG_PWR: round(power, 3),
            SIG_INLET: round(inlet + ctx.rng.gauss(0, 0.2), 1),
            SIG_HEAT: round(power * 1000.0 * 0.98, 1),
            SIG_CPU: round(load, 3),
        }
        return state
