"""scenario — the fix procedures, and the graded run an operator takes through one.

This is the "fix the issue" half of the loop. `work/` decides WHO fixes a fault;
this package decides WHAT THEY LEARN doing it and produces the score that becomes
their XP.

    models.py    what a procedure is made of — steps, options, the answer key
    catalog.py   the procedures themselves, mapped to the twin's behaviour ids
    guided.py    the run: one step at a time, graded server-side

WHY THIS IS NOT A PORT OF THE STANDALONE SCENARIO ENGINE
--------------------------------------------------------
The workforce platform's scenario engine simulates a whole environment — actors,
resources, cascading injects, Monte Carlo — to answer "what would happen if".
That is a planning tool. What an operator standing in front of a faulty chiller
needs is the much smaller thing: the ordered decisions that fix THIS fault, and a
score that says whether they knew them.

So this package borrows that engine's shape (phased steps, hints, deterministic
scoring) and none of its simulation. A run here is a pure function of the answers
given, which is what lets the same run always produce the same score — and a
score that could vary between two identical runs would not be evidence of
anything.
"""
from __future__ import annotations

from scenario.catalog import (  # noqa: F401
    all_procedures,
    for_behavior,
    get,
    scenario_id_for_behavior,
)
from scenario.models import Option, Procedure, Step  # noqa: F401

__all__ = [
    "Option", "Procedure", "Step", "all_procedures", "for_behavior", "get",
    "scenario_id_for_behavior",
]
