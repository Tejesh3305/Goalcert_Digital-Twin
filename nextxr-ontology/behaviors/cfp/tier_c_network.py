"""
CFP Tier-C network rules: edge node heartbeat loss.
"""
from datetime import timedelta
from behaviors.registry import Behavior, Tier, TelemetrySample, Finding


class HeartbeatLossRule(Behavior):
    """Fires when an edge node's heartbeat signal goes silent (value = 0) or
    when the gap between heartbeats exceeds the timeout."""
    behavior_id = "cfp.heartbeat_loss"
    tier = Tier.C
    watches = ["cfp:heartbeat"]
    reads = ["edge node heartbeat signal (1 = alive, 0 = missed)"]
    emits = "A warning Finding when heartbeat is missed, indicating node offline."

    def __init__(self, miss_threshold: int = 2):
        self.miss_threshold = miss_threshold
        self._state: dict[str, dict] = {}

    def evaluate(self, sample: TelemetrySample, query) -> list[Finding]:
        st = self._state.setdefault(sample.entity_id,
                                    {"misses": 0, "fired": False})

        if sample.value >= 1.0:
            st["misses"] = 0
            st["fired"] = False
            return []

        st["misses"] += 1

        if st["misses"] >= self.miss_threshold and not st["fired"]:
            st["fired"] = True
            return [Finding(
                behavior_id=self.behavior_id,
                tier=self.tier,
                flags=sample.entity_id,
                severity="warning",
                message=(f"Edge node heartbeat lost — {st['misses']} consecutive "
                         f"misses (threshold {self.miss_threshold})."),
                confidence=1.0,
                evidence={
                    "consecutive_misses": st["misses"],
                    "miss_threshold": self.miss_threshold,
                    "signal": sample.signal,
                },
            )]
        return []
