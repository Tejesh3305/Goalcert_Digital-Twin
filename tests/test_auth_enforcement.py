"""test_auth_enforcement.py — the API must be CLOSED, and the headers must be there.

WHAT THIS FILE EXISTS TO PREVENT
--------------------------------
`server/auth.py` used to serve any request without a credential whenever
`NXR_API_KEYS` was unset. That is the correct default for a laptop and a serious
one for a deployment: the service was open the moment someone forgot an
environment variable, including the `/copilot` endpoints that bill our Anthropic
key. There was a loud warning at boot, and a warning is not a control.

The default is now inverted — `auth_required()` returns True unless a deployment
explicitly opts out. These tests pin that inversion, the escape hatch, and the
handful of paths that must stay reachable without a credential (health probes,
and the login endpoint itself, which is how a caller OBTAINS one).

`test_default_posture_is_closed` is the one that matters most. It reads the
policy function directly with a cleared environment, because everything else in
this suite runs with `NXR_REQUIRE_AUTH=1` set by conftest — so a regression of
the DEFAULT would not fail any other test here.
"""

from __future__ import annotations

import pytest
from conftest import KEY_ADMIN, KEY_READER, hdr

# ── The default posture ─────────────────────────────────────────────────


@pytest.fixture
def clean_env(monkeypatch):
    """An environment with every auth switch removed, so the DEFAULT is what is
    being observed rather than conftest's configuration.

    The teardown re-reads NXR_API_KEYS, and that is load-bearing rather than
    tidiness. `auth._key_store` is MODULE-LEVEL state: a test that calls
    `reload_keys()` with the variable deleted leaves every legacy key
    unresolvable for the rest of the session, and the later tests fail with 401
    where they assert 403 — an ordering-dependent failure that looks like an
    authorization bug and is not.
    """
    for var in ("NXR_REQUIRE_AUTH", "NXR_DEV_MODE", "NXR_API_KEYS"):
        monkeypatch.delenv(var, raising=False)
    yield monkeypatch

    monkeypatch.undo()
    from server import auth
    auth.reload_keys()


def test_default_posture_is_closed(clean_env):
    """THE REGRESSION TEST FOR THE FAIL-OPEN HOLE.

    With nothing configured at all, authentication must be REQUIRED. The previous
    behaviour — open — meant every deployment that forgot a variable served
    customer data to anyone who found the URL.
    """
    from server.auth import auth_required

    assert auth_required() is True, (
        "the API defaults to OPEN again — an unconfigured deployment now serves "
        "every /api request without a credential")


def test_dev_mode_is_the_only_implicit_escape_hatch(clean_env):
    from server.auth import auth_required

    clean_env.setenv("NXR_DEV_MODE", "1")
    assert auth_required() is False


def test_explicit_setting_wins_over_dev_mode(clean_env):
    """`NXR_REQUIRE_AUTH` is the most specific signal, so it beats NXR_DEV_MODE in
    both directions. A developer running with dev mode on who explicitly asks for
    enforcement should get it."""
    from server.auth import auth_required

    clean_env.setenv("NXR_DEV_MODE", "1")
    clean_env.setenv("NXR_REQUIRE_AUTH", "1")
    assert auth_required() is True

    clean_env.setenv("NXR_REQUIRE_AUTH", "0")
    assert auth_required() is False


def test_no_default_credentials_are_compiled_in(clean_env):
    """The image once shipped with `nxr-demo-key` as a working ADMIN credential
    whenever NXR_API_KEYS was unset — anyone who read the source had root on any
    deployment that forgot to configure keys."""
    from server import auth

    auth.reload_keys()
    assert auth._key_store == {}, (
        f"default credentials are compiled in again: "
        f"{sorted(auth._key_store)}")


def test_malformed_key_config_yields_no_keys(clean_env):
    """Broken JSON must fail CLOSED and must not raise. Raising happens inside
    middleware construction, which surfaces as a 500 with a traceback on every
    request rather than an obvious configuration error."""
    from server import auth

    clean_env.setenv("NXR_API_KEYS", "{not valid json")
    auth.reload_keys()
    assert auth._key_store == {}


# ── What is refused ─────────────────────────────────────────────────────


@pytest.mark.parametrize("path", [
    "/api/v1/twins",
    "/api/v1/stats?tenant=acme",
    "/api/v1/entities?tenant=acme",
    "/api/v1/schema/types",
    "/api/v1/twins/acme/state",
    "/api/v1/agents/twin/some-session",
    "/api/v1/auth/me",
])
def test_unauthenticated_requests_are_refused(api, path):
    assert api.get(path).status_code == 401


def test_the_llm_surface_is_not_reachable_without_a_credential(api):
    """`/copilot` bills our Anthropic key per call. Open, it is an unbounded
    invoice payable by anyone who finds the URL — the single most expensive
    consequence of the old fail-open default."""
    for path in ("/api/v1/copilot/diagnosis", "/api/v1/copilot/dashboard-chat"):
        resp = api.post(path, json={"tenant": "acme"})
        assert resp.status_code == 401, f"{path} answered {resp.status_code}"


