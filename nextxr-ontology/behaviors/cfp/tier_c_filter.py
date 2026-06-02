"""
CFP Tier-C filter rule: AHU filter clogged via differential pressure.
"""
from datetime import timedelta
from behaviors.registry import Behavior, Tier, TelemetrySample, Finding


class FilterCloggedRule(Behavior):
    """Fires when AHU filter differential pressure exceeds threshold,
    indicating a clogged filter that needs replacement."""
    behavior_id = "cfp.filter_clogged"
    tier = Tier.C
    watches = ["cfp:filterDeltaP"]
    reads = ["filter differential pressure (Pa)"]
    emits = "A warning Finding when filter dP exceeds threshold for sustained time."

    def __init__(self, dp_threshold: float = 250.0, duration_minutes: float = 5.0):
        self.dp_threshold = dp_threshold
        self.duration = timedelta(minutes=duration_minutes)
        self._state: dict[str, dict] = {}

    def evaluate(self, sample: TelemetrySample, query) -> list[Finding]:
        st = self._state.setdefault(sample.entity_id,
                                    {"start": None, "fired": False})

        if sample.value <= self.dp_threshold:
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
                message=(f"Air filter clogged — ΔP {sample.value:.0f} Pa exceeds "
                         f"threshold {self.dp_threshold:.0f} Pa for {minutes:.0f} min. "
                         f"Recommend replacement."),
                confidence=1.0,
                evidence={
                    "value": sample.value,
                    "unit": "PA",
                    "dp_threshold": self.dp_threshold,
                    "sustained_minutes": round(minutes, 1),
                    "signal": sample.signal,
                },
            )]
        return []
