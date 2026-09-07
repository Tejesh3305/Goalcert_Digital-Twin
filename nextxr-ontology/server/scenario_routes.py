"""scenario_routes.py — the "fix the issue" API: /api/v1/scenario/*

    GET  /scenario/procedures            the catalog
    GET  /scenario/procedures/{id}       one procedure (steps WITHOUT answers)
    POST /scenario/runs                  start (or resume) a run against a task
    GET  /scenario/runs/{id}             the current step and score
    POST /scenario/runs/{id}/answer      grade one answer, advance if right
    POST /scenario/runs/{id}/hint        reveal the hint, charge for it
    GET  /scenario/runs/{id}/result      the debrief, answers revealed
    GET  /scenario/runs                  this operator's run history

THE ANSWER KEY NEVER LEAVES THE SERVER
--------------------------------------
`Step.public()` omits `correct` unless explicitly asked to reveal it, and the
only place that asks is `/result`, after the run is over. Grading happens in
`scenario/guided.py`. If the page held the answers, the score would be a claim
the client made about itself — and the entire reason for scoring a run is to
produce something somebody else can rely on.

A RUN BELONGS TO ONE OPERATOR
-----------------------------
Every run endpoint checks `run.user_id == caller`. Without it, one operator
could answer another's run — and since a completed run is what produces XP and
resolves a task, that is the whole authorization surface of this router.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from scenario import catalog, guided
from work import authority, store

from server.work_routes import Ctx, _ctx

router = APIRouter(prefix="/api/v1/scenario", tags=["scenario"])


def _require_run(run_id: str, ctx: Ctx):
    """Load a run the caller owns, or 404.

    A run belonging to somebody else reports 404 rather than 403, for the same
    reason `work.service._require_task` does: a 403 confirms the id exists and
    turns this into an enumeration oracle for other operators' work.
    """
    run = store.get_run(run_id)
    if run is None or run.org_id != ctx.org_id or run.user_id != ctx.user_id:
        raise HTTPException(status_code=404, detail="No such run")
    return run


def _procedure_for_run(run):
    procedure = catalog.get(run.scenario_id)
    if procedure is None:
        # The run references a procedure that no longer exists — content was
        # removed while somebody was mid-run. 410 rather than 404: the run is
        # real, its procedure is gone, and the operator needs a different
        # message from "no such run".
        raise HTTPException(status_code=410,
                            detail="The procedure for this run is no longer available")
    return procedure


# ── Catalog ─────────────────────────────────────────────────────────────
@router.get("/procedures")
async def list_procedures(domain: str | None = None, ctx: Ctx = Depends(_ctx)):
    """The training library: every procedure, with this operator's standing on it.

    `passed` and `required_by` are what turn a flat catalog into a training plan.
    An operator opening this page needs two things answered immediately — what am
    I expected to know for the work I have been given, and what have I already
    proved. A list that answered neither would just be a table of contents.
    """
    procedures = catalog.all_procedures()
    if domain:
        procedures = tuple(p for p in procedures if p.domain == domain)

    passed = store.passed_scenarios(ctx.user_id)
    # Attempts, last score and any half-finished run, per procedure. One query
    # for the whole library — the status chip on every card is drawn from this.
    progress = store.scenario_progress(ctx.user_id)

    # Procedures the operator's OPEN jobs will require. These sort first: the
    # drill you need for tonight's fault outranks the one you might need someday.
    #
    # The task ID travels with the code. The page used to receive only the code
    # and re-derive the id by fetching the inbox and matching on it, which meant
    # a second request whose sole purpose was to undo an omission here.
    from work import service as work_service
    required: dict[str, dict] = {}
    for task in work_service.inbox(org_id=ctx.org_id, user_id=ctx.user_id):
        if task.scenario_id and task.status in ("assigned", "in_progress", "blocked"):
            required[task.scenario_id] = {"code": task.code, "task_id": task.task_id}

    out = []
    for procedure in procedures:
        payload = procedure.summary_public()
        standing = progress.get(procedure.id, {})
        req = required.get(procedure.id)
        payload["passed"] = procedure.id in passed
        payload["required_by"] = req["code"] if req else None
        payload["required_task_id"] = req["task_id"] if req else ""
        payload["attempts"] = standing.get("attempts", 0)
        payload["last_score"] = standing.get("last_score")
        payload["best_score"] = standing.get("best_score")
        payload["last_at"] = standing.get("last_at") or ""
        # A run left open is the one thing on this page with a deadline attached,
        # so it is surfaced as its own field rather than inferred from a score of
        # null — which is also what an untouched procedure looks like.
        payload["in_progress_run_id"] = standing.get("in_progress_run_id", "")
        out.append(payload)
    out.sort(key=lambda p: (p["required_by"] is None, p["passed"], p["title"]))

    return {"procedures": out, "count": len(out),
            "required_count": sum(1 for p in out if p["required_by"]),
            "passed_count": sum(1 for p in out if p["passed"]),
            "in_progress_count": sum(1 for p in out if p["in_progress_run_id"]),
            "attempted_count": sum(1 for p in out if p["attempts"])}


@router.get("/procedures/{scenario_id}")
async def get_procedure(scenario_id: str, ctx: Ctx = Depends(_ctx)):
    """One procedure, with its steps — WITHOUT the answer key.

    Useful as a read-only runbook: an operator may want to review a procedure
    they are not currently running, and that must not hand them the answers to
    a run they take five minutes later.
    """
    procedure = catalog.get(scenario_id)
    if procedure is None:
        raise HTTPException(status_code=404, detail="No such procedure")
    payload = procedure.public()
    payload["steps"] = [s.public() for s in procedure.steps]
    return payload


# ── Runs ────────────────────────────────────────────────────────────────
class StartRunReq(BaseModel):
    task_id: str = Field(min_length=1)
    scenario_id: str = ""


@router.post("/runs")
async def start_run(body: StartRunReq, ctx: Ctx = Depends(_ctx)):
    """Start — or RESUME — a run against a task.

    Resumption is the default, not a feature flag. An operator whose phone
    locked at step 4 of a plant-room procedure comes back to step 4 with their
    score intact; starting fresh would reset the hint count and make the score
    meaningless. A new run is created only when they have no unfinished one.

    Starting a run also starts the TASK, so an operator who opens the fix page
    does not have to separately remember to press start.
    """
    if not authority.can(ctx.persona, "scenario.run"):
        raise HTTPException(status_code=403,
                            detail="Only a frontline operator runs a fix procedure")

    task = store.get_task(body.task_id)
    if task is None or task.org_id != ctx.org_id:
        raise HTTPException(status_code=404, detail="No such task")
    if task.assignee_id != ctx.user_id:
        raise HTTPException(status_code=403, detail="This task is assigned to somebody else")

    procedure = guided.procedure_for(body.scenario_id or task.scenario_id,
                                     task.behavior_id)
    if procedure is None:
        raise HTTPException(
            status_code=404,
            detail="No fix procedure is published for this fault yet")

    # Move the task to in_progress. Failures here are not fatal to the run: the
    # operator is plainly working on it either way, and refusing to open the
    # procedure because a status write failed would be the wrong trade.
    from work import service as work_service
    try:
        work_service.start(org_id=ctx.org_id, task_id=task.task_id,
                           actor_id=ctx.user_id, actor_name=ctx.name,
                           persona=ctx.persona)
    except work_service.WorkError:
        pass

    run = store.active_run_for_task(user_id=ctx.user_id, task_id=task.task_id)
    resumed = run is not None
    if run is None:
        run = store.create_run(org_id=ctx.org_id, user_id=ctx.user_id,
                               task_id=task.task_id, scenario_id=procedure.id,
                               total_steps=procedure.total_steps)
        store.add_event(org_id=ctx.org_id, task_id=task.task_id,
                        kind="run_started", actor_id=ctx.user_id,
                        actor_name=ctx.name,
                        summary=f"Started the {procedure.title} procedure",
                        payload={"run_id": run.run_id, "scenario_id": procedure.id})

    payload = guided.view(run, procedure)
    payload["resumed"] = resumed
    return payload


class PracticeReq(BaseModel):
    scenario_id: str = Field(min_length=1)


@router.post("/practice")
async def start_practice(body: PracticeReq, ctx: Ctx = Depends(_ctx)):
    """Run a procedure for TRAINING — no assigned fault, no task.

    Separate from `/runs` rather than a flag on it, because the two have
    genuinely different authorization: `/runs` demands a task assigned to you,
    and practice by definition has no task to check. Folding them together would
    mean the task check had a bypass, which is exactly the shape of bug that
    stops being noticed.

    A practice run carries `task_id = ""`, and completing one awards XP once per
    procedure (see `work/xp.practice_reason`) — enough to make drilling worth
    doing, not enough to farm.
    """
    if not authority.can(ctx.persona, "scenario.run"):
        raise HTTPException(status_code=403,
                            detail="Only a frontline operator runs a fix procedure")

    procedure = catalog.get(body.scenario_id)
    if procedure is None:
        raise HTTPException(status_code=404, detail="No such procedure")

    run = store.active_practice_run(user_id=ctx.user_id, scenario_id=procedure.id)
    resumed = run is not None
    if run is None:
        run = store.create_run(org_id=ctx.org_id, user_id=ctx.user_id, task_id="",
                               scenario_id=procedure.id, mode="practice",
                               total_steps=procedure.total_steps)

    payload = guided.view(run, procedure)
    payload["resumed"] = resumed
    payload["practice"] = True
    return payload


@router.get("/runs/{run_id}")
async def get_run(run_id: str, ctx: Ctx = Depends(_ctx)):
    run = _require_run(run_id, ctx)
    return guided.view(run, _procedure_for_run(run))


class AnswerReq(BaseModel):
    step_index: int = Field(ge=0)
    choice: str = Field(min_length=1)


@router.post("/runs/{run_id}/answer")
async def answer(run_id: str, body: AnswerReq, ctx: Ctx = Depends(_ctx)):
    """Grade one answer.

    `step_index` is required and checked against the run's current step, so a
    double-submitted form or a back button cannot have its answer graded against
    a step the run has since moved past.
    """
    run = _require_run(run_id, ctx)
    procedure = _procedure_for_run(run)
    try:
        outcome = guided.answer(run, procedure, step_index=body.step_index,
                                choice=body.choice)
    except guided.RunError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    updated = store.update_run(run_id, **outcome["run_fields"])

    awarded = 0
    reasons: list[str] = []
    if outcome["finished"]:
        if updated.task_id:
            # Against a real fault: the task is what records the outcome, and the
            # XP is awarded when the operator completes it. Nothing to pay here.
            store.add_event(
                org_id=ctx.org_id, task_id=updated.task_id, kind="run_completed",
                actor_id=ctx.user_id, actor_name=ctx.name,
                summary=(f"Completed {procedure.title} — "
                         f"{round(updated.score or 0)}%"),
                payload={"run_id": run_id, "score": updated.score,
                         "passed": updated.passed, "hints_used": updated.hints_used})
        elif updated.passed:
            # Practice. There is no task to complete, so the award happens here —
            # once per procedure, enforced by the ledger's unique key rather than
            # by a check that could race.
            from work import xp as work_xp
            entry = store.add_xp(
                org_id=ctx.org_id, user_id=ctx.user_id,
                points=work_xp.PRACTICE_AWARD,
                reason=work_xp.practice_reason(procedure.id), task_id="",
                detail=f"First clean pass of {procedure.title}")
            awarded = work_xp.PRACTICE_AWARD if entry else 0
            reasons = ([f"First clean pass of {procedure.title} (+{awarded})"]
                       if entry else
                       ["Practised again — XP is awarded once per procedure"])

    return {
        "correct": outcome["correct"],
        "why": outcome["why"],
        "rationale": outcome["rationale"],
        "finished": outcome["finished"],
        "practice": not updated.task_id,
        "awarded": awarded,
        "reasons": reasons,
        "run": guided.view(updated, procedure),
    }


@router.post("/runs/{run_id}/hint")
async def hint(run_id: str, ctx: Ctx = Depends(_ctx)):
    """Reveal the current step's hint and charge the score for it.

    The charge is what keeps a hint a decision rather than a reflex — see the
    scoring note in `scenario/guided.py`.
    """
    run = _require_run(run_id, ctx)
    procedure = _procedure_for_run(run)
    try:
        outcome = guided.hint(run, procedure)
    except guided.RunError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    store.update_run(run_id, **outcome["run_fields"])
    return {"hint": outcome["hint"], "cost": outcome["cost"],
            "score": outcome["score"]}


@router.get("/runs/{run_id}/result")
async def result(run_id: str, ctx: Ctx = Depends(_ctx)):
    """The debrief. Answers ARE revealed here — the run is over, and reviewing
    what you got wrong is the point of the exercise."""
    run = _require_run(run_id, ctx)
    return guided.result(run, _procedure_for_run(run))


@router.get("/runs")
async def list_runs(limit: int = 50, ctx: Ctx = Depends(_ctx)):
    runs = store.list_runs(user_id=ctx.user_id, limit=min(max(1, limit), 200))
    return {"runs": [r.public() for r in runs]}
