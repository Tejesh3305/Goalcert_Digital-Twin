"""
CFP Tier-B pump rule: vibration baseline for rotating equipment health.
Learns normal vibration velocity, flags when it exceeds the learned range.
"""
import math
from behaviors.registry import Behavior, Tier, TelemetrySample, Finding


class PumpVibrationBaseline(Behavior):
    """Statistical baseline on pump vibration velocity. Learns from the warmup
    window, then fires when vibration exceeds the learned upper bound."""
    behavior_id = "cfp.pump_vibration_baseline"
    tier = Tier.B
    watches = ["cfp:vibration"]
    reads = ["the live stream of pump vibration velocity readings (mm/s)"]
    emits = "A warning Finding when vibration exceeds the learned baseline by > z_threshold sigma."

    def __init__(self, warmup: int = 20, z_threshold: float = 3.0):
        self.warmup = warmup
        self.z_threshold = z_threshold
        self._samples: dict[str, list[float]] = {}
        self._baseline: dict[str, tuple[float, float]] = {}
        self._firing: dict[str, bool] = {}

    def _fit(self, values: list[float]) -> tuple[float, float]:
        n = len(values)
        mean = sum(values) / n
        var = sum((v - mean) ** 2 for v in values) / n
        std = math.sqrt(var)
        return mean, std

    def evaluate(self, sample: TelemetrySample, query) -> list[Finding]:
        ent = sample.entity_id

        # Learning phase
        if ent not in self._baseline:
            buf = self._samples.setdefault(ent, [])
            buf.append(sample.value)
            if len(buf) >= self.warmup:
                self._baseline[ent] = self._fit(buf)
            return []

        # Scoring phase
        mean, std = self._baseline[ent]
        if std < 1e-9:
            return []

        z = (sample.value - mean) / std

        # Only flag HIGH vibration (degradation), not low
        if z <= self.z_threshold:
            self._firing[ent] = False
            return []

        if self._firing.get(ent):
            return []
        self._firing[ent] = True

        confidence = min(1.0, abs(z) / (self.z_threshold * 2))
        return [Finding(
            behavior_id=self.behavior_id,
            tier=self.tier,
            flags=ent,
            severity="warning",
            message=(f"Pump vibration elevated — {sample.value:.2f} mm/s is "
                     f"{z:+.1f}σ above baseline (μ={mean:.2f}, σ={std:.3f}). "
                     f"Possible bearing wear or misalignment."),
            confidence=round(confidence, 2),
            evidence={
                "value": sample.value,
                "unit": "MM_PER_SEC",
                "z_score": round(z, 2),
                "baseline_mean": round(mean, 2),
                "baseline_std": round(std, 3),
                "z_threshold": self.z_threshold,
                "signal": sample.signal,
            },
        )]
