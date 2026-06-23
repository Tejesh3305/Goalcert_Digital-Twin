"""
hospital.py — dedicated generative models for hospital-specific equipment.

These complement the CFP archetypes (which already cover AHU, Chiller, UPS,
Transformer, etc.). They model the genuinely distinct dynamics a hospital twin
needs, each emitting cfp:activePower (so it loads the electrical chain AND heats
its zone) plus a DISTINCT fault signal (so its monitoring rule never cross-fires
with another concern):

  RefrigeratedUnit  — cold-chain / pharmacy / blood fridge. Hysteresis compressor
                      control toward a low setpoint; ambient leak + door-open
                      events warm it; a poor conditionIndex starves cooling so it
                      drifts over-temperature.            -> hospital:coldChainTemp
  GasManifold       — medical gas (O2/N2O) manifold. Pressure held near nominal,
                      reservoir drawn down by demand + refilled; a leak event
                      bleeds pressure below the safe floor.  -> hospital:gasPressure
  ImagingDevice     — MRI / CT. Diurnal scan duty drives heavy power; magnet
                      coolant temperature rises if the upstream chiller can't
                      supply cold water (a real chiller->imaging coupling); MRI
                      helium slowly boils off (faster when coolant is warm).
                      -> hospital:imagingCoolantTemp, hospital:heliumLevel
"""

from __future__ import annotations

import math

from dynamics.model import DynamicsModel, EntityState, EntityContext
from dynamics import flows

CFP = "https://ontology.nextxr.io/v3/cfp#"
HSP = "https://ontology.nextxr.io/v3/hospital#"

SIG_PWR = CFP + "activePower"
SIG_HEAT = CFP + "heatOutputW"
SIG_TEMP = CFP + "temperature"
SIG_CHWS = CFP + "chwSupplyTemp"      # chilled-water supply from an upstream chiller

SIG_COLD = HSP + "coldChainTemp"      # °C inside a refrigerated unit
SIG_GASP = HSP + "gasPressure"        # bar at a medical-gas manifold
SIG_GASLVL = HSP + "gasLevel"         # % reservoir remaining
SIG_COOL = HSP + "imagingCoolantTemp"  # °C magnet/scanner coolant
SIG_HE = HSP + "heliumLevel"          # % cryogen remaining (MRI)


class RefrigeratedUnitModel(DynamicsModel):
    archetype = "RefrigeratedUnit"
    produces = [SIG_COLD, SIG_PWR, SIG_HEAT]
    consumes = ["SPATIAL ambient (its room)"]

    def init_state(self, ctx):
        return EntityState(status="running",
                           internal={"temp": ctx.fnum("setpointC", 4.0), "comp": False})

    def step(self, ctx, state):
        sp = ctx.fnum("setpointC", 4.0)
        band = ctx.fnum("bandC", 2.0)
        amb = ctx.space.signals.get(SIG_TEMP, 22.0) if ctx.space else ctx.fnum("ambientC", 22.0)
        cond = ctx.fnum("conditionIndex", 1.0)
        T = state.internal.get("temp", sp)
        comp = state.internal.get("comp", False)

        # hysteresis on/off control
        if T > sp + band / 2:
            comp = True
        elif T < sp - band / 2:
            comp = False

        dmin = ctx.dt / 60.0
        cool = ctx.fnum("coolKperMin", 0.6) * max(cond, 0.05) if comp else 0.0     # K/min removed
        warm = ctx.fnum("uaLeak", 0.02) * max(0.0, amb - T)                        # K/min ingress
        door = ctx.fnum("doorWarmK", 3.0) if (
            ctx.rng.random() < ctx.fnum("doorEventRatePerHour", 0.0) * ctx.dt / 3600.0) else 0.0
        T += (-cool + warm) * dmin + door + ctx.rng.gauss(0, 0.04)
        T = max(-40.0, min(amb + 2.0, T))

        power = ctx.fnum("compressorKW", 0.8) if comp else ctx.fnum("idleKW", 0.08)
        state.internal["temp"] = T
        state.internal["comp"] = comp
        state.status = "degraded" if T > ctx.fnum("alarmAboveC", 8.0) else "running"
        state.signals = {SIG_COLD: round(T, 2), SIG_PWR: round(power, 3),
                         SIG_HEAT: round(power * 1000.0 * 0.9, 1)}
        return state


