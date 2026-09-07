"""The supervisor -> operator loop, over HTTP.

`test_work_dispatch.py` covers the RULES by calling `work.service` directly.
This file covers the WIRING: that a supervisor's assignment actually arrives in
the operator's inbox through the API the browser talks to, and that the two
dashboards can read the numbers they render.

That distinction matters because every bug this file is meant to catch lives
between the service and the route — a scope parameter that is parsed but not
passed, a payload key the page reads and the server never sets, an endpoint that
authorises against the wrong persona. None of those fail a service-level test.

NO LLM IS INVOLVED. Nothing here touches the copilot agents; the work-order and
procedure endpoints are the only ones that would, and they are not exercised.
The suite therefore costs nothing to run and needs no API key.
"""

from __future__ import annotations

import uuid

import pytest
from db import schema
from identity import store as ident
from identity import tokens
from identity.passwords import hash_secret
from work import from_finding, store
from work.models import PERSONA_FRONTLINE, PERSONA_SUPERVISOR


def _bearer(user, org_id, role="write"):
    """A real session plus a signed access token for it.

    Minted directly rather than through `/auth/login` so the test does not also
    depend on the password flow, rate limiting and signup posture — those have
    their own suite.
    """
    session = ident.create_session(
        user.user_id, org_id, f"refresh-{uuid.uuid4().hex}", ttl_seconds=3600)
    token, _ = tokens.issue_access(
        user_id=user.user_id, session_id=session.session_id, org_id=org_id,
        role=role, email=user.email)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def shift():
    """One org, one supervisor, one operator on their team, one off it."""
    schema.ensure("identity")
    schema.ensure("work")

    suffix = uuid.uuid4().hex[:8]
    org = ident.create_org(name=f"Dashboard Test {suffix}")
    sup = ident.create_user(f"sup-{suffix}@test.local", hash_secret("Pw!Test12345"),
                            name="Sam Supervisor")
    op = ident.create_user(f"op-{suffix}@test.local", hash_secret("Pw!Test12345"),
                           name="Ola Operator")
    other = ident.create_user(f"other-{suffix}@test.local", hash_secret("Pw!Test12345"),
                              name="Otto Otherteam")

    # The supervisor gets the DATA role `write`, matching `work/seed.py`.
    #
    # Worth knowing, because `work/README.md` implies otherwise: it describes a
    # read-only supervisor as an ordinary account, since dispatch is supposed to
    # be governed by `persona` and not by `role`. Over HTTP that is not true
    # today — `server/auth.py` refuses every POST from a read-only principal
    # before any persona check runs, so a `read` supervisor cannot assign. The
    # seeded accounts are `write`, so the shipped product works; the gap is
    # between the documented design and the middleware, and closing it is an
    # auth-posture decision rather than a test fixture's business.
    ident.add_member(org.org_id, sup.user_id, "write", persona=PERSONA_SUPERVISOR)
    ident.add_member(org.org_id, op.user_id, "write", persona=PERSONA_FRONTLINE)
    ident.add_member(org.org_id, other.user_id, "write", persona=PERSONA_FRONTLINE)

    team = store.create_team(org_id=org.org_id, name="Shift A", supervisor_id=sup.user_id)
    store.add_team_member(org_id=org.org_id, team_id=team.team_id, user_id=op.user_id)

    tenant = f"t_{suffix}"
    ident.claim_tenant(tenant, org.org_id)

    return {
        "org": org, "tenant": tenant, "team": team,
        "sup": sup, "op": op, "other": other,
        "sup_hdr": _bearer(sup, org.org_id, "write"),
        "op_hdr": _bearer(op, org.org_id, "write"),
    }


def _raise(shift, severity="critical"):
    return from_finding.handle_finding(
        org_id=shift["org"].org_id, tenant_id=shift["tenant"],
        finding_node_id=f"fnd_{uuid.uuid4().hex[:8]}",
        behavior_id="cfp.ups_on_battery", severity=severity,
        message="UPS on battery", asset_node_id="ast_ups_1", asset_name="UPS 1")


