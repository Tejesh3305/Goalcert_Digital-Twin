"""
archetypes.py — data-driven MONITORING archetypes (the detective half).

A monitoring rule is DATA (a dict): which signal it watches, what kind of check,
and parameters. `make_behavior(rule)` turns that dict into a live `Behavior` with
no new Python per asset. This generalises the original `DynamicThresholdRule`
(server/main.py) into the full family, so the binding layer, published bundles, and
the Bundle Author's `behavior_models` all wire to live behaviours through one path.

Six kinds cover every monitoring behaviour the platform has had so far:

  threshold           value crosses a fixed bound (UPS SoC<95, fuel<20, ΔP>limit)
  sustained_threshold value beyond a bound (or setpoint+offset) for ≥ duration
  rate_of_change      |Δvalue/Δt| exceeds a max rate
  zscore              learn μ/σ over a warmup window, fire when |z| (or one side) > k
  physics_residual    observed vs an expected value (param or companion signal)
  state               discrete/state signal equals/≠ an alarm value, or heartbeat lost
                      (supports count-in-window, e.g. repeated access denials)

Rule dict schema (all keys optional except behavior_id, kind, watches):
  {
    "behavior_id": str, "kind": str, "watches": "cfp:upsSoC",
    "tier": "A"|"B"|"C", "severity": "info"|"warning"|"critical", "message": str,
    # threshold / sustained_threshold:
    "bound": float, "direction": "above"|"below",
    "setpoint_from_graph": bool, "offset": float, "duration_minutes": float,
    # rate_of_change:  "max_rate": float, "per_minutes": float,
    # zscore:          "warmup": int, "z_threshold": float, "side": "both"|"high"|"low",
    # physics_residual:"expected": float, "expected_signal": "cfp:...", "residual_threshold": float,
    # state:           "alarm_value": float, "compare": "eq"|"ne"|"missing",
    #                  "count_in_window": int, "window_minutes": float,
  }
"""

from __future__ import annotations

from datetime import timedelta

from behaviors.registry import Behavior, Finding, TelemetrySample, Tier


def _tier(rule: dict) -> Tier:
    t = str(rule.get("tier", "C")).upper()
    return {"A": Tier.A, "B": Tier.B, "C": Tier.C}.get(t, Tier.C)


class _BaseRule(Behavior):
    """Shared plumbing: id, watched signal, severity, per-entity debounce state."""

    def __init__(self, rule: dict):
        self.rule = dict(rule)
        self.behavior_id = rule.get("behavior_id", "dynamic.rule")
        self.tier = _tier(rule)
        sig = rule.get("watches", "")
        self.watches = [sig] if isinstance(sig, str) and sig else list(sig or [])
        self.reads = rule.get("reads", [])
        self.emits = rule.get("message") or rule.get("description", "Finding")
        self.severity = rule.get("severity", "warning")
        self._state: dict[str, dict] = {}

    def _finding(self, sample, message, *, confidence=1.0, evidence=None) -> Finding:
        return Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
            severity=self.severity, message=message, confidence=confidence,
            evidence=evidence or {"value": sample.value, "signal": sample.signal},
        )


class ThresholdRule(_BaseRule):
    """Instantaneous bound check (fires once per excursion)."""

    def __init__(self, rule):
        super().__init__(rule)
        self.bound = float(rule.get("bound", 0.0))
        self.direction = rule.get("direction", "above")
        self.severity = rule.get("severity", "warning")

    def _breached(self, v):
        return v > self.bound if self.direction == "above" else v < self.bound

    def evaluate(self, sample, query):
        st = self._state.setdefault(sample.entity_id, {"fired": False})
        if not self._breached(sample.value):
            st["fired"] = False
            return []
        if st["fired"]:
            return []
        st["fired"] = True
        return [self._finding(
            sample,
            f"{self.emits} — {sample.value:.2f} {self.direction} {self.bound:.2f}.",
            evidence={"value": sample.value, "bound": self.bound,
                      "direction": self.direction, "signal": sample.signal})]


