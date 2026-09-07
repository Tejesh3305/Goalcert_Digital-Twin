"""store.py — every SQL statement against the work tables, and nothing else.

Same contract as `identity/store.py`: nothing outside this module names a column,
so the schema has exactly one consumer and a rename is a local change. Rows go
out as the frozen dataclasses in `work/models.py`; callers never see a raw dict.

SQL is written SQLite-flavoured with `?` placeholders and translated for MySQL by
`db/core.py`. Read that module before adding a statement — `datetime('now')` and
`INSERT OR IGNORE` do NOT survive the translation, which is why timestamps are
computed in Python here and upserts are written `ON CONFLICT ... DO NOTHING`.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from db import Json, connect, json_load, schema

from work.models import ScenarioRun, Task, TaskEvent, Team, XpEntry

_STORE = "work"


def _ensure() -> None:
    schema.ensure(_STORE)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _row(cursor) -> dict | None:
    row = cursor.fetchone()
    return dict(row) if row else None


def _rows(cursor) -> list[dict]:
    return [dict(r) for r in cursor.fetchall()]


# ── Row -> record ───────────────────────────────────────────────────────
def _task(row: dict) -> Task:
    return Task(
        task_id=row["task_id"], org_id=row["org_id"], tenant_id=row["tenant_id"],
        code=row.get("code") or "", title=row.get("title") or "",
        detail=row.get("detail") or "",
        finding_node_id=row.get("finding_node_id") or "",
        asset_node_id=row.get("asset_node_id") or "",
        asset_name=row.get("asset_name") or "",
        behavior_id=row.get("behavior_id") or "",
        severity=row.get("severity") or "warning",
        scenario_id=row.get("scenario_id") or "",
        status=row.get("status") or "open",
        priority=row.get("priority") or "normal",
        assignee_id=row.get("assignee_id") or None,
        assigned_by=row.get("assigned_by") or None,
        team_id=row.get("team_id") or None,
        source=row.get("source") or "twin_finding",
        score=row.get("score"),
        xp_awarded=int(row.get("xp_awarded") or 0),
        resolution=row.get("resolution") or "",
        changelog_ref=row.get("changelog_ref") or "",
        created_at=row.get("created_at") or "",
        assigned_at=row.get("assigned_at"),
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
    )


def _event(row: dict) -> TaskEvent:
    return TaskEvent(
        event_id=row["event_id"], task_id=row["task_id"], org_id=row["org_id"],
        kind=row.get("kind") or "", actor_id=row.get("actor_id") or "",
        actor_name=row.get("actor_name") or "", summary=row.get("summary") or "",
        payload=json_load(row.get("payload")) or {},
        created_at=row.get("created_at") or "",
    )


def _team(row: dict) -> Team:
    return Team(
        team_id=row["team_id"], org_id=row["org_id"], name=row.get("name") or "",
        site=row.get("site") or "", shift=row.get("shift") or "A",
        supervisor_id=row.get("supervisor_id") or None,
        created_at=row.get("created_at") or "",
    )


def _xp(row: dict) -> XpEntry:
    return XpEntry(
        entry_id=row["entry_id"], org_id=row["org_id"], user_id=row["user_id"],
        task_id=row.get("task_id") or "", reason=row.get("reason") or "",
        points=int(row.get("points") or 0), detail=row.get("detail") or "",
        created_at=row.get("created_at") or "",
    )


def _run(row: dict) -> ScenarioRun:
    return ScenarioRun(
        run_id=row["run_id"], org_id=row["org_id"], user_id=row["user_id"],
        task_id=row.get("task_id") or "", scenario_id=row.get("scenario_id") or "",
        mode=row.get("mode") or "guided", status=row.get("status") or "in_progress",
        step=int(row.get("step") or 0), total_steps=int(row.get("total_steps") or 0),
        score=row.get("score"), passed=bool(row.get("passed")),
        hints_used=int(row.get("hints_used") or 0),
        wrong_steps=int(row.get("wrong_steps") or 0),
        transcript=json_load(row.get("transcript")) or [],
        started_at=row.get("started_at") or "",
        completed_at=row.get("completed_at"),
    )


# ── Teams ───────────────────────────────────────────────────────────────
def create_team(*, org_id: str, name: str, supervisor_id: str | None = None,
                site: str = "", shift: str = "A") -> Team:
    _ensure()
    team = Team(team_id=_new_id("team"), org_id=org_id, name=name, site=site,
                shift=shift, supervisor_id=supervisor_id, created_at=_now())
    with connect(_STORE) as conn:
        conn.execute(
            "INSERT INTO teams (team_id, org_id, name, site, shift, "
            "supervisor_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (team.team_id, team.org_id, team.name, team.site, team.shift,
             team.supervisor_id, team.created_at))
    return team


def get_team(team_id: str) -> Team | None:
    _ensure()
    with connect(_STORE) as conn:
        row = _row(conn.execute("SELECT * FROM teams WHERE team_id = ?", (team_id,)))
    return _team(row) if row else None


def list_teams(org_id: str) -> list[Team]:
    _ensure()
    with connect(_STORE) as conn:
        rows = _rows(conn.execute(
            "SELECT * FROM teams WHERE org_id = ? ORDER BY created_at", (org_id,)))
    return [_team(r) for r in rows]


def team_ids_for_supervisor(*, org_id: str, user_id: str) -> list[str]:
    _ensure()
    with connect(_STORE) as conn:
        rows = _rows(conn.execute(
            "SELECT team_id FROM teams WHERE org_id = ? AND supervisor_id = ?",
            (org_id, user_id)))
    return [r["team_id"] for r in rows]


def set_team_supervisor(*, team_id: str, supervisor_id: str | None) -> None:
    _ensure()
    with connect(_STORE) as conn:
        conn.execute("UPDATE teams SET supervisor_id = ? WHERE team_id = ?",
                     (supervisor_id, team_id))


def add_team_member(*, org_id: str, team_id: str, user_id: str) -> None:
    """Idempotent: re-adding somebody already on the team is a no-op rather than
    an error, because the caller is usually a seed script or an admin clicking
    twice, and neither is a fault worth an exception."""
    _ensure()
    with connect(_STORE) as conn:
        conn.execute(
            "INSERT INTO team_members (team_id, user_id, org_id, created_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (team_id, user_id) DO NOTHING",
            (team_id, user_id, org_id, _now()))


def remove_team_member(*, team_id: str, user_id: str) -> None:
    _ensure()
    with connect(_STORE) as conn:
        conn.execute("DELETE FROM team_members WHERE team_id = ? AND user_id = ?",
                     (team_id, user_id))


def member_ids_of_teams(*, org_id: str, team_ids: list[str]) -> list[str]:
    """Distinct user ids across several teams, order-preserving.

    Returns `[]` for an empty `team_ids` rather than building `IN ()`, which is
    a syntax error on both backends — and, worse, would be easy to "fix" with a
    query that matches everything.
    """
    if not team_ids:
        return []
    _ensure()
    placeholders = ", ".join("?" for _ in team_ids)
    with connect(_STORE) as conn:
        rows = _rows(conn.execute(
            f"SELECT DISTINCT user_id FROM team_members "  # noqa: S608 - ids are bound
            f"WHERE org_id = ? AND team_id IN ({placeholders})",
            (org_id, *team_ids)))
    return [r["user_id"] for r in rows]


def teams_for_user(*, org_id: str, user_id: str) -> list[str]:
    _ensure()
    with connect(_STORE) as conn:
        rows = _rows(conn.execute(
            "SELECT team_id FROM team_members WHERE org_id = ? AND user_id = ?",
            (org_id, user_id)))
    return [r["team_id"] for r in rows]


# ── Tasks ───────────────────────────────────────────────────────────────
def next_task_code(org_id: str) -> str:
    """A human-facing id: TSK-0001, TSK-0002, ...

    Derived from a COUNT, so it can collide under two simultaneous creates. That
    is accepted deliberately: `task_id` is the real key and is a uuid, and this
    string exists only so an operator can read one out over a radio. Making it
    gapless would need a sequence table and a lock for no operational gain.
    """
    _ensure()
    with connect(_STORE) as conn:
        row = _row(conn.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE org_id = ?", (org_id,)))
    return f"TSK-{(row['n'] if row else 0) + 1:04d}"


def create_task(*, org_id: str, tenant_id: str, title: str, detail: str = "",
                finding_node_id: str = "", asset_node_id: str = "",
                asset_name: str = "", behavior_id: str = "",
                severity: str = "warning", scenario_id: str = "",
                priority: str = "normal", source: str = "twin_finding",
                status: str = "open", assignee_id: str | None = None,
                assigned_by: str | None = None, team_id: str | None = None,
                changelog_ref: str = "") -> Task:
    _ensure()
    now = _now()
    task = Task(
        task_id=_new_id("task"), org_id=org_id, tenant_id=tenant_id,
        code=next_task_code(org_id), title=title, detail=detail,
        finding_node_id=finding_node_id, asset_node_id=asset_node_id,
        asset_name=asset_name, behavior_id=behavior_id, severity=severity,
        scenario_id=scenario_id, status=status, priority=priority,
        assignee_id=assignee_id, assigned_by=assigned_by, team_id=team_id,
        source=source, changelog_ref=changelog_ref, created_at=now,
        assigned_at=now if assignee_id else None)
    with connect(_STORE) as conn:
        conn.execute(
            "INSERT INTO tasks (task_id, org_id, tenant_id, code, title, detail, "
            "finding_node_id, asset_node_id, asset_name, behavior_id, severity, "
            "scenario_id, status, priority, assignee_id, assigned_by, team_id, "
            "source, resolution, changelog_ref, created_at, assigned_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task.task_id, task.org_id, task.tenant_id, task.code, task.title,
             task.detail, task.finding_node_id, task.asset_node_id,
             task.asset_name, task.behavior_id, task.severity, task.scenario_id,
             task.status, task.priority, task.assignee_id, task.assigned_by,
             task.team_id, task.source, task.resolution, task.changelog_ref,
             task.created_at, task.assigned_at))
    return task


def get_task(task_id: str) -> Task | None:
    _ensure()
    with connect(_STORE) as conn:
        row = _row(conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)))
    return _task(row) if row else None


def list_tasks(*, org_id: str, statuses: tuple[str, ...] | None = None,
               assignee_id: str | None = None, tenant_id: str | None = None,
               unassigned_only: bool = False, limit: int = 200) -> list[Task]:
    """Tasks for one organisation, narrowed.

    Always filtered by `org_id` — there is no code path here that reads a task
    without one, which is what keeps a missing filter from turning into a
    cross-tenant disclosure.
    """
    _ensure()
    sql = "SELECT * FROM tasks WHERE org_id = ?"
    params: list = [org_id]
    if statuses:
        sql += f" AND status IN ({', '.join('?' for _ in statuses)})"
        params.extend(statuses)
    if assignee_id:
        sql += " AND assignee_id = ?"
        params.append(assignee_id)
    if tenant_id:
        sql += " AND tenant_id = ?"
        params.append(tenant_id)
    if unassigned_only:
        sql += " AND (assignee_id IS NULL OR assignee_id = '')"
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(int(limit))
    with connect(_STORE) as conn:
        rows = _rows(conn.execute(sql, tuple(params)))
    return [_task(r) for r in rows]


def live_task_for_finding(*, org_id: str, finding_node_id: str) -> Task | None:
    """An open task already covering this finding, if any.

    The uniqueness key is the FINDING NODE ID and the filter is on LIVE statuses
    only. Both halves matter: the findings loop collapses a persisting fault into
    one node, so one ongoing fault is one task however long it lasts — and a
    CLOSED task must not suppress work when the same fault recurs later.
    """
    if not finding_node_id:
        return None
    _ensure()
    with connect(_STORE) as conn:
        row = _row(conn.execute(
            "SELECT * FROM tasks WHERE org_id = ? AND finding_node_id = ? "
            "AND status != 'closed' ORDER BY created_at DESC LIMIT 1",
            (org_id, finding_node_id)))
    return _task(row) if row else None


def update_task(task_id: str, **fields) -> Task | None:
    """Patch named columns. Unknown keys are refused rather than ignored — a
    typo'd field name that silently does nothing is a bug that surfaces as
    "the status did not change" hours later."""
    allowed = {"title", "detail", "status", "priority", "assignee_id",
               "assigned_by", "team_id", "scenario_id", "score", "xp_awarded",
               "resolution", "assigned_at", "started_at", "completed_at",
               "severity", "changelog_ref"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unknown task fields: {', '.join(sorted(unknown))}")
    if not fields:
        return get_task(task_id)
    _ensure()
    sets = ", ".join(f"{k} = ?" for k in fields)
    with connect(_STORE) as conn:
        conn.execute(f"UPDATE tasks SET {sets} WHERE task_id = ?",  # noqa: S608
                     (*fields.values(), task_id))
    return get_task(task_id)


# ── Task events ─────────────────────────────────────────────────────────
def add_event(*, org_id: str, task_id: str, kind: str, actor_id: str = "",
              actor_name: str = "", summary: str = "",
              payload: dict | None = None) -> TaskEvent:
    _ensure()
    event = TaskEvent(event_id=_new_id("tev"), task_id=task_id, org_id=org_id,
                      kind=kind, actor_id=actor_id, actor_name=actor_name,
                      summary=summary, payload=payload or {}, created_at=_now())
    with connect(_STORE) as conn:
        conn.execute(
            "INSERT INTO task_events (event_id, task_id, org_id, kind, actor_id, "
            "actor_name, summary, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event.event_id, event.task_id, event.org_id, event.kind,
             event.actor_id, event.actor_name, event.summary,
             Json(event.payload), event.created_at))
    return event


def list_events(task_id: str, limit: int = 100) -> list[TaskEvent]:
    _ensure()
    with connect(_STORE) as conn:
        rows = _rows(conn.execute(
            "SELECT * FROM task_events WHERE task_id = ? "
            "ORDER BY created_at LIMIT ?", (task_id, int(limit))))
    return [_event(r) for r in rows]


# ── XP ──────────────────────────────────────────────────────────────────
def add_xp(*, org_id: str, user_id: str, points: int, reason: str,
           task_id: str = "", detail: str = "") -> XpEntry | None:
    """Append one award. Returns None if this exact award already exists.

    The `(user_id, task_id, reason)` unique index is what makes a double award
    impossible rather than merely unlikely: a retried request that reaches the
    insert is refused by the database, not by a check-then-write race in Python.
    """
    _ensure()
    entry = XpEntry(entry_id=_new_id("xp"), org_id=org_id, user_id=user_id,
                    task_id=task_id, reason=reason, points=int(points),
                    detail=detail, created_at=_now())
    with connect(_STORE) as conn:
        conn.execute(
            "INSERT INTO xp_ledger (entry_id, org_id, user_id, task_id, reason, "
            "points, detail, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (user_id, task_id, reason) DO NOTHING",
            (entry.entry_id, entry.org_id, entry.user_id, entry.task_id,
             entry.reason, entry.points, entry.detail, entry.created_at))
        row = _row(conn.execute(
            "SELECT * FROM xp_ledger WHERE user_id = ? AND task_id = ? "
            "AND reason = ?", (user_id, task_id, reason)))
    if row and row["entry_id"] == entry.entry_id:
        return entry
    return None       # somebody (or a retry) already awarded this


def total_xp(user_id: str) -> int:
    _ensure()
    with connect(_STORE) as conn:
        row = _row(conn.execute(
            "SELECT COALESCE(SUM(points), 0) AS total FROM xp_ledger "
            "WHERE user_id = ?", (user_id,)))
    return int(row["total"] if row else 0)


def totals_for_users(user_ids: list[str]) -> dict[str, int]:
    """One query for a whole team's totals — the supervisor's roster renders
    every operator's XP, and a per-row lookup there is N queries per page."""
    if not user_ids:
        return {}
    _ensure()
    placeholders = ", ".join("?" for _ in user_ids)
    with connect(_STORE) as conn:
        rows = _rows(conn.execute(
            f"SELECT user_id, COALESCE(SUM(points), 0) AS total "  # noqa: S608
            f"FROM xp_ledger WHERE user_id IN ({placeholders}) GROUP BY user_id",
            tuple(user_ids)))
    totals = {r["user_id"]: int(r["total"]) for r in rows}
    return {uid: totals.get(uid, 0) for uid in user_ids}


