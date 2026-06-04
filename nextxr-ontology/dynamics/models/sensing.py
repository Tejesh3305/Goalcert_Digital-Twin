"""
sensing.py — BinaryEventSource and DerivedObserver archetypes.

BinaryEventSource: an entity whose telemetry is a state/level that is normally at a
baseline and occasionally spikes to an alarm value (Poisson-triggered, persisting a
few minutes then decaying). Covers smoke detectors (obscuration), leak sensors,
door state, intrusion sensors, heartbeats (inverted: baseline 1, "event" = 0).
Parameterised entirely by the binding so one archetype serves all of them.

DerivedObserver: a SENSOR reports the TRUE state of the feature it monitors (the
Zone/Room it sits in, or the entity it `monitors`) plus measurement error — noise,
bias, slow drift, and occasional dropout. This guarantees the sensor agrees with
what it measures (self-consistency) and is where measurement realism lives.
"""

from __future__ import annotations

from dynamics.model import DynamicsModel, EntityState, EntityContext
from dynamics import flows

CFP = "https://ontology.nextxr.io/v3/cfp#"


class BinaryEventSourceModel(DynamicsModel):
    archetype = "BinaryEventSource"
    produces = []     # signal is set per-binding via params["signal"]
    consumes = []

    def init_state(self, ctx):
        return EntityState(status="running",
                           internal={"alarm_left": 0.0})

    def step(self, ctx, state):
        signal = ctx.param("signal", CFP + "doorState")
        baseline = ctx.fnum("baseline", 0.0)
        alarm_value = ctx.fnum("alarmValue", 1.0)
        noise = ctx.fnum("noise", 0.0)

        left = state.internal.get("alarm_left", 0.0)
        if left <= 0 and ctx.rng.random() < ctx.fnum("eventRatePerHour", 0.0) * ctx.dt / 3600.0:
            left = ctx.fnum("eventDurationMin", 1.0) * 60.0
        if left > 0:
            val = alarm_value
            left -= ctx.dt
            state.status = "degraded"
        else:
            val = baseline + (ctx.rng.gauss(0, noise) if noise else 0.0)
            state.status = "running"
        state.internal["alarm_left"] = max(0.0, left)
        state.signals = {signal: round(val, 3)}
        return state


class DerivedObserverModel(DynamicsModel):
    archetype = "DerivedObserver"
    produces = []     # the observed signal, from params["observes"]
    consumes = ["OBSERVATION (monitored feature) or SPATIAL (its space)"]

    def init_state(self, ctx):
        return EntityState(status="running", internal={"drift": 0.0})

    def step(self, ctx, state):
        observed = ctx.param("observes", CFP + "temperature")
        # source of truth: the entity this sensor monitors, else the space it's in
        true_val = None
        mon = flows.upstream_by_flow(ctx, flows.Flow.OBSERVATION)
        for s in mon:
            if observed in s.signals:
                true_val = s.signals[observed]; break
        if true_val is None and ctx.space is not None:
            true_val = ctx.space.signals.get(observed)
        if true_val is None:
            true_val = ctx.fnum("fallback", 22.0)

        # measurement error: bias + slow drift + noise + occasional dropout
        drift = state.internal.get("drift", 0.0) + ctx.fnum("driftPerHour", 0.0) * ctx.dt / 3600.0
        state.internal["drift"] = drift
        bias = ctx.fnum("bias", 0.0)
        noise = ctx.fnum("noise", 0.1)
        if ctx.rng.random() < ctx.fnum("dropoutRate", 0.0):
            return state                    # dropped sample: emit nothing this tick
        val = true_val + bias + drift + ctx.rng.gauss(0, noise)
        state.signals = {observed: round(val, 2)}
        return state
