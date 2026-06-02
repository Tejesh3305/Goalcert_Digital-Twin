"""
CFP Tier-C security rules: door forced/held-open, repeated access deny.
"""
from datetime import timedelta
from behaviors.registry import Behavior, Tier, TelemetrySample, Finding


class DoorForcedRule(Behavior):
    """Fires when an access door reports a forced-open or held-open event.
    Signal value encoding: 0=normal, 1=forced, 2=held-open."""
    behavior_id = "cfp.door_forced"
    tier = Tier.C
    watches = ["cfp:doorState"]
    reads = ["access door state signal"]
    emits = "A warning Finding when a door is forced open or held open."

    def __init__(self):
        self._state: dict[str, dict] = {}

    def evaluate(self, sample: TelemetrySample, query) -> list[Finding]:
        st = self._state.setdefault(sample.entity_id, {"fired": False})

        # 0 = normal
        if sample.value < 0.5:
            st["fired"] = False
            return []

        if st["fired"]:
            return []
        st["fired"] = True

        event_type = "forced open" if sample.value < 1.5 else "held open"
        return [Finding(
            behavior_id=self.behavior_id,
            tier=self.tier,
            flags=sample.entity_id,
            severity="warning",
            message=f"Access door {event_type} detected.",
            confidence=1.0,
            evidence={
                "value": sample.value,
                "event_type": event_type,
                "signal": sample.signal,
            },
        )]


class RepeatedDenyRule(Behavior):
    """Fires when an access reader logs repeated denials within a time window."""
    behavior_id = "cfp.repeated_deny"
    tier = Tier.C
    watches = ["cfp:accessDeny"]
    reads = ["access reader deny event count"]
    emits = "A warning Finding when repeated access denials suggest tailgating or intrusion."

    def __init__(self, deny_threshold: int = 3, window_minutes: float = 5.0):
        self.deny_threshold = deny_threshold
        self.window = timedelta(minutes=window_minutes)
        self._events: dict[str, list] = {}
        self._fired: dict[str, bool] = {}

    def evaluate(self, sample: TelemetrySample, query) -> list[Finding]:
        ent = sample.entity_id
        events = self._events.setdefault(ent, [])

        # Each sample with value >= 1 counts as a deny event
        if sample.value >= 1.0:
            events.append(sample.timestamp)

        # Trim events outside window
        cutoff = sample.timestamp - self.window
        self._events[ent] = [t for t in events if t >= cutoff]

        if len(self._events[ent]) < self.deny_threshold:
            self._fired[ent] = False
            return []

        if self._fired.get(ent):
            return []
        self._fired[ent] = True

        return [Finding(
            behavior_id=self.behavior_id,
            tier=self.tier,
            flags=ent,
            severity="warning",
            message=(f"Repeated access denials: {len(self._events[ent])} denies "
                     f"within {self.window.total_seconds() / 60:.0f} min window."),
            confidence=0.85,
            evidence={
                "deny_count": len(self._events[ent]),
                "window_minutes": self.window.total_seconds() / 60,
                "threshold": self.deny_threshold,
                "signal": sample.signal,
            },
        )]
