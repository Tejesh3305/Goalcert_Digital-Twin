"""service.py — the rules of the fault-to-fix loop.

`store.py` moves rows; this module decides whether a move is legal, records why,
and fans out the consequences. Routes call only this, so the same rules apply
whether a transition arrives from the operator's phone, a supervisor's console
or a seed script.

THE LOOP, END TO END
--------------------
    1. The twin detects a fault           behaviors -> FindingsLoop -> Finding node
    2. A rule turns it into a task        work/from_finding.py
    3. A supervisor assigns it            assign()          [supervisor only, team-scoped]
    4. The operator starts it             start()           [assignee only]
    5. The operator runs the procedure    scenario/*        the "fix the issue" page
    6. The operator completes it          complete()        -> score -> XP -> resolved
    7. The supervisor closes it           close()           -> the twin's Finding resolves

Steps 3 and 7 are the supervisor's; 4-6 are the operator's; nothing lets one do
the other's. That separation is the product requirement, and enforcing it in one
module is what stops it being re-implemented (differently) per route.

WHY EVERY TRANSITION WRITES AN EVENT
------------------------------------
A status column remembers only the present. "Who assigned this, when, and what
happened next" is the question asked after an incident, and answering it needs
the history — so each transition appends to `task_events` in the same call that
changes the status, not in a listener that might not be running.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from work import authority, store, xp
from work.models import (
    OPEN_STATUSES,
    PERSONA_FRONTLINE,
    PERSONA_SUPERVISOR,
    STATUS_ASSIGNED,
    STATUS_BLOCKED,
    STATUS_CLOSED,
    STATUS_IN_PROGRESS,
    STATUS_OPEN,
    STATUS_RESOLVED,
    Task,
    can_transition,
)

logger = logging.getLogger("work.service")


class WorkError(Exception):
    """A refusal with an HTTP status attached.

    Carrying the status here rather than raising HTTPException keeps this module
    importable from a script, a test or the findings ticker — none of which are
    inside a request — while still letting the routes translate one exception
    type into the right response.
    """

    def __init__(self, detail: str, status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class Forbidden(WorkError):
    def __init__(self, detail: str = "Not permitted"):
        super().__init__(detail, status_code=403)


class NotFound(WorkError):
    def __init__(self, detail: str = "Not found"):
        super().__init__(detail, status_code=404)


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ── Reading ─────────────────────────────────────────────────────────────
def _require_task(task_id: str, org_id: str) -> Task:
    """Load a task, or refuse.

    A task belonging to another organisation reports 404, not 403. A 403 would
    confirm the id exists, which turns this endpoint into an enumeration oracle
    for other tenants' work — the same reasoning as `Scope.describe()`.
    """
    task = store.get_task(task_id)
    if task is None or task.org_id != org_id:
        raise NotFound("No such task")
    return task


def queue(*, org_id: str, tenant_id: str | None = None) -> list[Task]:
    """The supervisor's queue: raised, nobody assigned yet."""
    return store.list_tasks(org_id=org_id, statuses=(STATUS_OPEN,),
                            tenant_id=tenant_id, unassigned_only=True)


def board(*, org_id: str, tenant_id: str | None = None) -> list[Task]:
    """Everything still owed, assigned or not — the supervisor's whole board."""
    return store.list_tasks(org_id=org_id, statuses=OPEN_STATUSES + (STATUS_RESOLVED,),
                            tenant_id=tenant_id)


def inbox(*, org_id: str, user_id: str) -> list[Task]:
    """One operator's work. Filtered by assignee in SQL, not in Python, so a
    paging bug cannot leak somebody else's row into the response."""
    return store.list_tasks(org_id=org_id, assignee_id=user_id,
                            statuses=OPEN_STATUSES + (STATUS_RESOLVED,))


def history(*, org_id: str, user_id: str, limit: int = 50) -> list[Task]:
    return store.list_tasks(org_id=org_id, assignee_id=user_id,
                            statuses=(STATUS_CLOSED, STATUS_RESOLVED), limit=limit)


