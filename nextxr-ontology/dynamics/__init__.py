"""
dynamics — the generative Entity-Dynamics layer.

Produces live, coupled, physics-based telemetry for every entity in a twin, driven
by the ontology's relationships. Complements (does not replace) behaviors/, which
DETECTS anomalies in telemetry. The engine is domain-agnostic: coupling works the
same for any pack via the relationship graph + flow vocabulary.

    from dynamics import build_dynamics_registry, DynamicsEngine
    reg = build_dynamics_registry()
    eng = DynamicsEngine(tenant_id, reg, GraphQuery(), speed=60.0)
    eng.run_realtime(on_samples=loop_push, should_stop=lambda: not running)
"""

from dynamics.model import (DynamicsModel, DynamicsRegistry, EntityState,
                            EntityContext)
from dynamics.engine import DynamicsEngine
from dynamics.registry_build import build_dynamics_registry

__all__ = ["DynamicsModel", "DynamicsRegistry", "EntityState", "EntityContext",
           "DynamicsEngine", "build_dynamics_registry"]
