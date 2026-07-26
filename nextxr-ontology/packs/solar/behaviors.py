"""
behaviors.py — the §4.2 maintenance triggers as live behaviour rules.

The specification's table is reproduced exactly, one class per row:

  | Detected Signature      | Condition                        | Diagnosis          | Action |
  | Symmetric Output Drop   | ΔP < -12 % across zone,          | Soiling / dust     | low-priority cleaning ticket |
  |                         | V_dc normal, I_dc uniformly down |                    | |
  | String Voltage Collapse | step drop in V_dc on ONE string, | Bypass diode /     | high-priority inspection, |
  |                         | I_dc constant                    | heavy local shading| isolate string in red |
  | Shunt Resistance Decay  | progressive R_sh decline over     | PID / delamination | schedule quarterly |
  |                         | 90 days                          |                    | diagnostics |

WHY EACH RULE CHECKS MORE THAN ITS HEADLINE CONDITION
----------------------------------------------------
Every one of these has a null hypothesis that will fire it spuriously, and a rule
that raises false tickets is worse than no rule: operators learn to close them
unread, and then the true positive is closed unread too. So each rule carries the
guards that separate the fault from its impostors:

  soiling         is not distinguishable from cloud, dawn, or an over-reading
                  pyranometer by ΔP alone. Guards: minimum irradiance, inverter
                  actually generating, voltage confirmed NORMAL (soiling suppresses
                  current, not voltage), and the drop confirmed UNIFORM across
                  strings — because if it is not uniform, it is shading.
  string collapse is not distinguishable from a cloud edge by a single sample.
                  Guards: the other strings must be healthy at the same instant,
                  and current on the affected string must be roughly INTACT — a
                  cloud takes current with it, a bypass diode does not.
  shunt decay     is not distinguishable from a cloudy fortnight unless R_sh is
                  irradiance-normalised first, which `desoto.estimate_rsh_ref`
                  does. Guards: a long baseline, a minimum sample count, and a
                  monotonic-trend test rather than a two-point comparison.

TIERS
-----
Tier A = physics residual (needs the model). Tier B = statistical/trend. Tier C =
hard limit. The §4.2 rules are A and B, which is the point of having a physics model
at all: a Tier-C threshold on output power cannot distinguish any of these three.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from behaviors.registry import Behavior, BehaviorRegistry, Finding, Tier
from packs._core.physics import HardLimit as _HardLimit, Latch as _Latch

from .physics import SIGNALS, redlines
from .signals import is_generating

# Below this irradiance nothing is diagnosable: the array is producing almost
# nothing, so a percentage residual has a tiny denominator and swings wildly.
# 150 W/m² is roughly the point at which a commercial inverter is reliably tracking.
MIN_DIAGNOSTIC_POA = 150.0


def _frame(query, sample, key: str, default=None):
    """Read a co-located signal from the same frame.

    The runtime passes a frame-view keyed by short local names (see
    twins/runtime.py `_FrameQuery`), which is how a Tier-A rule sees other signals
    measured at the SAME instant. Comparing against a value fetched separately would
    compare across time, and on a partly cloudy day two samples seconds apart are
    genuinely different conditions.
    """
    try:
        value = query.get_property(sample.tenant_id, sample.entity_id, key,
                                   default=default)
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return default


def _strings_from(query, sample) -> list[dict]:
    """Per-string detail from the frame, when the twin has combiner monitoring."""
    try:
        value = query.get_property(sample.tenant_id, sample.entity_id, "_strings",
                                   default=None)
        return value if isinstance(value, list) else []
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════════════
# §4.2 Row 1 — Symmetric Output Drop → Soiling / Dust Build-up
# ══════════════════════════════════════════════════════════════════════════


class SymmetricOutputDrop(Behavior):
    """ΔP < -12 % across the zone, V_dc normal, I_dc uniformly suppressed.

    Action per §4.2: a LOW-PRIORITY cleaning ticket. Severity is therefore
    "warning", never "critical" — a dusty array is a revenue-optimisation problem,
    and dispatching it as an emergency is how a maintenance queue loses its meaning.

    SUSTAINED over `duration_minutes` because a single cloud produces exactly this
    signature for thirty seconds. Soiling does not clear.
    """
    behavior_id = "solar.soiling_symmetric_drop"
    tier = Tier.A
    watches = [SIGNALS["delta_p_pct"]]
    reads = ["dc_voltage", "imbalance", "poa", "operating_state (co-located)"]
    emits = "Symmetric output drop — soiling / dust build-up"

    def __init__(self, threshold_pct: float = -12.0,
                 duration_minutes: float = 45.0,
                 max_imbalance_pct: float = 4.0,
                 voltage_tolerance_pct: float = 6.0):
        self._threshold = threshold_pct
        self._duration = timedelta(minutes=duration_minutes)
        self._max_imbalance = max_imbalance_pct
        self._voltage_tolerance = voltage_tolerance_pct
        self._since: dict[str, datetime] = {}
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        poa = _frame(query, sample, "poairradiance") or _frame(query, sample, "poa")
        state = _frame(query, sample, "operatingstate")
        imbalance = _frame(query, sample, "currentimbalance", 0.0)
        v_dc = _frame(query, sample, "dcvoltage")
        v_expected = _frame(query, sample, "expectedvoc")

        # Guard 1: enough light for the residual to mean anything.
        if poa is None or poa < MIN_DIAGNOSTIC_POA:
            self._since.pop(sample.entity_id, None)
            return []
        # Guard 2: the inverter must actually be generating. Zero output while
        # asleep or curtailed is expected, not a fault.
        if state is not None and not is_generating(state):
            self._since.pop(sample.entity_id, None)
            return []

        deficit = sample.value <= self._threshold

        # Guard 3: UNIFORM. This is the discriminator between §4.2 rows 1 and 2.
        # A large spread across strings means one string is the problem, which is
        # the shading signature and a different (higher-priority) ticket.
        uniform = (imbalance is None) or (imbalance <= self._max_imbalance)

        # Guard 4: voltage NORMAL. Soiling attenuates light, so it takes CURRENT.
        # If the voltage has fallen too, the mechanism is not soiling and this rule
        # must stay quiet rather than mislabel it.
        voltage_ok = True
        if v_dc is not None and v_expected:
            voltage_ok = abs(v_dc - v_expected) / v_expected * 100.0 \
                <= self._voltage_tolerance

        condition = deficit and uniform and voltage_ok
        if not condition:
            self._since.pop(sample.entity_id, None)
            self._latch.rising(sample.entity_id, False)
            return []

        started = self._since.setdefault(sample.entity_id, sample.timestamp)
        sustained = sample.timestamp - started
        if sustained < self._duration:
            return []
        if not self._latch.rising(sample.entity_id, True):
            return []

        minutes = sustained.total_seconds() / 60.0
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
            severity="warning",
            message=(f"Symmetric output drop: ΔP {sample.value:.1f}% sustained for "
                     f"{minutes:.0f} min at {poa:.0f} W/m² with voltage normal and "
                     f"current suppressed uniformly across strings "
                     f"(imbalance {imbalance:.1f}%). Signature matches soiling / "
                     f"dust build-up — raise a cleaning ticket."),
            confidence=0.86,
            evidence={"delta_p_pct": sample.value, "threshold_pct": self._threshold,
                      "sustained_minutes": round(minutes, 1),
                      "poa_irradiance": poa, "current_imbalance_pct": imbalance,
                      "dc_voltage": v_dc, "voltage_normal": voltage_ok,
                      "diagnosis": "soiling",
                      "recommended_action": "cleaning",
                      "priority": "low",
                      "spec_reference": "§4.2 Symmetric Output Drop"},
        )]


# ══════════════════════════════════════════════════════════════════════════
# §4.2 Row 2 — String Voltage Collapse → Bypass Diode / Heavy Local Shading
# ══════════════════════════════════════════════════════════════════════════


class StringVoltageCollapse(Behavior):
    """Step-function drop in V_dc on a SINGLE string, I_dc constant.

    Action per §4.2: HIGH-PRIORITY structural inspection, and the string is isolated
    in red in the 3-D scene. Severity "critical" and the evidence carries the
    string's asset id so §5.1 can highlight exactly that node — which is why the
    string ids follow §3 Step 1's hierarchical tagging.

    Detected as a STEP against the string's own recent baseline, not against its
    neighbours' absolute voltage. Strings legitimately differ (different module
    counts, different orientations), so an absolute comparison would flag the
    shortest string on a healthy site forever.
    """
    behavior_id = "solar.string_voltage_collapse"
    tier = Tier.A
    watches = [SIGNALS["dc_voltage"]]
    reads = ["_strings (per-string voltage + current)", "poa"]
    emits = "String voltage collapse — bypass diode activation or heavy shading"

    def __init__(self, voltage_drop_pct: float = 12.0,
                 current_retention_pct: float = 80.0,
                 warmup: int = 10, window: int = 40):
        self._drop = voltage_drop_pct
        self._current_retention = current_retention_pct
        self._warmup = warmup
        # Baseline per STRING, not per inverter: each string has its own normal.
        self._history: dict[str, deque] = defaultdict(lambda: deque(maxlen=window))
        self._current_history: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=window))
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        poa = _frame(query, sample, "poairradiance") or _frame(query, sample, "poa")
        if poa is None or poa < MIN_DIAGNOSTIC_POA:
            return []

        strings = _strings_from(query, sample)
        if len(strings) < 2:
            # Needs per-string data. §2.1 specifies combiner monitoring precisely
            # because this diagnosis is impossible without it — and staying silent
            # is right, rather than guessing from the aggregate.
            return []

        findings = []
        for entry in strings:
            string_id = entry.get("string_id") or ""
            voltage = entry.get("voltage")
            current = entry.get("current")
            if not string_id or voltage is None or current is None:
                continue
            if entry.get("open"):
                continue          # an open string is a different rule (hard limit)

            v_hist = self._history[string_id]
            i_hist = self._current_history[string_id]

            if len(v_hist) >= self._warmup:
                v_baseline = sum(v_hist) / len(v_hist)
                i_baseline = sum(i_hist) / len(i_hist) if i_hist else current
                if v_baseline > 1.0:
                    v_drop_pct = 100.0 * (v_baseline - voltage) / v_baseline
                    i_retention = (100.0 * current / i_baseline
                                   if i_baseline > 0.1 else 100.0)

                    # The signature: voltage stepped DOWN while current stayed UP.
                    # A cloud edge takes both, which is what this separates.
                    collapsed = (v_drop_pct >= self._drop
                                 and i_retention >= self._current_retention)

                    # And the OTHER strings must be fine — otherwise it is an
                    # array-wide event (irradiance change), not one string's fault.
                    others = [s for s in strings
                              if s.get("string_id") != string_id
                              and s.get("voltage") is not None]
                    peers_healthy = True
                    if others:
                        peer_mean = sum(s["voltage"] for s in others) / len(others)
                        peers_healthy = (peer_mean > 1.0
                                         and voltage < peer_mean * 0.92)

                    if collapsed and peers_healthy and self._latch.rising(
                            string_id, True):
                        findings.append(Finding(
                            behavior_id=self.behavior_id, tier=self.tier,
                            # Flags the STRING, not the inverter, so §5.1 can
                            # isolate the right node in red.
                            flags=string_id,
                            severity="critical",
                            message=(f"String voltage collapse on {string_id}: "
                                     f"{voltage:.1f} V is {v_drop_pct:.0f}% below its "
                                     f"{v_baseline:.1f} V baseline while current held "
                                     f"at {i_retention:.0f}% of normal and peer "
                                     f"strings are unaffected. Signature matches "
                                     f"bypass-diode activation or heavy local "
                                     f"shading — dispatch a structural inspection."),
                            confidence=0.9,
                            evidence={"string_id": string_id,
                                      "voltage": voltage,
                                      "voltage_baseline": round(v_baseline, 2),
                                      "voltage_drop_pct": round(v_drop_pct, 1),
                                      "current": current,
                                      "current_retention_pct": round(i_retention, 1),
                                      "peer_strings_healthy": peers_healthy,
                                      "poa_irradiance": poa,
                                      "diagnosis": "bypass_diode_or_shading",
                                      "recommended_action": "structural_inspection",
                                      "priority": "high",
                                      "isolate_in_3d": True,
                                      "spec_reference":
                                          "§4.2 String Voltage Collapse"},
                        ))
                    elif not collapsed:
                        self._latch.rising(string_id, False)

            v_hist.append(voltage)
            i_hist.append(current)
        return findings


# ══════════════════════════════════════════════════════════════════════════
# §4.2 Row 3 — Shunt Resistance Decay → PID / Delamination
# ══════════════════════════════════════════════════════════════════════════


class ShuntResistanceDecay(Behavior):
    """Gradual progressive decline in R_sh over 90 days → PID or delamination.

    Action per §4.2: schedule preventative engineering diagnostics on the next
    quarterly cycle. Severity "warning", and the finding carries a projected
    crossing date so the work can actually be scheduled rather than merely flagged.

    THE HARD PART IS NOT THE TREND, IT IS THE NORMALISATION.
    R_sh is inversely proportional to irradiance in the De Soto model itself, so raw
    R_sh rises every time a cloud passes. A trend on the raw value would find a
    "decline" every spring and a "recovery" every autumn. `desoto.estimate_rsh_ref`
    divides that dependence out, and this rule consumes the NORMALISED value — which
    is the only reason a 90-day trend on it means anything.

    Sampling is DAILY, not per-tick. A 1 Hz feed over 90 days is 7.8 M points, and
    fitting a regression over that in a behaviour evaluated every second would be
    absurd. One representative sample per day near solar noon (enforced by the
    irradiance guard) is both sufficient for a secular trend and honest about what
    resolution the measurement actually supports.
    """
    behavior_id = "solar.shunt_resistance_decay"
    tier = Tier.B
    watches = [SIGNALS["rsh"]]
    reads = ["r_shunt_ref history (irradiance-normalised, daily)"]
    emits = "Shunt resistance decay — potential-induced degradation or delamination"

    def __init__(self, window_days: int = 90, min_samples: int = 21,
                 decline_pct: float = 15.0, min_r2: float = 0.5):
        self._window = timedelta(days=window_days)
        self._min_samples = min_samples
        self._decline_pct = decline_pct
        self._min_r2 = min_r2
        # (timestamp, value) per entity. Bounded by the window, not by count.
        self._series: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
        self._last_sample: dict[str, datetime] = {}
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        poa = _frame(query, sample, "poairradiance") or _frame(query, sample, "poa")
        # High irradiance only. `estimate_rsh_ref` already abstains below 200 W/m²
        # because the inference is numerically meaningless there; requiring 600
        # additionally ensures the daily sample is taken near peak sun, so
        # consecutive days are comparable.
        if poa is None or poa < 600.0:
            return []
        if sample.value is None or sample.value <= 0:
            return []

        entity = sample.entity_id
        last = self._last_sample.get(entity)
        # One sample per day. See the docstring: a per-tick regression over 90 days
        # is neither affordable nor more informative.
        if last is not None and (sample.timestamp - last) < timedelta(hours=20):
            return []
        self._last_sample[entity] = sample.timestamp

        series = self._series[entity]
        series.append((sample.timestamp, float(sample.value)))
        cutoff = sample.timestamp - self._window
        series[:] = [(t, v) for t, v in series if t >= cutoff]

        if len(series) < self._min_samples:
            return []

        slope, r2 = _linear_trend(series)
        first_value = series[0][1]
        if first_value <= 0:
            return []

        span_days = (series[-1][0] - series[0][0]).total_seconds() / 86400.0
        if span_days < 1.0:
            return []
        decline_pct = -100.0 * slope * span_days / first_value

        # BOTH conditions matter. A decline with a poor fit is noise, and firing on
        # it would raise a PID ticket for a fortnight of bad weather. Requiring the
        # trend to be genuinely linear is what makes "progressive" mean progressive.
        declining = decline_pct >= self._decline_pct and r2 >= self._min_r2
        if not self._latch.rising(entity, declining):
            return []

        current = series[-1][1]
        # Project to the point where the shunt path costs ~25 % of output. Gives the
        # ticket a DATE, which is the difference between schedulable work and a
        # standing warning nobody actions.
        months_to_critical = None
        if slope < 0:
            critical = first_value * 0.5
            if current > critical:
                days = (current - critical) / abs(slope)
                months_to_critical = round(days / 30.44, 1)

        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=entity,
            severity="warning",
            message=(f"Shunt resistance decay: irradiance-normalised R_sh fell "
                     f"{decline_pct:.1f}% over {span_days:.0f} days "
                     f"({first_value:.0f} → {current:.0f} Ω, R²={r2:.2f}). "
                     f"Progressive decline of this shape indicates "
                     f"potential-induced degradation or delamination"
                     + (f"; projected to reach half its original value in "
                        f"~{months_to_critical} months" if months_to_critical
                        else "")
                     + ". Schedule preventative engineering diagnostics in the "
                       "next quarterly cycle."),
            confidence=round(min(0.92, 0.55 + 0.4 * r2), 2),
            evidence={"r_sh_ref_now": round(current, 1),
                      "r_sh_ref_start": round(first_value, 1),
                      "decline_pct": round(decline_pct, 1),
                      "trend_slope_ohm_per_day": round(slope, 4),
                      "r_squared": round(r2, 3),
                      "span_days": round(span_days, 1),
                      "samples": len(series),
                      "months_to_half": months_to_critical,
                      "diagnosis": "pid_or_delamination",
                      "recommended_action": "quarterly_engineering_diagnostics",
                      "priority": "scheduled",
                      "spec_reference": "§4.2 Shunt Resistance Decay"},
        )]


def _linear_trend(series: list[tuple[datetime, float]]) -> tuple[float, float]:
    """Least-squares slope (units/day) and R² over a (timestamp, value) series.

    R² is returned alongside the slope because the slope alone cannot distinguish a
    real trend from noise. §4.2 says "gradual progressive decline"; the slope
    measures "decline" and R² measures "progressive", and this rule needs both.
    """
    if len(series) < 3:
        return 0.0, 0.0
    t0 = series[0][0]
    xs = [(t - t0).total_seconds() / 86400.0 for t, _ in series]
    ys = [v for _, v in series]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx <= 0:
        return 0.0, 0.0
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return slope, max(0.0, min(1.0, r2))


# ══════════════════════════════════════════════════════════════════════════
# Supporting rules
# ══════════════════════════════════════════════════════════════════════════


class PerformanceRatioLow(Behavior):
    """IEC 61724 performance ratio below its floor — the industry headline KPI.

    Deliberately COARSE and slow (a 3-hour sustain). It catches whole-plant
    underperformance the three specific signatures miss, and its job is to be the
    net that never lets a bad plant look fine, not to diagnose anything.
    """
    behavior_id = "solar.performance_ratio_low"
    tier = Tier.B
    watches = [SIGNALS["pr"]]
    reads = ["poa", "operating_state"]
    emits = "Plant performance ratio below expectation"

    def __init__(self, floor: float = 0.70, duration_hours: float = 3.0):
        self._floor = floor
        self._duration = timedelta(hours=duration_hours)
        self._since: dict[str, datetime] = {}
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        poa = _frame(query, sample, "poairradiance") or _frame(query, sample, "poa")
        state = _frame(query, sample, "operatingstate")
        if poa is None or poa < MIN_DIAGNOSTIC_POA:
            self._since.pop(sample.entity_id, None)
            return []
        if state is not None and not is_generating(state):
            self._since.pop(sample.entity_id, None)
            return []
        if sample.value <= 0:
            return []

        if sample.value >= self._floor:
            self._since.pop(sample.entity_id, None)
            self._latch.rising(sample.entity_id, False)
            return []

        started = self._since.setdefault(sample.entity_id, sample.timestamp)
        sustained = sample.timestamp - started
        if sustained < self._duration or not self._latch.rising(
                sample.entity_id, True):
            return []
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
            severity="warning",
            message=(f"Performance ratio {sample.value:.3f} below the {self._floor:.2f} "
                     f"floor for {sustained.total_seconds() / 3600.0:.1f} h at "
                     f"{poa:.0f} W/m². Plant is underperforming its nameplate."),
            confidence=0.8,
            evidence={"performance_ratio": sample.value, "floor": self._floor,
                      "poa_irradiance": poa,
                      "sustained_hours": round(sustained.total_seconds() / 3600.0, 2),
                      "diagnosis": "plant_underperformance",
                      "priority": "medium"},
        )]


class StringCurrentImbalance(Behavior):
    """Spread across strings in one combiner — the early warning for row 2.

    Fires before a string has collapsed far enough to trip `StringVoltageCollapse`,
    which is the point: catching a developing mismatch is worth more than confirming
    a finished one. Names the worst string so the inspection has somewhere to go.
    """
    behavior_id = "solar.string_current_imbalance"
    tier = Tier.A
    watches = [SIGNALS["imbalance"]]
    reads = ["_strings", "poa"]
    emits = "String current imbalance — one string is underperforming its peers"

    def __init__(self, threshold_pct: float = 8.0, duration_minutes: float = 20.0):
        self._threshold = threshold_pct
        self._duration = timedelta(minutes=duration_minutes)
        self._since: dict[str, datetime] = {}
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        poa = _frame(query, sample, "poairradiance") or _frame(query, sample, "poa")
        if poa is None or poa < MIN_DIAGNOSTIC_POA:
            self._since.pop(sample.entity_id, None)
            return []
        if sample.value < self._threshold:
            self._since.pop(sample.entity_id, None)
            self._latch.rising(sample.entity_id, False)
            return []

        started = self._since.setdefault(sample.entity_id, sample.timestamp)
        if (sample.timestamp - started) < self._duration:
            return []
        if not self._latch.rising(sample.entity_id, True):
            return []

        strings = _strings_from(query, sample)
        worst = None
        if strings:
            candidates = [s for s in strings if s.get("current") is not None]
            if candidates:
                worst = min(candidates, key=lambda s: s["current"])

        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier,
            flags=(worst or {}).get("string_id") or sample.entity_id,
            severity="warning",
            message=(f"String current imbalance {sample.value:.1f}% exceeds the "
                     f"{self._threshold:.0f}% limit at {poa:.0f} W/m²"
                     + (f"; weakest string is {worst['string_id']} at "
                        f"{worst['current']:.2f} A" if worst else "")
                     + ". One string is underperforming its peers — inspect before "
                       "it becomes a voltage collapse."),
            confidence=0.82,
            evidence={"imbalance_pct": sample.value, "threshold_pct": self._threshold,
                      "weakest_string": (worst or {}).get("string_id"),
                      "weakest_current": (worst or {}).get("current"),
                      "poa_irradiance": poa,
                      "diagnosis": "string_mismatch",
                      "priority": "medium"},
        )]


class SensorPlausibility(Behavior):
    """Guard the guards: is the pyranometer itself trustworthy?

    EVERY §4.2 rule is a residual against a model driven by measured irradiance, so
    a drifting pyranometer does not produce a sensor alarm — it produces a stream of
    false SOILING tickets, because an inflated baseline looks exactly like reduced
    output. That failure mode is invisible from the residual alone.

    TWO ARMS, BECAUSE ONE IS NOT ENOUGH
    -----------------------------------
    1. ABSOLUTE BAND on the POA/GHI ratio. Catches gross failures — a dead sensor
       reading zero, a swapped signal pair, a stuck value.

       The band cannot be tightened much: on a tilted array POA/GHI genuinely ranges
       from below 1 at midday in summer to well above 1.5 at low winter sun angles,
       because the tilt that penalises one favours the other. So a fixed band that
       would catch a moderate drift would also fire every clear December morning.

    2. LEARNED BASELINE of the ratio at comparable irradiance. This is the arm that
       actually catches DRIFT, and it exists because arm 1 measurably does not: a
       25 % pyranometer inflation moves a typical 1.08 ratio to 1.35, which sits
       comfortably inside any defensible absolute band. Against the site's OWN
       historical ratio, though, 25 % is a glaring outlier.

    The baseline is conditioned on high irradiance so that samples are comparable —
    the ratio's legitimate variation is driven by sun angle, and near solar noon that
    variation is small. Mixing dawn and noon samples into one baseline would make its
    spread so wide that nothing would ever deviate from it.

    Best practice beyond this rule is a REDUNDANT second pyranometer; a single
    instrument cannot be fully validated against itself, and this rule is explicit
    about being a strong heuristic rather than a proof.
    """
    behavior_id = "solar.irradiance_sensor_implausible"
    tier = Tier.B
    watches = [SIGNALS["poa"]]
    reads = ["ghi (co-located)", "POA/GHI ratio baseline"]
    emits = "Irradiance sensor reading implausible — baseline is untrustworthy"

    def __init__(self, max_ratio: float = 1.60, min_ratio: float = 0.70,
                 min_ghi: float = 200.0, baseline_ghi: float = 500.0,
                 drift_pct: float = 12.0, warmup: int = 30, window: int = 240):
        self._max_ratio = max_ratio
        self._min_ratio = min_ratio
        self._min_ghi = min_ghi
        self._baseline_ghi = baseline_ghi
        self._drift_pct = drift_pct
        self._warmup = warmup
        self._ratios: dict[str, deque] = defaultdict(lambda: deque(maxlen=window))
        self._latch = _Latch()

    def evaluate(self, sample, query) -> list:
        ghi = _frame(query, sample, "ghi")
        if ghi is None or ghi < self._min_ghi:
            return []
        ratio = sample.value / ghi

        # Arm 1 — absolute band, for gross failure.
        if ratio > self._max_ratio or ratio < self._min_ratio:
            if self._latch.rising(sample.entity_id, True):
                return [self._finding(
                    sample, ratio, ghi, kind="absolute",
                    detail=(f"POA/GHI ratio {ratio:.2f} is outside the physically "
                            f"plausible {self._min_ratio:.2f}-{self._max_ratio:.2f} "
                            f"band"),
                    confidence=0.9)]
            return []

        # Arm 2 — deviation from the site's own learned ratio, at comparable sun.
        history = self._ratios[sample.entity_id]
        finding = None
        if ghi >= self._baseline_ghi and len(history) >= self._warmup:
            baseline = sum(history) / len(history)
            if baseline > 0.05:
                drift = 100.0 * (ratio - baseline) / baseline
                if abs(drift) >= self._drift_pct:
                    if self._latch.rising(sample.entity_id, True):
                        finding = self._finding(
                            sample, ratio, ghi, kind="drift",
                            detail=(f"POA/GHI ratio {ratio:.3f} has drifted "
                                    f"{drift:+.1f}% from this site's established "
                                    f"{baseline:.3f} baseline"),
                            confidence=0.84,
                            extra={"ratio_baseline": round(baseline, 4),
                                   "drift_pct": round(drift, 1),
                                   "baseline_samples": len(history)})
                else:
                    self._latch.rising(sample.entity_id, False)

        # Only comparable samples train the baseline, and only untainted ones: adding
        # a drifted reading would let the baseline chase the fault and the alarm would
        # silence itself within a few hundred samples.
        if ghi >= self._baseline_ghi and finding is None:
            history.append(ratio)
        return [finding] if finding else []

    def _finding(self, sample, ratio, ghi, *, kind, detail, confidence,
                 extra: dict | None = None) -> Finding:
        return Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
            severity="critical",
            message=(f"{detail} (POA {sample.value:.0f}, GHI {ghi:.0f} W/m²). An "
                     f"irradiance sensor has failed or drifted. Every performance "
                     f"residual is computed against this measurement, so soiling "
                     f"and degradation findings must be treated as unreliable until "
                     f"it is resolved — a high pyranometer manufactures exactly the "
                     f"deficit that looks like a dirty array."),
            confidence=confidence,
            evidence={"poa": sample.value, "ghi": ghi, "ratio": round(ratio, 3),
                      "detection": kind,
                      "diagnosis": "irradiance_sensor_fault",
                      "recommended_action": "sensor_calibration",
                      "priority": "high",
                      "invalidates": ["solar.soiling_symmetric_drop",
                                      "solar.shunt_resistance_decay",
                                      "solar.performance_ratio_low"],
                      **(extra or {})},
        )


# ── Registry ────────────────────────────────────────────────────────────────


def build_solar_registry() -> BehaviorRegistry:
    """Fresh registry so per-run baselines, trends and latches start clean.

    Order is not significant to the engine, but it is grouped so the §4.2 rules read
    together and the Tier-C hard limits — which are safety, not diagnosis — sit
    apart.
    """
    registry = BehaviorRegistry()

    # §4.2, the three specified signatures.
    registry.register(SymmetricOutputDrop())
    registry.register(StringVoltageCollapse())
    registry.register(ShuntResistanceDecay())

    # Supporting diagnosis.
    registry.register(StringCurrentImbalance())
    registry.register(PerformanceRatioLow())
    registry.register(SensorPlausibility())

    # Tier-C hard limits: safety and equipment protection, from IEC 62109/61727.
    registry.register(_HardLimit("solar.dc_overvoltage", SIGNALS["dc_voltage"],
                                 redlines.dc_voltage_max, "above",
                                 "DC bus voltage", " V"))
    registry.register(_HardLimit("solar.heatsink_overtemp", SIGNALS["heatsink_temp"],
                                 redlines.heatsink_temp, "above",
                                 "Inverter heat-sink temperature", "°C"))
    registry.register(_HardLimit("solar.cell_overtemp", SIGNALS["cell_temp"],
                                 redlines.cell_temp, "above",
                                 "Cell temperature", "°C"))
    registry.register(_HardLimit("solar.grid_underfrequency", SIGNALS["frequency"],
                                 redlines.frequency_min, "below",
                                 "Grid frequency", " Hz"))
    registry.register(_HardLimit("solar.grid_overfrequency", SIGNALS["frequency"],
                                 redlines.frequency_max, "above",
                                 "Grid frequency", " Hz"))
    return registry