def list_xp(user_id: str, limit: int = 50) -> list[XpEntry]:
    _ensure()
    with connect(_STORE) as conn:
        rows = _rows(conn.execute(
            "SELECT * FROM xp_ledger WHERE user_id = ? "
            "ORDER BY created_at DESC LIMIT ?", (user_id, int(limit))))
    return [_xp(r) for r in rows]


# ── Scenario runs ───────────────────────────────────────────────────────
def create_run(*, org_id: str, user_id: str, task_id: str, scenario_id: str,
               total_steps: int, mode: str = "guided") -> ScenarioRun:
    _ensure()
    run = ScenarioRun(run_id=_new_id("run"), org_id=org_id, user_id=user_id,
                      task_id=task_id, scenario_id=scenario_id, mode=mode,
                      status="in_progress", step=0, total_steps=total_steps,
                      transcript=[], started_at=_now())
    with connect(_STORE) as conn:
        conn.execute(
            "INSERT INTO scenario_runs (run_id, org_id, user_id, task_id, "
            "scenario_id, mode, status, step, total_steps, passed, hints_used, "
            "wrong_steps, transcript, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run.run_id, run.org_id, run.user_id, run.task_id, run.scenario_id,
             run.mode, run.status, run.step, run.total_steps, 0, 0, 0,
             Json([]), run.started_at))
    return run