# ── The loop the product is for ─────────────────────────────────────────

def test_a_supervisors_assignment_reaches_the_operators_inbox(api, shift):
    """The whole feature in one test: the supervisor assigns, the operator sees
    it on the page they land on. Everything else here is detail around this."""
    task = _raise(shift)

    assigned = api.post(f"/api/v1/work/tasks/{task['task_id']}/assign",
                        json={"assignee_id": shift["op"].user_id,
                              "note": "Yours tonight"},
                        headers=shift["sup_hdr"])
    assert assigned.status_code == 200, assigned.text

    inbox = api.get("/api/v1/work/inbox", headers=shift["op_hdr"])
    assert inbox.status_code == 200, inbox.text
    codes = [t["code"] for t in inbox.json()["today"]]
    assert task["code"] in codes

    # And the operator's OWN dashboard numbers move with it.
    stats = api.get("/api/v1/work/stats/me", headers=shift["op_hdr"])
    assert stats.status_code == 200, stats.text
    assert stats.json()["totals"]["open"] == 1


def test_an_operator_never_sees_another_operators_job(api, shift):
    task = _raise(shift)
    api.post(f"/api/v1/work/tasks/{task['task_id']}/assign",
             json={"assignee_id": shift["op"].user_id},
             headers=shift["sup_hdr"])

    other_hdr = _bearer(shift["other"], shift["org"].org_id, "write")
    inbox = api.get("/api/v1/work/inbox", headers=other_hdr)
    assert inbox.status_code == 200
    assert inbox.json()["today"] == []

    stats = api.get("/api/v1/work/stats/me", headers=other_hdr)
    assert stats.json()["totals"]["open"] == 0


# ── Raising work by hand ────────────────────────────────────────────────

def test_a_supervisor_can_raise_and_assign_in_one_step(api, shift):
    """The Dispatch form posts exactly this. Not every job starts as a
    detection, and before the form existed there was no way to say so."""
    created = api.post("/api/v1/work/tasks",
                       json={"tenant_id": shift["tenant"],
                             "title": "Quarterly filter change",
                             "detail": "Walk-round found it overdue",
                             "severity": "warning",
                             "asset_name": "AHU 3",
                             "assignee_id": shift["op"].user_id},
                       headers=shift["sup_hdr"])
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["status"] == "assigned"
    assert body["assignee_id"] == shift["op"].user_id

    inbox = api.get("/api/v1/work/inbox", headers=shift["op_hdr"]).json()
    assert "Quarterly filter change" in [t["title"] for t in inbox["today"]]


def test_an_operator_cannot_raise_a_job_for_themselves(api, shift):
    """Otherwise the dispatch boundary is decorative: an operator could mint
    their own work and the supervisor would never see the decision."""
    created = api.post("/api/v1/work/tasks",
                       json={"tenant_id": shift["tenant"], "title": "Self-assigned",
                             "assignee_id": shift["op"].user_id},
                       headers=shift["op_hdr"])
    assert created.status_code == 403, created.text


# ── The roster the console renders ──────────────────────────────────────

def test_roster_defaults_to_the_team_and_widens_on_request(api, shift):
    team_only = api.get("/api/v1/work/roster", headers=shift["sup_hdr"])
    assert team_only.status_code == 200, team_only.text
    ids = {o["user_id"] for o in team_only.json()["operators"]}
    assert ids == {shift["op"].user_id}

    everyone = api.get("/api/v1/work/roster?scope=all", headers=shift["sup_hdr"])
    assert everyone.status_code == 200, everyone.text
    rows = {o["user_id"]: o for o in everyone.json()["operators"]}
    assert rows[shift["op"].user_id]["assignable"] is True
    assert rows[shift["other"].user_id]["assignable"] is False