# ── Presentation helpers ────────────────────────────────────────────────
def names_for(user_ids) -> dict[str, str]:
    """user_id -> display name, for a whole page in one pass.

    An operator's card says "assigned by Sam Okafor", not "assigned by
    usr_1bbd0e40". The id is what the database stores and the name is what the
    person needs, and resolving it per card would be N queries per render — so
    every list endpoint resolves the whole page at once through here.

    An id with no user resolves to a readable placeholder rather than being
    dropped: a membership can outlive the account, and "assigned by (removed
    user)" is a more honest card than one that silently claims nobody assigned
    it.
    """
    from identity import store as identity_store

    wanted = {u for u in user_ids if u}
    out: dict[str, str] = {}
    for user_id in wanted:
        user = identity_store.get_user(user_id)
        out[user_id] = (user.name or user.email) if user else "(removed user)"
    return out


def decorate(tasks: list[Task]) -> list[dict]:
    """Public task dicts with the human names filled in.

    This is the shape every operator- and supervisor-facing list returns, so a
    card never has to make a second call to find out who gave it to them.
    """
    names = names_for([t.assigned_by for t in tasks] + [t.assignee_id for t in tasks])
    out = []
    for task in tasks:
        payload = task.public()
        payload["assigned_by_name"] = names.get(task.assigned_by or "", "")
        payload["assignee_name"] = names.get(task.assignee_id or "", "")
        out.append(payload)
    return out


def is_today(iso: str | None) -> bool:
    """Was this timestamp today, in UTC?

    UTC rather than the operator's local midnight, deliberately and with a known
    cost: a shift that crosses midnight will see "today" roll over mid-shift.
    Fixing that properly needs the site's timezone and shift pattern, which the
    org model does not carry yet — so this is the honest approximation rather
    than a guess dressed up as a feature.
    """
    if not iso:
        return False
    try:
        return datetime.fromisoformat(iso).date() == datetime.now(UTC).date()
    except (TypeError, ValueError):
        return False


# ── Creating ────────────────────────────────────────────────────────────
def create_manual(*, org_id: str, tenant_id: str, actor_id: str, actor_name: str,
                  persona: str, title: str, detail: str = "",
                  severity: str = "warning", priority: str = "normal",
                  asset_node_id: str = "", asset_name: str = "",
                  scenario_id: str = "", assignee_id: str | None = None) -> Task:
    """A supervisor raising work by hand, without waiting for the twin.

    Kept deliberately: not every job starts as a detected fault, and a platform
    that can only dispatch what its sensors noticed is not usable on a real
    shift.
    """
    if not authority.can(persona, "task.create"):
        raise Forbidden("Only a supervisor can raise a task")
    if assignee_id and not authority.may_assign_to(org_id, actor_id, persona, assignee_id):
        raise Forbidden("That operator is not on a team you supervise")

    task = store.create_task(
        org_id=org_id, tenant_id=tenant_id, title=title, detail=detail,
        severity=severity, priority=priority, asset_node_id=asset_node_id,
        asset_name=asset_name, scenario_id=scenario_id, source="manual",
        status=STATUS_ASSIGNED if assignee_id else STATUS_OPEN,
        assignee_id=assignee_id, assigned_by=actor_id if assignee_id else None)
    store.add_event(org_id=org_id, task_id=task.task_id, kind="raised",
                    actor_id=actor_id, actor_name=actor_name,
                    summary=f"Raised by hand: {title}",
                    payload={"severity": severity, "priority": priority})
    if assignee_id:
        store.add_event(org_id=org_id, task_id=task.task_id, kind="assigned",
                        actor_id=actor_id, actor_name=actor_name,
                        summary="Assigned on creation",
                        payload={"assignee_id": assignee_id})
    return task