def test_a_401_names_both_credential_schemes(api):
    resp = api.get("/api/v1/twins")
    assert resp.status_code == 401
    detail = resp.json()["detail"]
    assert "Bearer" in detail and "X-API-Key" in detail
    assert resp.headers.get("WWW-Authenticate", "").startswith("Bearer")


def test_an_invalid_credential_is_refused(api):
    assert api.get("/api/v1/twins", headers=hdr("nope")).status_code == 401
    assert api.get("/api/v1/twins",
                   headers={"Authorization": "Bearer not-a-token"}).status_code == 401


def test_a_bad_bearer_token_does_not_fall_through_to_anonymous(api):
    """A SUPPLIED but invalid credential must be a 401, not silently treated as
    anonymous. Otherwise a client with an expired session believes it is signed
    in and sees empty data instead of a prompt to sign in again."""
    resp = api.get("/api/v1/auth/me", headers={"Authorization": "Bearer garbage"})
    assert resp.status_code == 401


# ── What stays open, and why ────────────────────────────────────────────


@pytest.mark.parametrize("path", [
    "/api/v1/health",
    "/api/v1/health/live",
    "/api/v1/health/ready",
    "/api/v1/copilot/health",
])
def test_health_probes_need_no_credential(api, path):
    """An ALB target group and the Docker HEALTHCHECK cannot send one. Gating
    these makes the orchestrator declare a working service unhealthy and restart
    it in a loop."""
    assert api.get(path).status_code in (200, 503)


