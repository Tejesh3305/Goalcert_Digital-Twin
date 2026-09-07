"""work_routes.py — the dispatch API: /api/v1/work/*

Two audiences, one router, and the split between them is enforced by
`work/authority.py` rather than by which endpoint you happen to call:

    SUPERVISOR   GET  /work/queue        faults nobody owns yet
                 GET  /work/board        everything still owed
                 GET  /work/roster       who they may assign to, and their level
                 POST /work/tasks        raise one by hand
                 POST /work/tasks/{id}/assign
                 POST /work/tasks/{id}/unassign
                 POST /work/tasks/{id}/close

    OPERATOR     GET  /work/inbox        their own tasks
                 POST /work/tasks/{id}/start
                 POST /work/tasks/{id}/block
                 POST /work/tasks/{id}/complete
                 GET  /work/xp           their standing and ledger

    BOTH         GET  /work/me           persona + capabilities (the UI reads this)
                 GET  /work/tasks/{id}   one task, with its timeline

EVERY HANDLER IS ORG-SCOPED
---------------------------
`_ctx()` resolves the caller's org once and every service call takes it. There is
no path here that reads or writes a task without one, which is what keeps a
missing filter from becoming a cross-tenant disclosure. `server/tenancy.py`
remains the enforcement point for TENANT access; this router adds the
operational layer on top of it and widens nothing.

WHY THE CAPABILITY CHECKS LIVE IN THE SERVICE
---------------------------------------------
The routes pass `persona` down and let `work/service.py` refuse. Checking here
too would be a second implementation of the same rule, and the failure mode of
that pattern is a route that forgets one — so the router's job is transport and
translation, not authorization.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, Depends, HTTPException
from identity import Principal
from pydantic import BaseModel, Field
from work import authority, service, store
from work.models import PERSONA_SUPERVISOR

from server.auth_routes import current_principal

logger = logging.getLogger("server.work_routes")

router = APIRouter(prefix="/api/v1/work", tags=["work"])


# ── Context ─────────────────────────────────────────────────────────────
class Ctx:
    """Who is calling, in the three terms every handler needs.

    Resolved once per request rather than per service call: `persona_of` is a
    database read, and a handler that needs the persona three times should not
    pay for it three times.
    """

    __slots__ = ("org_id", "user_id", "name", "persona")

    def __init__(self, org_id: str, user_id: str, name: str, persona: str):
        self.org_id = org_id
        self.user_id = user_id
        self.name = name
        self.persona = persona


def _ctx(principal: Principal = Depends(current_principal)) -> Ctx:
    """The caller as a work-layer context, or 403.

    An org-less caller (a platform admin, or a key issued outside any org) has
    no workspace: work is org-scoped, and inventing a NULL org filter for them
    would return every tenant's tasks. Failing loudly is the only safe answer.
    """
    if not principal.org_id:
        raise HTTPException(
            status_code=403,
            detail="No organisation on this session; sign in as an org member.")
    if not principal.user_id:
        raise HTTPException(
            status_code=403,
            detail="Dispatch requires a signed-in user, not an API key.")
    persona = service.persona_of(org_id=principal.org_id, user_id=principal.user_id)
    return Ctx(org_id=principal.org_id, user_id=principal.user_id,
               name=principal.email or principal.label or "", persona=persona)


def _handle(exc: service.WorkError) -> HTTPException:
    """One place that maps a work refusal onto a status, so two routes cannot
    answer the same condition differently."""
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _task_payload(task, *, include_events: bool = False) -> dict:
    payload = task.public()
    if include_events:
        payload["events"] = [e.public() for e in store.list_events(task.task_id)]
    return payload


# ── Who am I ────────────────────────────────────────────────────────────
@router.get("/me")
async def whoami(ctx: Ctx = Depends(_ctx)):
    """Persona, capabilities and (for operators) XP standing.

    The frontend renders from `capabilities` rather than from a hardcoded list
    per persona, so the buttons it draws and the actions the API honours are
    generated from the same table and cannot drift.
    """
    return {
        "user_id": ctx.user_id,
        "org_id": ctx.org_id,
        "persona": ctx.persona,
        "capabilities": authority.capabilities_for(ctx.persona),
        "is_supervisor": ctx.persona == PERSONA_SUPERVISOR,
        "xp": service.standing(ctx.user_id),
    }


# ── Supervisor ──────────────────────────────────────────────────────────
@router.get("/queue")
async def queue(tenant: str | None = None, ctx: Ctx = Depends(_ctx)):
    """Faults the twin raised that nobody owns yet."""
    if not authority.can(ctx.persona, "queue.read"):
        raise HTTPException(status_code=403, detail="Supervisors only")
    tasks = service.queue(org_id=ctx.org_id, tenant_id=tenant)
    return {"tasks": service.decorate(tasks), "count": len(tasks)}


@router.get("/board")
async def board(tenant: str | None = None, ctx: Ctx = Depends(_ctx)):
    """Everything still owed — assigned or not — for the supervisor's board."""
    if not authority.can(ctx.persona, "queue.read"):
        raise HTTPException(status_code=403, detail="Supervisors only")
    tasks = service.board(org_id=ctx.org_id, tenant_id=tenant)
    return {"tasks": service.decorate(tasks), "count": len(tasks)}


