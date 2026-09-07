"""models.py — the work records, as plain frozen dataclasses.

Same shape and same reasoning as `identity/models.py`: these are READ models —
what a row means once it has left the database. Frozen, because a dispatch
decision must not be able to mutate the record it was derived from, and free of
persistence logic, so `work/store.py` stays the only module that knows a column
name.

WHAT A TASK IS, AND WHAT IT IS NOT
----------------------------------
A Task is a *dispatch record*: the statement that a named person owes a fix to a
named fault. It is NOT a copy of the fault. The fault is a Finding node in the
graph and stays there — `Task.finding_node_id` points at it. This split is
load-bearing:

    the graph  answers  "what is wrong with the plant?"
    tasks      answer   "who is fixing it, and what did they learn?"

Merging them would mean the twin's model of reality changed shape the moment
somebody was assigned to it, and every consumer of the graph would have to know
about staffing.

THE TWO ROLE VOCABULARIES
-------------------------
The platform now has two, and conflating them is the mistake this module exists
to prevent:

    role     owner / admin / write / read   — the DATA ladder (identity/models.py).
                                              What you may read and write.
    persona  supervisor / frontline         — the OPERATIONAL role, here.
                                              What job you do.

They are orthogonal on purpose. A supervisor who may only *read* the twin is an
ordinary account — they dispatch people, they do not edit the plant model. A
frontline operator may well need *write*, because closing a job writes back. If
persona were a rung on the data ladder, one of those two accounts would be
impossible to express.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Re-exported, NOT redefined. The persona column lives on `identity.Membership`,
# so identity owns the vocabulary and this module borrows it — two modules each
# declaring their own tuple of persona names is exactly how the API and the
# database end up disagreeing about what "supervisor" is spelled like.
#
# Imported from `identity.models` rather than the package root so the provenance
# is visible at the import site: these names are defined next to the Membership
# record that carries them.
from identity.models import (  # noqa: F401
    DEFAULT_PERSONA,
    PERSONA_FRONTLINE,
    PERSONA_SUPERVISOR,
    PERSONAS,
    normalize_persona,
)

# ── Task lifecycle ──────────────────────────────────────────────────────
#
#   open ──assign──▶ assigned ──start──▶ in_progress ──complete──▶ resolved
#     ▲                  │                    │                       │
#     └──unassign────────┘                    └──block──▶ blocked     └─close──▶ closed
#
# `open` means the twin raised it and no human owns it yet — that is the
# supervisor's queue. `resolved` means the operator finished and passed; `closed`
# means the supervisor accepted it and the twin's Finding was marked resolved.
# Keeping those two apart is what stops an operator being the sole judge of their
# own work.
STATUS_OPEN = "open"
STATUS_ASSIGNED = "assigned"
STATUS_IN_PROGRESS = "in_progress"
STATUS_RESOLVED = "resolved"
STATUS_BLOCKED = "blocked"
STATUS_CLOSED = "closed"

TASK_STATUSES = (STATUS_OPEN, STATUS_ASSIGNED, STATUS_IN_PROGRESS,
                 STATUS_RESOLVED, STATUS_BLOCKED, STATUS_CLOSED)

#: Statuses that still owe somebody something. The operator inbox and the
#: supervisor queue both filter on this, so "what counts as outstanding" is
#: defined once.
OPEN_STATUSES = (STATUS_OPEN, STATUS_ASSIGNED, STATUS_IN_PROGRESS, STATUS_BLOCKED)

#: Legal transitions. The service checks this table rather than trusting the
#: caller's requested status, so an out-of-order client cannot walk a task
#: backwards from `closed` into `in_progress` and re-award its XP.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    STATUS_OPEN:        (STATUS_ASSIGNED,),
    STATUS_ASSIGNED:    (STATUS_IN_PROGRESS, STATUS_OPEN, STATUS_BLOCKED),
    STATUS_IN_PROGRESS: (STATUS_RESOLVED, STATUS_BLOCKED, STATUS_ASSIGNED),
    STATUS_BLOCKED:     (STATUS_IN_PROGRESS, STATUS_ASSIGNED, STATUS_OPEN),
    STATUS_RESOLVED:    (STATUS_CLOSED, STATUS_IN_PROGRESS),
    STATUS_CLOSED:      (),
}


def can_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, ())


# ── Severity ────────────────────────────────────────────────────────────
#: The twin's Finding severities, ranked. An unknown severity ranks LOWEST, for
#: the same reason an unknown role does: a value we cannot interpret must not
#: escalate into a critical work order.
SEVERITY_RANK = {"info": 0, "warning": 1, "serious": 2, "critical": 3}


def severity_rank(severity: str) -> int:
    return SEVERITY_RANK.get((severity or "").strip().lower(), 0)


PRIORITIES = ("low", "normal", "high", "urgent")

#: Where a task came from. `twin_finding` is the one the twin raises by itself;
#: `manual` is a supervisor typing one in.
TASK_SOURCES = ("twin_finding", "manual")

#: Append-only event kinds on a task. The vocabulary is closed so the operator
#: timeline can render each kind without a fallback branch.
EVENT_KINDS = ("raised", "assigned", "unassigned", "started", "run_started",
               "run_completed", "resolved", "blocked", "closed", "commented")


@dataclass(frozen=True)
class Task:
    """One fault, and whoever owes its fix."""

    task_id: str
    org_id: str
    tenant_id: str
    title: str
    code: str = ""
    detail: str = ""
    finding_node_id: str = ""
    asset_node_id: str = ""
    asset_name: str = ""
    behavior_id: str = ""
    severity: str = "warning"
    scenario_id: str = ""
    status: str = STATUS_OPEN
    priority: str = "normal"
    assignee_id: str | None = None
    assigned_by: str | None = None
    team_id: str | None = None
    source: str = "twin_finding"
    score: float | None = None
    xp_awarded: int = 0
    resolution: str = ""
    changelog_ref: str = ""
    created_at: str = ""
    assigned_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def is_assigned(self) -> bool:
        return bool(self.assignee_id)

    def public(self) -> dict:
        """The API representation. Allow-listed rather than deny-listed, so a
        field added to this dataclass is published deliberately."""
        return {
            "task_id": self.task_id,
            "code": self.code,
            "title": self.title,
            "detail": self.detail,
            "tenant_id": self.tenant_id,
            "finding_node_id": self.finding_node_id,
            "asset_node_id": self.asset_node_id,
            "asset_name": self.asset_name,
            "behavior_id": self.behavior_id,
            "severity": self.severity,
            "scenario_id": self.scenario_id,
            "status": self.status,
            "priority": self.priority,
            "assignee_id": self.assignee_id,
            "assigned_by": self.assigned_by,
            "team_id": self.team_id,
            "source": self.source,
            "score": self.score,
            "xp_awarded": self.xp_awarded,
            "resolution": self.resolution,
            "created_at": self.created_at,
            "assigned_at": self.assigned_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass(frozen=True)
class TaskEvent:
    """One entry in a task's timeline. Append-only; nothing updates these."""

    event_id: str
    task_id: str
    org_id: str
    kind: str = ""
    actor_id: str = ""
    actor_name: str = ""
    summary: str = ""
    payload: dict = field(default_factory=dict)
    created_at: str = ""

    def public(self) -> dict:
        return {
            "event_id": self.event_id,
            "task_id": self.task_id,
            "kind": self.kind,
            "actor_id": self.actor_id,
            "actor_name": self.actor_name,
            "summary": self.summary,
            "payload": self.payload,
            "at": self.created_at,
        }


