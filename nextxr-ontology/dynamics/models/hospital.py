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

from dynamics import flows
from dynamics.model import DynamicsModel, EntityState

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

# ventilator
SIG_VENT_O2 = HSP + "o2Flow"          # L/min inspiratory O2 flow
SIG_VENT_TV = HSP + "tidalVolume"     # mL delivered tidal volume
SIG_VENT_FIO2 = HSP + "fiO2"          # % inspired O2 fraction
SIG_VENT_PEEP = HSP + "peep"          # cmH2O positive end-expiratory pressure
SIG_BATT = HSP + "batteryLevel"       # % internal battery (vent / pump)
# infusion pump
SIG_FLOW = HSP + "flowRate"           # mL/h programmed delivery rate
SIG_VINF = HSP + "volumeInfused"      # mL delivered this session
SIG_OCCL = HSP + "occlusionPressure"  # kPa line pressure (occlusion alarm)
# autoclave
SIG_CHTEMP = HSP + "chamberTemp"      # °C sterilisation chamber temperature
SIG_CHPRES = HSP + "chamberPressure"  # bar chamber pressure
SIG_F0 = HSP + "cycleF0"              # min accumulated F0 lethality
# smart bed
SIG_BED_OCC = HSP + "bedOccupied"     # 0/1 occupancy (load cells)
SIG_BED_ANGLE = HSP + "backrestAngle"  # deg backrest elevation
SIG_BED_WEIGHT = HSP + "patientWeight"  # kg (load cells)
SIG_BED_BRAKE = HSP + "brakeEngaged"  # 0/1 castor brake
SIG_BED_EXIT = HSP + "bedExitRisk"    # 0/1 bed-exit alarm


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


class VentilatorModel(DynamicsModel):
    """ICU / transport ventilator. Cycles a tidal-volume breath pattern and draws
    O2 flow; a small constant electronic load heats its room. On mains loss (no
    upstream feed powering it) it runs on internal battery, which drains — a poor
    conditionIndex means a weak battery that drops toward the low-battery alarm.
    -> hsp:o2Flow, hsp:tidalVolume, hsp:fiO2, hsp:peep, hsp:batteryLevel"""
    archetype = "Ventilator"
    produces = [SIG_VENT_O2, SIG_VENT_TV, SIG_VENT_FIO2, SIG_VENT_PEEP, SIG_BATT, SIG_PWR, SIG_HEAT]
    consumes = ["ELECTRICAL mains (upstream panel)", "SPATIAL ambient"]

    def init_state(self, ctx):
        return EntityState(status="running", internal={"batt": ctx.fnum("batteryPct", 100.0)})

    def step(self, ctx, state):
        rr = ctx.fnum("respRateBpm", 14.0)                     # breaths / minute
        set_tv = ctx.fnum("tidalVolumeMl", 450.0)
        fio2 = ctx.fnum("fiO2Pct", 40.0)
        peep = ctx.fnum("peepCmH2O", 5.0)
        cond = ctx.fnum("conditionIndex", 1.0)
        # breath phase → instantaneous tidal volume + inspiratory O2 flow
        phase = math.sin(2.0 * math.pi * (ctx.t / 60.0) * rr)
        insp = max(0.0, phase)
        tv = set_tv * (0.9 + 0.1 * insp) + ctx.rng.gauss(0, 4)
        o2_flow = (fio2 / 100.0) * set_tv * rr / 1000.0 * (0.8 + 0.4 * insp)   # ~L/min

        # Battery relaxes toward a health ceiling set by conditionIndex, so a
        # degraded unit sits at a chronically low charge (visible low-battery alarm)
        # while a healthy one stays near full.
        cap = max(10.0, cond * 100.0)
        batt = state.internal.get("batt", 100.0)
        batt += (cap - batt) * min(1.0, ctx.dt / ctx.fnum("battTauSec", 900.0))
        state.internal["batt"] = batt

        power = ctx.fnum("idleKW", 0.12) + 0.05 * insp
        state.status = ("fault" if batt < ctx.fnum("battAlarmPct", 15.0)
                        else "degraded" if batt < 40.0 else "running")
        state.signals = {
            SIG_VENT_O2: round(max(0.0, o2_flow), 1), SIG_VENT_TV: round(max(0.0, tv), 0),
            SIG_VENT_FIO2: round(fio2, 0), SIG_VENT_PEEP: round(peep, 1),
            SIG_BATT: round(batt, 1), SIG_PWR: round(power, 3),
            SIG_HEAT: round(power * 1000.0 * 0.9, 1)}
        return state