def get_run(run_id: str) -> ScenarioRun | None:
    _ensure()
    with connect(_STORE) as conn:
        row = _row(conn.execute(
            "SELECT * FROM scenario_runs WHERE run_id = ?", (run_id,)))
    return _run(row) if row else None


def update_run(run_id: str, **fields) -> ScenarioRun | None:
    allowed = {"status", "step", "score", "passed", "hints_used", "wrong_steps",
               "transcript", "completed_at"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unknown run fields: {', '.join(sorted(unknown))}")
    if not fields:
        return get_run(run_id)
    if "transcript" in fields:
        fields["transcript"] = Json(fields["transcript"])
    if "passed" in fields:
        fields["passed"] = 1 if fields["passed"] else 0
    _ensure()
    sets = ", ".join(f"{k} = ?" for k in fields)
    with connect(_STORE) as conn:
        conn.execute(f"UPDATE scenario_runs SET {sets} WHERE run_id = ?",  # noqa: S608
                     (*fields.values(), run_id))
    return get_run(run_id)


def active_run_for_task(*, user_id: str, task_id: str) -> ScenarioRun | None:
    """The operator's unfinished run on this task, if they have one.

    Resuming beats restarting: an operator whose phone locked mid-procedure
    should come back to step 4, not to step 1 with their hint count reset.
    """
    _ensure()
    with connect(_STORE) as conn:
        row = _row(conn.execute(
            "SELECT * FROM scenario_runs WHERE user_id = ? AND task_id = ? "
            "AND status = 'in_progress' ORDER BY started_at DESC LIMIT 1",
            (user_id, task_id)))
    return _run(row) if row else None


def active_practice_run(*, user_id: str, scenario_id: str) -> ScenarioRun | None:
    """This operator's unfinished PRACTICE run on one procedure.

    Practice runs carry no task, so they cannot be found by task id — and
    `active_run_for_task(task_id="")` would match a practice run on any
    procedure, handing somebody the wrong half-finished drill.
    """
    _ensure()
    with connect(_STORE) as conn:
        row = _row(conn.execute(
            "SELECT * FROM scenario_runs WHERE user_id = ? AND scenario_id = ? "
            "AND (task_id IS NULL OR task_id = '') AND status = 'in_progress' "
            "ORDER BY started_at DESC LIMIT 1", (user_id, scenario_id)))
    return _run(row) if row else None


def passed_scenarios(user_id: str) -> set[str]:
    """Procedures this operator has already passed, in practice or on a job.

    Drives the "practised / not yet" state on the training list, and is one
    query rather than one per card.
    """
    _ensure()
    with connect(_STORE) as conn:
        rows = _rows(conn.execute(
            "SELECT DISTINCT scenario_id FROM scenario_runs "
            "WHERE user_id = ? AND passed = 1", (user_id,)))
    return {r["scenario_id"] for r in rows if r["scenario_id"]}


def count_attempts(*, user_id: str, task_id: str) -> int:
    """Completed runs against this task. Feeds the first-attempt XP bonus."""
    _ensure()
    with connect(_STORE) as conn:
        row = _row(conn.execute(
            "SELECT COUNT(*) AS n FROM scenario_runs WHERE user_id = ? "
            "AND task_id = ? AND status = 'completed'", (user_id, task_id)))
    return int(row["n"] if row else 0)


def list_runs(*, user_id: str, limit: int = 50) -> list[ScenarioRun]:
    _ensure()
    with connect(_STORE) as conn:
        rows = _rows(conn.execute(
            "SELECT * FROM scenario_runs WHERE user_id = ? "
            "ORDER BY started_at DESC LIMIT ?", (user_id, int(limit))))
    return [_run(r) for r in rows]


# ── Work rules ──────────────────────────────────────────────────────────
def list_rules(org_id: str) -> list[dict]:
    _ensure()
    with connect(_STORE) as conn:
        rows = _rows(conn.execute(
            "SELECT * FROM work_rules WHERE org_id = ? AND enabled = 1 "
            "ORDER BY seq", (org_id,)))
    return rows


#: Orgs this process has already confirmed have a rule. See `ensure_default_rule`.
_seeded_rules: set[str] = set()


def ensure_default_rule(org_id: str) -> None:
    """The rule a tenant gets before anyone configures anything.

    Critical only, on purpose. A platform that raises a task for every warning
    teaches operators to ignore tasks, and that habit is far harder to undo than
    a missed notification.

    CACHED PER PROCESS, because this sits on the findings hot path:
    `from_finding.handle_finding` runs for every committed Finding, and without
    the cache every one of them pays a SELECT to re-learn something that cannot
    become false — the seeding is one-way, and nothing in the product deletes an
    org's last rule. Same bounded trade as `schema._done`: an org whose rules are
    deleted directly in the database will not be re-seeded until the process
    restarts, which is the correct priority for an administrative action nobody
    performs by accident.
    """
    if org_id in _seeded_rules:
        return
    _ensure()
    with connect(_STORE) as conn:
        row = _row(conn.execute(
            "SELECT rule_id FROM work_rules WHERE org_id = ? LIMIT 1", (org_id,)))
        if not row:
            conn.execute(
                "INSERT INTO work_rules (rule_id, org_id, tenant_id, behavior_glob, "
                "min_severity, priority, scenario_id, enabled, seq, created_at) "
                "VALUES (?, ?, '', '*', 'critical', 'high', '', 1, 0, ?)",
                (_new_id("rule"), org_id, _now()))
    _seeded_rules.add(org_id)


def reset_rule_cache() -> None:
    """Forget which orgs have been seeded. For tests that drop the work tables
    between cases — without this the cache would outlive the rows it describes."""
    _seeded_rules.clear()


# ── Dashboard aggregates ────────────────────────────────────────────────
#
# The operator dashboard and the supervisor's team view render counts, averages
# and a fortnight of history. Every one of those is derivable from tables that
# already exist, so nothing here adds a column — a denormalised "stats" table
# would be a second source of truth for numbers the task rows already carry, and
# the first time the two disagreed the table would be believed.
#
# These SELECT rows and let the caller shape them, rather than grouping in SQL.
# `substr(...)` date bucketing and `datetime('now')` do not survive the MySQL
# translation in `db/core.py` (see this module's header), and the row counts here
# are one operator's fortnight — small enough that Python is the cheaper place to
# be dialect-independent.

def closed_tasks_since(*, org_id: str, user_id: str, since: str,
                       limit: int = 500) -> list[dict]:
    """Everything this operator finished on or after `since`, newest first.

    `since` is an ISO-8601 string the CALLER computes — see the note above on why
    the window is not expressed in SQL.
    """
    _ensure()
    with connect(_STORE) as conn:
        return _rows(conn.execute(
            "SELECT task_id, code, title, severity, score, xp_awarded, "
            "       scenario_id, tenant_id, asset_name, status, completed_at "
            "FROM tasks WHERE org_id = ? AND assignee_id = ? "
            "AND status IN ('resolved', 'closed') AND completed_at IS NOT NULL "
            "AND completed_at >= ? ORDER BY completed_at DESC LIMIT ?",
            (org_id, user_id, since, int(limit))))


def task_severity_mix(*, org_id: str, user_id: str) -> dict[str, int]:
    """How this operator's live workload breaks down by severity.

    GROUP BY on two indexed columns is safe in both dialects — unlike the date
    bucketing above, there is no function call in the grouping key.
    """
    _ensure()
    with connect(_STORE) as conn:
        rows = _rows(conn.execute(
            "SELECT severity, COUNT(*) AS n FROM tasks "
            "WHERE org_id = ? AND assignee_id = ? "
            "AND status IN ('assigned', 'in_progress', 'blocked') "
            "GROUP BY severity", (org_id, user_id)))
    return {r["severity"]: int(r["n"]) for r in rows}


def scenario_progress(user_id: str, limit: int = 500) -> dict[str, dict]:
    """Per-procedure standing: attempts, best and last score, and any run still
    open. Drives the status chip and the Resume button on the training list.

    One query for the whole library. The alternative — a lookup per card — is
    dozens of round trips on a page that is mostly cards.
    """
    _ensure()
    with connect(_STORE) as conn:
        rows = _rows(conn.execute(
            "SELECT run_id, scenario_id, status, passed, score, started_at, "
            "       completed_at FROM scenario_runs WHERE user_id = ? "
            "ORDER BY started_at DESC LIMIT ?", (user_id, int(limit))))

    out: dict[str, dict] = {}
    for row in rows:                       # newest first, so the FIRST wins
        sid = row["scenario_id"]
        if not sid:
            continue
        entry = out.setdefault(sid, {
            "attempts": 0, "passed": False, "last_score": None,
            "best_score": None, "in_progress_run_id": "", "last_at": "",
        })
        if row["status"] == "completed":
            entry["attempts"] += 1
            score = row["score"]
            if score is not None:
                if entry["last_score"] is None:      # newest completed run
                    entry["last_score"] = float(score)
                    entry["last_at"] = row["completed_at"] or ""
                best = entry["best_score"]
                entry["best_score"] = float(score) if best is None else max(best, float(score))
            if row["passed"]:
                entry["passed"] = True
        elif not entry["in_progress_run_id"]:
            entry["in_progress_run_id"] = row["run_id"]
    return out


def throughput_since(*, org_id: str, user_ids: list[str], since: str) -> dict[str, dict]:
    """Closed count and mean score per operator since `since`.

    The supervisor's team panel renders a row per operator; this is the one query
    behind all of them.
    """
    if not user_ids:
        return {}
    _ensure()
    placeholders = ", ".join("?" for _ in user_ids)
    with connect(_STORE) as conn:
        rows = _rows(conn.execute(
            f"SELECT assignee_id, score FROM tasks "            # noqa: S608
            f"WHERE org_id = ? AND assignee_id IN ({placeholders}) "
            f"AND status IN ('resolved', 'closed') "
            f"AND completed_at IS NOT NULL AND completed_at >= ?",
            (org_id, *user_ids, since)))

    out = {uid: {"closed": 0, "avg_score": None} for uid in user_ids}
    scores: dict[str, list[float]] = {uid: [] for uid in user_ids}
    for row in rows:
        uid = row["assignee_id"]
        if uid not in out:
            continue
        out[uid]["closed"] += 1
        if row["score"] is not None:
            scores[uid].append(float(row["score"]))
    for uid, vals in scores.items():
        if vals:
            out[uid]["avg_score"] = round(sum(vals) / len(vals), 1)
    return out