class GasManifoldModel(DynamicsModel):
    archetype = "GasManifold"
    produces = [SIG_GASP, SIG_GASLVL, SIG_PWR]
    consumes = []

    def init_state(self, ctx):
        return EntityState(status="running",
                           internal={"p": ctx.fnum("nominalPressureBar", 4.1),
                                     "lvl": ctx.fnum("initialLevelPct", 90.0),
                                     "leak": False})

    def step(self, ctx, state):
        nom = ctx.fnum("nominalPressureBar", 4.1)
        p = state.internal.get("p", nom)
        lvl = state.internal.get("lvl", 90.0)
        leak = state.internal.get("leak", False)
        if not leak and ctx.rng.random() < ctx.fnum("leakRatePerHour", 0.0) * ctx.dt / 3600.0:
            leak = True

        lvl -= ctx.fnum("demandPctPerHour", 1.5) * ctx.dt / 3600.0
        if lvl < ctx.fnum("refillAtPct", 25.0):
            lvl = 100.0                                   # manifold/cylinder swap
        lvl = max(0.0, min(100.0, lvl))

        target = nom * (0.55 + 0.45 * min(1.0, lvl / 30.0))   # pressure sags as reserve empties
        if leak:
            target -= ctx.fnum("leakDropBar", 1.2)
        tau = ctx.fnum("tauSec", 120.0)
        p += (target - p) * min(1.0, ctx.dt / tau) + ctx.rng.gauss(0, 0.01)
        p = max(0.0, p)

        state.internal.update(p=p, lvl=lvl, leak=leak)
        state.status = "degraded" if p < ctx.fnum("lowPressureBar", 3.4) else "running"
        state.signals = {SIG_GASP: round(p, 2), SIG_GASLVL: round(lvl, 1),
                         SIG_PWR: round(ctx.fnum("baseLoadKW", 0.05), 3)}
        return state


class ImagingDeviceModel(DynamicsModel):
    archetype = "ImagingDevice"
    produces = [SIG_PWR, SIG_COOL, SIG_HE, SIG_HEAT]
    consumes = ["THERMAL chilled water (upstream chiller)", "SPATIAL ambient"]

    def init_state(self, ctx):
        return EntityState(status="running", internal={"he": ctx.fnum("heliumPct", 100.0)})

    def step(self, ctx, state):
        idle = ctx.fnum("idleKW", 5.0)
        scan = ctx.fnum("scanPowerKW", 40.0)
        h = (ctx.t / 3600.0) % 24.0
        duty = 0.0
        if 7 <= h <= 19:
            duty = max(0.0, math.sin((h - 7) / 12 * math.pi)) * (0.5 + 0.5 * ctx.rng.random())
        scanning = duty > 0.15
        power = idle + scan * duty

        # coolant rises when the upstream chiller can't deliver cold water
        chw = flows.first_signal(flows.upstream_with_signal(ctx, SIG_CHWS), SIG_CHWS,
                                 default=ctx.fnum("chwSetpoint", 7.0))
        cond = ctx.fnum("conditionIndex", 1.0)
        coolant = (ctx.fnum("coolantBaseC", 18.0) + max(0.0, chw - 9.0) * 1.5
                   + duty * 4.0 + (1.0 - cond) * 6.0 + ctx.rng.gauss(0, 0.2))

        he = state.internal.get("he", 100.0)
        boil = ctx.fnum("heBoiloffPctPerHour", 0.02) * (1.0 + max(0.0, coolant - 25.0) / 5.0)
        he = max(0.0, he - boil * ctx.dt / 3600.0)
        state.internal["he"] = he

        cool_max = ctx.fnum("coolantMaxC", 28.0)
        if coolant > cool_max:
            state.status = "fault"
        elif scanning and coolant > cool_max * 0.85:
            state.status = "degraded"
        else:
            state.status = "running"
        state.signals = {SIG_PWR: round(power, 2), SIG_COOL: round(coolant, 1),
                         SIG_HE: round(he, 2), SIG_HEAT: round(power * 1000.0 * 0.85, 1)}
        return state
