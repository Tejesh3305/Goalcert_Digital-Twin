"""work — dispatch: who fixes the fault the twin just found.

The twin could always detect. It could not say who was told, who started, or
what they learned. This package is that missing half, and it is deliberately
thin: the graph stays the source of truth for what is wrong with the plant, and
these tables only record what people did about it.

    models.py        the records, and the task lifecycle
    authority.py     which persona may do what, and to whose people
    store.py         every SQL statement against the work tables
    service.py       the rules of the loop — routes call only this
    xp.py            what a fix is worth, and what that makes you
    from_finding.py  the bridge: Finding node -> task -> Finding resolved

TWO ROLE VOCABULARIES, ON PURPOSE
---------------------------------
`identity` keeps the DATA ladder (owner/admin/write/read) and now also stores the
OPERATIONAL role on the membership: `persona`, one of supervisor / frontline.
They are separate axes — see the PERSONAS block in `identity/models.py` — and
`authority.py` is what turns a persona into a set of permitted actions.

WHERE AUTHORIZATION HAPPENS
---------------------------
Tenant access is still `server/tenancy.py`; nothing here widens it. This package
answers only the operational question on top of that: given a caller who may
already reach this tenant, may they dispatch THIS task to THAT operator?
"""
from __future__ import annotations

from work.models import (  # noqa: F401
    DEFAULT_PERSONA,
    OPEN_STATUSES,
    PERSONA_FRONTLINE,
    PERSONA_SUPERVISOR,
    PERSONAS,
    TASK_STATUSES,
    ScenarioRun,
    Task,
    TaskEvent,
    Team,
    XpEntry,
    normalize_persona,
)

__all__ = [
    "DEFAULT_PERSONA", "OPEN_STATUSES", "PERSONAS", "PERSONA_FRONTLINE",
    "PERSONA_SUPERVISOR", "ScenarioRun", "TASK_STATUSES", "Task", "TaskEvent",
    "Team", "XpEntry", "normalize_persona",
]
