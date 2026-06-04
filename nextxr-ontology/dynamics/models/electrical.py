"""
electrical.py — generative models for the power chain.

Producer order: UtilityFeed -> Transformer -> (UPS / switchgear) -> loads.
Each consumes the aggregate downstream load (summed activePower of the entities
it `feeds`) and produces voltage / current / losses / temperatures.

Engineering bases:
  * Transformer copper loss  P_cu = P_cu_rated · (I/I_rated)²   (I²R)
  * Top-oil thermal rise     IEEE C57.91:  θ_oil_ult = θ_amb + Δθ_or·((1+R·K²)/(1+R))^x
                             1st-order:    dθ/dt = (θ_oil_ult − θ)/τ_oil
  * UPS battery runtime      Peukert:  t = C·(I_ref/I)^(k−1)/I ; SoC integrated
"""

from __future__ import annotations

import math
from dynamics.model import DynamicsModel, EntityState, EntityContext
from dynamics import flows

CFP = "https://ontology.nextxr.io/v3/cfp#"
SIG_PWR = CFP + "activePower"      # kW
SIG_V = CFP + "voltage"            # V
SIG_I = CFP + "electricCurrent"    # A
SIG_PF = CFP + "powerFactor"
SIG_OIL = CFP + "oilTemperature"   # °C
SIG_SOC = CFP + "upsSoC"           # %  (matches cfp.ups_on_battery behaviour)
SIG_FUEL = CFP + "fuelLevel"       # %
SIG_ENERGY = CFP + "energy"        # kWh


def _downstream_load_kw(ctx) -> float:
    """Sum of activePower drawn by everything this node feeds (its electrical load).
    Downstream loads are in ctx.outputs (this node is their source). 1-tick lag."""
    loads = [s for sts in ctx.outputs.values() for s in sts]
    total = flows.sum_signal(loads, SIG_PWR)
    # if nothing downstream publishes power yet, fall back to a configured base load
    return total if total > 0 else ctx.fnum("baseLoadKW", 0.0)


class UtilityFeedModel(DynamicsModel):
    archetype = "PowerSource"
    models = [CFP + "UtilityFeed"]
    produces = [SIG_V, SIG_PWR]
    consumes = ["ELECTRICAL load (downstream)"]

    def init_state(self, ctx):
        return EntityState(status="running",
                           internal={"available": True},
                           signals={SIG_V: 400.0})

    def step(self, ctx, state):
        nominal_v = ctx.fnum("nominalVoltage", 400.0)
        # rare outage events (Poisson). rate per hour from params.
        rate = ctx.fnum("outageRatePerHour", 0.0)
        avail = state.internal.get("available", True)
        if avail and ctx.rng.random() < rate * (ctx.dt / 3600.0):
            avail = False
            state.internal["outage_left"] = ctx.fnum("outageMinutes", 5.0) * 60.0
        if not avail:
            state.internal["outage_left"] = state.internal.get("outage_left", 0) - ctx.dt
            if state.internal["outage_left"] <= 0:
                avail = True
        state.internal["available"] = avail
        v = (nominal_v * (1 + ctx.rng.gauss(0, 0.005))) if avail else 0.0
        state.status = "running" if avail else "fault"
        state.signals = {SIG_V: round(v, 1), SIG_PWR: 0.0,
                         CFP + "available": 1.0 if avail else 0.0}
        return state