# ── The supervisor's half ───────────────────────────────────────────────
def assign(*, org_id: str, task_id: str, assignee_id: str, actor_id: str,
           actor_name: str, persona: str, note: str = "") -> Task:
    """Hand a fault to an operator. THE supervisor action.

    Three gates, and all three are load-bearing:

      capability  is this persona allowed to assign at all?
      scope       is that operator on a team THIS supervisor runs?
      transition  is the task in a state that can be assigned?

    Dropping the middle one is the subtle failure: every supervisor in the org
    could then dispatch every operator in the org, and the team structure would
    be decoration.
    """
    if not authority.can(persona, "task.assign"):
        raise Forbidden("Only a supervisor can assign work")
    task = _require_task(task_id, org_id)
    if not authority.may_assign_to(org_id, actor_id, persona, assignee_id):
        raise Forbidden("That operator is not on a team you supervise")
    if task.status not in (STATUS_OPEN, STATUS_ASSIGNED, STATUS_BLOCKED):
        raise WorkError(f"A {task.status} task cannot be assigned")

    team_ids = authority.team_ids_supervised_by(org_id, actor_id)
    updated = store.update_task(
        task_id, status=STATUS_ASSIGNED, assignee_id=assignee_id,
        assigned_by=actor_id, assigned_at=_now(),
        team_id=team_ids[0] if team_ids else None)
    store.add_event(org_id=org_id, task_id=task_id, kind="assigned",
                    actor_id=actor_id, actor_name=actor_name,
                    summary=note or "Assigned to an operator",
                    payload={"assignee_id": assignee_id})
    logger.info("task %s assigned to %s by %s", task.code, assignee_id, actor_id)
    return updated


def unassign(*, org_id: str, task_id: str, actor_id: str, actor_name: str,
             persona: str, note: str = "") -> Task:
    """Pull a task back into the queue — the operator went home, or it was the
    wrong person. Returns it to `open` rather than deleting it, so the history
    of the mis-assignment survives."""
    if not authority.can(persona, "task.unassign"):
        raise Forbidden("Only a supervisor can unassign work")
    task = _require_task(task_id, org_id)
    if task.status in (STATUS_CLOSED, STATUS_RESOLVED):
        raise WorkError(f"A {task.status} task cannot be unassigned")

    updated = store.update_task(task_id, status=STATUS_OPEN, assignee_id=None,
                                assigned_by=None, assigned_at=None, started_at=None)
    store.add_event(org_id=org_id, task_id=task_id, kind="unassigned",
                    actor_id=actor_id, actor_name=actor_name,
                    summary=note or "Returned to the queue",
                    payload={"was_assigned_to": task.assignee_id})
    return updated


def close(*, org_id: str, task_id: str, actor_id: str, actor_name: str,
          persona: str, note: str = "") -> Task:
    """The supervisor accepts a fix and closes the loop back to the twin.

    Separate from `complete()` on purpose: an operator marking their own work
    finished is a claim, and a supervisor closing it is the acceptance. Rolling
    the two together would make the operator the sole judge of their own work.

    Closing is also what resolves the Finding in the graph — see
    `work/from_finding.resolve_finding`. If that write fails the task still
    closes: the twin's own state machine is authoritative over the Finding, and
    a graph blip must not strand a finished job in `resolved` forever.
    """
    if not authority.can(persona, "task.close"):
        raise Forbidden("Only a supervisor can close a task")
    task = _require_task(task_id, org_id)
    if not can_transition(task.status, STATUS_CLOSED):
        raise WorkError(f"A {task.status} task cannot be closed")

    updated = store.update_task(task_id, status=STATUS_CLOSED,
                                completed_at=task.completed_at or _now(),
                                resolution=note or task.resolution)
    store.add_event(org_id=org_id, task_id=task_id, kind="closed",
                    actor_id=actor_id, actor_name=actor_name,
                    summary=note or "Accepted and closed", payload={})

    from work import from_finding
    if from_finding.resolve_finding(updated):
        store.add_event(org_id=org_id, task_id=task_id, kind="resolved",
                        actor_id="system", actor_name="twin",
                        summary="Finding marked resolved in the twin",
                        payload={"finding_node_id": updated.finding_node_id})
    return updated


