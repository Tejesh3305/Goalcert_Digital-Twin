"""authority.py — who may do what to whose work.

`identity/service.py` already answers account-level permission (who may change a
role, issue a key, remove a member) and `server/tenancy.py` answers tenant
access. Neither answers the operational question this module exists for: may
THIS supervisor assign THAT fault to THAT operator?

TWO GATES, BOTH REQUIRED
------------------------
  1. CAPABILITY — a persona-level yes/no ("may a frontline operator assign
     work?"). Coarse, declarative, cheap. Enforced by `require_capability` in
     `server/work_routes.py`.

  2. SCOPE — a row-level "whose people?" ("may this supervisor assign to that
     operator?"). A capability alone would let any supervisor dispatch anyone in
     the organisation, which is not what a supervisor is. Enforced by
     `assignable_user_ids()` at the point of use.

Both live here so that the affordances the frontend renders and the refusals the
API issues are generated from ONE table. When they are written out separately
they drift, and the symptom is a button that 403s — which reads to the user as a
broken product rather than as a permission they do not have.

WHY THE DATA LADDER IS NOT ENOUGH
---------------------------------
It would be tempting to say "supervisor == admin, operator == write" and reuse
`identity`'s roles. That fails immediately: a supervisor who may only READ the
twin still dispatches people, and an operator who may WRITE telemetry still must
not be able to assign themselves the interesting jobs. Dispatch authority and
data authority are different axes, so they get different tables.
"""
from __future__ import annotations

from work.models import PERSONA_FRONTLINE, PERSONA_SUPERVISOR

# ── Capability matrix ───────────────────────────────────────────────────
#
# Read this as the whole product surface: if a capability is not listed for a
# persona, the API refuses it and the UI does not draw it.
CAPABILITIES: dict[str, set[str]] = {
    PERSONA_SUPERVISOR: {
        "queue.read",          # the unassigned fault queue
        "task.create",         # raise one by hand, without waiting for a finding
        "task.assign",         # the whole point of the persona — scoped to their team
        "task.unassign",
        "task.close",          # accept an operator's fix and resolve the finding
        "task.comment",
        "team.read",
        "xp.read_team",        # see their operators' progress, not just their own
    },
    PERSONA_FRONTLINE: {
        "task.read_own",       # only rows where they are the assignee
        "task.start",
        "task.complete",
        "task.block",          # "I cannot do this" is a first-class outcome
        "task.comment",
        "scenario.run",        # the fix-the-issue page
        "xp.read_own",
    },
}

#: Capabilities every KNOWN persona has. Kept separate from the matrix so adding
#: a persona cannot accidentally omit them — but granted only to a persona that
#: is actually in the matrix, so an unrecognised value still holds nothing.
COMMON = {"me.read"}


def _granted(persona: str | None) -> set[str]:
    """The full capability set for a persona, or empty for an unknown one.

    The `persona in CAPABILITIES` check is what makes "an unknown persona holds
    nothing" literally true. Unioning COMMON unconditionally would hand a
    corrupt persona a capability, which is a small grant made for no reason —
    and the point of failing closed is that there are no exceptions to reason
    about later.
    """
    if not persona or persona not in CAPABILITIES:
        return set()
    return CAPABILITIES[persona] | COMMON


def can(persona: str | None, capability: str) -> bool:
    """Does this persona hold this capability?

    An unknown persona holds nothing. That is the correct failure direction: a
    membership row with a corrupt persona should lock its owner out of dispatch,
    not grant them everything.
    """
    return capability in _granted(persona)


def capabilities_for(persona: str | None) -> list[str]:
    """The persona's capabilities, shipped to the frontend by `/api/v1/work/me`
    so it renders exactly the actions the API will honour."""
    return sorted(_granted(persona))


# ── Scope ───────────────────────────────────────────────────────────────
def team_ids_supervised_by(org_id: str, user_id: str) -> list[str]:
    """The teams this user is the supervisor of."""
    from work import store
    return store.team_ids_for_supervisor(org_id=org_id, user_id=user_id)


def assignable_user_ids(org_id: str, user_id: str, persona: str) -> list[str] | None:
    """The user ids this person may assign work TO.

    `None` means "anyone in the organisation" and is returned for nobody today —
    it exists because the caller must not treat an empty list and "unrestricted"
    as the same thing, and a future org-wide dispatcher role (an L&D or ops
    manager persona) will need it. Callers still filter by `org_id`, so `None`
    is not a cross-tenant escape hatch.

    A supervisor gets the concrete membership of the teams they supervise. A
    supervisor who supervises no team can assign to NOBODY, which is the correct
    failure direction: an unconfigured account should be inert, not omnipotent.
    """
    from work import store

    if persona != PERSONA_SUPERVISOR:
        return []
    team_ids = team_ids_supervised_by(org_id, user_id)
    if not team_ids:
        return []
    return store.member_ids_of_teams(org_id=org_id, team_ids=team_ids)


def may_assign_to(org_id: str, actor_id: str, persona: str, assignee_id: str) -> bool:
    allowed = assignable_user_ids(org_id, actor_id, persona)
    return True if allowed is None else assignee_id in allowed


def may_act_on_task(task, *, user_id: str, persona: str) -> bool:
    """Row-level check for the operator side.

    A frontline operator may act on a task IF AND ONLY IF it is assigned to
    them. This is the check that stops one operator starting, completing and
    collecting the XP for a job dispatched to somebody else — a capability gate
    alone cannot see whose row it is.
    """
    if persona == PERSONA_SUPERVISOR:
        return True
    return bool(task.assignee_id) and task.assignee_id == user_id
