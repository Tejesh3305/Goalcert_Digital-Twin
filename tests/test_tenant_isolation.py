"""
test_tenant_isolation.py — the regression suite for the cross-tenant hole.

WHAT WAS BROKEN
---------------
Tenant enforcement lived in one line of AuthMiddleware and read only the QUERY
string. The tenant actually arrives three ways, and two of them were unchecked:

    query  /api/v1/entities?tenant=victim              was checked
    path   /api/v1/twins/victim/state                  NOT checked
    body   POST /api/v1/copilot/diagnosis {"tenant":…} NOT checked

So a read-only key scoped to one tenant could read another tenant's live twin
state, diagnostics and physics projection, drive its throttle, inject faults
into it, and run every LLM agent against its telemetry.

WHY THE SWEEP TEST MATTERS MOST
-------------------------------
`test_every_scoped_route_denies_a_foreign_tenant` does not enumerate routes by
hand. It asks the app which routes carry a tenant identifier and then attacks
every one of them. A route added next month is attacked automatically. A
hand-written list is how the original hole survived: enforcement was per-route,
so each new route silently opted out.

The assertions are all "is 403", never "is 200". With no database running the
authorized paths legitimately answer 503-or-degraded, and pinning them to 200
would make this file a database test instead of an authorization test. What
matters is that a foreign tenant is refused BEFORE any store is touched.
"""

from __future__ import annotations

import pytest
from conftest import (
    KEY_ACME,
    KEY_ACME_MULTI,
    KEY_ACME_PREFIX,
    KEY_ADMIN,
    KEY_READER,
    hdr,
)

VICTIM = "victim-tenant"

# Server-Sent-Events endpoints are excluded from the generic sweep: the client
# reads a streaming body to completion, so if enforcement regressed the test
# would hang rather than fail. They are asserted explicitly in
# `test_sse_stream_denies_foreign_tenant`, which reads with `stream=True`.
_STREAMING = {"/api/v1/bus/stream"}


def _scoped_routes(app):
    from server.tenancy import audit_route_coverage
    return [r for r in audit_route_coverage(app)["scoped"]
            if r["path"] not in _STREAMING]


def _fill_path(path: str, tenant: str) -> str:
    """Substitute a foreign tenant for {tenant} and a placeholder for the rest."""
    out = path.replace("{tenant}", tenant).replace("{tenant_id}", tenant)
    while "{" in out:
        head, _, rest = out.partition("{")
        name, _, tail = rest.partition("}")
        out = f"{head}placeholder-{name.replace('_', '-')}{tail}"
    return out


def _call(api, method: str, path: str, tenant: str, key: str):
    """Attack one route with a foreign tenant on every carrier it might use."""
    url = _fill_path(path, tenant)
    params = {"tenant": tenant}
    body = {"tenant": tenant}
    headers = hdr(key)
    if method == "GET":
        return api.get(url, params=params, headers=headers)
    if method == "DELETE":
        return api.delete(url, params=params, headers=headers)
    return api.request(method, url, params=params, json=body, headers=headers)


# ── The sweep ───────────────────────────────────────────────────────────────


def test_scoped_route_inventory_is_not_empty(app):
    """Guard the guard: if the audit stops finding tenant carriers, the sweep
    below would pass while attacking nothing."""
    routes = _scoped_routes(app)
    assert len(routes) >= 40, f"only {len(routes)} scoped routes found"
    carriers = {c.split(":")[0] for r in routes for c in r["carriers"]}
    assert carriers == {"path", "query", "body"}, (
        f"expected all three tenant carriers to be represented, got {carriers}")


def test_every_scoped_route_denies_a_foreign_tenant(app, api):
    """THE regression test. Every tenant-carrying route must 403 a foreign
    tenant, whichever carrier it uses."""
    failures = []
    for route in _scoped_routes(app):
        for method in route["methods"]:
            if method in ("HEAD", "OPTIONS"):
                continue
            resp = _call(api, method, route["path"], VICTIM, KEY_ACME)
            if resp.status_code != 403:
                failures.append(
                    f"{method} {route['path']} ({','.join(route['carriers'])}) "
                    f"-> {resp.status_code} {resp.text[:120]}")
    assert not failures, (
        "these routes did NOT deny a foreign tenant:\n  " + "\n  ".join(failures))


def test_denial_precedes_body_validation(api):
    """A 403 must win over a 422.

    If validation ran first, an attacker would learn a route's schema — and more
    importantly the handler could run before authorization. This passes because
    the global dependency is resolved before body params are validated; it is
    pinned here because that ordering is a FastAPI internal, not a promise.
    """
    resp = api.post("/api/v1/copilot/diagnosis",
                    json={"tenant": VICTIM, "horizon_label": 12345},
                    headers=hdr(KEY_ACME))
    assert resp.status_code == 403, resp.text


def test_sse_stream_denies_foreign_tenant(api):
    with api.stream("GET", "/api/v1/bus/stream",
                    params={"tenant": VICTIM}, headers=hdr(KEY_ACME)) as resp:
        assert resp.status_code == 403