# ── The operator's half ─────────────────────────────────────────────────
def start(*, org_id: str, task_id: str, actor_id: str, actor_name: str,
          persona: str) -> Task:
    """The operator picks the job up. Idempotent: starting an already-started
    task returns it unchanged rather than resetting `started_at`, because the
    fix-the-issue page calls this on mount and a refresh must not rewrite
    history."""
    if not authority.can(persona, "task.start"):
        raise Forbidden("Only the assigned operator can start a task")
    task = _require_task(task_id, org_id)
    if not authority.may_act_on_task(task, user_id=actor_id, persona=persona):
        raise Forbidden("This task is assigned to somebody else")
    if task.status == STATUS_IN_PROGRESS:
        return task
    if not can_transition(task.status, STATUS_IN_PROGRESS):
        raise WorkError(f"A {task.status} task cannot be started")

    updated = store.update_task(task_id, status=STATUS_IN_PROGRESS,
                                started_at=task.started_at or _now())
    store.add_event(org_id=org_id, task_id=task_id, kind="started",
                    actor_id=actor_id, actor_name=actor_name,
                    summary="Operator started the job", payload={})
    return updated


def block(*, org_id: str, task_id: str, actor_id: str, actor_name: str,
          persona: str, reason: str) -> Task:
    """"I cannot do this" as a first-class outcome.

    Without it the only honest options are to abandon the task silently or to
    fail the procedure deliberately, and both destroy the signal the score
    exists to produce.
    """
    if not authority.can(persona, "task.block"):
        raise Forbidden("Only the assigned operator can block a task")
    task = _require_task(task_id, org_id)
    if not authority.may_act_on_task(task, user_id=actor_id, persona=persona):
        raise Forbidden("This task is assigned to somebody else")
    if not can_transition(task.status, STATUS_BLOCKED):
        raise WorkError(f"A {task.status} task cannot be blocked")

    updated = store.update_task(task_id, status=STATUS_BLOCKED)
    store.add_event(org_id=org_id, task_id=task_id, kind="blocked",
                    actor_id=actor_id, actor_name=actor_name,
                    summary=reason or "Blocked", payload={"reason": reason})
    return updated


def _verified_outcome(*, task: Task, actor_id: str, run_id: str,
                      claimed_passed: bool | None) -> tuple[float | None, bool, int]:
    """The outcome of a job, taken from the server's own record wherever one exists.

    Returns `(score, passed, hints_used)`.

    Two paths, and the distinction is which of them the caller is allowed to have
    an opinion about:

      * The task has a PROCEDURE. Then a finished run is mandatory, and the
        score, the pass and the hint count all come from that row. The caller
        supplies only its id, and every check below is about proving the run is
        theirs and is finished — an id alone would otherwise let one operator
        cite somebody else's perfect run.

      * The task has NO procedure published for its behaviour. Then there is
        nothing to score and `passed` is simply the operator's report that the
        fault is fixed. `score` stays None, which `work/xp.py` treats as an
        unscored job and pays a flat base award — so this path cannot be used to
        manufacture a better result than the scored one.
    """
    if not task.scenario_id:
        return None, bool(claimed_passed), 0

    if not run_id:
        raise WorkError(
            "This job has a fix procedure; complete it through the procedure")

    run = store.get_run(run_id)
    # Same 404-not-403 reasoning as _require_task: confirming a run id exists
    # would make this an oracle for other operators' work.
    if run is None or run.org_id != task.org_id or run.user_id != actor_id \
            or run.task_id != task.task_id:
        raise NotFound("No such run for this job")
    if run.status != "completed":
        raise WorkError("That procedure run is not finished yet")

    return run.score, bool(run.passed), int(run.hints_used or 0)