@router.get("/roster")
async def roster(scope: str = "team", ctx: Ctx = Depends(_ctx)):
    """The operators this supervisor can see, with load and XP.

    `scope=team` (default) is who they may assign to, derived from the same
    `assignable_user_ids` the assign endpoint enforces with — so the dialog
    cannot offer somebody the API would refuse.

    `scope=all` widens the VIEW to every frontline operator in the organisation,
    each row carrying `assignable`. The assign endpoint is unchanged, so this
    lets a supervisor see who is buried without letting them dispatch outside
    their teams.
    """
    if scope not in ("team", "all"):
        raise HTTPException(status_code=400, detail="scope must be 'team' or 'all'")
    try:
        return {"operators": service.roster(org_id=ctx.org_id, actor_id=ctx.user_id,
                                            persona=ctx.persona, scope=scope),
                "scope": scope}
    except service.WorkError as exc:
        raise _handle(exc) from None


@router.get("/stats/me")
async def my_stats(days: int = 14, ctx: Ctx = Depends(_ctx)):
    """The numbers and charts on the operator's dashboard, in one payload.

    Registered ABOVE `/tasks/{task_id}` in the module for readability only —
    the paths do not overlap, so ordering is not load-bearing here.
    """
    days = max(7, min(int(days), 90))
    return service.operator_stats(org_id=ctx.org_id, user_id=ctx.user_id, days=days)


@router.get("/team-stats")
async def team_stats(days: int = 14, ctx: Ctx = Depends(_ctx)):
    """Per-operator load and throughput for the supervisor's team panel."""
    days = max(7, min(int(days), 90))
    try:
        return service.team_stats(org_id=ctx.org_id, actor_id=ctx.user_id,
                                  persona=ctx.persona, days=days)
    except service.WorkError as exc:
        raise _handle(exc) from None


class CreateTaskReq(BaseModel):
    tenant_id: str = Field(default="", max_length=255)
    title: str = Field(min_length=1, max_length=200)
    detail: str = ""
    severity: str = "warning"
    priority: str = "normal"
    asset_node_id: str = ""
    asset_name: str = ""
    scenario_id: str = ""
    assignee_id: str | None = None


@router.post("/tasks")
async def create_task(body: CreateTaskReq, ctx: Ctx = Depends(_ctx)):
    """A supervisor raising work by hand. Not every job starts as a detection."""
    try:
        task = service.create_manual(
            org_id=ctx.org_id, tenant_id=body.tenant_id, actor_id=ctx.user_id,
            actor_name=ctx.name, persona=ctx.persona, title=body.title,
            detail=body.detail, severity=body.severity, priority=body.priority,
            asset_node_id=body.asset_node_id, asset_name=body.asset_name,
            scenario_id=body.scenario_id, assignee_id=body.assignee_id)
    except service.WorkError as exc:
        raise _handle(exc) from None
    return _task_payload(task)


class AssignReq(BaseModel):
    assignee_id: str = Field(min_length=1)
    note: str = ""


@router.post("/tasks/{task_id}/assign")
async def assign(task_id: str, body: AssignReq, ctx: Ctx = Depends(_ctx)):
    """THE supervisor action: hand a fault to one of their operators."""
    try:
        task = service.assign(org_id=ctx.org_id, task_id=task_id,
                              assignee_id=body.assignee_id, actor_id=ctx.user_id,
                              actor_name=ctx.name, persona=ctx.persona,
                              note=body.note)
    except service.WorkError as exc:
        raise _handle(exc) from None
    return _task_payload(task)


class NoteReq(BaseModel):
    note: str = ""


@router.post("/tasks/{task_id}/unassign")
async def unassign(task_id: str, body: NoteReq, ctx: Ctx = Depends(_ctx)):
    try:
        task = service.unassign(org_id=ctx.org_id, task_id=task_id,
                                actor_id=ctx.user_id, actor_name=ctx.name,
                                persona=ctx.persona, note=body.note)
    except service.WorkError as exc:
        raise _handle(exc) from None
    return _task_payload(task)


@router.post("/tasks/{task_id}/close")
async def close(task_id: str, body: NoteReq, ctx: Ctx = Depends(_ctx)):
    """Accept an operator's fix. This is what resolves the twin's Finding."""
    try:
        task = service.close(org_id=ctx.org_id, task_id=task_id,
                             actor_id=ctx.user_id, actor_name=ctx.name,
                             persona=ctx.persona, note=body.note)
    except service.WorkError as exc:
        raise _handle(exc) from None
    return _task_payload(task, include_events=True)