# ── The specific paths that were exploitable ────────────────────────────────


@pytest.mark.parametrize("path", [
    "/api/v1/twins/{t}/state",
    "/api/v1/twins/{t}/diagnostics",
    "/api/v1/twins/{t}/predict",
    "/api/v1/twins/{t}/network",
    "/api/v1/twins/{t}",
])
def test_path_param_routes_were_the_hole(api, path):
    """These leaked another tenant's live physics to any authenticated key."""
    assert api.get(path.format(t=VICTIM),
                   headers=hdr(KEY_ACME)).status_code == 403


@pytest.mark.parametrize("path", [
    "/api/v1/twins/{t}/running",
    "/api/v1/twins/{t}/simulate",
    "/api/v1/twins/{t}/project",
])
def test_path_param_control_routes_were_the_hole(api, path):
    """Worse than disclosure: these CONTROL another tenant's twin — start/stop
    its physics and inject faults into it."""
    assert api.post(path.format(t=VICTIM), json={},
                    headers=hdr(KEY_ACME)).status_code == 403


@pytest.mark.parametrize("path", [
    "/api/v1/copilot/diagnosis",
    "/api/v1/copilot/analysis",
    "/api/v1/copilot/cascade",
    "/api/v1/copilot/work-order",
    "/api/v1/copilot/dashboard-chat",
    "/api/v1/predict",
])
def test_body_tenant_routes_were_the_hole(api, path):
    """Every LLM agent accepted a tenant in its body and read that twin's live
    diagnostics — billing our Anthropic key to read someone else's data."""
    assert api.post(path, json={"tenant": VICTIM},
                    headers=hdr(KEY_ACME)).status_code == 403


def test_nested_body_tenant_is_found(api):
    """A tenant nested inside the payload must still be authorized — otherwise
    moving the field one level down silently bypasses enforcement."""
    resp = api.post("/api/v1/copilot/dashboard-chat",
                    json={"snapshot": {"context": {"tenant": VICTIM}}},
                    headers=hdr(KEY_ACME))
    assert resp.status_code == 403, resp.text


def test_write_api_body_tenant_denied(api):
    resp = api.post("/api/v1/entities",
                    json={"tenant": VICTIM,
                          "canonical_type": "https://ontology.nextxr.io/v3/core#Site"},
                    headers=hdr(KEY_ACME))
    assert resp.status_code == 403, resp.text


# ── The authorized side: scope must not over-refuse ─────────────────────────


def _not_denied(resp):
    """Authorized calls may legitimately 404/503/degrade with no stores running.
    What they must never be is 401/403."""
    assert resp.status_code not in (401, 403), \
        f"authorized call was refused: {resp.status_code} {resp.text[:200]}"


def test_own_tenant_is_allowed(api):
    _not_denied(api.get("/api/v1/twins/acme/state", headers=hdr(KEY_ACME)))
    _not_denied(api.get("/api/v1/stats", params={"tenant": "acme"},
                        headers=hdr(KEY_ACME)))
    _not_denied(api.post("/api/v1/copilot/diagnosis", json={"tenant": "acme"},
                         headers=hdr(KEY_ACME)))


def test_admin_reaches_every_tenant(api):
    _not_denied(api.get(f"/api/v1/twins/{VICTIM}/state", headers=hdr(KEY_ADMIN)))
    _not_denied(api.get("/api/v1/stats", params={"tenant": VICTIM},
                        headers=hdr(KEY_ADMIN)))


def test_multi_tenant_key_reaches_all_its_tenants(api):
    """The scope shape that did not exist before: one enterprise customer
    holding several twins WITHOUT being granted every other customer's data."""
    for tenant in ("acme", "acme-two"):
        _not_denied(api.get(f"/api/v1/twins/{tenant}/state",
                            headers=hdr(KEY_ACME_MULTI)))
    assert api.get(f"/api/v1/twins/{VICTIM}/state",
                   headers=hdr(KEY_ACME_MULTI)).status_code == 403


def test_prefix_key_reaches_its_subtree_only(api):
    for tenant in ("acme-plant-1", "acme-plant-2", "acme-"):
        _not_denied(api.get(f"/api/v1/twins/{tenant}/state",
                            headers=hdr(KEY_ACME_PREFIX)))
    for tenant in (VICTIM, "globex-plant-1", "acm", "notacme-1"):
        assert api.get(f"/api/v1/twins/{tenant}/state",
                       headers=hdr(KEY_ACME_PREFIX)).status_code == 403, tenant


def test_read_only_key_cannot_mutate_even_its_own_tenant(api):
    assert api.post("/api/v1/twins/acme/running", json={},
                    headers=hdr(KEY_READER)).status_code == 403


# ── Hub-forwarded identity: narrows, never widens ──────────────────────────


def test_hub_org_header_narrows_an_admin_key(api):
    """The Hub owns login and org membership and forwards the caller's org. A
    header may only REDUCE what the key already permits."""
    resp = api.get(f"/api/v1/twins/{VICTIM}/state",
                   headers=hdr(KEY_ADMIN, **{"X-Goalcert-Org": "acme"}))
    assert resp.status_code == 403, resp.text
    _not_denied(api.get("/api/v1/twins/acme-plant-1/state",
                        headers=hdr(KEY_ADMIN, **{"X-Goalcert-Org": "acme"})))


