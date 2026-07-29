"""service.py — the operations, and the rules that govern them.

`store.py` is persistence with no opinions. This module holds the ones that
matter: what makes a login safe to grant, when a refresh token is evidence of
theft, who is allowed to change whose role, and how the very first account comes
into existence on an empty database.

THREE THINGS WORTH READING BEFORE CHANGING ANYTHING HERE
--------------------------------------------------------
1. LOGIN IS DELIBERATELY UNIFORM. Unknown email, wrong password, disabled
   account and locked account all return the same `AuthFailed("Invalid email or
   password.")`, and the unknown-email path still runs a KDF against a dummy
   hash. Anything else is a user-enumeration oracle — by message OR by timing —
   and enumeration is the first step of every credential-stuffing campaign. The
   one exception is a LOCKED account, which says so, because a user who has
   locked themselves out needs to know that waiting fixes it; that branch is
   reached only AFTER the password verified, so it discloses nothing to someone
   who does not already hold the credential.

2. REFRESH TOKENS ROTATE, AND REUSE IS TREATED AS THEFT. Every refresh mints a
   new token and revokes the old one. Presenting an already-rotated token means
   two parties hold it, so the whole session family is revoked — the legitimate
   user is logged out and has to sign in again, which is the correct outcome
   when a token has demonstrably leaked.

3. PRIVILEGE CHANGES REVOKE SESSIONS. Access tokens are stateless and live up to
   15 minutes, so a demotion is not visible to them. Rather than make every
   request re-read the database (which would undo the reason the token is
   stateless), a role change or a password change revokes the affected user's
   sessions and forces a fresh token.
"""
from __future__ import annotations

import os
from collections.abc import Iterable

from . import store, tokens
from .models import (
    ROLE_ADMIN,
    ROLE_OWNER,
    ROLE_READ,
    ApiKeyRecord,
    Principal,
    User,
    normalize_role,
    role_at_least,
    role_rank,
)
from .passwords import (
    PasswordError,
    hash_password,
    needs_rehash,
    validate_strength,
    verify_password,
)

# A real Argon2/scrypt hash of a value nobody knows, verified against whenever
# the email is unknown. Without this the "no such user" path returns in
# microseconds while the real path spends ~60ms in the KDF, and that difference
# is measurable over the network — a timing oracle that enumerates accounts just
# as effectively as a different error message. Built lazily so importing this
# module costs nothing.
_DUMMY_HASH: str = ""


def _dummy_hash() -> str:
    global _DUMMY_HASH
    if not _DUMMY_HASH:
        _DUMMY_HASH = hash_password("nxr-timing-equalizer-" + os.urandom(8).hex())
    return _DUMMY_HASH


class AuthFailed(Exception):
    """Credentials rejected. Rendered as 401, with this message shown verbatim —
    so every message raised as AuthFailed must be safe for an anonymous caller."""


class Forbidden(Exception):
    """Authenticated, but not permitted. Rendered as 403."""


class Conflict(Exception):
    """The request conflicts with existing state (email taken, last owner).
    Rendered as 409."""


# ── Signup ──────────────────────────────────────────────────────────────


def signup(*, email: str, password: str, name: str = "",
           org_name: str = "", ip: str = "", user_agent: str = "") -> dict:
    """Create a user, their organisation, and an owner membership.

    Self-service signup creates an ORG as well as a user, because every
    authorization path in this platform runs through org → tenants. A user with
    no org is a user who can log in and see nothing, which looks like a broken
    product rather than an empty one.

    Whether this is reachable at all is deployment policy: `NXR_ALLOW_SIGNUP`
    gates it, defaulting to OPEN only when authentication is not being enforced
    (local dev). A production deployment that wants invite-only turns it off and
    provisions through `/auth/orgs/{org}/members`.
    """
    address = store.normalize_email(email)
    if not address or "@" not in address or address.startswith("@") \
            or address.endswith("@"):
        raise PasswordError("A valid email address is required.")

    validate_strength(password, email=address, name=name)

    if store.get_user_by_email(address) is not None:
        # Signup is one of the two places enumeration cannot be fully avoided —
        # the alternative (pretend to succeed, send a "you already have an
        # account" email) needs a working mailer, which is deployment-specific.
        # It is rate-limited instead; see server/ratelimit.py.
        raise Conflict("An account with that email already exists.")

    user = store.create_user(address, hash_password(password), name=name,
                             email_verified=not _verification_required())
    org = store.create_org(org_name or f"{name or address.split('@')[0]}'s workspace")
    store.add_member(org.org_id, user.user_id, ROLE_OWNER)

    store.audit("user.signup", org_id=org.org_id, actor_user=user.user_id,
                target_type="user", target_id=user.user_id, ip=ip,
                user_agent=user_agent)

    verify_token = ""
    if _verification_required():
        verify_token = store.create_auth_token(
            user.user_id, store.PURPOSE_VERIFY, ttl_seconds=48 * 3600)

    return {"user": user, "org": org, "verify_token": verify_token}