# ── Operator ────────────────────────────────────────────────────────────
@router.get("/inbox")
async def inbox(ctx: Ctx = Depends(_ctx)):
    """This operator's work, split into what is new TODAY and what is carried over.

    Filtered by assignee in SQL. There is deliberately no `user_id` parameter:
    an operator reads their own inbox and nobody else's, and an endpoint that
    took a user id would need a check that this one cannot forget.

    Every task carries `assigned_by_name`, because "assigned by Sam Okafor" is
    the thing an operator actually needs and `usr_1bbd0e40` is not.
    """
    tasks = service.inbox(org_id=ctx.org_id, user_id=ctx.user_id)
    decorated = service.decorate(tasks)

    today = [t for t in decorated if service.is_today(t.get("assigned_at"))]
    earlier = [t for t in decorated
               if not service.is_today(t.get("assigned_at"))
               and t["status"] in ("assigned", "in_progress", "blocked")]
    awaiting = [t for t in decorated if t["status"] == "resolved"]

    return {
        "tasks": decorated,
        "count": len(decorated),
        "today": today,
        "earlier": earlier,
        "awaiting_signoff": awaiting,
        "xp": service.standing(ctx.user_id),
    }


@router.get("/history")
async def history(limit: int = 50, ctx: Ctx = Depends(_ctx)):
    tasks = service.history(org_id=ctx.org_id, user_id=ctx.user_id,
                            limit=min(max(1, limit), 200))
    return {"tasks": service.decorate(tasks)}


@router.get("/tasks/{task_id}/workorder")
async def task_workorder(task_id: str, generate: bool = False,
                         ctx: Ctx = Depends(_ctx)):
    """THE WORK ORDER FOR THIS FAULT, generated by the twin's own agents.

    This is the "what am I actually fixing, and how" half of an operator's job,
    and it is deliberately NOT stored on the task. The twin already produces it
    from live state — `copilot.generate_work_order` reads the asset's current
    diagnostics, and the AR overlay grounds its steps in the findings that are
    open RIGHT NOW. Freezing a copy at dispatch time would hand the operator a
    procedure describing the plant as it was when the supervisor clicked assign,
    which on a developing fault is the one thing you must not do.

    Degrades rather than fails. With no LLM key the agents return their
    deterministic fallback, and with the graph unreachable the AR steps are
    simply absent — an operator with a task and no generated procedure still has
    the guided simulation, and that is a far better outcome than a 500.

    `generate` GATES THE PAID CALL, and defaults to FALSE.
    ------------------------------------------------------
    The AR steps come from the graph and cost nothing, so they are always
    returned. The work order is written by an LLM and costs money per call, so
    it is produced only when somebody asks for it.

    This endpoint originally generated on every view, which made simply OPENING
    a job an API charge. The twin's own co-pilot strip had already settled that
    question the other way — "Offer a trigger, not a background monitor" — and a
    page that silently bills the account each time it is looked at produces
    exactly the kind of spend nobody can attribute afterwards.
    """
    task = store.get_task(task_id)
    if task is None or task.org_id != ctx.org_id:
        raise HTTPException(status_code=404, detail="No such task")
    if ctx.persona != PERSONA_SUPERVISOR and task.assignee_id != ctx.user_id:
        raise HTTPException(status_code=404, detail="No such task")

    payload: dict = {
        "task_id": task.task_id,
        "code": task.code,
        "title": task.title,
        "asset": task.asset_name or task.asset_node_id,
        "severity": task.severity,
        "behavior_id": task.behavior_id,
        "work_order": None,
        "ar_steps": [],
        "source": None,
    }

    # 1. The on-asset guided steps. These come from the GRAPH and cost nothing,
    #    so they are always gathered — before the paid call below.
    if task.asset_node_id and task.tenant_id:
        try:
            from graph.query import GraphQuery

            from server.hub_routes import _ar_steps
            q = GraphQuery()
            node = q.get_node(task.tenant_id, task.asset_node_id)
            if node is not None:
                findings = q.get_findings(task.tenant_id,
                                          flagged_entity_id=task.asset_node_id)
                payload["ar_steps"] = _ar_steps(node, findings)
        except Exception:
            logger.debug("AR steps unavailable", exc_info=True)

    payload["generated"] = bool(generate)
    if not generate:
        return payload

    # 2. The agent-written work order. THIS IS THE CALL THAT COSTS MONEY, which
    #    is why it is behind `generate` and off by default.
    try:
        from copilot import agents as A
        with A.agent_trace() as trace:
            # Both `message` and `displayName`: the agents' stub reads one and
            # the live path reads the other, and a finding that carries only one
            # of them used to crash the stub outright.
            diagnostics = {"findings": [{
                "message": task.title,
                "displayName": task.title,
                "severity": task.severity,
                "signal": task.behavior_id,
            }]}
            wo = A.generate_work_order(diagnostics, task.asset_name or "asset",
                                       task.behavior_id.split(".")[0] or "facility")
        payload["work_order"] = wo.model_dump()
        payload["source"] = trace.get("backend") if isinstance(trace, dict) else None
    except Exception:
        logger.debug("work order generation unavailable", exc_info=True)

    return payload