class InfusionPumpModel(DynamicsModel):
    """Volumetric infusion / syringe pump. Delivers a programmed flow rate,
    accumulating volume infused; line back-pressure sits near a baseline and
    climbs toward the occlusion-alarm limit as the giving set degrades
    (conditionIndex). Battery behaves like the ventilator's on mains loss.
    -> hsp:flowRate, hsp:volumeInfused, hsp:occlusionPressure, hsp:batteryLevel"""
    archetype = "InfusionPump"
    produces = [SIG_FLOW, SIG_VINF, SIG_OCCL, SIG_BATT, SIG_PWR]
    consumes = ["ELECTRICAL mains (upstream panel)"]

    def init_state(self, ctx):
        return EntityState(status="running",
                           internal={"vol": 0.0, "batt": ctx.fnum("batteryPct", 100.0)})

    def step(self, ctx, state):
        rate = ctx.fnum("flowRateMlPerH", 120.0)
        cond = ctx.fnum("conditionIndex", 1.0)
        vol = state.internal.get("vol", 0.0) + rate * ctx.dt / 3600.0
        if vol > ctx.fnum("bagVolumeMl", 500.0):
            vol = 0.0                                          # bag change
        state.internal["vol"] = vol

        base_occl = ctx.fnum("baseOcclusionKpa", 12.0)
        occl = base_occl + (1.0 - cond) * 40.0 + ctx.rng.gauss(0, 0.6)

        # battery relaxes toward a conditionIndex-set health ceiling (see Ventilator)
        cap = max(10.0, cond * 100.0)
        batt = state.internal.get("batt", 100.0)
        batt += (cap - batt) * min(1.0, ctx.dt / ctx.fnum("battTauSec", 900.0))
        state.internal["batt"] = batt

        state.status = ("fault" if occl > ctx.fnum("occlusionAlarmKpa", 40.0)
                        else "degraded" if occl > ctx.fnum("occlusionAlarmKpa", 40.0) * 0.75
                        else "running")
        state.signals = {
            SIG_FLOW: round(rate, 1), SIG_VINF: round(vol, 1),
            SIG_OCCL: round(max(0.0, occl), 1), SIG_BATT: round(batt, 1),
            SIG_PWR: round(ctx.fnum("idleKW", 0.02), 3)}
        return state