def _verification_required() -> bool:
    """Email verification is opt-IN, because it is only meaningful with a mailer
    configured. Turning it on without one would create accounts nobody can
    activate — a worse failure than an unverified address."""
    return str(os.environ.get("NXR_REQUIRE_EMAIL_VERIFICATION", "")).strip().lower() \
        in ("1", "true", "yes", "on")


def signup_allowed() -> bool:
    raw = os.environ.get("NXR_ALLOW_SIGNUP", "").strip().lower()
    if raw:
        return raw in ("1", "true", "yes", "on")
    # Unset: open in local dev, closed once auth is enforced. A public deployment
    # should not gain self-service signup merely because nobody set the flag.
    from server.auth import auth_required
    return not auth_required()


# ── Login ───────────────────────────────────────────────────────────────


def login(*, email: str, password: str, org_id: str = "", ip: str = "",
          user_agent: str = "") -> dict:
    """Verify credentials and issue an access + refresh pair.

    Returns {access_token, refresh_token, expires_in, user, org, role, orgs}.
    """
    address = store.normalize_email(email)
    user = store.get_user_by_email(address)

    if user is None:
        verify_password(password or "", _dummy_hash())      # equalise timing
        store.audit("user.login", outcome="denied", target_type="email",
                    target_id=address, ip=ip, user_agent=user_agent,
                    detail={"reason": "unknown_email"})
        raise AuthFailed("Invalid email or password.")

    stored_hash = store.get_password_hash(user.user_id)
    if not verify_password(password or "", stored_hash):
        count = store.record_login_failure(user.user_id)
        store.audit("user.login", outcome="denied", actor_user=user.user_id,
                    ip=ip, user_agent=user_agent,
                    detail={"reason": "bad_password", "failed_logins": count})
        raise AuthFailed("Invalid email or password.")

    # Password was correct — from here on, messages may be specific.
    if store.is_locked(user):
        store.audit("user.login", outcome="denied", actor_user=user.user_id,
                    ip=ip, user_agent=user_agent, detail={"reason": "locked"})
        raise AuthFailed(
            "This account is temporarily locked after too many failed sign-in "
            "attempts. Try again shortly, or reset your password.")

    if not user.is_active:
        store.audit("user.login", outcome="denied", actor_user=user.user_id,
                    ip=ip, user_agent=user_agent, detail={"reason": "disabled"})
        raise AuthFailed("This account has been disabled. Contact your administrator.")

    if _verification_required() and not user.email_verified:
        raise AuthFailed("Confirm your email address before signing in.")

    # Upgrade the stored hash opportunistically. This is the only moment the
    # plaintext exists, so it is the only moment a re-hash is possible.
    if needs_rehash(stored_hash):
        try:
            store.set_password_hash(user.user_id, hash_password(password))
        except Exception:
            pass                                  # never fail a good login on this

    store.record_login_success(user.user_id)
    return _issue_session(user, org_id=org_id, ip=ip, user_agent=user_agent,
                          action="user.login")


