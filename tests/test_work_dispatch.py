"""test_work_dispatch.py — the fault -> supervisor -> operator -> XP loop.

What these tests are actually protecting:

  1. THE TWO AXES STAY SEPARATE. `role` (owner/admin/write/read) and `persona`
     (supervisor/frontline) answer different questions, and the bug this guards
     against is somebody "simplifying" them into one field — after which a
     read-only supervisor or a write-capable operator becomes inexpressible.

  2. AUTHORITY IS SCOPED, NOT JUST GATED. A capability check alone lets every
     supervisor dispatch every operator in the organisation. The team-scope
     tests are the ones that would catch that regression.

  3. XP CANNOT BE FARMED. A completed task must not be completable twice, and
     the ledger must refuse a duplicate award even if something upstream lets a
     second attempt through.

  4. THE ANSWER KEY NEVER REACHES THE CLIENT. If a step payload ever carries
     `correct`, every score the platform has ever produced becomes a claim the
     browser made about itself.

Each test provisions its own org, so nothing here depends on execution order.
"""

from __future__ import annotations

import uuid

import pytest
from db import schema
from identity import store as ident
from identity.passwords import hash_secret
from scenario import catalog, guided
from work import authority, service, store, xp
from work.models import (
    PERSONA_FRONTLINE,
    PERSONA_SUPERVISOR,
    can_transition,
    normalize_persona,
    severity_rank,
)


@pytest.fixture
def org_with_team():
    """One org, one supervisor, two operators — only ONE of them on the team.

    The off-team operator is the point of the fixture: without somebody a
    supervisor may NOT assign to, the scope tests would pass against a
    capability check alone.
    """
    schema.ensure("identity")
    schema.ensure("work")

    suffix = uuid.uuid4().hex[:8]
    org = ident.create_org(name=f"Test Facilities {suffix}")
    sup = ident.create_user(f"sup-{suffix}@test.local", hash_secret("Pw!Test12345"),
                            name="Supervisor")
    on_team = ident.create_user(f"op1-{suffix}@test.local", hash_secret("Pw!Test12345"),
                                name="On Team")
    off_team = ident.create_user(f"op2-{suffix}@test.local", hash_secret("Pw!Test12345"),
                                 name="Off Team")

    ident.add_member(org.org_id, sup.user_id, "read", persona=PERSONA_SUPERVISOR)
    ident.add_member(org.org_id, on_team.user_id, "write", persona=PERSONA_FRONTLINE)
    ident.add_member(org.org_id, off_team.user_id, "write", persona=PERSONA_FRONTLINE)

    team = store.create_team(org_id=org.org_id, name="Shift A",
                             supervisor_id=sup.user_id)
    store.add_team_member(org_id=org.org_id, team_id=team.team_id,
                          user_id=on_team.user_id)

    tenant = f"t_{suffix}"
    ident.claim_tenant(tenant, org.org_id)

    return {"org": org, "sup": sup, "on_team": on_team, "off_team": off_team,
            "team": team, "tenant": tenant}


def _raise_task(ctx, *, severity="critical", behavior="cfp.ups_on_battery",
                node=None):
    from work import from_finding
    return from_finding.handle_finding(
        org_id=ctx["org"].org_id, tenant_id=ctx["tenant"],
        finding_node_id=node or f"fnd_{uuid.uuid4().hex[:8]}",
        behavior_id=behavior, severity=severity,
        message="Test fault", asset_node_id="ast_1", asset_name="Asset 1")


# ── The two axes ────────────────────────────────────────────────────────
def test_role_and_persona_are_independent(org_with_team):
    """A read-only supervisor and a write-capable operator are both ordinary.

    This is the test that fails first if somebody collapses the two fields.
    """
    ctx = org_with_team
    sup = ident.get_membership(ctx["org"].org_id, ctx["sup"].user_id)
    op = ident.get_membership(ctx["org"].org_id, ctx["on_team"].user_id)

    assert sup.role == "read" and sup.persona == PERSONA_SUPERVISOR
    assert op.role == "write" and op.persona == PERSONA_FRONTLINE
    # The read-only account can still dispatch; the write account still cannot.
    assert authority.can(sup.persona, "task.assign")
    assert not authority.can(op.persona, "task.assign")


def test_unknown_persona_loses_authority():
    """A corrupt or typo'd persona must fail CLOSED."""
    assert normalize_persona("SUPERVISOR") == PERSONA_SUPERVISOR
    assert normalize_persona("boss") == PERSONA_FRONTLINE
    assert normalize_persona("") == PERSONA_FRONTLINE
    assert normalize_persona(None) == PERSONA_FRONTLINE
    assert not authority.can("boss", "task.assign")
    assert not authority.can(None, "task.assign")
    assert authority.capabilities_for("boss") == []


def test_changing_a_data_role_does_not_change_the_persona(org_with_team):
    """`add_member` is how a role change is applied, and it routes through the
    same upsert that carries the persona. Passing no persona must preserve it."""
    ctx = org_with_team
    ident.add_member(ctx["org"].org_id, ctx["sup"].user_id, "admin")
    after = ident.get_membership(ctx["org"].org_id, ctx["sup"].user_id)
    assert after.role == "admin"
    assert after.persona == PERSONA_SUPERVISOR, "role change demoted the supervisor"


