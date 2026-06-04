"""
water.py — StorageVessel archetype (water tank, fuel tank, reservoir).

Mass balance on the stored volume:
    dV/dt = inflow − demand − leak
    fillLevel% = 100 · V / V_max
Inflow comes from an upstream pump's flowRate (if any); demand is a profile param;
a leak event (Poisson) adds an outflow term that drains the tank → the low-level
and continuous-flow monitors fire.
"""

from __future__ import annotations

from dynamics.model import DynamicsModel, EntityState, EntityContext
from dynamics import flows

CFP = "https://ontology.nextxr.io/v3/cfp#"
SIG_LEVEL = CFP + "tankLevel"        # % (matches cfp.tank_low_level)
SIG_FLOW = CFP + "flowRate"          # L/s out (demand+leak), for leak monitors
SIG_TEMP = CFP + "temperature"


class StorageVesselModel(DynamicsModel):
    archetype = "StorageVessel"
    produces = [SIG_LEVEL, SIG_FLOW, SIG_TEMP]
    consumes = ["fluid inflow (upstream pump)"]

    def init_state(self, ctx):
        cap = ctx.fnum("capacityL", 10000.0)
        lvl0 = ctx.fnum("initialLevelPct", 80.0)
        return EntityState(status="running",
                           internal={"vol": cap * lvl0 / 100.0, "leaking": False})

    def step(self, ctx, state):
        cap = ctx.fnum("capacityL", 10000.0)
        # inflow: sum of upstream pump flow (L/s)
        inflow = flows.sum_signal(
            [s for sts in ctx.inputs.values() for s in sts], SIG_FLOW)
        demand = ctx.fnum("demandLps", 0.3)
        # leak event
        leaking = state.internal.get("leaking", False)
        if not leaking and ctx.rng.random() < ctx.fnum("leakRatePerHour", 0.0) * ctx.dt / 3600.0:
            leaking = True
        leak = ctx.fnum("leakLps", 2.0) if leaking else 0.0

        vol = state.internal.get("vol", cap * 0.8)
        vol = max(0.0, min(cap, vol + (inflow - demand - leak) * ctx.dt))
        state.internal["vol"] = vol
        state.internal["leaking"] = leaking

        level = 100.0 * vol / cap if cap else 0.0
        temp = ctx.fnum("waterTemp", 18.0) + ctx.rng.gauss(0, 0.1)
        state.status = "degraded" if level < ctx.fnum("lowLevelPct", 20.0) else "running"
        state.signals = {SIG_LEVEL: round(level, 1),
                         SIG_FLOW: round(demand + leak, 2),
                         SIG_TEMP: round(temp, 1)}
        return state
