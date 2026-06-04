"""
power_extra.py — PrimeMover (generator) and Aggregator (meter/panel/circuit).

PrimeMover: a backup genset. Off while mains is present; on mains loss it starts,
carries the downstream load, and burns fuel:
    fuel_burn (L/hr) = a_idle + b · load_kW      ;   dFuel/dt = −fuel_burn
Frequency settles to nominal after a startup transient.

Aggregator: a metering/distribution node with no generation of its own. It SUMS
the activePower of everything downstream (ctx.outputs) and integrates energy:
    activePower = Σ downstream kW ;  energy += P·dt ;  current = P·1000/(√3·V)
"""

from __future__ import annotations

import math
from dynamics.model import DynamicsModel, EntityState, EntityContext
from dynamics import flows

CFP = "https://ontology.nextxr.io/v3/cfp#"
SIG_PWR = CFP + "activePower"
SIG_FUEL = CFP + "fuelLevel"
SIG_FREQ = CFP + "frequency"
SIG_RUN = CFP + "runHours"
SIG_I = CFP + "electricCurrent"
SIG_ENERGY = CFP + "energy"
SIG_V = CFP + "voltage"
SIG_PF = CFP + "powerFactor"


def _downstream_kw(ctx) -> float:
    loads = [s for sts in ctx.outputs.values() for s in sts]
    total = flows.sum_signal(loads, SIG_PWR)
    return total if total > 0 else ctx.fnum("baseLoadKW", 0.0)


def _mains_present(ctx) -> bool:
    """Generic: any upstream that publishes availability/voltage tells us if mains
    is up. (A generator backsUp the load; its 'source' is the grid via the chain.)"""
    ok = None
    for sts in ctx.inputs.values():
        for s in sts:
            if (CFP + "available") in s.signals:
                ok = (ok if ok is not None else True) and s.signals[CFP + "available"] > 0.5
            elif SIG_V in s.signals:
                ok = (ok if ok is not None else True) and s.signals[SIG_V] > 100.0
    return True if ok is None else ok


class PrimeMoverModel(DynamicsModel):
    archetype = "PrimeMover"
    produces = [SIG_FUEL, SIG_PWR, SIG_FREQ, SIG_RUN]
    consumes = ["BACKUP/source mains presence", "ELECTRICAL load (downstream)"]

    def init_state(self, ctx):
        return EntityState(status="off",
                           internal={"fuel": ctx.fnum("fuelLevel", 100.0),
                                     "run_h": 0.0, "running": False})

    def step(self, ctx, state):
        mains = _mains_present(ctx)
        running = not mains            # start on mains loss
        fuel = state.internal.get("fuel", 100.0)
        run_h = state.internal.get("run_h", 0.0)
        load = _downstream_kw(ctx) if running else 0.0

        if running and fuel > 0:
            a = ctx.fnum("idleBurnLph", 5.0)
            b = ctx.fnum("burnLpkWh", 0.25)
            cap_l = ctx.fnum("fuelCapacityL", 1000.0)
            burn_lph = a + b * load
            fuel = max(0.0, fuel - (burn_lph * (ctx.dt / 3600.0)) / cap_l * 100.0)
            run_h += ctx.dt / 3600.0
            freq = ctx.fnum("nominalFreq", 50.0) + ctx.rng.gauss(0, 0.05)
            state.status = "running" if fuel > 0 else "fault"
            out = load if fuel > 0 else 0.0
        else:
            freq = 0.0; out = 0.0
            state.status = "off"
        state.internal.update(fuel=fuel, run_h=run_h, running=running)
        state.signals = {SIG_FUEL: round(fuel, 1), SIG_PWR: round(out, 2),
                         SIG_FREQ: round(freq, 2), SIG_RUN: round(run_h, 2)}
        return state


class AggregatorModel(DynamicsModel):
    archetype = "Aggregator"
    produces = [SIG_PWR, SIG_I, SIG_ENERGY, SIG_PF]
    consumes = ["ELECTRICAL load (downstream)"]

    def init_state(self, ctx):
        return EntityState(status="running", internal={"energy": 0.0})

    def step(self, ctx, state):
        kw = _downstream_kw(ctx)
        v = ctx.fnum("voltage", 400.0)
        pf = ctx.fnum("powerFactor", 0.95)
        energy = state.internal.get("energy", 0.0) + kw * (ctx.dt / 3600.0)
        state.internal["energy"] = energy
        amp = kw * 1000.0 / (math.sqrt(3) * v) if v else 0.0
        state.signals = {SIG_PWR: round(kw, 2), SIG_I: round(amp, 1),
                         SIG_ENERGY: round(energy, 3), SIG_PF: round(pf, 3)}
        return state
