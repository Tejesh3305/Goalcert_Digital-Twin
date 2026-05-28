"""
tier_b_zscore.py — TIER B (statistical): a learned baseline.

A z-score baseline learner. It learns the normal mean/standard-deviation of a
signal from a warm-up window, then flags any reading that deviates by more
than `z_threshold` standard deviations. This proves the registry handles
LEARNED behaviours, not just hand-written rules — the same slot a more
sophisticated model occupies.

Dependency-free by design (pure stdlib). If scikit-learn is present, an
IsolationForest variant slots in behind the identical Behavior interface;
the z-score is the zero-dependency default the backbone ships with.
"""

from __future__ import annotations

import math

from behaviors.registry import Behavior, Finding, TelemetrySample, Tier


class TemperatureZScoreBaseline(Behavior):
    behavior_id = "hvac.temp_zscore"
    tier = Tier.B
    watches = ["hvac:AirTemperature"]
    reads = ["the live stream of the signal it learns a baseline from"]
    emits = "A warning Finding when a reading deviates > z_threshold σ from the learned baseline."

    def __init__(self, warmup: int = 12, z_threshold: float = 3.0):
        self.warmup = warmup
        self.z_threshold = z_threshold
        # per-entity learning state
        self._samples: dict[str, list[float]] = {}
        self._baseline: dict[str, tuple[float, float]] = {}  # entity -> (mean, std)
        self._firing: dict[str, bool] = {}  # debounce: one finding per episode

    def _fit(self, values: list[float]) -> tuple[float, float]:
        n = len(values)
        mean = sum(values) / n
        var = sum((v - mean) ** 2 for v in values) / n
        std = math.sqrt(var)
        return mean, std

    def evaluate(self, sample: TelemetrySample, query) -> list[Finding]:
        ent = sample.entity_id

        # --- learning phase: accumulate the warm-up window, then freeze ---
        if ent not in self._baseline:
            buf = self._samples.setdefault(ent, [])
            buf.append(sample.value)
            if len(buf) >= self.warmup:
                self._baseline[ent] = self._fit(buf)
            return []   # never flags while still learning

        # --- scoring phase ---
        mean, std = self._baseline[ent]
        if std < 1e-9:                      # flat baseline; no meaningful z
            return []
        z = (sample.value - mean) / std

        # Debounce: emit one Finding when the signal ENTERS an anomalous
        # episode, not on every reading while it stays there.
        if abs(z) <= self.z_threshold:
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
            message=(f"Air temperature {sample.value:.1f}°C is {z:+.1f}σ from "
                     f"the learned baseline (μ={mean:.1f}°C, σ={std:.2f})."),
            confidence=round(confidence, 2),
            evidence={
                "value": sample.value,
                "z_score": round(z, 2),
                "baseline_mean": round(mean, 2),
                "baseline_std": round(std, 2),
                "z_threshold": self.z_threshold,
                "signal": sample.signal,
            },
        )]