def _issue_session(user: User, *, org_id: str = "", ip: str = "",
                   user_agent: str = "", rotated_from: str = "",
                   action: str = "user.login") -> dict:
    """Pick the acting org, mint both tokens, and record the session."""
    memberships = store.list_memberships_for_user(user.user_id)
    orgs = []
    for m in memberships:
        org = store.get_org(m.org_id)
        if org is not None:
            orgs.append({"org_id": org.org_id, "name": org.name,
                         "role": m.role, "status": org.status})

    active_org, role = _resolve_acting_org(user, memberships, org_id)

    refresh_token = tokens.issue_refresh()
    session = store.create_session(
        user.user_id, active_org, refresh_token,
        ttl_seconds=tokens.refresh_ttl(), ip=ip, user_agent=user_agent,
        rotated_from=rotated_from,
    )
    access_token, expires_in = tokens.issue_access(
        user_id=user.user_id, session_id=session.session_id, org_id=active_org,
        role=role, email=user.email, is_platform_admin=user.is_platform_admin,
    )

    store.audit(action, org_id=active_org or "", actor_user=user.user_id,
                target_type="session", target_id=session.session_id, ip=ip,
                user_agent=user_agent)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "user": user,
        "org_id": active_org,
        "role": role,
        "orgs": orgs,
        "session_id": session.session_id,
    }


def _resolve_acting_org(user: User, memberships, requested: str
                        ) -> tuple[str | None, str]:
    """Which org this session acts for, and the role it carries there.

    A requested org the user is NOT a member of is refused rather than silently
    ignored — falling back to their default org would hand them a session that
    looks like the org they asked for and is not, which is exactly the kind of
    confusion that ends in someone editing the wrong customer's twin.
    """
    by_id = {m.org_id: m for m in memberships}

    if requested:
        membership = by_id.get(requested)
        if membership is None:
            if user.is_platform_admin:
                # Platform staff can act for any org. Recorded in the audit log
                # by the caller; not a membership, so it does not appear in the
                # user's org list.
                return requested, ROLE_ADMIN
            raise Forbidden("You are not a member of that organisation.")
        return requested, membership.role

    if not memberships:
        # A user with no org — a platform admin created before any org exists, or
        # one whose last membership was removed. They authenticate, and reach no
        # tenant until they are given a membership.
        return None, ROLE_ADMIN if user.is_platform_admin else ROLE_READ

    # Highest-privileged membership first, then oldest. Deterministic, and it
    # means the org someone actually administers is the one they land in.
    best = sorted(memberships, key=lambda m: (-role_rank(m.role), m.created_at))[0]
    return best.org_id, best.role


# ── Refresh / logout ────────────────────────────────────────────────────


def refresh(*, refresh_token: str, ip: str = "", user_agent: str = "") -> dict:
    """Exchange a refresh token for a new pair, rotating it.

    See the module docstring on reuse detection — this is where it happens, and
    it is the reason `find_session_by_refresh` returns revoked rows.
    """
    session = store.find_session_by_refresh(refresh_token or "")
    if session is None:
        raise AuthFailed("Invalid or expired session. Sign in again.")

    if session.revoked_at is not None:
        # REUSE. This token was already exchanged, so two parties hold it. Revoke
        # the entire family: whoever is legitimate signs in again, and whoever is
        # not loses the newer token they were issued.
        revoked = store.revoke_session_family(session.session_id)
        store.audit("session.reuse_detected", org_id=session.org_id or "",
                    actor_user=session.user_id, outcome="denied",
                    target_type="session", target_id=session.session_id, ip=ip,
                    user_agent=user_agent, detail={"revoked_sessions": revoked})
        raise AuthFailed("This session has been revoked. Sign in again.")

    if not store.session_is_valid(session):
        raise AuthFailed("Invalid or expired session. Sign in again.")

    user = store.get_user(session.user_id)
    if user is None or not user.is_active:
        store.revoke_session(session.session_id)
        raise AuthFailed("This account is no longer active.")

    # Rotate: the new session records where it came from, and the old one is
    # revoked in the same breath.
    result = _issue_session(user, org_id=session.org_id or "", ip=ip,
                            user_agent=user_agent,
                            rotated_from=session.session_id,
                            action="session.refresh")
    store.revoke_session(session.session_id)
    return result


def logout(*, refresh_token: str = "", session_id: str = "") -> None:
    """Revoke one session. Silent on an unknown token — logging out is not an
    operation that should be able to report whether a token was real."""
    if refresh_token:
        session = store.find_session_by_refresh(refresh_token)
        if session is not None:
            store.revoke_session(session.session_id)
            store.audit("user.logout", org_id=session.org_id or "",
                        actor_user=session.user_id, target_type="session",
                        target_id=session.session_id)
            return
    if session_id:
        session = store.get_session(session_id)
        if session is not None:
            store.revoke_session(session_id)
            store.audit("user.logout", org_id=session.org_id or "",
                        actor_user=session.user_id, target_type="session",
                        target_id=session_id)


