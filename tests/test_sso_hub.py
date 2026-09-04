"""test_sso_hub.py — Goalcert Hub single sign-on.

WHAT THIS SUITE IS FOR
----------------------
The callback turns a bearer credential minted by ANOTHER system into a full
session here. That makes it the highest-leverage endpoint in the app: everything
else checks a credential this app issued, and this one accepts a credential it
did not. Each test below is named for the failure it prevents rather than the
function it calls, because most of them look like over-testing until you know
which specific hole they close.

  contract order      a check skipped, or done in an order that leaks which
                      guess was close
  algorithm pinning   `alg: none`, the oldest JWT forgery there is
  audience            a ticket minted for the Scenario Engine signing someone
                      into the Twin, using the same shared secret
  replay              one ticket, two sessions
  provisioning        a Hub user who has no account here getting one anyway
  open redirect       a signed `rt` sending the browser off-site, already
                      signed in
  session parity      an SSO session that is subtly not a real session

The end-to-end tests deliberately drive the REAL app through a TestClient rather
than calling the handler, because two of the bugs this feature can have —
the auth middleware refusing the POST form, and the SPA catch-all swallowing the
route — are invisible to a direct call and fatal in a browser.
"""

from __future__ import annotations

import time
import uuid

import jwt
import pytest

HUB_SECRET = "hub-sso-shared-secret-at-least-32-characters-long"
ISSUER = "https://hub.goal-cert.com"
AUDIENCE = "goalcert-twin"
PASSWORD = "correct-horse-battery-staple"


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def sso_env(monkeypatch):
    """Configure Hub SSO. `sso/config.py` reads the environment on every call,
    so this takes effect without rebuilding the app."""
    monkeypatch.setenv("NXR_SSO_HUB_SECRET", HUB_SECRET)
    monkeypatch.setenv("NXR_SSO_HUB_ISSUER", ISSUER)
    monkeypatch.setenv("NXR_SSO_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("NXR_SSO_DEFAULT_REDIRECT", "/")
    return True


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def account(client):
    """A real local user, created the ordinary way."""
    email = f"sso-{uuid.uuid4().hex[:10]}@example.com"
    resp = client.post("/api/v1/auth/signup", json={
        "email": email, "password": PASSWORD, "name": "SSO User",
        "org_name": f"Org {uuid.uuid4().hex[:6]}",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    return {"email": email, "user_id": data["user"]["user_id"],
            "org_id": data["org_id"]}


def mint(*, secret: str = HUB_SECRET, alg: str = "HS256", **overrides) -> str:
    """A Hub ticket. Overrides replace claims; passing None deletes one."""
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": f"hub-user-{uuid.uuid4().hex[:12]}",
        "jti": uuid.uuid4().hex,
        "iat": now,
        "nbf": now,
        "exp": now + 60,
        "email": "someone@example.com",
        "username": "someone",
        "name": "Some One",
        "role": "admin",
        "org_id": "hub-org-1",
        "rt": "/",
    }
    for key, value in overrides.items():
        if value is None:
            claims.pop(key, None)
        else:
            claims[key] = value
    return jwt.encode(claims, secret, algorithm=alg)


# ── The verifier: the six checks, and their order ───────────────────────


def test_a_good_ticket_verifies(sso_env):
    from sso import verifier

    ticket = verifier.verify(mint(email="a@example.com", rt="/twins"))
    assert ticket.email == "a@example.com"
    assert ticket.rt == "/twins"
    assert ticket.sub


def test_a_forged_signature_is_refused(sso_env):
    from sso import verifier

    with pytest.raises(verifier.TicketError) as e:
        verifier.verify(mint(secret="not-the-hub-secret-but-still-long-enough"))
    assert e.value.reason == "bad_signature"


def test_alg_none_is_refused(sso_env):
    """The oldest JWT forgery there is. Without `algorithms=["HS256"]` pinned in
    the verifier, an unsigned token with a forged header verifies trivially."""
    from sso import verifier

    now = int(time.time())
    unsigned = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "attacker", "jti": uuid.uuid4().hex,
         "iat": now, "nbf": now, "exp": now + 60, "email": "attacker@example.com"},
        key="", algorithm="none")

    with pytest.raises(verifier.TicketError):
        verifier.verify(unsigned)


def test_an_expired_ticket_is_refused(sso_env):
    from sso import verifier

    now = int(time.time())
    with pytest.raises(verifier.TicketError) as e:
        # Well past the configured leeway.
        verifier.verify(mint(iat=now - 600, nbf=now - 600, exp=now - 300))
    assert e.value.reason == "expired"


def test_a_ticket_from_the_future_is_refused(sso_env):
    from sso import verifier

    now = int(time.time())
    with pytest.raises(verifier.TicketError) as e:
        verifier.verify(mint(nbf=now + 600, exp=now + 900))
    assert e.value.reason == "not_yet_valid"