def test_persona_defaults_to_frontline_for_a_new_member(org_with_team):
    ctx = org_with_team
    newbie = ident.create_user(f"new-{uuid.uuid4().hex[:8]}@test.local",
                               hash_secret("Pw!Test12345"), name="New")
    ident.add_member(ctx["org"].org_id, newbie.user_id, "write")
    assert ident.get_membership(ctx["org"].org_id,
                                newbie.user_id).persona == PERSONA_FRONTLINE


# ── Capability matrix ───────────────────────────────────────────────────
@pytest.mark.parametrize("persona,capability,expected", [
    (PERSONA_SUPERVISOR, "task.assign", True),
    (PERSONA_SUPERVISOR, "task.close", True),
    (PERSONA_SUPERVISOR, "queue.read", True),
    (PERSONA_SUPERVISOR, "scenario.run", False),
    (PERSONA_SUPERVISOR, "task.complete", False),
    (PERSONA_FRONTLINE, "task.assign", False),
    (PERSONA_FRONTLINE, "task.close", False),
    (PERSONA_FRONTLINE, "queue.read", False),
    (PERSONA_FRONTLINE, "scenario.run", True),
    (PERSONA_FRONTLINE, "task.complete", True),
])
def test_capability_matrix(persona, capability, expected):
    assert authority.can(persona, capability) is expected


def test_neither_persona_can_do_the_other_half():
    """The DEFINING actions must not overlap — if they did, the personas would be
    decoration.

    Some overlap is correct and expected (both may comment, both may read
    themselves), so this asserts on the actions that constitute each role rather
    than on the size of the intersection.
    """
    sup = set(authority.capabilities_for(PERSONA_SUPERVISOR))
    front = set(authority.capabilities_for(PERSONA_FRONTLINE))

    supervisor_only = {"task.assign", "task.unassign", "task.close", "queue.read"}
    operator_only = {"task.complete", "task.start", "scenario.run"}

    assert supervisor_only <= sup and not (supervisor_only & front)
    assert operator_only <= front and not (operator_only & sup)


# ── Scope ───────────────────────────────────────────────────────────────
def test_supervisor_reaches_only_their_own_team(org_with_team):
    ctx = org_with_team
    allowed = authority.assignable_user_ids(
        ctx["org"].org_id, ctx["sup"].user_id, PERSONA_SUPERVISOR)
    assert ctx["on_team"].user_id in allowed
    assert ctx["off_team"].user_id not in allowed


def test_a_supervisor_with_no_team_can_assign_to_nobody(org_with_team):
    """The correct failure direction: an unconfigured account is inert, not
    omnipotent."""
    ctx = org_with_team
    lonely = ident.create_user(f"lone-{uuid.uuid4().hex[:8]}@test.local",
                               hash_secret("Pw!Test12345"), name="Lonely")
    ident.add_member(ctx["org"].org_id, lonely.user_id, "admin",
                     persona=PERSONA_SUPERVISOR)
    assert authority.assignable_user_ids(
        ctx["org"].org_id, lonely.user_id, PERSONA_SUPERVISOR) == []


def test_an_operator_can_assign_to_nobody(org_with_team):
    ctx = org_with_team
    assert authority.assignable_user_ids(
        ctx["org"].org_id, ctx["on_team"].user_id, PERSONA_FRONTLINE) == []


# ── Finding -> task ─────────────────────────────────────────────────────
def test_a_critical_finding_raises_a_task(org_with_team):
    raised = _raise_task(org_with_team)
    assert raised is not None
    assert raised["status"] == "open"
    assert raised["assignee_id"] is None
    assert raised["source"] == "twin_finding"
    # The procedure is resolved at raise time so the operator's page needs no
    # lookup of its own.
    assert raised["scenario_id"] == "ups-on-battery"


def test_a_warning_finding_does_not(org_with_team):
    """The default rule is critical-only. A platform that raises a task per
    warning teaches operators to ignore tasks."""
    assert _raise_task(org_with_team, severity="warning",
                       behavior="hvac.temp_threshold") is None


def test_the_same_finding_raises_one_task(org_with_team):
    node = f"fnd_{uuid.uuid4().hex[:8]}"
    first = _raise_task(org_with_team, node=node)
    second = _raise_task(org_with_team, node=node)
    assert first is not None
    assert second is None


def test_a_closed_task_does_not_suppress_a_recurrence(org_with_team):
    """Dedup is over LIVE tasks only — the same fault next month is new work."""
    ctx = org_with_team
    node = f"fnd_{uuid.uuid4().hex[:8]}"
    first = _raise_task(ctx, node=node)
    store.update_task(first["task_id"], status="closed")
    again = _raise_task(ctx, node=node)
    assert again is not None
    assert again["task_id"] != first["task_id"]


def test_every_twin_behaviour_has_a_procedure():
    """A detected fault with no procedure leaves an operator a task they cannot
    learn from. This is the list from behaviors/cfp and behaviors/hvac."""
    behaviours = [
        "cfp.chiller_cop_baseline", "cfp.continuous_flow_leak", "cfp.door_forced",
        "cfp.filter_clogged", "cfp.generator_fuel_low", "cfp.heartbeat_loss",
        "cfp.leak_detected", "cfp.pump_vibration_baseline", "cfp.repeated_deny",
        "cfp.smoke_alarm", "cfp.tank_low_level", "cfp.transformer_over_temp",
        "cfp.ups_on_battery", "hvac.temp_threshold", "hvac.temp_zscore",
        "hvac.thermal_physics",
    ]
    unmapped = [b for b in behaviours if not catalog.scenario_id_for_behavior(b)]
    assert unmapped == [], f"no fix procedure for: {unmapped}"


