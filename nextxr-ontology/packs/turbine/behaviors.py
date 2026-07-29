"""
turbine/behaviors.py — the 3-tier behaviour registry for the gas turbine.

Contract is the platform's generic one (behaviors.registry.Behavior): each rule
watches one signal and returns 0+ Findings. Rules are EDGE-LATCHED — they emit a
Finding once when a condition becomes true and stay quiet until it clears — so the
1 Hz ticker persists one Finding per event, not one per tick.

Cross-signal (Tier-A) rules read co-located signals of the SAME frame through the
query object the runtime passes in (a frame-view keyed by short local names, e.g.
"epr"), exactly like the reference engine.
"""
from __future__ import annotations

from collections import defaultdict, deque

from behaviors.registry import Behavior, BehaviorRegistry, Finding, Tier

from packs._core.physics import HardLimit as _HardLimit
from packs._core.physics import Latch as _Latch

from .physics import _EGT_AMBIENT, _EGT_SPAN, _EPR_100, SIGNALS, redlines


class TurbineEGTDivergence(Behavior):
    """Tier-A physics residual: EGT running hotter than the clean-engine model
    predicts for the current power setting (inferred from co-located EPR)."""
    behavior_id = "turbine.egt_residual"
    tier = Tier.A
    watches = [SIGNALS["egt"]]
    reads = ["egt", "epr (co-located)"]
    emits = "EGT hot for power setting (efficiency loss)"

    def __init__(self, margin: float = 55.0):
        self._margin = margin
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        epr = query.get_property(sample.tenant_id, sample.entity_id, "epr")
        try:
            epr = float(epr)
        except (TypeError, ValueError):
            return []
        thr = max(0.0, min(1.05, (epr / _EPR_100 - 0.55) / 0.45))
        egt_clean = _EGT_AMBIENT + _EGT_SPAN * (0.25 + 0.75 * thr)
        residual = sample.value - egt_clean
        if not self._latch.rising(sample.entity_id, residual > self._margin):
            return []
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
            severity="warning",
            message=f"EGT {residual:.0f}°C above the clean-engine expectation "
                    f"for this power — compressor efficiency loss likely",
            confidence=0.85,
            evidence={"egt": sample.value, "egt_expected": round(egt_clean, 1),
                      "residual": round(residual, 1), "signal": sample.signal},
        )]


class TurbineVibTrend(Behavior):
    """Tier-B statistical baseline: flag vibration deviating from its learned
    mean by more than z sigma once a warm-up window is filled."""
    behavior_id = "turbine.vib_zscore"
    tier = Tier.B
    watches = [SIGNALS["vib"]]
    reads = ["vibration history"]
    emits = "Vibration statistically abnormal"

    def __init__(self, warmup: int = 18, z: float = 3.0, window: int = 60):
        self._warmup = warmup
        self._z = z
        self._hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=window))
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        h = self._hist[sample.entity_id]
        abnormal = False
        detail = {}
        if len(h) >= self._warmup:
            mean = sum(h) / len(h)
            var = sum((x - mean) ** 2 for x in h) / len(h)
            std = var ** 0.5 or 1e-6
            zscore = (sample.value - mean) / std
            abnormal = zscore >= self._z
            detail = {"mean": round(mean, 3), "z": round(zscore, 2)}
        h.append(sample.value)
        if not self._latch.rising(sample.entity_id, abnormal):
            return []
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
            severity="warning",
            message=f"Vibration {sample.value:.2f}g is {detail.get('z', '?')}σ above "
                    f"baseline {detail.get('mean', '?')}g — developing imbalance",
            confidence=0.8,
            evidence={"value": sample.value, "signal": sample.signal, **detail},
        )]


def build_turbine_registry() -> BehaviorRegistry:
    """Fresh registry so per-run baselines/latches start clean."""
    reg = BehaviorRegistry()
    reg.register(_HardLimit("turbine.egt_overtemp", SIGNALS["egt"], redlines.egt,
                            "above", "EGT", "°C"))
    reg.register(_HardLimit("turbine.vib_high", SIGNALS["vib"], redlines.vib,
                            "above", "Vibration", "g"))
    reg.register(_HardLimit("turbine.oil_temp_high", SIGNALS["oil_temp"],
                            redlines.oil_temp, "above", "Oil temperature", "°C"))
    reg.register(_HardLimit("turbine.oil_press_low", SIGNALS["oil_press"],
                            redlines.oil_press_min, "below", "Oil pressure", " PSI"))
    reg.register(TurbineEGTDivergence())
    reg.register(TurbineVibTrend())
    return reg