class SustainedThresholdRule(_BaseRule):
    """Value beyond a bound (absolute, or setpoint+offset read from the graph) for
    ≥ duration. Fires once per sustained excursion."""

    def __init__(self, rule):
        super().__init__(rule)
        self.offset = float(rule.get("offset", rule.get("offset_c", 0.0)))
        self.abs_bound = rule.get("bound")
        self.setpoint_from_graph = bool(rule.get("setpoint_from_graph",
                                                  self.abs_bound is None))
        self.default_setpoint = float(rule.get("default_setpoint", 22.0))
        self.direction = rule.get("direction", "above")
        self.duration = timedelta(minutes=float(rule.get("duration_minutes", 3.0)))
        self.severity = rule.get("severity", "critical")

    def _threshold(self, sample, query) -> float:
        if self.abs_bound is not None and not self.setpoint_from_graph:
            return float(self.abs_bound)
        sp = query.get_property(sample.tenant_id, sample.entity_id, "setpoint",
                                default=self.default_setpoint)
        try:
            sp = float(sp)
        except (TypeError, ValueError):
            sp = self.default_setpoint
        return sp + self.offset

    def evaluate(self, sample, query):
        threshold = self._threshold(sample, query)
        st = self._state.setdefault(sample.entity_id, {"start": None, "fired": False})
        breached = (sample.value > threshold if self.direction == "above"
                    else sample.value < threshold)
        if not breached:
            st["start"] = None; st["fired"] = False
            return []
        if st["start"] is None:
            st["start"] = sample.timestamp
            return []
        sustained = sample.timestamp - st["start"]
        if sustained >= self.duration and not st["fired"]:
            st["fired"] = True
            mins = sustained.total_seconds() / 60.0
            return [self._finding(
                sample,
                f"{self.emits} — {sample.value:.2f} {self.direction} "
                f"{threshold:.2f} for {mins:.0f} min.",
                evidence={"value": sample.value, "threshold": threshold,
                          "sustained_minutes": round(mins, 1),
                          "signal": sample.signal})]
        return []


class RateOfChangeRule(_BaseRule):
    """Fires when |Δvalue/Δt| exceeds max_rate (per `per_minutes`)."""

    def __init__(self, rule):
        super().__init__(rule)
        self.max_rate = float(rule.get("max_rate", 1.0))
        self.per_minutes = float(rule.get("per_minutes", 1.0))
        self.severity = rule.get("severity", "warning")

    def evaluate(self, sample, query):
        st = self._state.setdefault(sample.entity_id, {"prev": None, "prev_t": None})
        prev, prev_t = st["prev"], st["prev_t"]
        st["prev"], st["prev_t"] = sample.value, sample.timestamp
        if prev is None:
            return []
        dt_min = (sample.timestamp - prev_t).total_seconds() / 60.0
        if dt_min <= 0:
            return []
        rate = abs(sample.value - prev) / dt_min * self.per_minutes
        if rate > self.max_rate:
            return [self._finding(
                sample,
                f"{self.emits} — rate {rate:.2f}/{self.per_minutes:g}min "
                f"exceeds {self.max_rate:.2f}.",
                evidence={"value": sample.value, "rate": round(rate, 3),
                          "max_rate": self.max_rate, "signal": sample.signal})]
        return []