# ── Assignment ──────────────────────────────────────────────────────────
def test_an_operator_cannot_assign(org_with_team):
    ctx = org_with_team
    task = _raise_task(ctx)
    with pytest.raises(service.Forbidden):
        service.assign(org_id=ctx["org"].org_id, task_id=task["task_id"],
                       assignee_id=ctx["on_team"].user_id,
                       actor_id=ctx["on_team"].user_id, actor_name="op",
                       persona=PERSONA_FRONTLINE)


def test_a_supervisor_cannot_assign_outside_their_team(org_with_team):
    ctx = org_with_team
    task = _raise_task(ctx)
    with pytest.raises(service.Forbidden):
        service.assign(org_id=ctx["org"].org_id, task_id=task["task_id"],
                       assignee_id=ctx["off_team"].user_id,
                       actor_id=ctx["sup"].user_id, actor_name="sup",
                       persona=PERSONA_SUPERVISOR)


def test_assignment_records_who_and_when(org_with_team):
    ctx = org_with_team
    task = _raise_task(ctx)
    assigned = service.assign(
        org_id=ctx["org"].org_id, task_id=task["task_id"],
        assignee_id=ctx["on_team"].user_id, actor_id=ctx["sup"].user_id,
        actor_name="sup", persona=PERSONA_SUPERVISOR, note="take this")

    assert assigned.status == "assigned"
    assert assigned.assignee_id == ctx["on_team"].user_id
    assert assigned.assigned_by == ctx["sup"].user_id
    assert assigned.assigned_at

    kinds = [e.kind for e in store.list_events(task["task_id"])]
    assert kinds == ["raised", "assigned"]


def test_a_task_from_another_org_is_not_found(org_with_team):
    """404 rather than 403: a 403 would confirm the id exists and make this an
    enumeration oracle for other tenants' work."""
    ctx = org_with_team
    task = _raise_task(ctx)
    other = ident.create_org(name=f"Other {uuid.uuid4().hex[:6]}")
    with pytest.raises(service.NotFound):
        service.assign(org_id=other.org_id, task_id=task["task_id"],
                       assignee_id=ctx["on_team"].user_id,
                       actor_id=ctx["sup"].user_id, actor_name="sup",
                       persona=PERSONA_SUPERVISOR)


# ── The operator's half ─────────────────────────────────────────────────
def _assigned_task(ctx):
    task = _raise_task(ctx)
    return service.assign(org_id=ctx["org"].org_id, task_id=task["task_id"],
                          assignee_id=ctx["on_team"].user_id,
                          actor_id=ctx["sup"].user_id, actor_name="sup",
                          persona=PERSONA_SUPERVISOR)


def _finished_run(ctx, task, *, pass_it: bool, hints: int = 0):
    """Drive a real graded run to completion and return it.

    `complete()` will not accept a score any more — it reads one from a finished
    `scenario_runs` row — so the tests have to produce the same artefact the UI
    does. Answering every step correctly scores 100; deliberately answering the
    LAST step wrong forever is not possible (a wrong answer keeps you on the
    step), so a failing run is produced by burning score on wrong answers first.
    """
    procedure = catalog.get(task.scenario_id)
    run = store.create_run(org_id=ctx["org"].org_id, user_id=ctx["on_team"].user_id,
                           task_id=task.task_id, scenario_id=procedure.id,
                           total_steps=procedure.total_steps)
    for _ in range(hints):
        run = store.get_run(run.run_id)
        store.update_run(run.run_id, **guided.hint(run, procedure)["run_fields"])

    if not pass_it:
        # Drop below the pass mark with wrong answers on the first step. Each
        # costs PENALTY_WRONG and leaves the run where it is.
        needed = int((100 - guided.PASS_SCORE) / guided.PENALTY_WRONG) + 1
        step = procedure.steps[0]
        wrong = next(o.key for o in step.options if o.key != step.correct)
        for _ in range(needed):
            run = store.get_run(run.run_id)
            store.update_run(run.run_id, **guided.answer(
                run, procedure, step_index=run.step, choice=wrong)["run_fields"])

    for i, step in enumerate(procedure.steps):
        run = store.get_run(run.run_id)
        store.update_run(run.run_id, **guided.answer(
            run, procedure, step_index=i, choice=step.correct)["run_fields"])
    return store.get_run(run.run_id)


def test_only_the_assignee_can_start(org_with_team):
    ctx = org_with_team
    task = _assigned_task(ctx)
    with pytest.raises(service.Forbidden):
        service.start(org_id=ctx["org"].org_id, task_id=task.task_id,
                      actor_id=ctx["off_team"].user_id, actor_name="other",
                      persona=PERSONA_FRONTLINE)


def test_starting_twice_is_idempotent(org_with_team):
    """The fix page calls start() on mount; a refresh must not rewrite history."""
    ctx = org_with_team
    task = _assigned_task(ctx)
    first = service.start(org_id=ctx["org"].org_id, task_id=task.task_id,
                          actor_id=ctx["on_team"].user_id, actor_name="op",
                          persona=PERSONA_FRONTLINE)
    second = service.start(org_id=ctx["org"].org_id, task_id=task.task_id,
                           actor_id=ctx["on_team"].user_id, actor_name="op",
                           persona=PERSONA_FRONTLINE)
    assert first.started_at == second.started_at


