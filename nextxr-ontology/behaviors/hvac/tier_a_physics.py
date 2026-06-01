"""
tier_a_physics.py — TIER A (physics): Simplified thermal balance model.

Implements a first-order lumped-parameter thermal model for an air handler.
The model predicts what the supply air temperature SHOULD be given:
  - The setpoint (from the graph)
  - Time since startup (thermal inertia)
  - A simplified thermal decay constant

When the observed temperature diverges from the model's prediction beyond
a residual threshold, a Finding is emitted. This catches faults that
rules and statistics miss: the system is behaving physically wrong even
if no threshold is crossed yet.

The model equation (first-order exponential approach):
    T_predicted(t) = T_setpoint + (T_initial - T_setpoint) * exp(-t / tau)

Where tau is the thermal time constant of the system (how fast it reaches
setpoint). A real implementation would use asset mass, heat capacity, and
U-value from the graph. This simplified version uses a fixed tau.
"""

from __future__ import annotations

import math
from behaviors.registry import Behavior, Finding, TelemetrySample, Tier


class ThermalPhysicsBehavior(Behavior):
    behavior_id = "hvac.thermal_physics"
    tier = Tier.A
    watches = ["hvac:AirTemperature"]
    reads = [
        "asset setpoint from graph",
        "thermal time constant (simplified)",
    ]
    emits = ("A Finding when observed temperature diverges from the physics "
             "model's prediction by more than the residual threshold.")

    is_skeleton = False  # Now a working model

    def __init__(self, tau_minutes: float = 10.0, residual_threshold: float = 4.0):
        """
        tau_minutes: thermal time constant — how many minutes to reach 63%
                     of the way from initial to setpoint. Lower = faster response.
        residual_threshold: degrees C divergence from prediction before firing.
        """
        self.tau = tau_minutes
        self.threshold = residual_threshold
        self._state: dict[str, dict] = {}  # per-entity state

    def _predict(self, setpoint: float, t_initial: float, minutes_elapsed: float) -> float:
        """First-order thermal model: exponential approach to setpoint."""
        if self.tau <= 0:
            return setpoint
        return setpoint + (t_initial - setpoint) * math.exp(-minutes_elapsed / self.tau)

    def evaluate(self, sample: TelemetrySample, query) -> list[Finding]:
        key = sample.entity_id

        # Initialize state on first sample
        if key not in self._state:
            setpoint = 22.0  # default
            try:
                sp = query.get_property(sample.tenant_id, sample.entity_id, "setpoint")
                if sp is not None:
                    setpoint = float(sp)
            except Exception:
                pass

            self._state[key] = {
                "setpoint": setpoint,
                "t_initial": sample.value,  # first observed value
                "start_time": sample.timestamp,
                "fired": False,
            }
            return []

        state = self._state[key]
        setpoint = state["setpoint"]
        t_initial = state["t_initial"]
        elapsed = (sample.timestamp - state["start_time"]).total_seconds() / 60.0

        # Model prediction
        predicted = self._predict(setpoint, t_initial, elapsed)
        residual = sample.value - predicted

        if abs(residual) > self.threshold and not state["fired"]:
            state["fired"] = True
            direction = "above" if residual > 0 else "below"
            return [Finding(
                behavior_id=self.behavior_id,
                tier=Tier.A,
                flags=sample.entity_id,
                severity="warning",
                confidence=min(0.95, 0.6 + abs(residual) / 10.0),
                message=(f"Physics model residual: observed {sample.value:.1f}C is "
                         f"{abs(residual):.1f}C {direction} predicted {predicted:.1f}C "
                         f"(setpoint={setpoint}C, tau={self.tau}min). "
                         f"Thermal behavior deviates from expected energy balance."),
            )]

        # Reset if residual drops back
        if abs(residual) <= self.threshold * 0.5:
            state["fired"] = False

        return []
