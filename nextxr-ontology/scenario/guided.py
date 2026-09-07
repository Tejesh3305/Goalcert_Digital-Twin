"""guided.py — the "fix the issue" run: one step at a time, graded server-side.

The operator is walked through a procedure step by step. At each step they choose
an action; the server says whether it was right, explains why, and moves on. At
the end there is a score, and the score is what the work layer turns into XP.

WHY GRADING IS SERVER-SIDE
--------------------------
`Step.public()` omits the answer key, and this module is the only thing that ever
reads `Step.correct`. If the page knew the answers, the score would be a claim
made by the client about itself — and the whole point of scoring a run is to
produce evidence somebody else can rely on. So the browser gets questions, sends
answers, and is told outcomes.

THE SCORE
---------
Starts at 100 and only decreases:

    -12   each wrong answer
    -5    each hint opened

A step is not failed permanently — the operator retries until they get it right,
because the goal is that they LEARN the procedure, not that they are ranked on
one guess. What the score measures is how much help they needed to get there,
which is the useful signal and the one that improves with genuine competence.

RESUMPTION
----------
Run state lives in the database (`scenario_runs`), not in the page. An operator
whose phone locked at step 4 of a plant-room procedure comes back to step 4 with
their score intact. A client-side run would silently restart, and the score it
then produced would be meaningless.
"""
from __future__ import annotations

from datetime import UTC, datetime

from scenario import catalog
from scenario.models import Procedure

#: Score deductions. Both are per occurrence.
PENALTY_WRONG = 12
PENALTY_HINT = 5

#: At or above this, the run passes. Kept equal to `work.xp.PASS_SCORE` — the
#: threshold the operator's page promises and the one the server enforces must
#: be the same number, so it is defined once and imported here.
try:
    from work.xp import PASS_SCORE
except Exception:                                  # pragma: no cover - import guard
    PASS_SCORE = 70.0


class RunError(Exception):
    """A run-level refusal (unknown procedure, run already finished)."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def score_for(*, wrong_steps: int, hints_used: int) -> float:
    """The run's score. Pure, so a run can be re-graded from its stored counters
    and always produce the same number."""
    raw = 100.0 - (PENALTY_WRONG * max(0, wrong_steps)) - (PENALTY_HINT * max(0, hints_used))
    return float(max(0.0, min(100.0, raw)))


def procedure_for(scenario_id: str, behavior_id: str = "") -> Procedure | None:
    """The procedure to run: by id, falling back to the task's behaviour.

    The fallback matters for tasks raised before a procedure existed for their
    behaviour — the task carries an empty `scenario_id`, and this resolves one
    at run time rather than leaving the operator with a dead button.
    """
    procedure = catalog.get(scenario_id) if scenario_id else None
    if procedure is None and behavior_id:
        procedure = catalog.for_behavior(behavior_id)
    return procedure


def view(run, procedure: Procedure) -> dict:
    """The whole page payload for a run in progress.

    Everything the operator's screen needs in one object: which procedure, where
    they are, the current question (WITHOUT its answer), and the running score.
    """
    step = procedure.step_at(run.step)
    finished = step is None or run.status != "in_progress"
    return {
        "run_id": run.run_id,
        "task_id": run.task_id,
        "status": run.status,
        "procedure": procedure.public(),
        "step_index": run.step,
        "total_steps": procedure.total_steps,
        "step": step.public() if step and not finished else None,
        "score": score_for(wrong_steps=run.wrong_steps, hints_used=run.hints_used),
        "wrong_steps": run.wrong_steps,
        "hints_used": run.hints_used,
        "pass_score": PASS_SCORE,
        "transcript": run.transcript,
        "finished": finished,
    }


def answer(run, procedure: Procedure, *, step_index: int, choice: str) -> dict:
    """Grade one answer and advance if it was right.

    Returns `{correct, why, rationale, advanced, run_fields}` — `run_fields` is
    the patch the caller persists. This function does no I/O of its own: keeping
    grading pure means it can be unit-tested and re-run without a database, and
    the route stays the only place that writes.

    A stale `step_index` (a double-submitted form, a back button) is refused
    rather than applied. Without that check a resubmitted answer would be graded
    against whichever step the run had since moved to.
    """
    if run.status != "in_progress":
        raise RunError("This run is already finished")
    if step_index != run.step:
        raise RunError("That answer is for a different step; reload the run")

    step = procedure.step_at(run.step)
    if step is None:
        raise RunError("No such step")

    option = step.option(choice)
    if option is None:
        raise RunError("No such option")

    correct = choice == step.correct
    transcript = list(run.transcript or [])
    transcript.append({
        "step_id": step.id,
        "step_index": step_index,
        "title": step.title,
        "choice": choice,
        "choice_label": option.label,
        "correct": correct,
        "at": _now(),
    })

    wrong_steps = run.wrong_steps + (0 if correct else 1)
    fields: dict = {"transcript": transcript, "wrong_steps": wrong_steps}

    if correct:
        fields["step"] = run.step + 1

    # Finishing is decided here rather than by the client saying "I'm done": the
    # run ends when the last step is answered correctly, and only then.
    finished = correct and (run.step + 1) >= procedure.total_steps
    if finished:
        final = score_for(wrong_steps=wrong_steps, hints_used=run.hints_used)
        fields.update({
            "status": "completed",
            "score": final,
            "passed": final >= PASS_SCORE,
            "completed_at": _now(),
        })

    return {
        "correct": correct,
        "why": option.why,
        "rationale": step.rationale if correct else "",
        "advanced": correct,
        "finished": finished,
        "run_fields": fields,
    }


def hint(run, procedure: Procedure) -> dict:
    """Reveal the current step's hint, and charge for it.

    The charge is what keeps a hint a decision. Free hints would make the score
    a measure of patience rather than competence — everyone would open every one.
    """
    if run.status != "in_progress":
        raise RunError("This run is already finished")
    step = procedure.step_at(run.step)
    if step is None:
        raise RunError("No such step")
    hints_used = run.hints_used + 1
    return {
        "hint": step.hint or "No hint for this step.",
        "cost": PENALTY_HINT,
        "run_fields": {"hints_used": hints_used},
        "score": score_for(wrong_steps=run.wrong_steps, hints_used=hints_used),
    }


def result(run, procedure: Procedure) -> dict:
    """The debrief shown once a run finishes.

    Includes the full transcript WITH the right answers — revealing them is
    correct now and would have been cheating ten seconds ago, which is exactly
    why `Step.public(reveal=...)` takes a flag.
    """
    final = run.score if run.score is not None else score_for(
        wrong_steps=run.wrong_steps, hints_used=run.hints_used)
    return {
        "run_id": run.run_id,
        "task_id": run.task_id,
        "procedure": procedure.public(),
        "score": final,
        "passed": bool(run.passed or final >= PASS_SCORE),
        "pass_score": PASS_SCORE,
        "wrong_steps": run.wrong_steps,
        "hints_used": run.hints_used,
        "transcript": run.transcript,
        "steps": [s.public(reveal=True) for s in procedure.steps],
        "started_at": run.started_at,
        "completed_at": run.completed_at,
    }