class ZScoreRule(_BaseRule):
    """Learn μ/σ over a warmup window, then flag deviations (both sides, or one)."""

    def __init__(self, rule):
        super().__init__(rule)
        self.warmup = int(rule.get("warmup", 12))
        self.z_threshold = float(rule.get("z_threshold", 3.0))
        self.side = rule.get("side", "both")          # both | high | low
        self.severity = rule.get("severity", "warning")
        self._samples: dict[str, list] = {}
        self._baseline: dict[str, tuple] = {}

    def _fit(self, vals):
        import math
        n = len(vals); mean = sum(vals) / n
        var = sum((v - mean) ** 2 for v in vals) / n
        return mean, math.sqrt(var)

    def evaluate(self, sample, query):
        ent = sample.entity_id
        if ent not in self._baseline:
            buf = self._samples.setdefault(ent, [])
            buf.append(sample.value)
            if len(buf) >= self.warmup:
                self._baseline[ent] = self._fit(buf)
            return []
        mean, std = self._baseline[ent]
        if std < 1e-9:
            return []
        z = (sample.value - mean) / std
        st = self._state.setdefault(ent, {"fired": False})
        breach = (abs(z) > self.z_threshold if self.side == "both"
                  else z > self.z_threshold if self.side == "high"
                  else z < -self.z_threshold)
        if not breach:
            st["fired"] = False
            return []
        if st["fired"]:
            return []
        st["fired"] = True
        conf = min(1.0, abs(z) / (self.z_threshold * 2))
        return [self._finding(
            sample,
            f"{self.emits} — {sample.value:.2f} is {z:+.1f}σ from baseline "
            f"(μ={mean:.2f}, σ={std:.3f}).",
            confidence=round(conf, 2),
            evidence={"value": sample.value, "z_score": round(z, 2),
                      "baseline_mean": round(mean, 2), "baseline_std": round(std, 3),
                      "signal": sample.signal})]


class PhysicsResidualRule(_BaseRule):
    """Compare observed value to an expected one (a static param, or a companion
    signal the dynamics layer publishes on the same entity) and flag large residual.
    Generalises the Tier-A physics residual detector."""

    def __init__(self, rule):
        super().__init__(rule)
        self.expected = rule.get("expected")               # static expected value
        self.expected_signal = rule.get("expected_signal")  # graph property holding expected
        self.residual_threshold = float(rule.get("residual_threshold", 4.0))
        self.tier = _tier({"tier": rule.get("tier", "A")})
        self.severity = rule.get("severity", "warning")

    def _expected(self, sample, query):
        if self.expected_signal:
            v = query.get_property(sample.tenant_id, sample.entity_id,
                                   self.expected_signal)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        return float(self.expected) if self.expected is not None else None

    def evaluate(self, sample, query):
        exp = self._expected(sample, query)
        if exp is None:
            return []
        residual = sample.value - exp
        st = self._state.setdefault(sample.entity_id, {"fired": False})
        if abs(residual) <= self.residual_threshold:
            if abs(residual) <= self.residual_threshold * 0.5:
                st["fired"] = False
            return []
        if st["fired"]:
            return []
        st["fired"] = True
        direction = "above" if residual > 0 else "below"
        return [self._finding(
            sample,
            f"{self.emits} — observed {sample.value:.2f} is {abs(residual):.2f} "
            f"{direction} expected {exp:.2f}.",
            confidence=min(0.95, 0.6 + abs(residual) / 10.0),
            evidence={"value": sample.value, "expected": exp,
                      "residual": round(residual, 2), "signal": sample.signal})]


class StateRule(_BaseRule):
    """Discrete/state alarm. Fires when the signal equals (eq) / differs from (ne)
    an alarm value, or is missing (heartbeat). Optional count-in-window so repeated
    events (e.g. access denials) only fire after N within a window."""

    def __init__(self, rule):
        super().__init__(rule)
        self.alarm_value = float(rule.get("alarm_value", 1.0))
        self.compare = rule.get("compare", "eq")          # eq | ne | missing
        self.count_in_window = int(rule.get("count_in_window", 1))
        self.window = timedelta(minutes=float(rule.get("window_minutes", 5.0)))
        self.severity = rule.get("severity", "warning")

    def _alarm(self, v):
        if self.compare == "eq":
            return abs(v - self.alarm_value) < 1e-6
        if self.compare == "ne":
            return abs(v - self.alarm_value) >= 1e-6
        if self.compare == "missing":      # heartbeat lost: value == 0
            return v < 0.5
        return False

    def evaluate(self, sample, query):
        st = self._state.setdefault(sample.entity_id, {"events": [], "fired": False})
        if not self._alarm(sample.value):
            if self.count_in_window <= 1:
                st["fired"] = False
            return []
        if self.count_in_window <= 1:
            if st["fired"]:
                return []
            st["fired"] = True
            return [self._finding(sample, f"{self.emits} — state {sample.value:g}.",
                                  evidence={"value": sample.value,
                                            "signal": sample.signal})]
        # count-in-window mode
        st["events"].append(sample.timestamp)
        st["events"] = [t for t in st["events"]
                        if sample.timestamp - t <= self.window]
        if len(st["events"]) >= self.count_in_window:
            st["events"] = []
            return [self._finding(
                sample,
                f"{self.emits} — {self.count_in_window} events within "
                f"{self.window.total_seconds()/60:.0f} min.",
                evidence={"count": self.count_in_window, "signal": sample.signal})]
        return []


