"""
CFP Tier-C power rules: UPS on-battery, generator fuel-low, transformer over-temp.
"""
from datetime import timedelta
from behaviors.registry import Behavior, Tier, TelemetrySample, Finding


class UPSOnBatteryRule(Behavior):
    """Fires when a UPS switches to battery mode (SoC dropping below threshold)."""
    behavior_id = "cfp.ups_on_battery"
    tier = Tier.C
    watches = ["cfp:upsSoC"]
    reads = ["UPS state-of-charge percentage"]
    emits = "A critical Finding when UPS SoC drops below threshold, indicating mains loss."

    def __init__(self, soc_threshold: float = 95.0):
        self.soc_threshold = soc_threshold
        self._state: dict[str, dict] = {}

    def evaluate(self, sample: TelemetrySample, query) -> list[Finding]:
        st = self._state.setdefault(sample.entity_id, {"fired": False})

        if sample.value >= self.soc_threshold:
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
            message=(f"UPS on battery — SoC {sample.value:.0f}% "
                     f"(threshold {self.soc_threshold:.0f}%). Mains may be lost."),
            confidence=1.0,
            evidence={
                "value": sample.value,
                "unit": "PERCENT",
                "soc_threshold": self.soc_threshold,
                "signal": sample.signal,
            },
        )]


class GeneratorFuelLowRule(Behavior):
    """Fires when backup generator fuel drops below a critical level."""
    behavior_id = "cfp.generator_fuel_low"
    tier = Tier.C
    watches = ["cfp:fuelLevel"]
    reads = ["generator fuel level percentage"]
    emits = "A warning Finding when generator fuel drops below 20%."

    def __init__(self, fuel_threshold: float = 20.0):
        self.fuel_threshold = fuel_threshold
        self._state: dict[str, dict] = {}

    def evaluate(self, sample: TelemetrySample, query) -> list[Finding]:
        st = self._state.setdefault(sample.entity_id, {"fired": False})

        if sample.value >= self.fuel_threshold:
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
            message=(f"Generator fuel low — {sample.value:.0f}% remaining "
                     f"(threshold {self.fuel_threshold:.0f}%)."),
            confidence=1.0,
            evidence={
                "value": sample.value,
                "unit": "PERCENT",
                "fuel_threshold": self.fuel_threshold,
                "signal": sample.signal,
            },
        )]


class TransformerOverTempRule(Behavior):
    """Fires when transformer oil temperature exceeds safe limit."""
    behavior_id = "cfp.transformer_over_temp"
    tier = Tier.C
    watches = ["cfp:oilTemperature"]
    reads = ["transformer oil temperature"]
    emits = "A critical Finding when oil temperature exceeds the limit for sustained time."

    def __init__(self, temp_limit: float = 85.0, duration_minutes: float = 2.0):
        self.temp_limit = temp_limit
        self.duration = timedelta(minutes=duration_minutes)
        self._state: dict[str, dict] = {}

    def evaluate(self, sample: TelemetrySample, query) -> list[Finding]:
        st = self._state.setdefault(sample.entity_id,
                                    {"start": None, "fired": False})

        if sample.value <= self.temp_limit:
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
                severity="critical",
                message=(f"Transformer oil temperature {sample.value:.1f}°C exceeds "
                         f"limit {self.temp_limit:.0f}°C for {minutes:.0f} min."),
                confidence=1.0,
                evidence={
                    "value": sample.value,
                    "unit": "DEG_C",
                    "temp_limit": self.temp_limit,
                    "sustained_minutes": round(minutes, 1),
                    "signal": sample.signal,
                },
            )]
        return []