@router.post("/tasks/{task_id}/start")
async def start(task_id: str, ctx: Ctx = Depends(_ctx)):
    try:
        task = service.start(org_id=ctx.org_id, task_id=task_id,
                             actor_id=ctx.user_id, actor_name=ctx.name,
                             persona=ctx.persona)
    except service.WorkError as exc:
        raise _handle(exc) from None
    return _task_payload(task)


class BlockReq(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


@router.post("/tasks/{task_id}/block")
async def block(task_id: str, body: BlockReq, ctx: Ctx = Depends(_ctx)):
    """"I cannot do this" as a first-class outcome — see work/service.block."""
    try:
        task = service.block(org_id=ctx.org_id, task_id=task_id,
                             actor_id=ctx.user_id, actor_name=ctx.name,
                             persona=ctx.persona, reason=body.reason)
    except service.WorkError as exc:
        raise _handle(exc) from None
    return _task_payload(task)


class CompleteReq(BaseModel):
    """What the caller may say about a finished job.

    NOTE WHAT IS ABSENT: `score` and `hints_used`. They used to be here, and
    that was a hole — a hand-rolled POST could claim 100% on a procedure it
    never opened. Both are now read from the operator's own `scenario_runs` row
    (see `work.service._verified_outcome`), so the client supplies the run's ID
    and the server supplies the result.

    `passed` survives only for a task with NO published procedure, where there
    is nothing to grade and it means "I fixed it". On a task that has one it is
    ignored in favour of the run.
    """

    run_id: str = ""
    passed: bool | None = None
    resolution: str = ""


@router.post("/tasks/{task_id}/complete")
async def complete(task_id: str, body: CompleteReq, ctx: Ctx = Depends(_ctx)):
    """Finish the job. The server reads the score; the caller cannot assert it."""
    try:
        outcome = service.complete(
            org_id=ctx.org_id, task_id=task_id, actor_id=ctx.user_id,
            actor_name=ctx.name, persona=ctx.persona,
            run_id=body.run_id, passed=body.passed, resolution=body.resolution)
    except service.WorkError as exc:
        raise _handle(exc) from None
    return {
        "task": outcome["task"].public(),
        "xp": outcome["xp"],
        "awarded": outcome["awarded"],
        "reasons": outcome["reasons"],
    }


# ── Shared ──────────────────────────────────────────────────────────────
@router.get("/tasks/{task_id}")
async def get_task(task_id: str, ctx: Ctx = Depends(_ctx)):
    """One task with its timeline.

    An operator may read a task they are not assigned to only if it is
    unassigned — otherwise they could enumerate their colleagues' work. A
    supervisor reads any task in their org.
    """
    task = store.get_task(task_id)
    if task is None or task.org_id != ctx.org_id:
        raise HTTPException(status_code=404, detail="No such task")
    if ctx.persona != PERSONA_SUPERVISOR:
        if task.assignee_id and task.assignee_id != ctx.user_id:
            raise HTTPException(status_code=404, detail="No such task")
    return _task_payload(task, include_events=True)


class CommentReq(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.post("/tasks/{task_id}/comment")
async def comment(task_id: str, body: CommentReq, ctx: Ctx = Depends(_ctx)):
    try:
        service.comment(org_id=ctx.org_id, task_id=task_id, actor_id=ctx.user_id,
                        actor_name=ctx.name, persona=ctx.persona, text=body.text)
    except service.WorkError as exc:
        raise _handle(exc) from None
    return {"ok": True}


# ── XP ──────────────────────────────────────────────────────────────────
@router.get("/xp")
async def xp_standing(ctx: Ctx = Depends(_ctx)):
    """The caller's own standing and recent awards."""
    return {
        "xp": service.standing(ctx.user_id),
        "ledger": [e.public() for e in store.list_xp(ctx.user_id)],
    }


# ── Teams ───────────────────────────────────────────────────────────────
@router.get("/teams")
async def list_teams(ctx: Ctx = Depends(_ctx)):
    if not authority.can(ctx.persona, "team.read"):
        raise HTTPException(status_code=403, detail="Supervisors only")
    teams = store.list_teams(ctx.org_id)
    return {"teams": [t.public() for t in teams]}