def test_an_unknown_roster_scope_is_refused(api, shift):
    """A typo must not silently fall back to the widest list."""
    resp = api.get("/api/v1/work/roster?scope=everyone", headers=shift["sup_hdr"])
    assert resp.status_code == 400


def test_the_widened_roster_does_not_widen_assignment_over_http(api, shift):
    """The route-level twin of the service test. `scope=all` is a view."""
    task = _raise(shift)
    resp = api.post(f"/api/v1/work/tasks/{task['task_id']}/assign",
                    json={"assignee_id": shift["other"].user_id},
                    headers=shift["sup_hdr"])
    assert resp.status_code == 403, resp.text


def test_an_operator_cannot_read_the_roster_or_team_stats(api, shift):
    assert api.get("/api/v1/work/roster", headers=shift["op_hdr"]).status_code == 403
    assert api.get("/api/v1/work/team-stats", headers=shift["op_hdr"]).status_code == 403


# ── The payloads the dashboards render ──────────────────────────────────

def test_operator_stats_carries_everything_the_dashboard_draws(api, shift):
    """The page reads these keys by name. A rename here is a blank card there,
    with no error anywhere — which is exactly the failure this catches."""
    body = api.get("/api/v1/work/stats/me", headers=shift["op_hdr"]).json()
    assert set(body) >= {"totals", "xp", "score", "severity_mix", "series",
                         "streak_days", "window_days", "recent"}
    assert set(body["totals"]) >= {"open", "in_progress", "blocked",
                                   "awaiting_signoff", "closed_window"}
    assert set(body["score"]) == {"avg", "best", "n"}
    assert len(body["series"]) == body["window_days"]
    assert all(set(d) == {"date", "closed", "xp"} for d in body["series"])


def test_the_stats_window_is_clamped(api, shift):
    """`days` reaches SQL as a range bound. Unbounded, a bored client asking for
    a million days turns a dashboard poll into a table scan."""
    wide = api.get("/api/v1/work/stats/me?days=9999", headers=shift["op_hdr"]).json()
    assert wide["window_days"] == 90
    narrow = api.get("/api/v1/work/stats/me?days=1", headers=shift["op_hdr"]).json()
    assert narrow["window_days"] == 7


def test_team_stats_carries_load_and_throughput(api, shift):
    task = _raise(shift)
    api.post(f"/api/v1/work/tasks/{task['task_id']}/assign",
             json={"assignee_id": shift["op"].user_id}, headers=shift["sup_hdr"])

    body = api.get("/api/v1/work/team-stats", headers=shift["sup_hdr"]).json()
    rows = {o["user_id"]: o for o in body["operators"]}
    assert rows[shift["op"].user_id]["open_tasks"] == 1
    assert set(rows[shift["op"].user_id]) >= {
        "name", "xp", "open_tasks", "assignable", "closed_window", "avg_score"}


# ── Training status ─────────────────────────────────────────────────────

def test_the_procedure_list_carries_its_standing_and_the_task_id(api, shift):
    """The training card renders a status chip from these, and its Repair with
    AI button needs the task id — which the page used to re-derive by fetching
    the inbox and matching on a code."""
    task = _raise(shift)
    api.post(f"/api/v1/work/tasks/{task['task_id']}/assign",
             json={"assignee_id": shift["op"].user_id}, headers=shift["sup_hdr"])

    body = api.get("/api/v1/scenario/procedures", headers=shift["op_hdr"]).json()
    assert body["procedures"], "the catalog should not be empty"
    for proc in body["procedures"]:
        assert set(proc) >= {"passed", "required_by", "required_task_id", "attempts",
                             "last_score", "best_score", "in_progress_run_id"}

    required = [p for p in body["procedures"] if p["required_by"] == task["code"]]
    assert required, "the assigned fault should require its procedure"
    assert required[0]["required_task_id"] == task["task_id"]
    assert required[0]["attempts"] == 0
    assert required[0]["in_progress_run_id"] == ""