_KINDS = {
    "threshold": ThresholdRule,
    "sustained_threshold": SustainedThresholdRule,
    "rate_of_change": RateOfChangeRule,
    "zscore": ZScoreRule,
    "physics_residual": PhysicsResidualRule,
    "state": StateRule,
}


def make_behavior(rule: dict) -> Behavior | None:
    """Instantiate a live Behavior from a monitoring rule dict. Returns None for an
    unknown kind or a rule with no `watches` (so callers can skip silently).

    Back-compat: an authored bundle rule with kind="threshold" but no absolute
    `bound` (it carries offset_c/duration_minutes) means setpoint+offset SUSTAINED —
    the historical DynamicThresholdRule semantics — so we route it to
    sustained_threshold."""
    if not rule or not rule.get("watches"):
        return None
    kind = str(rule.get("kind", "threshold")).lower()
    if kind == "threshold" and rule.get("bound") is None and (
            rule.get("offset") is not None or rule.get("offset_c") is not None
            or rule.get("duration_minutes") is not None):
        kind = "sustained_threshold"
    cls = _KINDS.get(kind)
    if cls is None:
        return None
    try:
        return cls(rule)
    except Exception:
        return None


def make_behaviors(rules: list[dict]) -> list[Behavior]:
    out = []
    for r in rules or []:
        b = make_behavior(r)
        if b is not None:
            out.append(b)
    return out


def behavior_models_to_rules(behavior_models: list[dict]) -> list[dict]:
    """Translate the Bundle Author's `behavior_models` artefacts into monitoring
    rule dicts. Each model is {fault, tier, artefact_type, artefact}. Tier-C ->
    sustained_threshold; Tier-B -> zscore; Tier-A -> physics_residual (best-effort)."""
    rules = []
    for m in behavior_models or []:
        art = m.get("artefact") or {}
        tier = str(m.get("tier", "C")).upper()
        watches = art.get("watches")
        if not watches:
            continue
        if tier == "C":
            rules.append({
                "behavior_id": art.get("behavior_id", f"authored.{m.get('fault','rule')}"),
                "kind": "sustained_threshold", "watches": watches, "tier": "C",
                "offset": art.get("offset_c", art.get("offset", 3.0)),
                "duration_minutes": art.get("duration_minutes", 3.0),
                "direction": art.get("direction", "above"), "severity": "critical",
                "message": art.get("description", m.get("fault", "Threshold breach")),
            })
        elif tier == "B":
            rules.append({
                "behavior_id": art.get("behavior_id", f"authored.{m.get('fault','baseline')}"),
                "kind": "zscore", "watches": watches, "tier": "B",
                "warmup": art.get("warmup", 12),
                "z_threshold": art.get("z_threshold", 3.0),
                "side": art.get("side", "both"), "severity": "warning",
                "message": art.get("description", m.get("fault", "Statistical anomaly")),
            })
        elif tier == "A":
            rules.append({
                "behavior_id": art.get("behavior_id", f"authored.{m.get('fault','physics')}"),
                "kind": "physics_residual", "watches": watches, "tier": "A",
                "expected_signal": art.get("expected_signal"),
                "expected": art.get("expected"),
                "residual_threshold": art.get("residual_threshold", 4.0),
                "severity": "warning",
                "message": art.get("description", m.get("fault", "Physics residual")),
            })
    return rules