def complete(*, org_id: str, task_id: str, actor_id: str, actor_name: str,
             persona: str, run_id: str = "", passed: bool | None = None,
             resolution: str = "") -> dict:
    """The operator finishes the job. XP out.

    THE SCORE IS NOT AN ARGUMENT. It is read from the operator's own completed
    `scenario_runs` row, because a score supplied by the caller is a claim the
    client makes about itself — and the entire purpose of scoring a run is to
    produce something an auditor can rely on. An earlier version of this took
    `score` and `passed` from the request body, which meant a hand-rolled POST
    could claim 100% on a procedure it never opened.

    So: a task that HAS a published procedure can only be completed by
    presenting a finished run for that task, owned by this operator. A task with
    no procedure (nothing published for its behaviour yet) is completed on the
    operator's word, with no score and no quality multiplier — `passed` is the
    only thing the caller supplies, and it cannot inflate an award.

    Returns `{task, xp}` where `xp` is the standing AFTER the award, so the page
    can move the level bar without a second round trip.

    A FAILED run still completes and still earns (see `work/xp.py`), but the
    task goes back to `in_progress` rather than `resolved` — the fault is not
    fixed, and the operator can run the procedure again. Only a pass resolves
    it.
    """
    if not authority.can(persona, "task.complete"):
        raise Forbidden("Only the assigned operator can complete a task")
    task = _require_task(task_id, org_id)
    if not authority.may_act_on_task(task, user_id=actor_id, persona=persona):
        raise Forbidden("This task is assigned to somebody else")
    if task.status not in (STATUS_IN_PROGRESS, STATUS_ASSIGNED, STATUS_BLOCKED):
        raise WorkError(f"A {task.status} task cannot be completed")

    score, passed, hints_used = _verified_outcome(
        task=task, actor_id=actor_id, run_id=run_id, claimed_passed=passed)

    # The attempt number is counted BEFORE this run is recorded as completed, so
    # the first pass sees attempt 1 and earns the first-attempt bonus.
    attempt = store.count_attempts(user_id=actor_id, task_id=task_id) + 1
    points, reasons = xp.award_for(severity=task.severity, score=score,
                                   passed=passed, hints_used=hints_used,
                                   attempt=attempt)

    # `reason` is part of the ledger's unique key, so it must be stable for a
    # given (task, outcome) — a retried completion re-derives the same string
    # and the insert is refused instead of paying twice.
    reason = f"task:{'passed' if passed else 'attempted'}"
    entry = store.add_xp(org_id=org_id, user_id=actor_id, points=points,
                         reason=reason, task_id=task_id, detail=" · ".join(reasons))
    awarded = points if entry else 0

    updated = store.update_task(
        task_id,
        status=STATUS_RESOLVED if passed else STATUS_IN_PROGRESS,
        # None stays None: a job with no published procedure was never scored,
        # and recording 0.0 would be a lie that reads as a terrible run.
        score=None if score is None else float(score),
        xp_awarded=task.xp_awarded + awarded,
        completed_at=_now() if passed else None,
        resolution=resolution or task.resolution)

    if score is None:
        summary = "Fixed — no procedure published, unscored"
    elif passed:
        summary = f"Fixed — scored {round(score)}%"
    else:
        summary = f"Attempt scored {round(score)}% — not passed"
    store.add_event(
        org_id=org_id, task_id=task_id,
        kind="resolved" if passed else "run_completed",
        actor_id=actor_id, actor_name=actor_name,
        summary=summary,
        payload={"score": score, "passed": passed, "xp": awarded,
                 "run_id": run_id, "attempt": attempt, "reasons": reasons})

    return {"task": updated, "xp": standing(actor_id),
            "awarded": awarded, "reasons": reasons}


def comment(*, org_id: str, task_id: str, actor_id: str, actor_name: str,
            persona: str, text: str) -> None:
    if not authority.can(persona, "task.comment"):
        raise Forbidden("Not permitted to comment")
    task = _require_task(task_id, org_id)
    if not authority.may_act_on_task(task, user_id=actor_id, persona=persona):
        raise Forbidden("This task is assigned to somebody else")
    store.add_event(org_id=org_id, task_id=task_id, kind="commented",
                    actor_id=actor_id, actor_name=actor_name, summary=text,
                    payload={})


# ── XP ──────────────────────────────────────────────────────────────────
def standing(user_id: str) -> dict:
    """One operator's XP standing, level and progress bar."""
    return xp.progress(store.total_xp(user_id))


