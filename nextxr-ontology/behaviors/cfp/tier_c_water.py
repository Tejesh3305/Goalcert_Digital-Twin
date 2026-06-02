"""
CFP Tier-C water rules: leak detect, tank low-level, continuous flow (pipe leak).
"""
from datetime import timedelta
from behaviors.registry import Behavior, Tier, TelemetrySample, Finding


class LeakDetectedRule(Behavior):
    """Fires when a leak sensor reports wet state (value >= 1)."""
    behavior_id = "cfp.leak_detected"
    tier = Tier.C
    watches = ["cfp:leakState"]
    reads = ["leak sensor wet/dry state"]
    emits = "A critical Finding when a leak sensor detects water."

    def __init__(self):
        self._state: dict[str, dict] = {}

    def evaluate(self, sample: TelemetrySample, query) -> list[Finding]:
        st = self._state.setdefault(sample.entity_id, {"fired": False})

        if sample.value < 1.0:
            st["fired"] = False
            return []

        if st["fired"]:
            return []
        st["fired"] = True

        return [Finding(
            behavior_id=self.behavior_id,
            tier=self.tier,
            flags=sample.entity_id,
            severity="critical",
            message="Water leak detected — sensor reports wet state.",
            confidence=1.0,
            evidence={
                "value": sample.value,
                "signal": sample.signal,
            },
        )]


class TankLowLevelRule(Behavior):
    """Fires when a water tank level drops below a critical threshold."""
    behavior_id = "cfp.tank_low_level"
    tier = Tier.C
    watches = ["cfp:tankLevel"]
    reads = ["water tank fill level percentage"]
    emits = "A warning Finding when tank level drops below threshold."

    def __init__(self, level_threshold: float = 15.0):
        self.level_threshold = level_threshold
        self._state: dict[str, dict] = {}

    def evaluate(self, sample: TelemetrySample, query) -> list[Finding]:
        st = self._state.setdefault(sample.entity_id, {"fired": False})

        if sample.value >= self.level_threshold:
            st["fired"] = False
            return []

        if st["fired"]:
            return []
        st["fired"] = True

        return [Finding(
            behavior_id=self.behavior_id,
            tier=self.tier,
            flags=sample.entity_id,
            severity="warning",
            message=(f"Water tank low — {sample.value:.0f}% remaining "
                     f"(threshold {self.level_threshold:.0f}%)."),
            confidence=1.0,
            evidence={
                "value": sample.value,
                "unit": "PERCENT",
                "level_threshold": self.level_threshold,
                "signal": sample.signal,
            },
        )]


class ContinuousFlowLeakRule(Behavior):
    """Fires when a water meter reports continuous non-zero flow for too long,
    suggesting a pipe leak or stuck valve."""
    behavior_id = "cfp.continuous_flow_leak"
    tier = Tier.C
    watches = ["cfp:waterFlow"]
    reads = ["water meter flow rate"]
    emits = "A warning Finding when continuous flow exceeds duration threshold."

    def __init__(self, min_flow: float = 0.1, duration_minutes: float = 30.0):
        self.min_flow = min_flow
        self.duration = timedelta(minutes=duration_minutes)
        self._state: dict[str, dict] = {}

    def evaluate(self, sample: TelemetrySample, query) -> list[Finding]:
        st = self._state.setdefault(sample.entity_id,
                                    {"start": None, "fired": False})

        if sample.value < self.min_flow:
            st["start"] = None
            st["fired"] = False
            return []

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
                severity="warning",
                message=(f"Continuous water flow ({sample.value:.2f} L/s) "
                         f"for {minutes:.0f} min — possible pipe leak."),
                confidence=0.8,
                evidence={
                    "value": sample.value,
                    "unit": "L_PER_SEC",
                    "sustained_minutes": round(minutes, 1),
                    "min_flow_threshold": self.min_flow,
                    "signal": sample.signal,
                },
            )]
        return []
