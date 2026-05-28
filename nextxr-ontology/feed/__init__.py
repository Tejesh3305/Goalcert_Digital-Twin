"""Simulated telemetry feed + the findings-loop driver.

The feed produces TelemetrySamples; the driver routes each through the
BehaviorRegistry and writes every emitted Finding back into the graph via
the Graph Writer — the same single write path everything else uses. This is
the component that closes the Track 3 loop.
"""

from .simulate import simulate_temperature, FindingsLoop

__all__ = ["simulate_temperature", "FindingsLoop"]
