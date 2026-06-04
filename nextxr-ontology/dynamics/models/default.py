"""
default.py — the fallback model so EVERY entity emits something sensible.

Registered on cfp:FacilityEquipment (and nxr:Sensor), so any class without a
dedicated model still produces a believable signal instead of going silent.
This is the "constant + noise + small electrical load" baseline; replace it with
a real model per class as you build them out.

A bare sensor with no monitored feature reports its own nominal value; a sensor
that monitors a feature should eventually get a derived-observer model (reads the
monitored entity's signal + measurement noise). For now this keeps the twin alive.
"""

from __future__ import annotations

from dynamics.model import DynamicsModel, EntityState, EntityContext

CFP = "https://ontology.nextxr.io/v3/cfp#"
SIG_PWR = CFP + "activePower"


class DefaultEquipmentModel(DynamicsModel):
    archetype = "DefaultEquipment"
    models = [CFP + "FacilityEquipment"]
    produces = [SIG_PWR]
    consumes = []

    def step(self, ctx: EntityContext, state: EntityState) -> EntityState:
        status = str(ctx.props.get("status") or "running")
        base_kw = ctx.fnum("baseLoadKW", 1.0) if status == "running" else 0.0
        power = base_kw * (1 + ctx.rng.gauss(0, 0.03))
        state.status = status
        state.signals = {SIG_PWR: round(max(0.0, power), 3)}
        return state