def switch_org(*, user_id: str, session_id: str, org_id: str, ip: str = "",
               user_agent: str = "") -> dict:
    """Re-issue tokens for a different organisation the user belongs to.

    A new session rather than a mutated one, so the audit log shows which org
    each token was acting for. The old session is revoked so switching does not
    leave a usable credential for the previous org behind.
    """
    user = store.get_user(user_id)
    if user is None or not user.is_active:
        raise AuthFailed("This account is no longer active.")
    result = _issue_session(user, org_id=org_id, ip=ip, user_agent=user_agent,
                            rotated_from=session_id, action="session.switch_org")
    if session_id:
        store.revoke_session(session_id)
    return result


# ── Passwords ───────────────────────────────────────────────────────────


def change_password(*, user_id: str, current_password: str, new_password: str,
                    keep_session: str = "") -> None:
    user = store.get_user(user_id)
    if user is None:
        raise AuthFailed("This account is no longer active.")
    if not verify_password(current_password or "",
                           store.get_password_hash(user_id)):
        store.audit("user.password_change", outcome="denied", actor_user=user_id,
                    detail={"reason": "bad_current_password"})
        raise AuthFailed("Current password is incorrect.")

    validate_strength(new_password, email=user.email, name=user.name)
    store.set_password_hash(user_id, hash_password(new_password))
    # Every other session dies. A password change is the standard response to
    # "someone else may have access", and leaving their sessions alive makes it
    # useless for that.
    store.revoke_user_sessions(user_id, except_session=keep_session)
    store.audit("user.password_change", actor_user=user_id)


def request_password_reset(*, email: str, ip: str = "") -> str:
    """Start a reset. Returns the token — the CALLER decides how it is delivered.

    Always succeeds, whether or not the address exists, and returns "" for an
    unknown one. The route reports the same message either way: a reset endpoint
    that distinguishes them is a bulk account-existence oracle that needs no
    credential at all.
    """
    user = store.get_user_by_email(email)
    if user is None or not user.is_active:
        return ""
    token = store.create_auth_token(user.user_id, store.PURPOSE_RESET,
                                    ttl_seconds=3600)
    store.audit("user.password_reset_requested", actor_user=user.user_id, ip=ip)
    return token


def complete_password_reset(*, token: str, new_password: str) -> None:
    user_id = store.consume_auth_token(token, store.PURPOSE_RESET)
    if not user_id:
        raise AuthFailed("This reset link is invalid or has expired.")
    user = store.get_user(user_id)
    if user is None:
        raise AuthFailed("This reset link is invalid or has expired.")

    validate_strength(new_password, email=user.email, name=user.name)
    store.set_password_hash(user_id, hash_password(new_password))
    store.invalidate_auth_tokens(user_id, store.PURPOSE_RESET)
    store.revoke_user_sessions(user_id)
    store.audit("user.password_reset", actor_user=user_id)


def verify_email(*, token: str) -> bool:
    user_id = store.consume_auth_token(token, store.PURPOSE_VERIFY)
    if not user_id:
        return False
    store.mark_email_verified(user_id)
    store.audit("user.email_verified", actor_user=user_id)
    return True


# ── Members ─────────────────────────────────────────────────────────────


def invite_member(*, org_id: str, email: str, role: str, actor: Principal,
                  name: str = "") -> dict:
    """Add someone to an org, creating their account if it does not exist.

    A brand-new user gets a random password and a reset token — they set their
    own on first use. That is deliberately not "the admin chooses a password and
    tells them": a password one other person has typed is a shared secret, and
    the reset flow already exists to avoid inventing a second one.
    """
    _require_role(actor, org_id, ROLE_ADMIN)
    target_role = normalize_role(role)

    # An admin must not be able to mint an owner. Only an owner can, and the
    # check is against the ACTOR's role rather than a fixed list, so it stays
    # correct if the ladder grows.
    if role_rank(target_role) > role_rank(actor.role):
        raise Forbidden(f"You cannot grant a role above your own ({actor.role}).")

    user = store.get_user_by_email(email)
    reset_token = ""
    if user is None:
        from .passwords import generate_secret
        user = store.create_user(email, hash_password(generate_secret(nbytes=24)),
                                 name=name, status="active",
                                 email_verified=False)
        reset_token = store.create_auth_token(user.user_id, store.PURPOSE_RESET,
                                              ttl_seconds=7 * 86400)

    store.add_member(org_id, user.user_id, target_role)
    store.audit("member.added", org_id=org_id, actor_user=actor.user_id or "",
                actor_key=actor.key_id or "", target_type="user",
                target_id=user.user_id, detail={"role": target_role})
    return {"user": user, "role": target_role, "reset_token": reset_token}