def standings_for(user_ids: list[str]) -> dict[str, dict]:
    """The same, for a whole team, in one query. The supervisor's roster shows
    every operator's level, and a per-row lookup there is N queries per page."""
    totals = store.totals_for_users(user_ids)
    return {uid: xp.progress(total) for uid, total in totals.items()}


# ── Roster ──────────────────────────────────────────────────────────────
def roster(*, org_id: str, actor_id: str, persona: str,
           scope: str = "team") -> list[dict]:
    """The operators this supervisor can see, with their standing and load.

    `scope="team"` (the default) returns only who they may ASSIGN to, and is
    what the assign dialog renders. It is derived from the SAME
    `assignable_user_ids` the assign endpoint enforces with, so the dialog
    cannot offer somebody the API would then refuse.

    `scope="all"` returns every operator in the organisation, each carrying an
    `assignable` flag. That is a VIEW widening, not an authority one: the assign
    endpoint still consults `assignable_user_ids`, so a row with
    `assignable: false` is a person this supervisor can see the load of and
    cannot dispatch to. Keeping the flag on the row — rather than shipping two
    different lists — is what lets the UI explain the boundary instead of merely
    hiding people behind it.
    """
    if not authority.can(persona, "team.read"):
        raise Forbidden("Only a supervisor can read the roster")

    from identity import store as identity_store

    assignable = authority.assignable_user_ids(org_id, actor_id, persona)
    everyone = [m.user_id for m, _ in identity_store.list_members(org_id)]
    # None means "no team restriction" — this actor may assign to the whole org.
    assignable_set = set(everyone if assignable is None else assignable)

    visible = everyone if scope == "all" else sorted(assignable_set)

    standings = standings_for(visible)
    open_counts = _open_counts(org_id, visible)

    out: list[dict] = []
    for user_id in visible:
        user = identity_store.get_user(user_id)
        if user is None:
            continue                      # membership outlived the user
        membership = identity_store.get_membership(org_id, user_id)
        member_persona = membership.persona if membership else PERSONA_FRONTLINE
        # A supervisor is not somebody you dispatch a fault to. They stay out of
        # the roster entirely rather than appearing greyed out, which would read
        # as a permissions problem rather than as a different job.
        if scope == "all" and member_persona != PERSONA_FRONTLINE:
            continue
        out.append({
            "user_id": user_id,
            "name": user.name or user.email,
            "email": user.email,
            "persona": member_persona,
            "xp": standings.get(user_id, xp.progress(0)),
            "open_tasks": open_counts.get(user_id, 0),
            "assignable": user_id in assignable_set,
        })
    # Least loaded first: the dialog's job is to help the supervisor balance the
    # shift, and sorting by name would bury that behind the alphabet. People this
    # supervisor cannot dispatch to sort last — they are context, not choices.
    out.sort(key=lambda r: (not r["assignable"], r["open_tasks"], r["name"].lower()))
    return out


def _open_counts(org_id: str, user_ids: list[str]) -> dict[str, int]:
    """How much each operator already has on. One query over the org's live
    tasks rather than one per operator."""
    if not user_ids:
        return {}
    counts = dict.fromkeys(user_ids, 0)
    for task in store.list_tasks(org_id=org_id, statuses=OPEN_STATUSES, limit=1000):
        if task.assignee_id in counts:
            counts[task.assignee_id] += 1
    return counts


# ── Dashboard aggregates ────────────────────────────────────────────────
#
# The operator's dashboard replaced a separate Progress page. That merge is the
# reason these live here rather than on the route: "how much have I got on" and
# "how am I doing" used to be two screens answering with two different shapes of
# data, and the operator had to hold both in their head to know where they
# stood. One payload, one screen, one answer.

def _day_keys(days: int) -> list[str]:
    """The last `days` dates, oldest first, as YYYY-MM-DD."""
    today = datetime.now(UTC).date()
    return [(today - timedelta(days=n)).isoformat() for n in range(days - 1, -1, -1)]