def test_an_operator_cannot_close_their_own_work(org_with_team):
    """Completion is a claim; closing is the acceptance. Rolling them together
    would make the operator the sole judge of their own work."""
    ctx = org_with_team
    task = _assigned_task(ctx)
    with pytest.raises(service.Forbidden):
        service.close(org_id=ctx["org"].org_id, task_id=task.task_id,
                      actor_id=ctx["on_team"].user_id, actor_name="op",
                      persona=PERSONA_FRONTLINE)


def test_a_failed_run_does_not_resolve_the_task(org_with_team):
    ctx = org_with_team
    task = _assigned_task(ctx)
    service.start(org_id=ctx["org"].org_id, task_id=task.task_id,
                  actor_id=ctx["on_team"].user_id, actor_name="op",
                  persona=PERSONA_FRONTLINE)
    run = _finished_run(ctx, task, pass_it=False)
    assert not run.passed, "the helper was meant to produce a failing run"

    outcome = service.complete(
        org_id=ctx["org"].org_id, task_id=task.task_id,
        actor_id=ctx["on_team"].user_id, actor_name="op",
        persona=PERSONA_FRONTLINE, run_id=run.run_id)
    assert outcome["task"].status == "in_progress"
    assert outcome["awarded"] == xp.CONSOLATION


# ── XP ──────────────────────────────────────────────────────────────────
def test_a_task_cannot_be_completed_twice(org_with_team):
    ctx = org_with_team
    task = _assigned_task(ctx)
    service.start(org_id=ctx["org"].org_id, task_id=task.task_id,
                  actor_id=ctx["on_team"].user_id, actor_name="op",
                  persona=PERSONA_FRONTLINE)
    run = _finished_run(ctx, task, pass_it=True)
    first = service.complete(
        org_id=ctx["org"].org_id, task_id=task.task_id,
        actor_id=ctx["on_team"].user_id, actor_name="op",
        persona=PERSONA_FRONTLINE, run_id=run.run_id)
    assert first["awarded"] > 0

    with pytest.raises(service.WorkError):
        service.complete(org_id=ctx["org"].org_id, task_id=task.task_id,
                         actor_id=ctx["on_team"].user_id, actor_name="op",
                         persona=PERSONA_FRONTLINE, run_id=run.run_id)


def test_the_ledger_refuses_a_duplicate_award(org_with_team):
    """The backstop under the status guard: even a direct second write is
    refused, by the database rather than by a check-then-write race."""
    ctx = org_with_team
    task = _assigned_task(ctx)
    first = store.add_xp(org_id=ctx["org"].org_id, user_id=ctx["on_team"].user_id,
                         points=50, reason="task:passed", task_id=task.task_id)
    second = store.add_xp(org_id=ctx["org"].org_id, user_id=ctx["on_team"].user_id,
                          points=999, reason="task:passed", task_id=task.task_id)
    assert first is not None
    assert second is None
    assert store.total_xp(ctx["on_team"].user_id) == 50


def test_a_harder_fault_is_worth_more():
    critical, _ = xp.award_for(severity="critical", score=90, passed=True)
    warning, _ = xp.award_for(severity="warning", score=90, passed=True)
    assert critical > warning


def test_a_cleaner_run_is_worth_more():
    clean, _ = xp.award_for(severity="critical", score=100, passed=True, hints_used=0)
    scraped, _ = xp.award_for(severity="critical", score=70, passed=True, hints_used=4)
    assert clean > scraped


def test_finishing_a_losing_run_beats_abandoning_it():
    """CONSOLATION is non-zero on purpose — see the note in work/xp.py."""
    failed, _ = xp.award_for(severity="critical", score=30, passed=False)
    assert failed > 0


def test_levels_are_monotonic_and_explainable():
    assert xp.level_for(0) == 0
    assert xp.level_for(99) == 0
    assert xp.level_for(100) == 1
    assert xp.level_for(300) == 2
    # Each level costs 100 more than the last.
    assert xp.threshold_for(1) == 100
    assert xp.threshold_for(2) == 300
    assert xp.threshold_for(3) == 600
    last = -1
    for total in range(0, 5000, 37):
        level = xp.level_for(total)
        assert level >= last
        last = level


def test_progress_bar_never_exceeds_its_track():
    for total in (0, 1, 99, 100, 299, 300, 1234, 9999):
        p = xp.progress(total)
        assert 0 <= p["into_level"] <= p["level_span"]
        assert 0 <= p["pct"] <= 100
        assert p["title"]


# ── Guided runs ─────────────────────────────────────────────────────────
def test_a_step_payload_never_carries_the_answer():
    """If this fails, every score the platform has produced is worthless."""
    for procedure in catalog.all_procedures():
        for step in procedure.steps:
            public = step.public()
            assert "correct" not in public, f"{procedure.id}/{step.id} leaked the answer"
            assert "rationale" not in public
            for option in public["options"]:
                assert set(option) == {"key", "label"}


def test_the_debrief_does_reveal_the_answers():
    """After the run, revealing is the point of the exercise."""
    procedure = catalog.all_procedures()[0]
    revealed = procedure.steps[0].public(reveal=True)
    assert revealed["correct"] == procedure.steps[0].correct


