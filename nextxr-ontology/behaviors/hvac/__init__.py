"""HVAC bundle behaviours — one per tier, registered into the generic
BehaviorRegistry. This is Track 3's toy domain: the cleanest test case for
the three-tier model."""

from .tier_c_threshold import TemperatureThresholdRule
from .tier_b_zscore import TemperatureZScoreBaseline
from .tier_a_physics import ThermalPhysicsBehavior

__all__ = [
    "TemperatureThresholdRule",
    "TemperatureZScoreBaseline",
    "ThermalPhysicsBehavior",
]
