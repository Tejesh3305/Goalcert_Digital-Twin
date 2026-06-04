"""
fluid.py — FluidMover archetype (pumps, fans, water pumps, sump pumps).

Affinity laws for a rotodynamic machine driven at speed fraction N (0..1):
    flow      Q  ∝ N
    head/ΔP   H  ∝ N²
    shaft pwr P  ∝ N³ ;  P_elec = P_shaft / η_motor
Vibration: a healthy baseline that rises as bearings wear over run-hours,
modulated by conditionIndex, plus an imbalance harmonic and noise. This is what
the Tier-B vibration baseline monitor detects.
"""

from __future__ import annotations

from dynamics.model import DynamicsModel, EntityState, EntityContext

CFP = "https://ontology.nextxr.io/v3/cfp#"
SIG_FLOW = CFP + "flowRate"
SIG_PRESS = CFP + "pressure"
SIG_PWR = CFP + "activePower"
SIG_VIB = CFP + "vibration"          # mm/s (matches cfp.pump_vibration_baseline)
SIG_RUN = CFP + "runHours"


class FluidMoverModel(DynamicsModel):
    archetype = "FluidMover"
    produces = [SIG_FLOW, SIG_PRESS, SIG_PWR, SIG_VIB, SIG_RUN]
    consumes = ["CONTROL speed (optional)"]

    def init_state(self, ctx):
        return EntityState(status=str(ctx.props.get("status") or "running"),
                           internal={"run_h": ctx.fnum("initialRunHours", 0.0),
                                     "wear": 0.0})

    def step(self, ctx, state):
        running = (state.status or "running") != "off"
        speed = ctx.fnum("speed", 1.0) if running else 0.0
        rated_flow = ctx.fnum("ratedFlowLps", 50.0)      # L/s
        rated_head = ctx.fnum("ratedHeadM", 30.0)        # m
        rated_kw = ctx.fnum("ratedPowerKW", 15.0)

        flow = rated_flow * speed
        press = rated_head * speed * speed * 9.81 * 1000.0 / 1000.0   # kPa approx
        power = rated_kw * (speed ** 3)

        # run-hours + bearing wear (slow), worse at low conditionIndex
        run_h = state.internal.get("run_h", 0.0) + (ctx.dt / 3600.0) * (1 if running else 0)
        cond = ctx.fnum("conditionIndex", 1.0)
        wear = state.internal.get("wear", 0.0) + (ctx.dt / 3600.0) * ctx.fnum(
            "wearPerHour", 0.002) * (2.0 - cond) * speed
        state.internal["run_h"] = run_h
        state.internal["wear"] = wear

        base_vib = ctx.fnum("baselineVibration", 2.5)
        vib = base_vib + wear * ctx.fnum("vibPerWear", 1.0) \
            + ctx.fnum("imbalance", 0.2) * speed + ctx.rng.gauss(0, 0.2)

        if vib > ctx.fnum("vibFault", 11.0):
            state.status = "fault"
        elif vib > ctx.fnum("vibWarn", 7.0):
            state.status = "degraded"

        state.signals = {
            SIG_FLOW: round(flow, 1), SIG_PRESS: round(press, 1),
            SIG_PWR: round(power, 2), SIG_VIB: round(max(0.0, vib), 2),
            SIG_RUN: round(run_h, 2),
        }
        return state
