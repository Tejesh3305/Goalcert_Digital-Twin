"""test_identity.py — accounts, sessions, roles and machine credentials.

WHAT THIS SUITE IS FOR
----------------------
The platform had no users. Its only credential was a JSON blob in an environment
variable, so there was no login, no signup, no revocation without a redeploy, and
the change log's `actor` column pointed at nothing. `identity/` is that
subsystem, and these are the properties it has to hold.

They are grouped by the failure each one prevents, because several are
non-obvious and would look like over-testing without the reason:

  password storage      a hash that can be reversed, or one that cannot be
                        upgraded without a forced reset
  login                 user enumeration by message OR by timing
  sessions              a stolen refresh token that keeps working
  organisations         one customer reaching another's data or account
  API keys              a secret that can be read back, or survives revocation
  roles                 privilege escalation through the role ladder

Every test uses its OWN TestClient and its own temporary state. `conftest.api` is
a session-scoped client whose whole purpose is asserting on the legacy env-key
path; sharing it would let one test's org and sessions leak into another's
assertions, and the ordering-dependent failures that produces are the worst kind
to debug.
"""

from __future__ import annotations

import time

import pytest

PASSWORD = "correct-horse-battery-staple"


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def client(app):
    """A fresh TestClient with an empty cookie jar.

    Fresh matters: `TestClient` persists cookies, and the refresh cookie is what
    /auth/refresh prefers over the request body. A shared client would make a
    token-replay test silently exercise the NEW cookie instead of the replayed
    token — which is precisely the mistake that makes a reuse-detection test
    pass while detecting nothing.
    """
    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def account(client):
    """A signed-up user, their org, and an owner session."""
    import uuid

    email = f"user-{uuid.uuid4().hex[:10]}@example.com"
    resp = client.post("/api/v1/auth/signup", json={
        "email": email, "password": PASSWORD, "name": "Test User",
        "org_name": f"Org {uuid.uuid4().hex[:6]}",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    return {
        "email": email,
        "password": PASSWORD,
        "token": data["access_token"],
        "refresh": data["refresh_token"],
        "org_id": data["org_id"],
        "user_id": data["user"]["user_id"],
        "headers": {"Authorization": f"Bearer {data['access_token']}"},
    }


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Password storage ────────────────────────────────────────────────────


def test_password_is_never_stored_in_plaintext(account):
    from identity import store

    stored = store.get_password_hash(account["user_id"])
    assert stored
    assert PASSWORD not in stored
    # Self-describing, so the algorithm can be upgraded without a forced reset.
    assert stored.split("$")[0] in ("argon2id", "scrypt", "pbkdf2_sha256")


def test_password_hash_is_salted():
    """Two identical passwords must not produce identical hashes. Without a salt,
    one rainbow table breaks every account that shares a password."""
    from identity.passwords import hash_password

    assert hash_password(PASSWORD) != hash_password(PASSWORD)


def test_every_supported_scheme_verifies():
    """A hash written by an older build must still open. This is what makes the
    algorithm upgradable rather than a flag day."""
    import base64
    import hashlib

    from identity.passwords import hash_password, verify_password

    assert verify_password(PASSWORD, hash_password(PASSWORD))

    # A pbkdf2 hash, as an earlier revision would have written it. Never written
    # now — only read.
    salt = b"0123456789abcdef"
    digest = hashlib.pbkdf2_hmac("sha256", PASSWORD.encode(), salt, 1000, dklen=32)
    legacy = (f"pbkdf2_sha256$1000${base64.b64encode(salt).decode()}$"
              f"{base64.b64encode(digest).decode()}")
    assert verify_password(PASSWORD, legacy)
    assert not verify_password("wrong", legacy)


def test_weak_hash_is_flagged_for_upgrade():
    from identity.passwords import hash_password, needs_rehash

    assert needs_rehash("pbkdf2_sha256$1000$c2FsdA==$aGFzaA==")
    # Today's scheme does not need upgrading to itself.
    assert not needs_rehash(hash_password(PASSWORD))


def test_verify_never_raises_on_garbage():
    """A corrupt row must be a failed login, not a 500 — and not a signal that
    distinguishes 'wrong password' from 'broken record' to whoever is probing."""
    from identity.passwords import verify_password

    for junk in ("", "not-a-hash", "argon2id$", "scrypt$$$$", None, "$$$"):
        assert verify_password(PASSWORD, junk) is False


def test_short_passwords_are_rejected(client):
    resp = client.post("/api/v1/auth/signup", json={
        "email": "short@example.com", "password": "short", "name": "S",
    })
    assert resp.status_code == 422
    assert "12 characters" in resp.json()["detail"]


def test_password_containing_the_email_is_rejected(client):
    resp = client.post("/api/v1/auth/signup", json={
        "email": "alexandra@example.com",
        "password": "alexandra-alexandra-1",
        "name": "A",
    })
    assert resp.status_code == 422
    assert "email" in resp.json()["detail"].lower()


# ── Login ───────────────────────────────────────────────────────────────


def test_login_succeeds_and_returns_a_usable_token(client, account):
    resp = client.post("/api/v1/auth/login",
                       json={"email": account["email"], "password": PASSWORD})
    assert resp.status_code == 200
    token = resp.json()["access_token"]

    me = client.get("/api/v1/auth/me", headers=auth(token))
    assert me.status_code == 200
    assert me.json()["user"]["email"] == account["email"]


def test_unknown_email_and_wrong_password_are_indistinguishable(client, account):
    """THE ENUMERATION TEST. A different message for a known and an unknown
    address turns the login form into a bulk account-existence oracle, which is
    step one of every credential-stuffing campaign."""
    unknown = client.post("/api/v1/auth/login",
                          json={"email": "nobody@example.com", "password": "whatever-long"})
    wrong = client.post("/api/v1/auth/login",
                        json={"email": account["email"], "password": "wrong-but-long-enough"})

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]


def test_unknown_email_still_costs_a_kdf(client):
    """The timing half of the same oracle. Returning early for an unknown address
    is measurably faster than running the KDF, which enumerates accounts just as
    well as a different message.

    The bound is loose on purpose — CI runners are noisy — but it fails outright
    if the dummy-hash verification is removed, because the unknown path would
    then be microseconds against tens of milliseconds.
    """
    import uuid

    email = f"real-{uuid.uuid4().hex[:8]}@example.com"
    client.post("/api/v1/auth/signup",
                json={"email": email, "password": PASSWORD, "name": "R"})

    def timed(address: str) -> float:
        samples = []
        for _ in range(3):
            start = time.perf_counter()
            client.post("/api/v1/auth/login",
                        json={"email": address, "password": "definitely-wrong-x"})
            samples.append(time.perf_counter() - start)
        return min(samples)

    known = timed(email)
    unknown = timed(f"absent-{uuid.uuid4().hex[:8]}@example.com")

    assert unknown > known * 0.2, (
        f"unknown-email login returned in {unknown:.4f}s vs {known:.4f}s for a "
        f"known one — the timing oracle is back")


def test_repeated_failures_lock_the_account(client):
    """Online guessing has to become expensive. The offline threat is the KDF's
    job; this is the online one."""
    import uuid

    from identity import store

    email = f"lock-{uuid.uuid4().hex[:8]}@example.com"
    client.post("/api/v1/auth/signup",
                json={"email": email, "password": PASSWORD, "name": "L"})

    for _ in range(store.MAX_FAILED_LOGINS + 1):
        client.post("/api/v1/auth/login",
                    json={"email": email, "password": "wrong-password-here"})

    user = store.get_user_by_email(email)
    assert store.is_locked(user)

    # Even the RIGHT password is refused while locked — otherwise the lock is
    # decorative.
    resp = client.post("/api/v1/auth/login",
                       json={"email": email, "password": PASSWORD})
    assert resp.status_code == 401
    assert "locked" in resp.json()["detail"].lower()


# ── Sessions and refresh ────────────────────────────────────────────────


def test_refresh_rotates_the_token(client, account):
    resp = client.post("/api/v1/auth/refresh",
                       json={"refresh_token": account["refresh"]})
    assert resp.status_code == 200
    assert resp.json()["refresh_token"] != account["refresh"]


def test_replaying_a_rotated_refresh_token_kills_the_family(app, account):
    """THE REUSE-DETECTION TEST, and the reason each test gets its own client.

    A refresh token presented twice means two parties hold it. Revoking only the
    replayed session would leave the thief with the newer token and log the
    victim out — exactly backwards. The whole family goes.
    """
    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as c:
        first = c.post("/api/v1/auth/refresh",
                       json={"refresh_token": account["refresh"]})
        assert first.status_code == 200
        rotated = first.json()["refresh_token"]

        # Clear the jar so the BODY token is what gets used, not the cookie the
        # response just set.
        c.cookies.clear()

        replay = c.post("/api/v1/auth/refresh",
                        json={"refresh_token": account["refresh"]})
        assert replay.status_code == 401

        # The token issued by the legitimate rotation is dead too.
        c.cookies.clear()
        descendant = c.post("/api/v1/auth/refresh",
                            json={"refresh_token": rotated})
        assert descendant.status_code == 401


def test_logout_invalidates_the_access_token_immediately(client, account):
    """Access tokens are stateless, so nothing in the SIGNATURE expires on
    logout. `principal_from_access_token` checks the session row, which is what
    makes 'sign out' mean something before the 15-minute expiry."""
    assert client.get("/api/v1/auth/me", headers=account["headers"]).status_code == 200

    client.post("/api/v1/auth/logout", json={"refresh_token": account["refresh"]})

    assert client.get("/api/v1/auth/me", headers=account["headers"]).status_code == 401


def test_changing_the_password_signs_out_other_devices(app, account):
    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as other:
        second = other.post("/api/v1/auth/login", json={
            "email": account["email"], "password": PASSWORD,
        })
        other_token = second.json()["access_token"]
        assert other.get("/api/v1/auth/me", headers=auth(other_token)).status_code == 200

    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.post("/api/v1/auth/password/change",
                      headers=account["headers"],
                      json={"current_password": PASSWORD,
                            "new_password": "a-different-long-password-9"})
        assert resp.status_code == 200

        # The other device is out. The session that made the change survives —
        # changing your password should not sign YOU out.
        assert c.get("/api/v1/auth/me", headers=auth(other_token)).status_code == 401
        assert c.get("/api/v1/auth/me", headers=account["headers"]).status_code == 200


def test_a_forged_token_is_rejected(client):
    """Signed with the wrong key. Pins that `decode()` verifies the signature —
    a JWT library misconfigured to skip that is the classic critical bug."""
    import jwt as pyjwt

    forged = pyjwt.encode(
        {"sub": "usr_attacker", "sid": "ses_x", "org": "victim-org",
         "role": "admin", "typ": "access", "iss": "nextxr-twin",
         "iat": int(time.time()), "exp": int(time.time()) + 3600},
        "not-the-real-signing-key", algorithm="HS256")

    assert client.get("/api/v1/auth/me", headers=auth(forged)).status_code == 401


def test_the_alg_none_attack_is_rejected(client):
    """A token declaring `"alg": "none"` must not be honoured. `decode_access`
    pins `algorithms=["HS256"]` precisely for this."""
    import base64
    import json as _json

    def b64(payload: dict) -> str:
        raw = _json.dumps(payload).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    token = (b64({"alg": "none", "typ": "JWT"}) + "." +
             b64({"sub": "usr_attacker", "role": "admin", "typ": "access",
                  "iss": "nextxr-twin", "exp": int(time.time()) + 3600}) + ".")

    assert client.get("/api/v1/auth/me", headers=auth(token)).status_code == 401


def test_an_expired_token_is_rejected(client, account):
    from identity import tokens

    expired, _ = tokens.issue_access(
        user_id=account["user_id"], session_id="ses_x",
        org_id=account["org_id"], role="admin", ttl=-10)

    assert client.get("/api/v1/auth/me", headers=auth(expired)).status_code == 401


# ── Organisations and tenant scoping ────────────────────────────────────


def test_a_new_org_reaches_no_tenants(client, account):
    me = client.get("/api/v1/auth/me", headers=account["headers"]).json()
    assert me["tenants"] == []

    denied = client.get("/api/v1/stats", params={"tenant": "someone-elses-twin"},
                        headers=account["headers"])
    assert denied.status_code == 403


def test_claiming_a_tenant_makes_it_reachable(client, account):
    """The core authorization primitive: reach comes from `org_tenants`, not from
    a string-prefix convention on the tenant id."""
    from identity import store

    store.claim_tenant("owned-twin", account["org_id"])

    allowed = client.get("/api/v1/stats", params={"tenant": "owned-twin"},
                         headers=account["headers"])
    assert allowed.status_code != 403

    me = client.get("/api/v1/auth/me", headers=account["headers"]).json()
    assert "owned-twin" in me["tenants"]


def test_one_org_cannot_reach_another(app, client, account):
    """The bug that ends an enterprise deal. Asserted across BOTH the data plane
    and the account plane, because they are enforced by different code."""
    from fastapi.testclient import TestClient
    from identity import store

    store.claim_tenant("victim-twin", account["org_id"])

    with TestClient(app, raise_server_exceptions=False) as other:
        resp = other.post("/api/v1/auth/signup", json={
            "email": "attacker@example.com", "password": PASSWORD,
            "name": "Attacker", "org_name": "Attacker Inc",
        })
        headers = auth(resp.json()["access_token"])

        # Data plane.
        assert other.get("/api/v1/stats", params={"tenant": "victim-twin"},
                         headers=headers).status_code == 403
        assert other.get("/api/v1/twins/victim-twin/state",
                         headers=headers).status_code == 403

        # Account plane.
        org = account["org_id"]
        assert other.get(f"/api/v1/auth/orgs/{org}/members",
                         headers=headers).status_code == 403
        assert other.get(f"/api/v1/auth/orgs/{org}/keys",
                         headers=headers).status_code == 403
        assert other.get(f"/api/v1/auth/orgs/{org}/audit",
                         headers=headers).status_code == 403


def test_a_tenant_already_owned_cannot_be_reassigned():
    """`claim_tenant` is DO NOTHING, not DO UPDATE. A twin must not change hands
    as a side effect of someone creating one with a colliding name."""
    from identity import store

    store.claim_tenant("contested", "org-first")
    store.claim_tenant("contested", "org-second")

    assert store.tenant_owner("contested") == "org-first"


# ── Roles ───────────────────────────────────────────────────────────────


def test_a_read_member_cannot_mutate(client, account):
    from identity import store

    store.claim_tenant("role-twin", account["org_id"])

    invited = client.post(
        f"/api/v1/auth/orgs/{account['org_id']}/members",
        headers=account["headers"],
        json={"email": "reader@example.com", "role": "read", "name": "Reader"})
    assert invited.status_code == 200

    # Sign the reader in via a reset token, the invitation flow.
    token = invited.json().get("invite_token")
    assert token, "no mailer configured, so the invite token should be returned"
    client.post("/api/v1/auth/password/reset",
                json={"token": token, "new_password": "quiet-lantern-9182"})

    session = client.post("/api/v1/auth/login",
                          json={"email": "reader@example.com",
                                "password": "quiet-lantern-9182"})
    reader = auth(session.json()["access_token"])

    assert client.get("/api/v1/stats", params={"tenant": "role-twin"},
                      headers=reader).status_code != 403
    assert client.post("/api/v1/entities",
                       json={"tenant": "role-twin", "id": "x"},
                       headers=reader).status_code == 403


def test_an_admin_cannot_grant_owner(client, account):
    """No role may grant one above itself. Otherwise the ladder is decorative:
    an admin promotes themselves to owner and the distinction is gone."""
    from identity import store

    org = account["org_id"]
    client.post(f"/api/v1/auth/orgs/{org}/members", headers=account["headers"],
                json={"email": "admin@example.com", "role": "admin", "name": "Ad"})

    admin_user = store.get_user_by_email("admin@example.com")
    from identity import service
    from identity.models import Principal

    actor = Principal(kind="user", user_id=admin_user.user_id, org_id=org,
                      role="admin", label="admin@example.com")

    with pytest.raises(service.Forbidden):
        service.set_member_role(org_id=org, user_id=admin_user.user_id,
                                role="owner", actor=actor)


def test_the_last_owner_cannot_be_demoted(client, account):
    """An org with no owner is unrecoverable without platform staff, and a
    support ticket is not an access model."""
    from identity import service
    from identity.models import Principal

    actor = Principal(kind="user", user_id=account["user_id"],
                      org_id=account["org_id"], role="owner", label="owner")

    with pytest.raises(service.Conflict):
        service.set_member_role(org_id=account["org_id"],
                                user_id=account["user_id"],
                                role="read", actor=actor)


def test_a_demotion_takes_effect_immediately(app, client, account):
    """Access tokens carry the role and live ~15 minutes, so a demotion is
    invisible to them. `set_member_role` revokes the user's sessions, which is
    what makes it take effect now rather than eventually."""
    from fastapi.testclient import TestClient
    from identity import service, store
    from identity.models import Principal

    org = account["org_id"]
    client.post(f"/api/v1/auth/orgs/{org}/members", headers=account["headers"],
                json={"email": "demote@example.com", "role": "write", "name": "D"})

    user = store.get_user_by_email("demote@example.com")
    reset = store.create_auth_token(user.user_id, store.PURPOSE_RESET)
    client.post("/api/v1/auth/password/reset",
                json={"token": reset, "new_password": "quiet-lantern-9182"})

    with TestClient(app, raise_server_exceptions=False) as c:
        token = c.post("/api/v1/auth/login",
                       json={"email": "demote@example.com",
                             "password": "quiet-lantern-9182"}
                       ).json()["access_token"]
        assert c.get("/api/v1/auth/me", headers=auth(token)).status_code == 200

        service.set_member_role(
            org_id=org, user_id=user.user_id, role="read",
            actor=Principal(kind="user", user_id=account["user_id"], org_id=org,
                            role="owner", label="owner"))

        # Same token, now dead — the session behind it was revoked.
        assert c.get("/api/v1/auth/me", headers=auth(token)).status_code == 401


# ── API keys ────────────────────────────────────────────────────────────


def test_an_api_key_authenticates_and_is_scoped(client, account):
    from identity import store

    store.claim_tenant("key-twin", account["org_id"])

    created = client.post(f"/api/v1/auth/orgs/{account['org_id']}/keys",
                          headers=account["headers"],
                          json={"name": "ci", "role": "write"})
    assert created.status_code == 200
    secret = created.json()["secret"]

    assert client.get("/api/v1/stats", params={"tenant": "key-twin"},
                      headers={"X-API-Key": secret}).status_code != 403
    assert client.get("/api/v1/stats", params={"tenant": "not-ours"},
                      headers={"X-API-Key": secret}).status_code == 403


def test_the_key_secret_is_returned_once_and_never_again(client, account):
    """Same posture as an SSH authorized_keys file or a GitHub PAT. A registry an
    operator can read back is a registry an attacker can read once."""
    org = account["org_id"]
    created = client.post(f"/api/v1/auth/orgs/{org}/keys",
                          headers=account["headers"],
                          json={"name": "once", "role": "read"}).json()
    secret = created["secret"]

    listed = client.get(f"/api/v1/auth/orgs/{org}/keys",
                        headers=account["headers"]).json()["keys"]
    entry = next(k for k in listed if k["name"] == "once")

    assert "secret" not in entry
    assert secret not in str(listed)
    # Only a short, useless prefix is shown, so the UI can say WHICH key.
    assert entry["prefix"] and len(entry["prefix"]) <= 20


def test_only_the_hash_reaches_the_database(client, account):

    created = client.post(f"/api/v1/auth/orgs/{account['org_id']}/keys",
                          headers=account["headers"],
                          json={"name": "hashed", "role": "read"}).json()

    with __import__("db").connect("identity") as conn:
        rows = conn.execute("SELECT key_hash FROM api_keys").fetchall()

    hashes = [dict(r)["key_hash"] for r in rows]
    assert created["secret"] not in hashes


def test_a_revoked_key_stops_working(client, account):
    org = account["org_id"]
    created = client.post(f"/api/v1/auth/orgs/{org}/keys",
                          headers=account["headers"],
                          json={"name": "doomed", "role": "read"}).json()
    secret, key_id = created["secret"], created["key_id"]

    assert client.get("/api/v1/auth/me",
                      headers={"X-API-Key": secret}).status_code == 200

    client.delete(f"/api/v1/auth/orgs/{org}/keys/{key_id}",
                  headers=account["headers"])

    assert client.get("/api/v1/auth/me",
                      headers={"X-API-Key": secret}).status_code == 401


def test_a_key_cannot_be_scoped_outside_its_org(client, account):
    """Otherwise an org admin mints a credential for another customer's data."""
    resp = client.post(f"/api/v1/auth/orgs/{account['org_id']}/keys",
                       headers=account["headers"],
                       json={"name": "greedy", "role": "read",
                             "tenants": ["another-orgs-twin"]})
    assert resp.status_code == 403


def test_an_api_key_cannot_hold_the_owner_role(client, account):
    """A key that can issue keys survives its own revocation."""
    resp = client.post(f"/api/v1/auth/orgs/{account['org_id']}/keys",
                       headers=account["headers"],
                       json={"name": "root", "role": "owner"})
    assert resp.status_code == 422


def test_an_api_key_cannot_change_a_human_password(client, account):
    """A machine credential must not be able to take over a person's account."""
    created = client.post(f"/api/v1/auth/orgs/{account['org_id']}/keys",
                          headers=account["headers"],
                          json={"name": "sneaky", "role": "admin"}).json()

    resp = client.post("/api/v1/auth/password/change",
                       headers={"X-API-Key": created["secret"]},
                       json={"current_password": PASSWORD,
                             "new_password": "attacker-chosen-pw-1"})
    assert resp.status_code == 403


# ── Password reset ──────────────────────────────────────────────────────


def test_forgot_password_does_not_reveal_whether_an_account_exists(client, account):
    known = client.post("/api/v1/auth/password/forgot",
                        json={"email": account["email"]})
    unknown = client.post("/api/v1/auth/password/forgot",
                          json={"email": "nobody-at-all@example.com"})

    assert known.status_code == unknown.status_code == 200
    assert known.json()["message"] == unknown.json()["message"]


def test_a_reset_token_is_single_use(client, account):
    from identity import store

    token = store.create_auth_token(account["user_id"], store.PURPOSE_RESET)

    first = client.post("/api/v1/auth/password/reset",
                        json={"token": token, "new_password": "brand-new-password-1"})
    assert first.status_code == 200

    second = client.post("/api/v1/auth/password/reset",
                         json={"token": token, "new_password": "another-password-22"})
    assert second.status_code == 401


def test_a_reset_signs_out_every_session(client, account):
    from identity import store

    assert client.get("/api/v1/auth/me", headers=account["headers"]).status_code == 200

    token = store.create_auth_token(account["user_id"], store.PURPOSE_RESET)
    client.post("/api/v1/auth/password/reset",
                json={"token": token, "new_password": "post-reset-password-1"})

    assert client.get("/api/v1/auth/me", headers=account["headers"]).status_code == 401


# ── Audit ───────────────────────────────────────────────────────────────


def test_account_actions_are_audited(client, account):
    org = account["org_id"]
    client.post(f"/api/v1/auth/orgs/{org}/keys", headers=account["headers"],
                json={"name": "audited", "role": "read"})

    events = client.get(f"/api/v1/auth/orgs/{org}/audit",
                        headers=account["headers"]).json()["events"]
    actions = {e["action"] for e in events}

    assert "user.signup" in actions
    assert "apikey.issued" in actions
    # The actor is recorded — the column that pointed at nothing before users
    # existed.
    assert any(e["actor_user"] == account["user_id"] for e in events)


def test_a_failed_login_is_audited(client, account):
    client.post("/api/v1/auth/login",
                json={"email": account["email"], "password": "wrong-password-x"})

    from identity import store
    events = store.list_audit(limit=50, action="user.login")

    assert any(e["outcome"] == "denied" for e in events)


def test_an_owner_can_create_a_twin(client, account):
    """REGRESSION. `tenancy.require_write` compared `scope.role not in ("admin",
    "write")` — correct for the three roles that existed before `identity/`, and
    silently wrong afterwards: `owner` is the MOST privileged role and was
    refused by it, so an organisation's owner could not provision anything.

    The check is ordered against the role ladder now, so a role added later is
    ranked rather than accidentally excluded.
    """
    resp = client.post("/api/v1/twins",
                       json={"name": "Owner Twin", "domain": "solar",
                             "slug": "owner-twin"},
                       headers=account["headers"])

    assert resp.status_code != 403, (
        f"an owner was refused twin creation: {resp.text[:200]}")


def test_every_role_at_or_above_write_may_mutate():
    """The property the regression above violated, asserted directly on the
    ladder so it holds for roles that do not exist yet."""
    from identity import role_at_least

    for role in ("owner", "admin", "write"):
        assert role_at_least(role, "write"), f"{role} should be able to mutate"

    assert not role_at_least("read", "write")
    # An unknown role must rank BELOW read — a typo must lose privileges.
    assert not role_at_least("wrIte-typo", "read")


# ── Twins every account lands on ────────────────────────────────────────
#
# A signed-in user reaches a twin because `org_tenants` says their organisation
# owns it. That is the correct rule and it made the platform's OWN demo twins
# invisible: they are seeded on boot, owned by nobody, so a brand-new account
# signed in and found an empty library. The reserved `nxr:shared` organisation is
# how a twin becomes common to every account — and the tests below are what keep
# "shared with everyone" from quietly becoming "shared with everyone, including
# things that should not be".


@pytest.fixture
def shared_twin():
    """A tenant owned by the reserved shared organisation."""
    import uuid

    from identity import clear_shared_cache, store

    tenant = f"shared-{uuid.uuid4().hex[:8]}"
    store.share_tenant(tenant)
    clear_shared_cache()
    yield tenant
    store.release_tenant(tenant)
    clear_shared_cache()


def _principal(org_id: str, *, tenants=None, role: str = "owner",
               platform_admin: bool = False):
    from identity.models import Principal

    return Principal(kind="user", user_id="usr_test", org_id=org_id, role=role,
                     tenants=tenants, is_platform_admin=platform_admin,
                     label="test")


def test_a_shared_twin_is_reachable_by_any_account(account, shared_twin):
    """THE REQUIREMENT: the demo set is what every account lands on, without an
    administrator assigning anything per user."""
    from identity import tenants_for

    reachable = tenants_for(_principal(account["org_id"]))

    assert shared_twin in reachable


def test_a_user_who_belongs_to_no_organisation_still_sees_shared(shared_twin):
    """A user mid-invitation, or one whose last membership was removed, owns
    nothing. That must mean "nothing of their own", not "a blank application"."""
    from identity import tenants_for

    assert tenants_for(_principal("")) == frozenset({shared_twin})


def test_sharing_does_not_leak_another_organisations_twin(account, shared_twin):
    """The whole risk of a shared set: that it becomes a way to reach data nobody
    shared. Only the tenants explicitly given to `nxr:shared` are common."""
    from identity import store, tenants_for

    private = "someone-elses-private-twin"
    other = store.create_org("Some Other Customer")
    store.claim_tenant(private, other.org_id)

    reachable = tenants_for(_principal(account["org_id"]))

    assert shared_twin in reachable
    assert private not in reachable


def test_a_narrowed_api_key_is_not_widened_by_the_shared_set(shared_twin):
    """An API key issued with an explicit tenant list was deliberately scoped by
    whoever issued it. Handing it the shared twins as well would be the platform
    granting more than the issuer asked for — the exact over-grant `identity/`
    exists to prevent."""
    from identity import tenants_for

    narrowed = _principal("some-org", tenants=("only-this-one",), role="write")

    assert tenants_for(narrowed) == frozenset()


def test_a_key_that_names_a_shared_tenant_still_reaches_it(shared_twin):
    """The other side of the rule above: narrowing must not REVOKE something the
    issuer explicitly listed."""
    from identity import tenants_for

    keyed = _principal("some-org", tenants=(shared_twin,), role="read")

    assert tenants_for(keyed) == frozenset({shared_twin})


def test_unsharing_takes_the_twin_back_out_of_reach(account):
    from identity import clear_shared_cache, store, tenants_for

    tenant = "temporarily-shared-twin"
    store.share_tenant(tenant)
    clear_shared_cache()
    assert tenant in tenants_for(_principal(account["org_id"]))

    store.release_tenant(tenant)
    clear_shared_cache()

    assert tenant not in tenants_for(_principal(account["org_id"]))


def test_the_shared_org_id_cannot_be_produced_by_a_signup():
    """`nxr:shared` must stay reserved. `slugify()` emits only [a-z0-9-], so no
    company name can mint it — asserted here because the guarantee that keeps a
    customer from owning the shared set lives in that function."""
    from identity.store import SHARED_ORG_ID, slugify

    for attempt in ("nxr:shared", "NXR:Shared", "nxr shared", "nxr_shared",
                    "nxr-shared", "  nxr:shared  "):
        assert slugify(attempt) != SHARED_ORG_ID, (
            f"{attempt!r} slugified onto the reserved shared organisation")


def test_a_shared_twin_is_visible_over_http_to_a_fresh_account(client, shared_twin):
    """End to end through the listing endpoint, which is the first call the UI
    makes and the one that decides whether the library looks empty."""
    import uuid

    resp = client.post("/api/v1/auth/signup", json={
        "email": f"fresh-{uuid.uuid4().hex[:8]}@example.com",
        "password": PASSWORD, "org_name": "Fresh Co",
    })
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]

    listing = client.get("/api/v1/twins", headers=auth(token))

    assert listing.status_code == 200, listing.text
    # The tenant is shared but has no registry row in this suite (no Neo4j), so
    # what is asserted is the SCOPE: it is not counted as hidden.
    from identity import tenants_for
    assert shared_twin in tenants_for(_principal(resp.json()["org_id"]))