class TransformerModel(DynamicsModel):
    archetype = "ElectricalConverter"
    models = [CFP + "Transformer"]
    produces = [SIG_OIL, SIG_I, SIG_PWR, SIG_V, SIG_PF]
    consumes = ["ELECTRICAL load (downstream)", "ELECTRICAL source (upstream feed)"]

    def init_state(self, ctx):
        amb = ctx.fnum("ambientTemp", 30.0)
        return EntityState(status="running",
                           internal={"oil": amb},
                           signals={SIG_OIL: amb})

    def step(self, ctx, state):
        rated_kva = ctx.fnum("ratedCapacity", 1000.0)      # kVA
        pf = ctx.fnum("powerFactor", 0.95)
        v = ctx.fnum("secondaryVoltage", 400.0)
        load_kw = _downstream_load_kw(ctx)
        load_kva = load_kw / max(pf, 0.1)
        K = load_kva / max(rated_kva, 1.0)                 # per-unit load
        I = load_kva * 1000.0 / (math.sqrt(3) * v) if v else 0.0  # 3-phase amps

        # IEEE C57.91 top-oil rise (simplified, x=0.8, R=loss ratio)
        R = ctx.fnum("lossRatio", 5.0)
        dtheta_or = ctx.fnum("oilRiseRated", 55.0)         # °C at rated
        amb = ctx.fnum("ambientTemp", state.internal.get("ambient", 30.0))
        cond = ctx.fnum("conditionIndex", 1.0)             # fouling/aging
        oil_ult = amb + dtheta_or * ((1 + R * K * K) / (1 + R)) ** 0.8 / max(cond, 0.5)
        tau = ctx.fnum("oilTimeConstantSec", 3.0 * 3600.0)
        oil = state.internal.get("oil", amb)
        oil += (oil_ult - oil) / tau * ctx.dt + ctx.rng.gauss(0, 0.05)
        state.internal["oil"] = oil

        # status from load/oil
        if oil > ctx.fnum("oilTrip", 95.0):
            state.status = "fault"
        elif K > 1.1 or oil > ctx.fnum("oilWarn", 85.0):
            state.status = "degraded"
        else:
            state.status = "running"

        state.signals = {SIG_OIL: round(oil, 1), SIG_I: round(I, 1),
                         SIG_PWR: round(load_kw, 2), SIG_V: round(v, 1),
                         SIG_PF: round(pf, 3)}
        return state


class UPSModel(DynamicsModel):
    archetype = "EnergyStore"
    models = [CFP + "UPS"]
    produces = [SIG_SOC, SIG_V, SIG_PWR]
    consumes = ["ELECTRICAL load (downstream)", "BACKUP/source (utility upstream)"]

    def init_state(self, ctx):
        return EntityState(status="running",
                           internal={"soc": 100.0, "cycles": 0.0, "mode": "online"},
                           signals={SIG_SOC: 100.0})

    def step(self, ctx, state):
        load_kw = _downstream_load_kw(ctx)
        eff = ctx.fnum("inverterEff", 0.95)
        # is mains present? look at any upstream that publishes 'available' or voltage
        mains_ok = True
        for sts in ctx.inputs.values():
            for s in sts:
                if (CFP + "available") in s.signals:
                    mains_ok = mains_ok and s.signals[CFP + "available"] > 0.5
                elif SIG_V in s.signals:
                    mains_ok = mains_ok and s.signals[SIG_V] > 100.0
        soc = state.internal.get("soc", 100.0)

        E_rated = ctx.fnum("batteryEnergyKWh", 20.0)
        cond = ctx.fnum("conditionIndex", 1.0)
        cycles = state.internal.get("cycles", 0.0)
        E_batt = E_rated * (1 - 0.0002 * cycles) * cond          # capacity fade

        if mains_ok:
            # online: recharge toward 100, draw from mains
            if soc < 100.0:
                soc = min(100.0, soc + ctx.fnum("rechargePctPerHr", 30.0) * ctx.dt / 3600.0)
            state.internal["mode"] = "online"
            v_out = ctx.fnum("outputVoltage", 230.0) * (1 + ctx.rng.gauss(0, 0.003))
        else:
            # on battery: Peukert-derated discharge
            k = ctx.fnum("peukert", 1.2)
            p_draw = load_kw / max(eff, 0.5)
            # effective drain rate (%/s); higher load drains faster than linear
            base_rate = p_draw / max(E_batt, 0.1)               # C-rate (1/hr)
            derate = (max(base_rate, 0.01)) ** (k - 1.0)
            soc -= base_rate * derate * 100.0 * ctx.dt / 3600.0
            if state.internal.get("mode") != "battery":
                state.internal["cycles"] = cycles + 1
            state.internal["mode"] = "battery"
            v_out = ctx.fnum("outputVoltage", 230.0) * (1 - 0.02 * (load_kw / max(ctx.fnum("ratedCapacity", 50.0), 1)))
        soc = max(0.0, min(100.0, soc))
        state.internal["soc"] = soc
        state.status = "running" if mains_ok else "degraded"
        state.signals = {SIG_SOC: round(soc, 1), SIG_V: round(v_out, 1),
                         SIG_PWR: round(load_kw, 2)}
        return state