def operator_stats(*, org_id: str, user_id: str, days: int = 14) -> dict:
    """Everything the operator dashboard's numbers and charts are drawn from.

    One call, because the page draws five things from the same rows and five
    endpoints would make them disagree the moment one of them was slow.
    """
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    closed = store.closed_tasks_since(org_id=org_id, user_id=user_id, since=since)

    live = store.list_tasks(org_id=org_id, assignee_id=user_id,
                            statuses=OPEN_STATUSES, limit=500)
    by_status: dict[str, int] = {}
    for task in live:
        by_status[task.status] = by_status.get(task.status, 0) + 1

    # Awaiting sign-off is 'resolved': the operator is done, the supervisor is
    # not. It is deliberately NOT counted as open work — showing it in the "you
    # have N jobs" number is what made operators chase jobs already finished.
    awaiting = len([r for r in closed if r["status"] == STATUS_RESOLVED])

    buckets = {day: {"date": day, "closed": 0, "xp": 0} for day in _day_keys(days)}
    scores: list[float] = []
    for row in closed:
        day = (row["completed_at"] or "")[:10]
        if day in buckets:
            buckets[day]["closed"] += 1
            buckets[day]["xp"] += int(row["xp_awarded"] or 0)
        if row["score"] is not None:
            scores.append(float(row["score"]))

    series = list(buckets.values())

    # A streak counts back from today, and today being empty does not break it —
    # a shift that has not started yet is not a lapse. Yesterday being empty is.
    streak = 0
    for entry in reversed(series):
        if entry["closed"] > 0:
            streak += 1
        elif streak or entry is not series[-1]:
            break

    return {
        "totals": {
            "open": by_status.get(STATUS_ASSIGNED, 0),
            "in_progress": by_status.get(STATUS_IN_PROGRESS, 0),
            "blocked": by_status.get(STATUS_BLOCKED, 0),
            "awaiting_signoff": awaiting,
            "closed_window": len(closed),
        },
        "xp": xp.progress(store.total_xp(user_id)),
        "score": {
            "avg": round(sum(scores) / len(scores), 1) if scores else None,
            "best": round(max(scores), 1) if scores else None,
            "n": len(scores),
        },
        "severity_mix": store.task_severity_mix(org_id=org_id, user_id=user_id),
        "series": series,
        "streak_days": streak,
        "window_days": days,
        "recent": closed[:8],
    }


def team_stats(*, org_id: str, actor_id: str, persona: str, days: int = 14) -> dict:
    """Per-operator load and throughput for the supervisor's team panel.

    Scoped exactly like `roster(scope="all")` — a supervisor may SEE the whole
    organisation's frontline load, because balancing a shift means knowing who
    is buried even when they are not yours to dispatch to.
    """
    if not authority.can(persona, "team.read"):
        raise Forbidden("Only a supervisor can read team statistics")

    people = roster(org_id=org_id, actor_id=actor_id, persona=persona, scope="all")
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    throughput = store.throughput_since(
        org_id=org_id, user_ids=[p["user_id"] for p in people], since=since)

    for person in people:
        stats = throughput.get(person["user_id"], {})
        person["closed_window"] = stats.get("closed", 0)
        person["avg_score"] = stats.get("avg_score")

    return {"operators": people, "window_days": days}


# ── Persona resolution ──────────────────────────────────────────────────
def persona_of(*, org_id: str | None, user_id: str | None) -> str:
    """The caller's operational role in this organisation.

    Falls back to `frontline` when there is no membership — an API key, a device
    token or a platform admin acting outside any org. That is the safe default:
    such a caller gets no dispatch authority, and if they genuinely need it the
    fix is to give them a membership rather than to widen this.
    """
    if not org_id or not user_id:
        return PERSONA_FRONTLINE
    from identity import store as identity_store
    membership = identity_store.get_membership(org_id, user_id)
    return membership.persona if membership else PERSONA_FRONTLINE


__all__ = [
    "Forbidden", "NotFound", "WorkError", "assign", "block", "board", "close",
    "comment", "complete", "create_manual", "history", "inbox", "persona_of",
    "queue", "roster", "standing", "standings_for", "start", "unassign",
    "PERSONA_SUPERVISOR", "PERSONA_FRONTLINE",
]
