"""
tier_c_threshold.py — TIER C (rule): a hand-written threshold.

"If air temperature exceeds setpoint + N for M minutes, emit a Finding."

This is the simplest tier and the one that exercises the whole loop end to
end. It reads the monitored asset's `setpoint` from the graph (the read
contract), tracks how long the excursion has been sustained per entity, and
fires exactly once per excursion (resetting when the value falls back).
"""

from __future__ import annotations

from datetime import timedelta

from behaviors.registry import Behavior, Finding, TelemetrySample, Tier


class TemperatureThresholdRule(Behavior):
    behavior_id = "hvac.temp_threshold"
    tier = Tier.C
    watches = ["hvac:AirTemperature"]
    reads = ["setpoint (°C) of the monitored asset (graph property)"]
    emits = "A critical Finding when temperature exceeds setpoint+N for ≥M minutes."

    def __init__(self, offset_c: float = 3.0, duration_minutes: float = 3.0,
                 default_setpoint: float = 22.0):
        self.offset_c = offset_c
        self.duration = timedelta(minutes=duration_minutes)
        self.default_setpoint = default_setpoint
        # per-entity excursion state: {entity_id: {"start": ts, "fired": bool}}
        self._state: dict[str, dict] = {}

    def evaluate(self, sample: TelemetrySample, query) -> list[Finding]:
        setpoint = query.get_property(
            sample.tenant_id, sample.entity_id, "setpoint",
            default=self.default_setpoint,
        )
        try:
            setpoint = float(setpoint)
        except (TypeError, ValueError):
            setpoint = self.default_setpoint
        threshold = setpoint + self.offset_c

        st = self._state.setdefault(sample.entity_id, {"start": None, "fired": False})

        # Back below threshold -> excursion over, reset.
        if sample.value <= threshold:
            st["start"] = None
            st["fired"] = False
            return []

        # Above threshold: start (or continue) timing the excursion.
        if st["start"] is None:
            st["start"] = sample.timestamp
            return []

        sustained = sample.timestamp - st["start"]
        if sustained >= self.duration and not st["fired"]:
            st["fired"] = True
            minutes = sustained.total_seconds() / 60.0
            return [Finding(
                behavior_id=self.behavior_id,
                tier=self.tier,
                flags=sample.entity_id,
                severity="critical",
                message=(f"Air temperature {sample.value:.1f}°C exceeded "
                         f"setpoint+{self.offset_c:.0f} ({threshold:.1f}°C) "
                         f"for {minutes:.0f} min."),
                confidence=1.0,
                evidence={
                    "value": sample.value,
                    "unit": sample.unit,
                    "setpoint": setpoint,
                    "threshold": threshold,
                    "sustained_minutes": round(minutes, 1),
                    "signal": sample.signal,
                },
            )]
        return []