def test_liveness_is_cheap_and_always_200(api):
    """`/health/live` must touch NO dependency — it is the probe whose only
    remedy is a restart, and a database outage is not fixed by restarting every
    task at once."""
    resp = api.get("/api/v1/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "alive"}


def test_readiness_reports_its_checks(api):
    resp = api.get("/api/v1/health/ready")
    assert resp.status_code in (200, 503)
    assert "checks" in resp.json()


@pytest.mark.parametrize("path", [
    "/api/v1/auth/login",
    "/api/v1/auth/signup",
    "/api/v1/auth/refresh",
    "/api/v1/auth/password/forgot",
])
def test_the_credential_issuing_endpoints_are_reachable(api, path):
    """These are how a caller OBTAINS a credential, so requiring one is circular.

    The assertion is on the middleware's refusal specifically, not on any 401.
    `/auth/refresh` legitimately answers 401 to an empty body — "no refresh token
    supplied" is the endpoint working, and rejecting every 401 here would forbid
    it from ever saying so.
    """
    resp = api.post(path, json={})
    detail = str(resp.json().get("detail", "")) if resp.headers.get(
        "content-type", "").startswith("application/json") else ""

    assert "Authentication required" not in detail, (
        f"{path} is gated by AuthMiddleware, so nobody can ever sign in")
    assert resp.status_code != 403


def test_the_spa_shell_is_public(api):
    """A non-API GET serves the client-router shell. Requiring auth here means
    the login page itself 401s."""
    assert api.get("/").status_code == 200
    assert api.get("/some/client/route").status_code == 200


# ── Role enforcement ────────────────────────────────────────────────────


def test_a_read_credential_cannot_mutate(api):
    assert api.post("/api/v1/entities", json={"tenant": "acme", "id": "x"},
                    headers=hdr(KEY_READER)).status_code == 403


def test_pure_function_posts_are_allowed_to_a_read_credential(api):
    """A short, explicit allow-list, not a heuristic. An analyst with read access
    should be able to run the PV model against a datasheet without being handed a
    credential that can also delete twins."""
    resp = api.post("/api/v1/solar/model/evaluate",
                    json={"irradiance": 800, "cell_temp_c": 45},
                    headers=hdr(KEY_READER))
    assert resp.status_code != 403


def test_self_service_endpoints_are_reachable_by_a_read_user(api):
    """A `read` member must still be able to change their own password. Treating
    every POST as a mutation would refuse it — the check is on the ROLE's reach
    over tenant data, not over the caller's own account."""
    resp = api.post("/api/v1/auth/password/change",
                    json={"current_password": "x", "new_password": "y"},
                    headers=hdr(KEY_READER))
    # 403 for "an API key is not a user" is correct; 403 for "read-only" is not.
    assert resp.status_code in (401, 403, 422)
    if resp.status_code == 403:
        assert "read-only" not in resp.json()["detail"].lower()


# ── Security headers ────────────────────────────────────────────────────


def test_security_headers_are_present(api):
    resp = api.get("/api/v1/health")

    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "Referrer-Policy" in resp.headers
    assert "Permissions-Policy" in resp.headers


def test_the_spa_gets_a_content_security_policy(api):
    csp = api.get("/").headers.get("Content-Security-Policy", "")

    assert "default-src 'self'" in csp
    # The directive that actually stops XSS. Styles carry 'unsafe-inline'
    # (the 3-D viewer needs it); scripts must not.
    assert "script-src 'self'" in csp
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
    assert "frame-ancestors 'none'" in csp


def test_auth_responses_are_never_cached(api):
    resp = api.post("/api/v1/auth/login",
                    json={"email": "x@example.com", "password": "wrong-long-pw"})
    assert resp.headers.get("Cache-Control") == "no-store"


def test_every_response_carries_a_request_id(api):
    resp = api.get("/api/v1/health")
    assert resp.headers.get("X-Request-ID")


def test_an_inbound_request_id_is_honoured(api):
    """An ALB or gateway trace id must survive, so one identifier correlates the
    whole path rather than just our slice of it."""
    resp = api.get("/api/v1/health", headers={"X-Request-ID": "trace-abc-123"})
    assert resp.headers.get("X-Request-ID") == "trace-abc-123"


def test_a_hostile_request_id_is_sanitised(api):
    """It is echoed in a header and written to logs, so an unbounded
    attacker-controlled string is both log injection and per-line bloat."""
    resp = api.get("/api/v1/health",
                   headers={"X-Request-ID": "a" * 500 + "\r\nX-Injected: yes"})
    echoed = resp.headers.get("X-Request-ID", "")

    assert len(echoed) <= 64
    assert "\n" not in echoed and "\r" not in echoed
    assert "X-Injected" not in resp.headers


# ── Body size ───────────────────────────────────────────────────────────


def test_an_oversized_body_is_refused(api):
    """`enforce_tenant_scope` reads request bodies to find tenant identifiers, so
    an unbounded body is memory an anonymous caller allocates on our task."""
    from server.security import max_body_bytes

    resp = api.post("/api/v1/entities",
                    headers={**hdr(KEY_ADMIN),
                             "Content-Length": str(max_body_bytes() + 1)},
                    content=b"{}")
    assert resp.status_code == 413


# ── CORS ────────────────────────────────────────────────────────────────


def test_cors_is_not_wide_open_in_production(monkeypatch):
    """With auth enforced and nothing configured, the answer is SAME-ORIGIN
    (an empty list) — not `*`. The container serves the SPA and the API from one
    origin, so that is both correct and needs no configuration."""
    from server import security

    monkeypatch.delenv("NXR_CORS_ORIGINS", raising=False)
    monkeypatch.setenv("NXR_REQUIRE_AUTH", "1")

    assert security.cors_origins() == []


def test_cors_allows_everything_only_when_auth_is_off(monkeypatch):
    from server import security

    monkeypatch.delenv("NXR_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("NXR_REQUIRE_AUTH", raising=False)
    monkeypatch.setenv("NXR_DEV_MODE", "1")

    assert security.cors_origins() == ["*"]


def test_credentials_are_never_combined_with_a_wildcard(monkeypatch):
    """The CORS spec forbids it, and Starlette silently drops one of the two —
    a confusing way to discover the refresh cookie never arrives."""
    from server import security

    monkeypatch.setenv("NXR_CORS_ORIGINS", "*")
    assert security.cors_allow_credentials() is False

    monkeypatch.setenv("NXR_CORS_ORIGINS", "https://app.example.com")
    assert security.cors_allow_credentials() is True


# ── Secrets are not compiled in ─────────────────────────────────────────


def test_the_neo4j_dev_password_is_not_a_production_fallback(monkeypatch):
    """It is published in this repository, so it is a fixture rather than a
    credential. Falling back to it meant a deployment that forgot the variable
    authenticated to its production graph with a password anyone can read."""
    from graph import connection

    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    monkeypatch.setenv("NXR_REQUIRE_AUTH", "1")

    with pytest.raises(RuntimeError, match="NEO4J_PASSWORD"):
        connection._default_password()


def test_the_dev_password_is_still_available_locally(monkeypatch):
    from graph import connection

    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    monkeypatch.setenv("NXR_REQUIRE_AUTH", "0")

    assert connection._default_password() == connection._DEV_PASSWORD


def test_an_ephemeral_jwt_key_fails_the_boot(monkeypatch):
    """Each task would sign with its own random key, so a token minted by one is
    rejected by every other — intermittent 401s under a load balancer, which
    reads as a client bug and is miserable to trace to a missing variable."""
    from identity import tokens

    monkeypatch.delenv("NXR_JWT_SECRET", raising=False)
    monkeypatch.setenv("NXR_REQUIRE_AUTH", "1")

    with pytest.raises(RuntimeError, match="NXR_JWT_SECRET"):
        tokens.require_secret()


def test_a_short_jwt_key_is_reported_as_weak(monkeypatch):
    from identity import tokens

    monkeypatch.setenv("NXR_JWT_SECRET", "short")
    assert tokens.secret_is_weak() is True

    monkeypatch.setenv("NXR_JWT_SECRET", "x" * 64)
    assert tokens.secret_is_weak() is False
