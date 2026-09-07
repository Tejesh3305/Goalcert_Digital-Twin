"""xp.py — what a fix is worth, and what that makes you.

The award is a pure function of things the system already knows, and that is the
design constraint rather than an implementation detail. If XP depended on
anything discretionary, two operators who did identical work would be able to
end up with different numbers, and the moment that happens the number stops
being a competency signal and becomes an office politics artifact.

    award = base(severity) x quality(score) + bonuses

Every term is derived from the run:

    base       how bad the fault was — a critical fault teaches more than an
               info-level one, and rewarding them equally would push operators
               toward cherry-picking easy work.
    quality    the scenario score, as a multiplier. A bare pass earns markedly
               less than a clean run, so "get it over with" is a poor strategy.
    bonuses    no hints, and first-attempt passes. Small on purpose: they should
               reward mastery, not punish somebody who needed help. An operator
               who used every hint still earns the base award.

A FAILED RUN STILL EARNS SOMETHING. `CONSOLATION` is deliberately non-zero: the
operator did the work and learned the procedure, and zeroing them out teaches
people to abandon a run they think they are losing rather than finish it — which
destroys exactly the signal the score is there to produce.
"""
from __future__ import annotations

# ── The award ───────────────────────────────────────────────────────────
#: Base points by the twin's Finding severity.
BASE_BY_SEVERITY = {
    "info": 10,
    "warning": 25,
    "serious": 50,
    "critical": 80,
}
DEFAULT_BASE = 25

#: A run at or above this score passes. Also the threshold the operator's UI
#: shows, so the number the page promises and the number the server enforces are
#: the same constant.
PASS_SCORE = 70.0

#: Points for a run that did not pass. See the module docstring: finishing a
#: losing run must be worth more than abandoning it.
CONSOLATION = 5

#: What a PRACTICE run is worth — a procedure run for training rather than
#: against an assigned fault.
#:
#: Awarded ONCE per procedure, on the first clean pass, and never again. That
#: rule is the whole design: practice has to be worth something or nobody does
#: it, and it must not be repeatable or the easiest two-step procedure becomes
#: an XP tap that makes every level meaningless. `store.add_xp` enforces the
#: once-ness through the ledger's unique key rather than by checking first.
PRACTICE_AWARD = 15
PRACTICE_REASON_PREFIX = "practice:"


def practice_reason(scenario_id: str) -> str:
    """The ledger key for practising one procedure. Stable, so the unique index
    refuses the second award for the same procedure by the same person."""
    return f"{PRACTICE_REASON_PREFIX}{scenario_id}"

#: Multiplicative bonuses, applied to the base award.
BONUS_NO_HINTS = 0.20        # completed without opening a single hint
BONUS_FIRST_ATTEMPT = 0.10   # passed on the first run against this task

#: The quality multiplier floor. A 70% pass is worth 0.70 of base, not 0.0 —
#: without a floor the curve would make a marginal pass nearly worthless and
#: reintroduce the abandonment incentive at a different threshold.
MIN_QUALITY = 0.5


def award_for(*, severity: str, score: float | None, passed: bool,
              hints_used: int = 0, attempt: int = 1) -> tuple[int, list[str]]:
    """Points for one completed run, and the human-readable reasons.

    Returns `(points, reasons)`. The reasons are stored on the ledger entry and
    shown to the operator, because "you earned 96 XP" with no breakdown is a
    number nobody trusts or learns from.
    """
    base = BASE_BY_SEVERITY.get((severity or "").strip().lower(), DEFAULT_BASE)

    if not passed:
        return CONSOLATION, [f"Attempted a {severity or 'warning'} fix (+{CONSOLATION})"]

    # `score is None` means there was no procedure to run — nothing is published
    # for this behaviour yet, so the operator fixed it on their own judgement and
    # the server has nothing to grade. Pay the flat base: no quality multiplier
    # to apply, and no bonuses, so this path can never beat a scored run. It must
    # not pay MORE than a scored job, or the incentive would be to avoid the
    # procedure entirely.
    if score is None:
        return base, [f"{severity or 'warning'} fault fixed (base {base})",
                      "No procedure published — unscored, flat award"]

    quality = max(MIN_QUALITY, min(1.0, (score or 0.0) / 100.0))
    points = base * quality
    reasons = [f"{severity or 'warning'} fault fixed (base {base})",
               f"Run scored {round(score or 0)}% (x{quality:.2f})"]

    if hints_used == 0:
        points += base * BONUS_NO_HINTS
        reasons.append(f"No hints used (+{int(base * BONUS_NO_HINTS)})")
    if attempt <= 1:
        points += base * BONUS_FIRST_ATTEMPT
        reasons.append(f"Passed first time (+{int(base * BONUS_FIRST_ATTEMPT)})")

    return int(round(points)), reasons


# ── Levels ──────────────────────────────────────────────────────────────
#
# Each level costs 100 XP more than the one before it, so the cumulative
# threshold for level L is 100 x L x (L+1) / 2:
#
#     L1 100    L2 300    L3 600    L4 1000    L5 1500    L6 2100 ...
#
# Chosen because it is explainable in one sentence. A curve an operator cannot
# predict ("why am I still level 3?") is a curve they stop paying attention to,
# and the progression is meant to be motivating rather than mysterious.
LEVEL_STEP = 100

LEVEL_TITLES = (
    "Trainee",             # 0
    "Operator",            # 1
    "Technician",          # 2
    "Senior Technician",   # 3
    "Specialist",          # 4
    "Lead Technician",     # 5
    "Master Technician",   # 6
)


def threshold_for(level: int) -> int:
    """Cumulative XP required to REACH `level`. Level 0 starts at zero."""
    if level <= 0:
        return 0
    return LEVEL_STEP * level * (level + 1) // 2


def level_for(total_xp: int) -> int:
    """The level a total buys. Uncapped — the titles run out before the levels
    do, and `title_for` handles that rather than clamping progression."""
    level = 0
    while threshold_for(level + 1) <= max(0, total_xp):
        level += 1
    return level


def title_for(level: int) -> str:
    """The rank name. Levels past the last title keep it and gain a numeral, so
    progression never hits a wall that reads like a bug."""
    if level < len(LEVEL_TITLES):
        return LEVEL_TITLES[level]
    return f"{LEVEL_TITLES[-1]} {level - len(LEVEL_TITLES) + 2}"


def progress(total_xp: int) -> dict:
    """The whole XP standing for one operator, in the shape the UI renders.

    `into` / `span` are the numerator and denominator of the level bar. They are
    computed here rather than in the frontend so the bar cannot disagree with
    the level badge next to it.
    """
    total = max(0, int(total_xp or 0))
    level = level_for(total)
    floor = threshold_for(level)
    ceiling = threshold_for(level + 1)
    span = max(1, ceiling - floor)
    into = total - floor
    return {
        "total_xp": total,
        "level": level,
        "title": title_for(level),
        "level_floor": floor,
        "next_level_at": ceiling,
        "into_level": into,
        "level_span": span,
        "pct": round(100.0 * into / span, 1),
        "to_next": max(0, ceiling - total),
    }
