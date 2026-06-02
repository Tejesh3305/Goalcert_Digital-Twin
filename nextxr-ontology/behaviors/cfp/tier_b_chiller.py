"""
CFP Tier-B chiller rule: COP (coefficient of performance) baseline drift.
Learns a normal COP from the warmup window, then flags efficiency degradation.
"""
import math
from behaviors.registry import Behavior, Tier, TelemetrySample, Finding


class ChillerCOPBaseline(Behavior):
    """Statistical baseline on chiller COP. Learns a normal range during warmup,
    then fires when COP drops significantly below the learned mean."""
    behavior_id = "cfp.chiller_cop_baseline"
    tier = Tier.B
    watches = ["cfp:chillerCOP"]
    reads = ["the live stream of chiller COP readings"]
    emits = "A warning Finding when COP drops below the learned baseline by > z_threshold sigma."

    def __init__(self, warmup: int = 15, z_threshold: float = 2.5):
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

        # Only flag LOW COP (efficiency degradation), not high
        if z >= -self.z_threshold:
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
            message=(f"Chiller efficiency drift — COP {sample.value:.2f} is "
                     f"{z:+.1f}σ below baseline (μ={mean:.2f}, σ={std:.3f}). "
                     f"Possible refrigerant issue or fouled condenser."),
            confidence=round(confidence, 2),
            evidence={
                "value": sample.value,
                "z_score": round(z, 2),
                "baseline_mean": round(mean, 2),
                "baseline_std": round(std, 3),
                "z_threshold": self.z_threshold,
                "signal": sample.signal,
            },
        )]