def test_every_procedure_is_answerable():
    """Each step's `correct` must name an option that actually exists — a typo
    here makes a procedure impossible to finish."""
    for procedure in catalog.all_procedures():
        assert procedure.steps, f"{procedure.id} has no steps"
        for step in procedure.steps:
            keys = [o.key for o in step.options]
            assert step.correct in keys, f"{procedure.id}/{step.id}: bad answer key"
            assert len(keys) == len(set(keys)), f"{procedure.id}/{step.id}: duplicate keys"
            assert len(keys) >= 2, f"{procedure.id}/{step.id}: needs distractors"
            # Every option explains itself — the wrong ones carry the teaching.
            for option in step.options:
                assert option.why.strip(), f"{procedure.id}/{step.id}/{option.key}: no why"


def test_scoring_is_deterministic_and_only_decreases():
    assert guided.score_for(wrong_steps=0, hints_used=0) == 100.0
    assert guided.score_for(wrong_steps=0, hints_used=0) == guided.score_for(
        wrong_steps=0, hints_used=0)
    assert guided.score_for(wrong_steps=1, hints_used=0) == 100 - guided.PENALTY_WRONG
    assert guided.score_for(wrong_steps=0, hints_used=1) == 100 - guided.PENALTY_HINT
    # Never negative, however badly it went.
    assert guided.score_for(wrong_steps=99, hints_used=99) == 0.0


def test_a_perfect_run_scores_100_and_passes(org_with_team):
    ctx = org_with_team
    task = _assigned_task(ctx)
    procedure = catalog.get(task.scenario_id)
    run = store.create_run(org_id=ctx["org"].org_id, user_id=ctx["on_team"].user_id,
                           task_id=task.task_id, scenario_id=procedure.id,
                           total_steps=procedure.total_steps)
    for i, step in enumerate(procedure.steps):
        run = store.get_run(run.run_id)
        outcome = guided.answer(run, procedure, step_index=i, choice=step.correct)
        store.update_run(run.run_id, **outcome["run_fields"])

    run = store.get_run(run.run_id)
    assert run.status == "completed"
    assert run.score == 100.0
    assert run.passed


def test_a_stale_answer_is_refused(org_with_team):
    """A double-submitted form must not be graded against a step the run has
    already moved past."""
    ctx = org_with_team
    task = _assigned_task(ctx)
    procedure = catalog.get(task.scenario_id)
    run = store.create_run(org_id=ctx["org"].org_id, user_id=ctx["on_team"].user_id,
                           task_id=task.task_id, scenario_id=procedure.id,
                           total_steps=procedure.total_steps)
    outcome = guided.answer(run, procedure, step_index=0,
                            choice=procedure.steps[0].correct)
    store.update_run(run.run_id, **outcome["run_fields"])
    run = store.get_run(run.run_id)

    with pytest.raises(guided.RunError):
        guided.answer(run, procedure, step_index=0,
                      choice=procedure.steps[0].correct)


def test_a_wrong_answer_keeps_the_operator_on_the_step(org_with_team):
    """The goal is that they learn the procedure, not that they are ranked on
    one guess."""
    ctx = org_with_team
    task = _assigned_task(ctx)
    procedure = catalog.get(task.scenario_id)
    run = store.create_run(org_id=ctx["org"].org_id, user_id=ctx["on_team"].user_id,
                           task_id=task.task_id, scenario_id=procedure.id,
                           total_steps=procedure.total_steps)
    step = procedure.steps[0]
    wrong = next(o.key for o in step.options if o.key != step.correct)

    outcome = guided.answer(run, procedure, step_index=0, choice=wrong)
    assert outcome["correct"] is False
    assert outcome["advanced"] is False
    store.update_run(run.run_id, **outcome["run_fields"])

    run = store.get_run(run.run_id)
    assert run.step == 0
    assert run.wrong_steps == 1


def test_a_finished_run_cannot_be_answered(org_with_team):
    ctx = org_with_team
    task = _assigned_task(ctx)
    procedure = catalog.get(task.scenario_id)
    run = store.create_run(org_id=ctx["org"].org_id, user_id=ctx["on_team"].user_id,
                           task_id=task.task_id, scenario_id=procedure.id,
                           total_steps=procedure.total_steps)
    for i, step in enumerate(procedure.steps):
        run = store.get_run(run.run_id)
        store.update_run(run.run_id, **guided.answer(
            run, procedure, step_index=i, choice=step.correct)["run_fields"])

    run = store.get_run(run.run_id)
    with pytest.raises(guided.RunError):
        guided.answer(run, procedure, step_index=run.step, choice="anything")


def test_an_unfinished_run_is_resumable(org_with_team):
    """A locked phone mid-procedure must not reset the score."""
    ctx = org_with_team
    task = _assigned_task(ctx)
    procedure = catalog.get(task.scenario_id)
    run = store.create_run(org_id=ctx["org"].org_id, user_id=ctx["on_team"].user_id,
                           task_id=task.task_id, scenario_id=procedure.id,
                           total_steps=procedure.total_steps)
    store.update_run(run.run_id, **guided.answer(
        run, procedure, step_index=0, choice=procedure.steps[0].correct)["run_fields"])

    resumed = store.active_run_for_task(user_id=ctx["on_team"].user_id,
                                        task_id=task.task_id)
    assert resumed is not None
    assert resumed.run_id == run.run_id
    assert resumed.step == 1