def test_hub_org_header_cannot_widen_a_scoped_key(api):
    """The escalation that must not work: a narrow key claiming a bigger org."""
    for org in ("victim", "victim-tenant", "*", "../victim", "globex"):
        resp = api.get(f"/api/v1/twins/{VICTIM}/state",
                       headers=hdr(KEY_ACME, **{"X-Goalcert-Org": org}))
        assert resp.status_code == 403, f"org={org!r} widened scope: {resp.text[:160]}"


def test_hub_org_header_is_sanitised(api):
    """A hostile org value must not smuggle comparison-breaking syntax into the
    prefix it becomes."""
    for org in ("acme/../victim", "acme%2f", "acme'; --", "ACME"):
        resp = api.get(f"/api/v1/twins/{VICTIM}/state",
                       headers=hdr(KEY_ADMIN, **{"X-Goalcert-Org": org}))
        assert resp.status_code == 403, f"org={org!r}: {resp.text[:160]}"


# ── Listings: no tenant named, so the route must filter ────────────────────


def test_twin_listing_is_scope_filtered(api):
    """`GET /api/v1/twins` named no tenant, so the dependency has nothing to
    authorize — it returned every tenant's twin to any key, which made it the
    fastest way to enumerate the whole platform."""
    resp = api.get("/api/v1/twins", headers=hdr(KEY_ACME))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "hidden_by_scope" in body, "listing is not applying a scope filter"
    for twin in body["twins"]:
        assert twin["tenant_id"] in ("acme",), \
            f"listing leaked twin {twin['tenant_id']!r} to an acme-scoped key"


def test_listing_response_does_not_name_other_tenants(api):
    """`hidden_by_scope` must be a COUNT. Returning the ids it hid would make
    the filter an enumeration oracle instead of a control."""
    body = api.get("/api/v1/twins", headers=hdr(KEY_ACME)).json()
    assert isinstance(body["hidden_by_scope"], int)


# ── Provisioning: scope decides the id it cannot yet check ─────────────────


def test_fixed_scope_key_cannot_create_twins(api):
    """A key with a fixed tenant set has no id it could be given that its own
    static scope would later allow — so creating would produce a twin the
    creator cannot read. Refuse explicitly instead of succeeding uselessly."""
    resp = api.post("/api/v1/twins", json={"name": "New Plant", "domain": "blank"},
                    headers=hdr(KEY_ACME))
    assert resp.status_code == 403, resp.text
    assert "tenant_prefix" in resp.json()["detail"]


def test_error_body_does_not_leak_other_tenants(api):
    """A 403 must name only what the caller already put on the request."""
    detail = api.get(f"/api/v1/twins/{VICTIM}/state",
                     headers=hdr(KEY_ACME)).json()["detail"]
    assert VICTIM in detail
    assert "acme-two" not in detail and "globex" not in detail


# ── Authentication still behaves ───────────────────────────────────────────


def test_missing_key_is_401_not_500(api):
    """A raised HTTPException in outer middleware becomes a 500 with a
    traceback; auth.py returns a response instead. Pinned so that never
    regresses — it once turned every unauthenticated call into "server crash".

    The assertion is on the STATUS and on the response being actionable, not on
    the exact wording. There are now two credential types, so the message names
    both; pinning the old literal string tested the copy rather than the
    behaviour, and would fail again the next time a word changed.
    """
    resp = api.get("/api/v1/stats", params={"tenant": "acme"})
    assert resp.status_code == 401
    detail = resp.json()["detail"]
    assert "X-API-Key" in detail and "Bearer" in detail
    # A 401 must tell the client which scheme to use, or a CLI cannot recover.
    assert resp.headers.get("WWW-Authenticate", "").startswith("Bearer")


def test_no_default_demo_key_exists(api):
    """The image once shipped with `nxr-demo-key` as a working ADMIN credential
    whenever NXR_API_KEYS was unset. Anyone who read the source had root on any
    deployment that forgot to configure keys."""
    for guess in ("nxr-demo-key", "nxr-read-only"):
        resp = api.get("/api/v1/stats", params={"tenant": "acme"},
                       headers=hdr(guess))
        assert resp.status_code == 401, f"{guess} still authenticates"


def test_invalid_key_is_401(api):
    assert api.get("/api/v1/stats", params={"tenant": "acme"},
                   headers=hdr("not-a-real-key")).status_code == 401


def test_health_stays_public(api):
    """Platform health probes cannot send a key; gating them makes the
    orchestrator restart a healthy task in a loop."""
    assert api.get("/api/v1/health").status_code == 200
    assert api.get("/api/v1/copilot/health").status_code == 200


def test_schema_api_needs_a_key_but_no_tenant(api):
    assert api.get("/api/v1/schema/types").status_code == 401
    _not_denied(api.get("/api/v1/schema/types", headers=hdr(KEY_READER)))