def set_member_role(*, org_id: str, user_id: str, role: str,
                    actor: Principal) -> None:
    _require_role(actor, org_id, ROLE_ADMIN)
    target_role = normalize_role(role)

    if role_rank(target_role) > role_rank(actor.role):
        raise Forbidden(f"You cannot grant a role above your own ({actor.role}).")

    existing = store.get_membership(org_id, user_id)
    if existing is None:
        raise Forbidden("That user is not a member of this organisation.")

    # Nor demote someone above you.
    if role_rank(existing.role) > role_rank(actor.role):
        raise Forbidden("You cannot change the role of a more privileged member.")

    if existing.role == ROLE_OWNER and target_role != ROLE_OWNER \
            and store.count_owners(org_id) <= 1:
        raise Conflict(
            "This is the organisation's last owner. Promote another member to "
            "owner first.")

    store.add_member(org_id, user_id, target_role)
    # The demoted user's live access tokens still carry the old role for up to
    # their remaining lifetime. Revoking their sessions is what makes the change
    # take effect now rather than in fifteen minutes.
    store.revoke_user_sessions(user_id)
    store.audit("member.role_changed", org_id=org_id,
                actor_user=actor.user_id or "", actor_key=actor.key_id or "",
                target_type="user", target_id=user_id,
                detail={"from": existing.role, "to": target_role})


def remove_member(*, org_id: str, user_id: str, actor: Principal) -> None:
    _require_role(actor, org_id, ROLE_ADMIN)
    existing = store.get_membership(org_id, user_id)
    if existing is None:
        return
    if role_rank(existing.role) > role_rank(actor.role):
        raise Forbidden("You cannot remove a more privileged member.")
    if existing.role == ROLE_OWNER and store.count_owners(org_id) <= 1:
        raise Conflict("This is the organisation's last owner.")

    store.remove_member(org_id, user_id)
    store.revoke_user_sessions(user_id)
    store.audit("member.removed", org_id=org_id, actor_user=actor.user_id or "",
                actor_key=actor.key_id or "", target_type="user",
                target_id=user_id)


# ── API keys ────────────────────────────────────────────────────────────


def issue_api_key(*, org_id: str, name: str, role: str, actor: Principal,
                  tenants: Iterable[str] = (),
                  expires_in_days: int | None = None
                  ) -> tuple[ApiKeyRecord, str]:
    _require_role(actor, org_id, ROLE_ADMIN)
    target_role = normalize_role(role)
    if target_role == ROLE_OWNER:
        raise Forbidden(
            "An API key cannot hold the owner role — a key that can issue keys "
            "survives its own revocation.")
    if role_rank(target_role) > role_rank(actor.role):
        raise Forbidden(f"You cannot issue a key above your own role ({actor.role}).")

    # A key may be narrowed to a subset of the org's tenants, never widened past
    # them. Anything else would let an org admin mint a credential for another
    # customer's data.
    owned = set(store.org_tenants(org_id))
    requested = {t for t in tenants if t}
    if requested - owned:
        raise Forbidden(
            "A key can only be scoped to tenants this organisation owns.")

    record, secret = store.create_api_key(
        org_id, name=name, role=target_role, tenants=sorted(requested),
        created_by=actor.user_id or actor.key_id or "",
        expires_in_days=expires_in_days)
    store.audit("apikey.issued", org_id=org_id, actor_user=actor.user_id or "",
                actor_key=actor.key_id or "", target_type="api_key",
                target_id=record.key_id,
                detail={"role": target_role, "tenants": sorted(requested),
                        "expires_in_days": expires_in_days})
    return record, secret