def test_a_ticket_with_no_expiry_is_refused(sso_env):
    """A 60-second credential with `exp` stripped is a permanent one."""
    from sso import verifier

    with pytest.raises(verifier.TicketError) as e:
        verifier.verify(mint(exp=None))
    assert e.value.reason == "missing_claim"


def test_a_lookalike_issuer_is_refused(sso_env):
    """Exact equality, not a prefix test — the impostor below starts with the
    real issuer string."""
    from sso import verifier

    with pytest.raises(verifier.TicketError) as e:
        verifier.verify(mint(iss="https://hub.goal-cert.com.evil.example"))
    assert e.value.reason == "bad_issuer"


def test_a_ticket_for_another_app_is_refused(sso_env):
    """THE audience test. Scenario Engine and AUTOMIND Hive are signed with the
    SAME shared secret, so `aud` is the only thing keeping their tickets out."""
    from sso import verifier

    with pytest.raises(verifier.TicketError) as e:
        verifier.verify(mint(aud="goalcert-scenario"))
    assert e.value.reason == "bad_audience"


def test_an_audience_list_containing_us_is_accepted(sso_env):
    """`aud` may be a string or a list (RFC 7519)."""
    from sso import verifier

    ticket = verifier.verify(mint(aud=["goalcert-scenario", AUDIENCE]))
    assert ticket.sub


def test_a_ticket_works_exactly_once(sso_env):
    from sso import verifier

    token = mint()
    verifier.verify(token)
    with pytest.raises(verifier.TicketError) as e:
        verifier.verify(token)
    assert e.value.reason == "replayed"


def test_a_ticket_without_a_subject_is_refused(sso_env):
    from sso import verifier

    with pytest.raises(verifier.TicketError) as e:
        verifier.verify(mint(sub=None))
    assert e.value.reason == "missing_claim"


def test_tickets_are_refused_when_sso_is_unconfigured(monkeypatch):
    from sso import verifier

    monkeypatch.delenv("NXR_SSO_HUB_SECRET", raising=False)
    with pytest.raises(verifier.TicketError) as e:
        verifier.verify(mint())
    assert e.value.reason == "sso_disabled"


def test_sharing_the_apps_own_jwt_secret_disables_sso(monkeypatch):
    """The Hub's ticket key is shared with every satellite app. If this app's own
    signing key equalled it, any of those apps could forge an access token for
    this one — so the collision refuses tickets rather than merely warning."""
    from identity.tokens import signing_secret
    from sso import config

    monkeypatch.setenv("NXR_SSO_HUB_SECRET", signing_secret())
    assert config.secret_collides() is True
    assert config.enabled() is False


# ── Redirect targets ────────────────────────────────────────────────────


@pytest.mark.parametrize("target", [
    "/", "/twins", "/twins/acme?tab=live", "/a/b/c#frag",
])
def test_relative_targets_are_allowed(target):
    from sso import redirect

    assert redirect.is_safe(target) is True


@pytest.mark.parametrize("target", [
    "//evil.example/path",                 # protocol-relative
    "https://evil.example/path",           # absolute
    "http://evil.example",
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "/\\evil.example",                     # backslash reads as a slash in browsers
    "\\\\evil.example",
    "twins",                               # not rooted
    "/twins\r\nSet-Cookie: a=b",           # header injection
    "/twins\nLocation: https://evil.example",
    "",
])
def test_off_site_and_malformed_targets_are_rejected(target):
    from sso import redirect

    assert redirect.is_safe(target) is False


def test_the_callback_is_not_a_landing_page():
    """Redirecting back to the callback would replay a spent ticket and bounce
    the user to the login page — a loop that looks like a broken login."""
    from sso import redirect

    assert redirect.is_safe("/sso/hub/callback") is False


def test_an_unsafe_default_falls_back_to_root():
    from sso import redirect

    assert redirect.resolve("https://evil.example", "https://also-evil.example") == "/"


# ── Identity resolution ─────────────────────────────────────────────────


def test_an_unknown_hub_user_gets_no_account(sso_env, client):
    """The Hub vouches for identity; it does not provision accounts here."""
    from sso import resolver, verifier

    ticket = verifier.verify(mint(email=f"nobody-{uuid.uuid4().hex[:8]}@example.com"))
    with pytest.raises(resolver.ResolutionError) as e:
        resolver.resolve(ticket)
    assert e.value.reason == "no_local_account"


def test_the_first_visit_links_by_email_and_later_visits_use_the_sub(
        sso_env, client, account):
    """Email is the bootstrap; `sub` is the permanent key. The second ticket
    below carries a DIFFERENT email — as it would after the person changed it at
    the Hub, or after their old address was reassigned — and must still resolve
    to the same local user."""
    from sso import resolver, verifier

    sub = f"hub-user-{uuid.uuid4().hex[:12]}"

    first = resolver.resolve(verifier.verify(mint(sub=sub, email=account["email"])))
    assert first.user.user_id == account["user_id"]
    assert first.matched_by == "email"
    assert first.linked is True

    second = resolver.resolve(
        verifier.verify(mint(sub=sub, email="changed-address@example.com")))
    assert second.user.user_id == account["user_id"]
    assert second.matched_by == "hub_sub"
    assert second.linked is False