# ── Lifecycle ───────────────────────────────────────────────────────────
def test_a_closed_task_is_terminal():
    assert not can_transition("closed", "in_progress")
    assert not can_transition("closed", "assigned")
    assert can_transition("resolved", "closed")
    assert can_transition("open", "assigned")
    assert not can_transition("open", "resolved")


def test_unknown_severity_ranks_lowest():
    """A severity we cannot interpret must not escalate into critical work."""
    assert severity_rank("nonsense") == 0
    assert severity_rank("") == 0
    assert severity_rank("critical") > severity_rank("warning")


# ── Seeding ─────────────────────────────────────────────────────────────
def test_seeded_accounts_can_actually_log_in():
    """The seed's whole purpose is two accounts somebody can sign in as.

    This asserts on `service.login`, not on the rows, because the bug it guards
    against is invisible at the row level: hashing a password with `hash_secret`
    (sha256, for API keys) instead of `hash_password` (argon2) creates an account
    that looks completely normal in every listing and can never authenticate.
    Every other test here works with store objects directly and would pass.
    """
    from identity import service as identity_service
    from work.seed import seed

    suffix = uuid.uuid4().hex[:8]
    password = f"Seed-Test-{suffix}-Pw!9"
    result = seed(org_name=f"Seed Test {suffix}", tenant=f"t_{suffix}",
                  domain=f"{suffix}.test", password=password)

    for user, expected in ((result["supervisor"], PERSONA_SUPERVISOR),
                           (result["operator"], PERSONA_FRONTLINE)):
        identity_service.login(email=user.email, password=password)
        membership = ident.get_membership(result["org"].org_id, user.user_id)
        assert membership.persona == expected

    with pytest.raises(identity_service.AuthFailed):
        identity_service.login(email=result["operator"].email, password="wrong")


def test_seeding_twice_does_not_duplicate_anything():
    """Re-running against a seeded database is safe — it is the demo-reset path."""
    from work.seed import seed

    suffix = uuid.uuid4().hex[:8]
    org_name = f"Seed Idem {suffix}"
    first = seed(org_name=org_name, tenant=f"t_{suffix}", domain=f"{suffix}.test")
    second = seed(org_name=org_name, tenant=f"t_{suffix}", domain=f"{suffix}.test")

    assert first["org"].org_id == second["org"].org_id
    assert first["team"].team_id == second["team"].team_id
    assert first["supervisor"].user_id == second["supervisor"].user_id
    # A second run reports no password, because it created no account.
    assert second["supervisor_password"] is None

    members = store.member_ids_of_teams(org_id=first["org"].org_id,
                                        team_ids=[first["team"].team_id])
    assert members.count(first["operator"].user_id) == 1


def test_the_seeded_supervisor_can_dispatch_to_the_seeded_operator():
    """The seed's output must be a WORKING pair — the team wiring is the part
    that is easy to get wrong and looks identical to a broken feature."""
    from work.seed import seed

    suffix = uuid.uuid4().hex[:8]
    result = seed(org_name=f"Seed Flow {suffix}", tenant=f"t_{suffix}",
                  domain=f"{suffix}.test")
    org_id = result["org"].org_id

    allowed = authority.assignable_user_ids(
        org_id, result["supervisor"].user_id, PERSONA_SUPERVISOR)
    assert result["operator"].user_id in allowed

    from work import from_finding
    raised = from_finding.handle_finding(
        org_id=org_id, tenant_id=result["tenant"],
        finding_node_id=f"fnd_{suffix}", behavior_id="cfp.ups_on_battery",
        severity="critical", message="Seeded fault", asset_node_id="a1",
        asset_name="A1")
    assert raised is not None, "the seeded tenant did not dispatch"

    assigned = service.assign(
        org_id=org_id, task_id=raised["task_id"],
        assignee_id=result["operator"].user_id,
        actor_id=result["supervisor"].user_id, actor_name="sup",
        persona=PERSONA_SUPERVISOR)
    assert assigned.assignee_id == result["operator"].user_id


# ── The score cannot be asserted by the caller ──────────────────────────
def test_a_task_with_a_procedure_cannot_be_completed_without_a_run(org_with_team):
    """The hole this closes: `complete` used to take `score` and `passed` from
    the request body, so a hand-rolled POST could claim 100% on a procedure it
    never opened."""
    ctx = org_with_team
    task = _assigned_task(ctx)
    service.start(org_id=ctx["org"].org_id, task_id=task.task_id,
                  actor_id=ctx["on_team"].user_id, actor_name="op",
                  persona=PERSONA_FRONTLINE)
    with pytest.raises(service.WorkError) as exc:
        service.complete(org_id=ctx["org"].org_id, task_id=task.task_id,
                         actor_id=ctx["on_team"].user_id, actor_name="op",
                         persona=PERSONA_FRONTLINE, passed=True)
    assert "procedure" in str(exc.value).lower()