@dataclass(frozen=True)
class Team:
    """What bounds a supervisor's authority. See `work/authority.py`."""

    team_id: str
    org_id: str
    name: str = ""
    site: str = ""
    shift: str = "A"
    supervisor_id: str | None = None
    created_at: str = ""

    def public(self) -> dict:
        return {
            "team_id": self.team_id,
            "name": self.name,
            "site": self.site,
            "shift": self.shift,
            "supervisor_id": self.supervisor_id,
        }


@dataclass(frozen=True)
class XpEntry:
    """One award, with the reason that produced it.

    XP is stored as a ledger rather than a running total on the user row,
    because a number that can only go up cannot be explained or corrected. The
    total is a SUM over these, and every point in it has a row saying why.
    """

    entry_id: str
    org_id: str
    user_id: str
    task_id: str = ""
    reason: str = ""
    points: int = 0
    detail: str = ""
    created_at: str = ""

    def public(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "task_id": self.task_id,
            "reason": self.reason,
            "points": self.points,
            "detail": self.detail,
            "at": self.created_at,
        }


@dataclass(frozen=True)
class ScenarioRun:
    """One attempt at a fix procedure. See `scenario/` for what a run contains."""

    run_id: str
    org_id: str
    user_id: str
    task_id: str = ""
    scenario_id: str = ""
    mode: str = "guided"
    status: str = "in_progress"
    step: int = 0
    total_steps: int = 0
    score: float | None = None
    passed: bool = False
    hints_used: int = 0
    wrong_steps: int = 0
    transcript: list = field(default_factory=list)
    started_at: str = ""
    completed_at: str | None = None

    def public(self) -> dict:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "scenario_id": self.scenario_id,
            "mode": self.mode,
            "status": self.status,
            "step": self.step,
            "total_steps": self.total_steps,
            "score": self.score,
            "passed": self.passed,
            "hints_used": self.hints_used,
            "wrong_steps": self.wrong_steps,
            "transcript": self.transcript,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }
