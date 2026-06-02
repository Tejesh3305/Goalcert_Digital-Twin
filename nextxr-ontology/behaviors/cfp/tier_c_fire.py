"""
CFP Tier-C fire rules: smoke alarm, suppression discharge.
Safety-critical — these rules are human-authored and human-reviewed.
"""
from behaviors.registry import Behavior, Tier, TelemetrySample, Finding


class SmokeAlarmRule(Behavior):
    """Fires when smoke detector obscuration exceeds alarm threshold.
    SAFETY-CRITICAL: rule logic is human-authored, never agent-drafted."""
    behavior_id = "cfp.smoke_alarm"
    tier = Tier.C
    watches = ["cfp:smokeObscuration"]
    reads = ["smoke detector obscuration percentage"]
    emits = "A critical Incident-grade Finding when smoke obscuration exceeds threshold."

    def __init__(self, obscuration_threshold: float = 3.0):
        self.obscuration_threshold = obscuration_threshold
        self._state: dict[str, dict] = {}

    def evaluate(self, sample: TelemetrySample, query) -> list[Finding]:
        st = self._state.setdefault(sample.entity_id, {"fired": False})

        if sample.value <= self.obscuration_threshold:
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
            message=(f"SMOKE ALARM — obscuration {sample.value:.1f}% exceeds "
                     f"threshold {self.obscuration_threshold:.1f}%. "
                     f"[SAFETY-CRITICAL: human review required]"),
            confidence=1.0,
            evidence={
                "value": sample.value,
                "unit": "PERCENT",
                "obscuration_threshold": self.obscuration_threshold,
                "signal": sample.signal,
                "safety_critical": True,
            },
        )]
