"""
transport.py — DiscreteTransport archetype (elevators, escalators, doors).

A simple queueing/usage model: trips arrive at a diurnal rate; each trip moves the
car and increments wear counters. Produces runState (0/1), floorPosition,
doorCycles + cycleCount (monotonic, for predictive-maintenance), and occasional
faults as cycle count grows.
"""

from __future__ import annotations

import math
from dynamics.model import DynamicsModel, EntityState, EntityContext

CFP = "https://ontology.nextxr.io/v3/cfp#"
SIG_RUN = CFP + "runState"
SIG_POS = CFP + "floorPosition"
SIG_CYCLES = CFP + "cycleCount"
SIG_PWR = CFP + "activePower"


class DiscreteTransportModel(DynamicsModel):
    archetype = "DiscreteTransport"
    produces = [SIG_RUN, SIG_POS, SIG_CYCLES, SIG_PWR]
    consumes = []

    def init_state(self, ctx):
        return EntityState(status="idle",
                           internal={"cycles": ctx.fnum("initialCycles", 0.0),
                                     "pos": 0.0, "moving": False})

    def step(self, ctx, state):
        floors = ctx.fnum("floorCount", 10.0)
        h = (ctx.t / 3600.0) % 24.0
        # diurnal demand: busy 8–19h
        demand = max(0.05, math.sin(max(0.0, (h - 6) / 14.0) * math.pi)) \
            if 6 <= h <= 20 else 0.05
        trips_per_hr = ctx.fnum("maxTripsPerHour", 60.0) * demand
        moving = ctx.rng.random() < trips_per_hr * ctx.dt / 3600.0

        cycles = state.internal.get("cycles", 0.0)
        pos = state.internal.get("pos", 0.0)
        if moving:
            pos = float(ctx.rng.randint(0, int(floors)))
            cycles += 1
            state.status = "moving"
            power = ctx.fnum("movingKW", 10.0)
        else:
            state.status = "idle"
            power = ctx.fnum("idleKW", 0.5)
        state.internal.update(cycles=cycles, pos=pos, moving=moving)

        # fault probability rises slowly with accumulated cycles
        if ctx.rng.random() < ctx.fnum("faultPerCycle", 1e-6) * cycles * ctx.dt / 60.0:
            state.status = "fault"; power = 0.0

        state.signals = {SIG_RUN: 1.0 if moving else 0.0, SIG_POS: round(pos, 0),
                         SIG_CYCLES: round(cycles, 0), SIG_PWR: round(power, 2)}
        return state