class AutoclaveModel(DynamicsModel):
    """CSSD steam steriliser. Runs a periodic sterilisation cycle: the chamber
    heats to ~134 °C at ~3 bar, holds, then vents. Accumulated F0 lethality is
    integrated over the hold (L = 10^((T-121.1)/z)); a degraded conditionIndex
    can't reach temperature, so F0 falls short of the 15-min sterility floor.
    -> hsp:chamberTemp, hsp:chamberPressure, hsp:cycleF0"""
    archetype = "Autoclave"
    produces = [SIG_CHTEMP, SIG_CHPRES, SIG_F0, SIG_PWR, SIG_HEAT]
    consumes = ["ELECTRICAL mains (upstream panel)", "SPATIAL ambient"]

    def init_state(self, ctx):
        amb = ctx.space.signals.get(SIG_TEMP, 22.0) if ctx.space else 22.0
        return EntityState(status="running", internal={"temp": amb, "f0": 0.0, "phase": "idle"})

    def step(self, ctx, state):
        cond = ctx.fnum("conditionIndex", 1.0)
        peak = ctx.fnum("holdTempC", 134.0) - (1.0 - cond) * 22.0   # can't reach temp when degraded
        cycle_min = ctx.fnum("cyclePeriodMin", 45.0)
        hold_min = ctx.fnum("holdMinutes", 15.0)
        amb = ctx.space.signals.get(SIG_TEMP, 22.0) if ctx.space else 22.0
        T = state.internal.get("temp", amb)
        f0 = state.internal.get("f0", 0.0)

        # where are we in the repeating cycle?
        pos = (ctx.t / 60.0) % cycle_min
        heating = pos < (cycle_min * 0.35)
        holding = (cycle_min * 0.35) <= pos < (cycle_min * 0.35 + hold_min)
        target = peak if (heating or holding) else amb
        T += (target - T) * min(1.0, ctx.dt / ctx.fnum("tauSec", 90.0)) + ctx.rng.gauss(0, 0.3)

        if holding:
            f0 += 10.0 ** ((T - 121.1) / 10.0) * ctx.dt / 60.0     # trapezoid-ish accumulation
        elif pos < cycle_min * 0.02:
            f0 = 0.0                                                # new cycle resets F0

        pressure = max(1.0, 1.0 + max(0.0, T - 100.0) / 34.0 * 2.0)   # ~3 bar at 134 °C
        state.internal.update(temp=T, f0=f0)
        state.status = ("degraded" if (holding and f0 < ctx.fnum("f0FloorMin", 15.0) * 0.5)
                        else "running")
        power = ctx.fnum("heatKW", 9.0) if heating else (ctx.fnum("holdKW", 3.0)
                if holding else ctx.fnum("idleKW", 0.2))
        state.signals = {
            SIG_CHTEMP: round(T, 1), SIG_CHPRES: round(pressure, 2), SIG_F0: round(f0, 1),
            SIG_PWR: round(power, 2), SIG_HEAT: round(power * 1000.0 * 0.85, 1)}
        return state


class BedModel(DynamicsModel):
    """Smart hospital bed: load-cell occupancy + patient weight, backrest angle,
    castor brake and a bed-exit alarm (occupied + steep backrest + brake released).
    Occupancy is stable per bed (deterministic per-entity RNG); the backrest drifts
    slowly as the patient repositions. -> hsp:bedOccupied, backrestAngle,
    patientWeight, brakeEngaged, bedExitRisk."""
    archetype = "Bed"
    produces = [SIG_BED_OCC, SIG_BED_ANGLE, SIG_BED_WEIGHT, SIG_BED_BRAKE, SIG_BED_EXIT, SIG_PWR]
    consumes = []

    def init_state(self, ctx):
        occ = 1.0 if ctx.rng.random() < ctx.fnum("occupancyProb", 0.78) else 0.0
        wt = round(ctx.rng.uniform(55.0, 95.0), 1) if occ else 0.0
        angle = ctx.rng.uniform(15.0, 45.0) if occ else 3.0
        return EntityState(status="running", internal={"occ": occ, "wt": wt, "angle": angle})

    def step(self, ctx, state):
        occ = state.internal.get("occ", 0.0)
        wt = state.internal.get("wt", 0.0)
        angle = state.internal.get("angle", 3.0)
        if occ:
            angle += ctx.rng.gauss(0.0, 0.6) * ctx.dt / 60.0     # slow repositioning
            angle = max(0.0, min(70.0, angle))
        state.internal["angle"] = angle
        brake = 0.0 if ctx.rng.random() < 0.02 else 1.0          # occasionally released
        exit_risk = 1.0 if (occ and angle > 55.0 and brake < 0.5) else 0.0
        pw = round(max(0.0, wt + (ctx.rng.gauss(0.0, 0.3) if occ else 0.0)), 1)
        state.status = "degraded" if exit_risk else "running"
        state.signals = {
            SIG_BED_OCC: occ, SIG_BED_ANGLE: round(angle, 1), SIG_BED_WEIGHT: pw,
            SIG_BED_BRAKE: brake, SIG_BED_EXIT: exit_risk,
            SIG_PWR: round(ctx.fnum("idleKW", 0.03), 3)}
        return state
