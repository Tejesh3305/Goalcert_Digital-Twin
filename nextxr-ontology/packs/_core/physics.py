"""
_core/physics.py — shared physics primitives for every machine-twin pack.

Before this module each pack re-implemented the same handful of helpers (rng
jitter, health-margin maps, a rising-edge latch, a Tier-C hard-limit behaviour,
the guard-crossing RUL loop). That is ~600 lines of copy-paste and, worse, it
means a fidelity fix has to be made seven times. Everything reusable now lives
here so a correction lands everywhere at once.

Nothing here is domain-specific. Packs import what they need:

    from packs._core.physics import (
        clamp, jitter, first_order_lag, margin_hi, margin_lo, worst_health,
        status_from_health, Latch, HardLimit, run_rul,
    )
"""
from __future__ import annotations

import math
from typing import Callable, Iterable, Optional

from behaviors.registry import Behavior, Finding, Tier


# ── scalar helpers ──────────────────────────────────────────────────────────
def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp x into [lo, hi]."""
    return lo if x < lo else hi if x > hi else x


def jitter(rng, value: float, frac: float) -> float:
    """Multiplicative measurement noise: value * (1 ± frac). `rng` is a
    random.Random so runs stay reproducible per entity."""
    return value * (1.0 + rng.uniform(-frac, frac))


def first_order_lag(current: float, target: float, tau: float, dt: float) -> float:
    """One step of a first-order (exponential) approach of `current` toward
    `target` with time constant `tau` seconds over `dt` seconds:

        x[k+1] = x[k] + (target - x[k]) * (1 - exp(-dt/tau))

    This is the discrete solution of  tau·dx/dt = target - x, so a thermal or
    mechanical quantity ramps toward its steady state instead of snapping to it.
    tau<=0 or dt<=0 returns the target (no lag)."""
    if tau <= 0.0 or dt <= 0.0:
        return target
    a = 1.0 - math.exp(-dt / tau)
    return current + (target - current) * a


def arrhenius_factor(temp_c: float, ref_c: float, ea_ev: float = 0.5) -> float:
    """Relative reaction-rate multiplier vs a reference temperature, Arrhenius
    form with activation energy `ea_ev` (eV). Used for battery calendar/SEI
    ageing and similar temperature-accelerated processes. Boltzmann k = 8.617e-5
    eV/K. Returns 1.0 at temp_c == ref_c and rises with temperature."""
    tk = temp_c + 273.15
    tref = ref_c + 273.15
    return math.exp((ea_ev / 8.617e-5) * (1.0 / tref - 1.0 / tk))


def doubling_factor(temp_c: float, ref_c: float, per_c: float = 6.0) -> float:
    """Rate multiplier that doubles every `per_c` °C above `ref_c` (the classic
    '×2 per 6 °C' rule of thumb for transformer/insulation ageing)."""
    return 2.0 ** ((temp_c - ref_c) / per_c)


# ── health margins ──────────────────────────────────────────────────────────
def margin_hi(value: float, nominal: float, limit: float) -> float:
    """Health margin for an 'upper-limit' signal (worse as it RISES toward the
    limit). 1.0 at/below nominal, 0.0 at/above the limit."""
    if limit == nominal:
        return 1.0
    return clamp((limit - value) / (limit - nominal))


def margin_lo(value: float, nominal: float, limit: float) -> float:
    """Health margin for a 'lower-limit' signal (worse as it FALLS toward the
    limit). 1.0 at/above nominal, 0.0 at/below the limit."""
    if nominal == limit:
        return 1.0
    return clamp((value - limit) / (nominal - limit))


def worst_health(margins: Iterable[float]) -> float:
    """Overall condition as the worst subsystem margin (the platform convention:
    the health ring collapses as any one signal approaches its redline)."""
    vals = [m for m in margins if m is not None]
    return min(vals) if vals else 1.0


def status_from_health(health: float, crit: float = 0.4, warn: float = 0.72) -> str:
    """Map a 0..1 health value to the shared status vocabulary used across the
    read surfaces: ok / warning / critical."""
    return "critical" if health < crit else "warning" if health < warn else "ok"


# ── behaviour helpers ───────────────────────────────────────────────────────
class Latch:
    """Per-entity rising-edge detector: True only on the false→true transition,
    so an edge-latched rule emits one Finding per event, not one per tick."""

    def __init__(self):
        self._on: dict[str, bool] = {}

    def rising(self, key: str, cond: bool) -> bool:
        prev = self._on.get(key, False)
        self._on[key] = cond
        return cond and not prev


class HardLimit(Behavior):
    """Tier-C hard-limit rule: fire once when a signal crosses its redline.

    Replaces the near-identical `_HardLimit` re-authored in every pack. `above`
    fires when value >= limit; otherwise when value <= limit."""

    tier = Tier.C

    def __init__(self, behavior_id, signal, limit, direction, label, unit):
        self.behavior_id = behavior_id
        self.watches = [signal]
        self.reads = [f"{label} vs redline"]
        self.emits = f"{label} out of limits"
        self._limit = limit
        self._dir = direction
        self._label = label
        self._unit = unit
        self._latch = Latch()

    def evaluate(self, sample, query) -> list:
        breach = (sample.value >= self._limit if self._dir == "above"
                  else sample.value <= self._limit)
        if not self._latch.rising(sample.entity_id, breach):
            return []
        rel = "≥" if self._dir == "above" else "≤"
        return [Finding(
            behavior_id=self.behavior_id, tier=self.tier, flags=sample.entity_id,
            severity="critical",
            message=f"{self._label} out of limits: {sample.value:.1f}{self._unit} "
                    f"{rel} {self._limit:.0f}{self._unit}",
            confidence=1.0,
            evidence={"value": sample.value, "limit": self._limit,
                      "signal": sample.signal, "unit": self._unit},
        )]


# ── forward-model RUL ───────────────────────────────────────────────────────
def run_rul(state, physics, guards, *, horizon_min: float = 120.0, points: int = 120,
            traj_signals: Optional[Iterable[str]] = None,
            degrade: Optional[Callable] = None,
            crit_fraction: float = 0.34) -> dict:
    """Generic forward-model remaining-useful-life projection.

    Integrates the twin's OWN `physics.forward` forward `points` steps over
    `horizon_min`, recording a trajectory and the first time each guarded signal
    crosses its limit — that crossing is the subsystem's RUL. Because it drives
    the same forward model the live twin runs, the predicted trajectory matches
    the twin's real behaviour (no separate, faster demo ramp).

    guards: iterable of (label, signal, limit, direction, subsystem) where
            direction is 'above' or 'below'.
    degrade(state, dt_s, i): optional per-step hook to advance any degradation the
            forward model does not itself integrate (kept identical live vs predicted
            by having forward call the same hook — see the turbine pack).
    """
    import copy as _copy
    st = _copy.deepcopy(state)
    if hasattr(st, "_rng"):
        st._rng = None  # fresh deterministic stream for the projection
    dt_min = horizon_min / max(1, points)
    dt_s = dt_min * 60.0
    sigs = list(traj_signals) if traj_signals is not None else None

    trajectory, events, rul = [], [], {}
    for i in range(points):
        t_min = round(i * dt_min, 2)
        if degrade is not None:
            degrade(st, dt_s, i)
        frame = physics.forward(st, dt=dt_s)
        row = {"t": t_min, "health": round(physics.health_index(frame), 3)}
        for s in (sigs if sigs is not None else frame.keys()):
            if s in frame:
                row[s] = frame[s]
        trajectory.append(row)
        for label, sig, lim, direction, subsystem in guards:
            v = frame.get(sig)
            if v is None:
                continue
            crossed = v >= lim if direction == "above" else v <= lim
            if crossed and subsystem not in rul:
                rul[subsystem] = t_min
                events.append({"t": t_min, "signal": sig, "component": subsystem,
                               "message": f"{label} reaches limit ({v:.1f}) at ~{t_min:.0f} min"})

    rul_list = sorted(({"component": k, "minutes": v, "hours": round(v / 60.0, 2)}
                       for k, v in rul.items()), key=lambda r: r["minutes"])
    if not rul_list:
        severity = "nominal"
    elif rul_list[0]["minutes"] <= horizon_min * crit_fraction:
        severity = "critical"
    else:
        severity = "warning"
    return {"trajectory": trajectory, "rul": rul_list, "events": events,
            "severity": severity, "horizon_min": horizon_min}