def test_an_unfinished_run_cannot_be_cashed_in(org_with_team):
    ctx = org_with_team
    task = _assigned_task(ctx)
    procedure = catalog.get(task.scenario_id)
    run = store.create_run(org_id=ctx["org"].org_id, user_id=ctx["on_team"].user_id,
                           task_id=task.task_id, scenario_id=procedure.id,
                           total_steps=procedure.total_steps)
    with pytest.raises(service.WorkError):
        service.complete(org_id=ctx["org"].org_id, task_id=task.task_id,
                         actor_id=ctx["on_team"].user_id, actor_name="op",
                         persona=PERSONA_FRONTLINE, run_id=run.run_id)


def test_one_operator_cannot_cite_anothers_run(org_with_team):
    """A run id alone would otherwise be enough to borrow somebody's perfect score."""
    ctx = org_with_team
    task = _assigned_task(ctx)
    run = _finished_run(ctx, task, pass_it=True)

    # The off-team operator gets the task reassigned to them, then tries to
    # complete it by citing the first operator's finished run.
    service.assign(org_id=ctx["org"].org_id, task_id=task.task_id,
                   assignee_id=ctx["off_team"].user_id, actor_id=ctx["sup"].user_id,
                   actor_name="sup", persona=PERSONA_SUPERVISOR) \
        if False else None   # assign is team-scoped; set the row directly instead
    store.update_task(task.task_id, assignee_id=ctx["off_team"].user_id,
                      status="in_progress")

    with pytest.raises(service.NotFound):
        service.complete(org_id=ctx["org"].org_id, task_id=task.task_id,
                         actor_id=ctx["off_team"].user_id, actor_name="other",
                         persona=PERSONA_FRONTLINE, run_id=run.run_id)


def test_the_score_recorded_is_the_runs_own(org_with_team):
    """Whatever the caller believes, the task's score is the run's."""
    ctx = org_with_team
    task = _assigned_task(ctx)
    run = _finished_run(ctx, task, pass_it=True, hints=1)

    outcome = service.complete(
        org_id=ctx["org"].org_id, task_id=task.task_id,
        actor_id=ctx["on_team"].user_id, actor_name="op",
        persona=PERSONA_FRONTLINE, run_id=run.run_id, passed=False)

    # `passed=False` was supplied and correctly ignored — the run passed.
    assert outcome["task"].status == "resolved"
    assert outcome["task"].score == run.score
    assert run.score < 100, "a hint should have cost score"


def test_a_task_with_no_procedure_is_completed_on_the_operators_word(org_with_team):
    """And pays a flat base award, never more than a scored run would."""
    ctx = org_with_team
    raised = _raise_task(ctx)
    store.update_task(raised["task_id"], scenario_id="")     # nothing published
    service.assign(org_id=ctx["org"].org_id, task_id=raised["task_id"],
                   assignee_id=ctx["on_team"].user_id, actor_id=ctx["sup"].user_id,
                   actor_name="sup", persona=PERSONA_SUPERVISOR)
    service.start(org_id=ctx["org"].org_id, task_id=raised["task_id"],
                  actor_id=ctx["on_team"].user_id, actor_name="op",
                  persona=PERSONA_FRONTLINE)

    outcome = service.complete(
        org_id=ctx["org"].org_id, task_id=raised["task_id"],
        actor_id=ctx["on_team"].user_id, actor_name="op",
        persona=PERSONA_FRONTLINE, passed=True)

    assert outcome["task"].status == "resolved"
    assert outcome["task"].score is None
    base = xp.BASE_BY_SEVERITY["critical"]
    assert outcome["awarded"] == base

    best_scored, _ = xp.award_for(severity="critical", score=100, passed=True,
                                  hints_used=0, attempt=1)
    assert outcome["awarded"] <= best_scored, (
        "skipping the procedure must never pay better than running it")


# ── The dashboard aggregates ────────────────────────────────────────────
#
# These back the operator's dashboard (which absorbed the old Progress page) and
# the supervisor's team panel. The risk they cover is not arithmetic — it is
# SCOPE. Every one of these numbers is per-operator or per-org, and a dropped
# filter here leaks one person's record onto another's screen while still
# looking like a plausible dashboard.

def _resolved_task(ctx):
    """Drive one task all the way to `resolved` and return it."""
    task = _assigned_task(ctx)
    service.start(org_id=ctx["org"].org_id, task_id=task.task_id,
                  actor_id=ctx["on_team"].user_id, actor_name="op",
                  persona=PERSONA_FRONTLINE)
    run = _finished_run(ctx, task, pass_it=True)
    service.complete(org_id=ctx["org"].org_id, task_id=task.task_id,
                     actor_id=ctx["on_team"].user_id, actor_name="op",
                     persona=PERSONA_FRONTLINE, run_id=run.run_id)
    return store.get_task(task.task_id)


def test_roster_team_scope_hides_the_off_team_operator(org_with_team):
    ctx = org_with_team
    people = service.roster(org_id=ctx["org"].org_id, actor_id=ctx["sup"].user_id,
                            persona=PERSONA_SUPERVISOR, scope="team")
    ids = {p["user_id"] for p in people}
    assert ctx["on_team"].user_id in ids
    assert ctx["off_team"].user_id not in ids
    assert all(p["assignable"] for p in people)


def test_roster_all_scope_shows_them_but_marks_them_unassignable(org_with_team):
    """The widening is a VIEW, not authority. A supervisor balancing a shift
    needs to see who is buried; they still may not dispatch outside their team."""
    ctx = org_with_team
    people = service.roster(org_id=ctx["org"].org_id, actor_id=ctx["sup"].user_id,
                            persona=PERSONA_SUPERVISOR, scope="all")
    by_id = {p["user_id"]: p for p in people}
    assert by_id[ctx["on_team"].user_id]["assignable"] is True
    assert by_id[ctx["off_team"].user_id]["assignable"] is False