def revoke_api_key(*, key_id: str, actor: Principal) -> bool:
    record = store.get_api_key(key_id)
    if record is None:
        return False
    _require_role(actor, record.org_id, ROLE_ADMIN)
    revoked = store.revoke_api_key(key_id)
    if revoked:
        store.audit("apikey.revoked", org_id=record.org_id,
                    actor_user=actor.user_id or "", actor_key=actor.key_id or "",
                    target_type="api_key", target_id=key_id)
    return revoked


# ── Authorization helper ────────────────────────────────────────────────


def _require_role(actor: Principal, org_id: str, minimum: str) -> None:
    """Assert the actor holds at least `minimum` IN THIS ORG.

    The org check is the important half. Without it a legitimate admin of org A
    could administer org B simply by putting B's id in the path — the same class
    of bug `server/tenancy.py` exists to prevent for twin data, and it has to be
    prevented separately here because these routes are about accounts rather
    than tenants.
    """
    if actor.is_platform_admin:
        return
    if not org_id or actor.org_id != org_id:
        raise Forbidden("You do not have access to that organisation.")
    if not role_at_least(actor.role, minimum):
        raise Forbidden(f"This action requires the {minimum} role.")


# ── Bootstrap ───────────────────────────────────────────────────────────


def bootstrap_admin() -> str | None:
    """Create the first platform admin from the environment, if asked.

    THE CHICKEN-AND-EGG PROBLEM: on a fresh database there is no user, so there
    is nobody who can create one, and self-service signup would only produce an
    ordinary org owner. `NXR_BOOTSTRAP_ADMIN_EMAIL` + `NXR_BOOTSTRAP_ADMIN_PASSWORD`
    solve it once.

    Idempotent and one-shot in two independent ways: it returns immediately if
    the email already exists, and it refuses to run at all once ANY user exists
    unless `NXR_BOOTSTRAP_FORCE` is set. That second guard is what stops a
    variable left in a task definition from being a standing "create an admin"
    instruction — the credential is meant to be used once and removed.
    """
    email = os.environ.get("NXR_BOOTSTRAP_ADMIN_EMAIL", "").strip()
    password = os.environ.get("NXR_BOOTSTRAP_ADMIN_PASSWORD", "")
    if not email or not password:
        return None

    if store.get_user_by_email(email) is not None:
        return None

    forced = str(os.environ.get("NXR_BOOTSTRAP_FORCE", "")).strip().lower() \
        in ("1", "true", "yes", "on")
    if store.count_users() > 0 and not forced:
        return None

    try:
        validate_strength(password, email=email)
    except PasswordError as e:
        raise RuntimeError(
            f"NXR_BOOTSTRAP_ADMIN_PASSWORD is not acceptable: {e}") from None

    user = store.create_user(email, hash_password(password), name="Platform Admin",
                             is_platform_admin=True, email_verified=True)
    org_name = os.environ.get("NXR_BOOTSTRAP_ORG", "NextXR").strip() or "NextXR"
    org = store.create_org(org_name)
    store.add_member(org.org_id, user.user_id, ROLE_OWNER)
    store.audit("platform.bootstrap", org_id=org.org_id, actor_user=user.user_id,
                target_type="user", target_id=user.user_id)
    return user.user_id


def posture() -> list[str]:
    """Lines describing the identity configuration, logged at boot alongside the
    `[auth]` / `[db]` lines. An operator reading CloudWatch after a rollout should
    be able to see whether signup is open and whether tokens survive a restart."""
    lines = []
    if tokens.has_configured_secret():
        weak = " (WEAK: under 32 characters)" if tokens.secret_is_weak() else ""
        lines.append(f"[identity] JWT signing key configured{weak}; "
                     f"access TTL {tokens.access_ttl()}s, "
                     f"refresh TTL {tokens.refresh_ttl()}s")
    else:
        lines.append("[identity] !! NXR_JWT_SECRET unset - tokens are signed with "
                     "a per-process random key. Sessions die on restart and each "
                     "task rejects the others' tokens. Fine locally; set it before "
                     "running more than one task.")
    from .passwords import preferred_algorithm
    lines.append(f"[identity] password hashing: {preferred_algorithm()}")
    if not os.environ.get("NXR_SECRET_PEPPER", "").strip():
        lines.append("[identity] NXR_SECRET_PEPPER unset - API key and refresh "
                     "token hashes are unpeppered.")
    lines.append(f"[identity] self-service signup: "
                 f"{'OPEN' if signup_allowed() else 'closed'}")
    return lines