def test_a_second_hub_account_cannot_claim_a_linked_user(sso_env, client, account):
    """Two Hub subjects, one local email. Picking either is a guess, so refuse."""
    from sso import resolver, verifier

    resolver.resolve(verifier.verify(mint(email=account["email"])))

    with pytest.raises(resolver.ResolutionError) as e:
        resolver.resolve(verifier.verify(mint(email=account["email"])))
    assert e.value.reason == "user_already_linked"


def test_sso_is_not_a_way_around_a_disabled_account(sso_env, client, account):
    """`identity.service.login()` checks account state before issuing a session.
    SSO calls `_issue_session` directly, so it has to make the same check — or
    disabling someone would stop their password working and leave SSO open."""
    from identity import store
    from sso import resolver, verifier

    sub = f"hub-user-{uuid.uuid4().hex[:12]}"
    resolver.resolve(verifier.verify(mint(sub=sub, email=account["email"])))

    store.update_user(account["user_id"], status="disabled")

    with pytest.raises(resolver.ResolutionError) as e:
        resolver.resolve(verifier.verify(mint(sub=sub, email=account["email"])))
    assert e.value.reason == "account_disabled"


# ── End to end, through the real app ────────────────────────────────────


def test_a_valid_ticket_signs_the_browser_in(sso_env, client, account):
    resp = client.get("/sso/hub/callback",
                      params={"token": mint(email=account["email"], rt="/twins")},
                      follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/twins"
    assert "nxr_refresh" in resp.cookies, "no refresh cookie — nothing was started"


def test_the_post_form_works(sso_env, client, account):
    """The Hub PREFERS POST, and a non-API POST is not public by default in
    `server/auth.py`. Without `_PUBLIC_SSO_PATHS` this 401s while the query-param
    fallback still works — a bug that only appears on the safer path."""
    resp = client.post("/sso/hub/callback",
                       data={"token": mint(email=account["email"])},
                       follow_redirects=False)

    assert resp.status_code == 303, f"got {resp.status_code}: {resp.text[:300]}"
    assert "nxr_refresh" in resp.cookies


def test_the_session_it_starts_is_a_real_one(sso_env, client, account):
    """Session parity: the cookie the callback sets must be redeemable at
    /auth/refresh, because that is exactly what the SPA does on boot."""
    signed_in = client.get("/sso/hub/callback",
                           params={"token": mint(email=account["email"])},
                           follow_redirects=False)
    assert signed_in.status_code == 303

    # The TestClient keeps the cookie jar, as a browser would.
    resumed = client.post("/api/v1/auth/refresh")
    assert resumed.status_code == 200, resumed.text
    body = resumed.json()
    assert body["user"]["email"] == account["email"]
    assert body["access_token"]

    who = client.get("/api/v1/auth/me",
                     headers={"Authorization": f"Bearer {body['access_token']}"})
    assert who.status_code == 200
    assert who.json()["user"]["email"] == account["email"]


def test_an_off_site_redirect_target_is_ignored(sso_env, client, account):
    """A signed `rt` is still not a trusted one — an open redirect here would
    launder its credibility through our domain, with the victim already signed
    in when they land."""
    resp = client.get("/sso/hub/callback",
                      params={"token": mint(email=account["email"],
                                            rt="https://evil.example/steal")},
                      follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


@pytest.mark.parametrize("token,label", [
    ("", "no token"),
    ("not-a-jwt", "garbage"),
])
def test_failures_land_on_the_login_page_not_a_500(sso_env, client, token, label):
    resp = client.get("/sso/hub/callback", params={"token": token},
                      follow_redirects=False)

    assert resp.status_code == 303, f"{label} produced {resp.status_code}"
    assert resp.headers["location"].startswith("/login")
    assert "nxr_refresh" not in resp.cookies


def test_a_failure_reveals_nothing_about_why(sso_env, client, account):
    """An unauthenticated caller who learns the audience was wrong has been told
    which guess was close. The detail belongs in the log, not the redirect."""
    resp = client.get("/sso/hub/callback",
                      params={"token": mint(email=account["email"],
                                            aud="goalcert-scenario")},
                      follow_redirects=False)

    assert resp.status_code == 303
    location = resp.headers["location"]
    assert location == "/login?sso=failed"
    for leak in ("aud", "audience", "scenario", "signature", "jti"):
        assert leak not in location


def test_a_replayed_ticket_does_not_sign_anyone_in_twice(sso_env, client, account):
    token = mint(email=account["email"])

    first = client.get("/sso/hub/callback", params={"token": token},
                       follow_redirects=False)
    assert first.status_code == 303
    assert first.headers["location"] == "/"

    client.cookies.clear()

    second = client.get("/sso/hub/callback", params={"token": token},
                        follow_redirects=False)
    assert second.headers["location"].startswith("/login")
    assert "nxr_refresh" not in second.cookies