def test_the_widened_roster_does_not_widen_the_assign_endpoint(org_with_team):
    """The regression this exists to catch: someone reads `scope=all` as the new
    source of truth for assignment and drops the team check."""
    ctx = org_with_team
    task = _raise_task(ctx)
    with pytest.raises(service.Forbidden):
        service.assign(org_id=ctx["org"].org_id, task_id=task["task_id"],
                       assignee_id=ctx["off_team"].user_id,
                       actor_id=ctx["sup"].user_id, actor_name="sup",
                       persona=PERSONA_SUPERVISOR)


def test_a_supervisor_is_not_in_the_all_roster(org_with_team):
    """You do not dispatch a fault to another supervisor."""
    ctx = org_with_team
    people = service.roster(org_id=ctx["org"].org_id, actor_id=ctx["sup"].user_id,
                            persona=PERSONA_SUPERVISOR, scope="all")
    assert ctx["sup"].user_id not in {p["user_id"] for p in people}


def test_an_operator_cannot_read_the_roster(org_with_team):
    ctx = org_with_team
    with pytest.raises(service.Forbidden):
        service.roster(org_id=ctx["org"].org_id, actor_id=ctx["on_team"].user_id,
                       persona=PERSONA_FRONTLINE, scope="all")


def test_operator_stats_counts_only_this_operators_work(org_with_team):
    ctx = org_with_team
    _resolved_task(ctx)

    mine = service.operator_stats(org_id=ctx["org"].org_id,
                                  user_id=ctx["on_team"].user_id)
    theirs = service.operator_stats(org_id=ctx["org"].org_id,
                                    user_id=ctx["off_team"].user_id)

    assert mine["totals"]["closed_window"] == 1
    assert mine["score"]["avg"] is not None
    assert mine["xp"]["total_xp"] > 0
    # The off-team operator did none of this and must see none of it.
    assert theirs["totals"]["closed_window"] == 0
    assert theirs["score"]["avg"] is None


def test_operator_stats_series_spans_the_whole_window(org_with_team):
    """The chart draws one bar per day. A series that only carried days with
    activity would render a bar chart with no gaps and silently lie about pace."""
    ctx = org_with_team
    stats = service.operator_stats(org_id=ctx["org"].org_id,
                                   user_id=ctx["on_team"].user_id, days=14)
    assert len(stats["series"]) == 14
    assert stats["series"] == sorted(stats["series"], key=lambda d: d["date"])
    assert all(set(d) == {"date", "closed", "xp"} for d in stats["series"])


def test_a_resolved_task_is_not_counted_as_open_work(org_with_team):
    """`resolved` means the operator is done and the supervisor is not. Counting
    it as open is what had operators chasing jobs they had already finished."""
    ctx = org_with_team
    _resolved_task(ctx)
    stats = service.operator_stats(org_id=ctx["org"].org_id,
                                   user_id=ctx["on_team"].user_id)
    assert stats["totals"]["open"] == 0
    assert stats["totals"]["in_progress"] == 0
    assert stats["totals"]["awaiting_signoff"] == 1


def test_scenario_progress_records_attempts_and_scores(org_with_team):
    ctx = org_with_team
    task = _resolved_task(ctx)
    progress = store.scenario_progress(ctx["on_team"].user_id)
    standing = progress[task.scenario_id]
    assert standing["attempts"] == 1
    assert standing["passed"] is True
    assert standing["last_score"] == 100.0
    assert standing["in_progress_run_id"] == ""


def test_an_unfinished_run_is_reported_as_in_progress(org_with_team):
    """The Resume button is drawn from this field. An open run must not look
    like an untouched procedure, which is also `last_score is None`."""
    ctx = org_with_team
    task = _assigned_task(ctx)
    procedure = catalog.get(task.scenario_id)
    run = store.create_run(org_id=ctx["org"].org_id, user_id=ctx["on_team"].user_id,
                           task_id=task.task_id, scenario_id=procedure.id,
                           total_steps=procedure.total_steps)
    standing = store.scenario_progress(ctx["on_team"].user_id)[procedure.id]
    assert standing["in_progress_run_id"] == run.run_id
    assert standing["attempts"] == 0


def test_team_stats_is_supervisor_only(org_with_team):
    ctx = org_with_team
    with pytest.raises(service.Forbidden):
        service.team_stats(org_id=ctx["org"].org_id, actor_id=ctx["on_team"].user_id,
                           persona=PERSONA_FRONTLINE)


def test_team_stats_reports_throughput_per_operator(org_with_team):
    ctx = org_with_team
    _resolved_task(ctx)
    data = service.team_stats(org_id=ctx["org"].org_id, actor_id=ctx["sup"].user_id,
                              persona=PERSONA_SUPERVISOR)
    by_id = {o["user_id"]: o for o in data["operators"]}
    assert by_id[ctx["on_team"].user_id]["closed_window"] == 1
    assert by_id[ctx["on_team"].user_id]["avg_score"] == 100.0
    assert by_id[ctx["off_team"].user_id]["closed_window"] == 0
