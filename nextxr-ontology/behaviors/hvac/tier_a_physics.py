"""
tier_a_physics.py — TIER A (physics): the SKELETON only.

Per the substrate-vs-artifact principle, the actual physics equations are
human-authored later, per domain, by an engineer who owns that physics. The
backbone's job is only to prove the SLOT exists: a registered Tier-A
behaviour with the same Behavior interface as every other tier, that the
registry routes to identically.

So `evaluate` is intentionally inert — it returns no Findings and documents
the contract a real thermal model will implement. Calling `model()` makes the
"not yet authored" boundary explicit and loud, on purpose.
"""

from __future__ import annotations

from behaviors.registry import Behavior, Finding, TelemetrySample, Tier


class ThermalPhysicsBehavior(Behavior):
    behavior_id = "hvac.thermal_physics"
    tier = Tier.A
    watches = ["hvac:AirTemperature"]
    reads = [
        "asset thermal parameters (mass, capacity, U-value)",
        "boundary conditions (ambient temperature, load)",
    ]
    emits = ("A Finding when observed behaviour diverges from the physics "
             "model's prediction (residual beyond tolerance).")

    #: marks this as a reserved slot, not a working model — surfaced to the UI
    is_skeleton = True

    def model(self, sample: TelemetrySample, query):
        """The first-principles prediction. Human-authored later: a real
        implementation solves the thermal balance for the asset and returns a
        predicted value to compare against the observation."""
        raise NotImplementedError(
            "Tier-A physics equations are human-authored per domain; this is "
            "the reserved registry slot, not a working model."
        )

    def evaluate(self, sample: TelemetrySample, query) -> list[Finding]:
        # Inert until a physics model is authored. The slot exists and the
        # registry routes to it exactly like Tier B/C — that is all the
        # backbone needs to prove.
        return []
