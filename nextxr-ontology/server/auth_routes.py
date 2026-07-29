"""auth_routes.py — the HTTP surface of `identity/`.

    POST /api/v1/auth/signup            create a user + their organisation
    POST /api/v1/auth/login             email + password  -> access + refresh
    POST /api/v1/auth/refresh           rotate a refresh token
    POST /api/v1/auth/logout            revoke this session
    GET  /api/v1/auth/me                the caller, their org and their orgs
    POST /api/v1/auth/switch-org        re-issue tokens for another org
    POST /api/v1/auth/password/change   change, given the current password
    POST /api/v1/auth/password/forgot   start a reset (always 200)
    POST /api/v1/auth/password/reset    complete a reset with the token
    POST /api/v1/auth/verify-email      redeem an email-verification token
    GET  /api/v1/auth/sessions          this user's active sessions
    DELETE /api/v1/auth/sessions/{id}   revoke one
    GET/POST/PATCH/DELETE  .../orgs/{org}/members     membership management
    GET/POST/DELETE        .../orgs/{org}/keys        API-key management
    GET  /api/v1/auth/orgs/{org}/audit  the account audit trail

WHERE THE REFRESH TOKEN LIVES
-----------------------------
It is returned in the response body AND set as an HttpOnly, SameSite=Lax cookie.
Both, on purpose, because the two clients have different constraints:

  * The SPA is served from the same origin as the API, so it uses the cookie —
    HttpOnly means XSS cannot read it, which is the whole reason not to keep a
    30-day credential in localStorage.
  * A native/CLI/server-side client has no cookie jar and reads the body.

The ACCESS token is never a cookie. It goes in the Authorization header, which is
what makes the API immune to CSRF: a cross-site form post carries cookies but
cannot set that header. The refresh cookie is SameSite=Lax so it is not sent on
cross-site POSTs either, and /auth/refresh is the only route that reads it.

WHY THESE ROUTES ARE EXEMPT FROM `enforce_tenant_scope`
--------------------------------------------------------
They carry an `org`, not a `tenant`, and the global tenant dependency has nothing
to authorize on them. Account-level permission is checked inside
`identity/service.py` (`_require_role`), which verifies the actor's role IN THE
ORG NAMED IN THE PATH — the same class of check, applied to a different noun.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from identity import Principal, service, store, tokens
from identity.models import API_KEY_ROLES, ROLE_ADMIN, ROLES
from identity.passwords import PasswordError
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

REFRESH_COOKIE = "nxr_refresh"


# ── Request/response models ─────────────────────────────────────────────


class SignupReq(BaseModel):
    email: str
    password: str
    name: str = ""
    org_name: str = ""


class LoginReq(BaseModel):
    email: str
    password: str
    org_id: str = ""


class RefreshReq(BaseModel):
    refresh_token: str = ""


class SwitchOrgReq(BaseModel):
    org_id: str


class ChangePasswordReq(BaseModel):
    current_password: str
    new_password: str


class ForgotReq(BaseModel):
    email: str


class ResetReq(BaseModel):
    token: str
    new_password: str


class VerifyEmailReq(BaseModel):
    token: str


class InviteReq(BaseModel):
    email: str
    role: str = "read"
    name: str = ""


class RoleReq(BaseModel):
    role: str


class KeyReq(BaseModel):
    name: str = ""
    role: str = "read"
    tenants: list[str] = Field(default_factory=list)
    expires_in_days: int | None = None


# ── Helpers ─────────────────────────────────────────────────────────────


def _client_ip(request: Request) -> str:
    """The caller's address, trusting X-Forwarded-For only behind a proxy we said
    is there. Reading that header unconditionally lets any client forge the
    address recorded in the audit log and used as a rate-limit key, so it is read
    only when NXR_TRUST_PROXY is set — which it is on ECS, behind the ALB."""
    if str(os.environ.get("NXR_TRUST_PROXY", "")).strip().lower() in ("1", "true", "yes", "on"):
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            # Leftmost entry is the original client; the rest are proxies.
            return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


def _user_agent(request: Request) -> str:
    return (request.headers.get("User-Agent") or "")[:400]


def _cookie_secure() -> bool:
    """Whether to mark the refresh cookie Secure.

    On by default and disabled only for plain-HTTP local dev, because a Secure
    cookie is simply not sent over http://localhost and the login would appear to
    succeed and then immediately fail to refresh.
    """
    raw = os.environ.get("NXR_COOKIE_SECURE", "").strip().lower()
    if raw:
        return raw in ("1", "true", "yes", "on")
    from server.auth import auth_required
    return auth_required()


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE, token,
        max_age=tokens.refresh_ttl(),
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        # Scoped to the auth surface: the cookie is only ever needed by
        # /auth/refresh and /auth/logout, so it is not attached to the hundreds
        # of ordinary API calls the SPA makes.
        path="/api/v1/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth",
                           httponly=True, samesite="lax",
                           secure=_cookie_secure())


def _session_payload(result: dict) -> dict:
    """The body returned by login / refresh / switch-org."""
    user = result["user"]
    return {
        "access_token": result["access_token"],
        "refresh_token": result["refresh_token"],
        "token_type": "Bearer",
        "expires_in": result["expires_in"],
        "user": user.public(),
        "org_id": result["org_id"],
        "role": result["role"],
        "orgs": result["orgs"],
    }


def current_principal(request: Request) -> Principal:
    """The authenticated caller, or 401.

    `AuthMiddleware` has already resolved and stashed this. Re-deriving it here
    would be a second implementation of authentication, which is how the two
    drift apart.
    """
    principal = getattr(request.state, "principal", None)
    if principal is None or principal.kind == "anonymous":
        raise HTTPException(status_code=401, detail="Authentication required.")
    return principal


def current_user_principal(request: Request) -> Principal:
    """A caller who is a PERSON. Routes that manage the signed-in user's own
    account (change password, list sessions) require this: an API key has no
    password to change and no sessions to list, and letting it act on a user's
    behalf would make a machine credential able to reset a human's access."""
    principal = current_principal(request)
    if principal.kind != "user" or not principal.user_id:
        raise HTTPException(
            status_code=403,
            detail="This endpoint requires a signed-in user, not an API key.")
    return principal


def _handle(exc: Exception) -> HTTPException:
    """Map an identity exception onto its HTTP status. One place, so a new route
    cannot invent a different status for the same condition."""
    if isinstance(exc, service.AuthFailed):
        return HTTPException(status_code=401, detail=str(exc))
    if isinstance(exc, service.Forbidden):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, service.Conflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, PasswordError):
        return HTTPException(status_code=422, detail=str(exc))
    raise exc


# ── Signup / login / refresh / logout ───────────────────────────────────


@router.post("/signup")
async def signup(body: SignupReq, request: Request, response: Response):
    if not service.signup_allowed():
        raise HTTPException(
            status_code=403,
            detail="Self-service signup is disabled. Ask an administrator for "
                   "an invitation.")
    try:
        created = service.signup(
            email=body.email, password=body.password, name=body.name,
            org_name=body.org_name, ip=_client_ip(request),
            user_agent=_user_agent(request))
    except Exception as e:
        raise _handle(e) from None

    # Sign the new user straight in. Requiring a separate login immediately after
    # signup is friction with no security value — the password was just proven.
    result = service.login(email=body.email, password=body.password,
                           ip=_client_ip(request),
                           user_agent=_user_agent(request))
    _set_refresh_cookie(response, result["refresh_token"])
    payload = _session_payload(result)
    # Only present when a mailer is configured to send it; the token is returned
    # so a deployment without one can still complete the flow out of band.
    if created.get("verify_token"):
        payload["verify_token"] = created["verify_token"]
    return payload


@router.post("/login")
async def login(body: LoginReq, request: Request, response: Response):
    try:
        result = service.login(email=body.email, password=body.password,
                               org_id=body.org_id, ip=_client_ip(request),
                               user_agent=_user_agent(request))
    except Exception as e:
        raise _handle(e) from None
    _set_refresh_cookie(response, result["refresh_token"])
    return _session_payload(result)


@router.post("/refresh")
async def refresh(request: Request, response: Response,
                  body: RefreshReq | None = None):
    """Rotate. The cookie is preferred over the body: a browser always has it,
    and preferring it means the SPA never has to hold the refresh token in
    JavaScript at all."""
    token = request.cookies.get(REFRESH_COOKIE) or (body.refresh_token if body else "")
    if not token:
        raise HTTPException(status_code=401, detail="No refresh token supplied.")
    try:
        result = service.refresh(refresh_token=token, ip=_client_ip(request),
                                 user_agent=_user_agent(request))
    except Exception as e:
        # A failed refresh must clear the cookie, or the browser retries a dead
        # token forever and the user sees a login page that will not stick.
        _clear_refresh_cookie(response)
        raise _handle(e) from None
    _set_refresh_cookie(response, result["refresh_token"])
    return _session_payload(result)


@router.post("/logout")
async def logout(request: Request, response: Response,
                 body: RefreshReq | None = None):
    token = request.cookies.get(REFRESH_COOKIE) or (body.refresh_token if body else "")
    principal = getattr(request.state, "principal", None)
    session_id = principal.session_id if principal else ""
    service.logout(refresh_token=token, session_id=session_id or "")
    _clear_refresh_cookie(response)
    return {"ok": True}


@router.get("/me")
async def me(request: Request, principal: Principal = Depends(current_principal)):
    """Who the caller is. Serves both credential types — an API key reports its
    org and role with `user: null`, which is what a CLI needs to confirm which
    key it is holding."""
    orgs = []
    user_payload = None
    if principal.user_id:
        user = store.get_user(principal.user_id)
        if user is not None:
            user_payload = user.public()
        for membership in store.list_memberships_for_user(principal.user_id):
            org = store.get_org(membership.org_id)
            if org is not None:
                orgs.append({"org_id": org.org_id, "name": org.name,
                             "role": membership.role, "status": org.status})

    org = store.get_org(principal.org_id) if principal.org_id else None
    from identity import tenants_for
    reachable = tenants_for(principal)

    return {
        "kind": principal.kind,
        "user": user_payload,
        "org": {"org_id": org.org_id, "name": org.name, "plan": org.plan}
               if org else None,
        "role": principal.role,
        "is_platform_admin": principal.is_platform_admin,
        "orgs": orgs,
        "key_id": principal.key_id,
        # `null` means unrestricted (platform admin). A list is exactly what this
        # caller can reach, which is what the UI's twin picker renders.
        "tenants": None if reachable is None else sorted(reachable),
    }


@router.post("/switch-org")
async def switch_org(body: SwitchOrgReq, request: Request, response: Response,
                     principal: Principal = Depends(current_user_principal)):
    try:
        result = service.switch_org(
            user_id=principal.user_id, session_id=principal.session_id or "",
            org_id=body.org_id, ip=_client_ip(request),
            user_agent=_user_agent(request))
    except Exception as e:
        raise _handle(e) from None
    _set_refresh_cookie(response, result["refresh_token"])
    return _session_payload(result)


# ── Passwords ───────────────────────────────────────────────────────────


@router.post("/password/change")
async def change_password(body: ChangePasswordReq,
                          principal: Principal = Depends(current_user_principal)):
    try:
        service.change_password(
            user_id=principal.user_id, current_password=body.current_password,
            new_password=body.new_password,
            keep_session=principal.session_id or "")
    except Exception as e:
        raise _handle(e) from None
    return {"ok": True, "message": "Password changed. Other sessions were signed out."}


@router.post("/password/forgot")
async def forgot_password(body: ForgotReq, request: Request):
    """Always 200, always the same message.

    A different response for a known and an unknown address turns this into a
    bulk account-existence oracle that needs no credential. The token is returned
    ONLY when no mailer is configured (local dev), so the flow is completable
    offline without that being a production disclosure.
    """
    token = service.request_password_reset(email=body.email, ip=_client_ip(request))
    payload = {
        "ok": True,
        "message": "If that email has an account, a reset link is on its way.",
    }
    if token and not _mailer_configured():
        payload["reset_token"] = token
        payload["note"] = ("No mailer is configured, so the token is returned "
                           "here. Set NXR_MAIL_FROM to send it by email instead.")
    return payload


def _mailer_configured() -> bool:
    return bool(os.environ.get("NXR_MAIL_FROM", "").strip())


@router.post("/password/reset")
async def reset_password(body: ResetReq):
    try:
        service.complete_password_reset(token=body.token,
                                        new_password=body.new_password)
    except Exception as e:
        raise _handle(e) from None
    return {"ok": True, "message": "Password reset. Sign in with your new password."}


@router.post("/verify-email")
async def verify_email(body: VerifyEmailReq):
    if not service.verify_email(token=body.token):
        raise HTTPException(status_code=400,
                            detail="This link is invalid or has expired.")
    return {"ok": True}


# ── Sessions ────────────────────────────────────────────────────────────


@router.get("/sessions")
async def list_sessions(principal: Principal = Depends(current_user_principal)):
    sessions = store.list_sessions(principal.user_id)
    return {"sessions": [
        {**s.public(), "current": s.session_id == principal.session_id}
        for s in sessions]}


@router.delete("/sessions/{session_id}")
async def revoke_session(session_id: str,
                         principal: Principal = Depends(current_user_principal)):
    session = store.get_session(session_id)
    # Check ownership before acting. Without it, session ids from one user would
    # be revocable by another — a denial-of-service against any account whose
    # session id leaked into a log.
    if session is None or session.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="No such session.")
    store.revoke_session(session_id)
    store.audit("session.revoked", org_id=session.org_id or "",
                actor_user=principal.user_id, target_type="session",
                target_id=session_id)
    return {"ok": True}


@router.post("/sessions/revoke-others")
async def revoke_other_sessions(principal: Principal = Depends(current_user_principal)):
    count = store.revoke_user_sessions(principal.user_id,
                                       except_session=principal.session_id or "")
    return {"ok": True, "revoked": count}


# ── Organisation: members ───────────────────────────────────────────────


@router.get("/orgs/{org_id}/members")
async def list_members(org_id: str,
                       principal: Principal = Depends(current_principal)):
    _require_org_access(principal, org_id)
    return {"members": [
        {**user.public(), "role": membership.role}
        for membership, user in store.list_members(org_id)]}


@router.post("/orgs/{org_id}/members")
async def invite_member(org_id: str, body: InviteReq,
                        principal: Principal = Depends(current_principal)):
    if body.role not in ROLES:
        raise HTTPException(status_code=422,
                            detail=f"role must be one of {', '.join(ROLES)}.")
    try:
        result = service.invite_member(org_id=org_id, email=body.email,
                                       role=body.role, actor=principal,
                                       name=body.name)
    except Exception as e:
        raise _handle(e) from None
    payload = {"user": result["user"].public(), "role": result["role"]}
    if result.get("reset_token") and not _mailer_configured():
        payload["invite_token"] = result["reset_token"]
        payload["note"] = ("No mailer is configured. Send this token to the user "
                           "so they can set a password at /reset-password.")
    return payload


@router.patch("/orgs/{org_id}/members/{user_id}")
async def set_member_role(org_id: str, user_id: str, body: RoleReq,
                          principal: Principal = Depends(current_principal)):
    if body.role not in ROLES:
        raise HTTPException(status_code=422,
                            detail=f"role must be one of {', '.join(ROLES)}.")
    try:
        service.set_member_role(org_id=org_id, user_id=user_id, role=body.role,
                                actor=principal)
    except Exception as e:
        raise _handle(e) from None
    return {"ok": True}


@router.delete("/orgs/{org_id}/members/{user_id}")
async def remove_member(org_id: str, user_id: str,
                        principal: Principal = Depends(current_principal)):
    try:
        service.remove_member(org_id=org_id, user_id=user_id, actor=principal)
    except Exception as e:
        raise _handle(e) from None
    return {"ok": True}


# ── Organisation: API keys ──────────────────────────────────────────────


@router.get("/orgs/{org_id}/keys")
async def list_keys(org_id: str, include_revoked: bool = False,
                    principal: Principal = Depends(current_principal)):
    _require_org_role(principal, org_id, ROLE_ADMIN)
    return {"keys": [k.public()
                     for k in store.list_api_keys(org_id,
                                                  include_revoked=include_revoked)]}


@router.post("/orgs/{org_id}/keys")
async def create_key(org_id: str, body: KeyReq,
                     principal: Principal = Depends(current_principal)):
    if body.role not in API_KEY_ROLES:
        raise HTTPException(
            status_code=422,
            detail=f"An API key's role must be one of {', '.join(API_KEY_ROLES)}.")
    try:
        record, secret = service.issue_api_key(
            org_id=org_id, name=body.name, role=body.role, actor=principal,
            tenants=body.tenants, expires_in_days=body.expires_in_days)
    except Exception as e:
        raise _handle(e) from None
    return {
        **record.public(),
        # The ONLY time this value exists outside the client's hands.
        "secret": secret,
        "warning": "Copy this key now — it is not stored and cannot be shown again.",
    }


@router.delete("/orgs/{org_id}/keys/{key_id}")
async def revoke_key(org_id: str, key_id: str,
                     principal: Principal = Depends(current_principal)):
    record = store.get_api_key(key_id)
    if record is None or record.org_id != org_id:
        raise HTTPException(status_code=404, detail="No such key.")
    try:
        service.revoke_api_key(key_id=key_id, actor=principal)
    except Exception as e:
        raise _handle(e) from None
    # Drop this task's resolution cache so the revocation is immediate here. Other
    # tasks honour it within the cache TTL (identity/resolve.py).
    from identity import clear_cache
    clear_cache()
    return {"ok": True}


# ── Organisation: audit ─────────────────────────────────────────────────


@router.get("/orgs/{org_id}/audit")
async def org_audit(org_id: str, limit: int = 200, action: str = "",
                    principal: Principal = Depends(current_principal)):
    _require_org_role(principal, org_id, ROLE_ADMIN)
    return {"events": store.list_audit(org_id, limit=limit, action=action)}


@router.get("/orgs/{org_id}/tenants")
async def org_tenant_list(org_id: str,
                          principal: Principal = Depends(current_principal)):
    """Which twins this organisation owns. The UI's twin picker reads this rather
    than the global twin listing, so the list is a fact about ownership rather
    than a filtered view of everything."""
    _require_org_access(principal, org_id)
    return {"tenants": list(store.org_tenants(org_id))}


def _require_org_access(principal: Principal, org_id: str) -> None:
    if principal.is_platform_admin:
        return
    if principal.org_id != org_id:
        # 403 rather than 404: the org id came from the caller, so confirming it
        # exists tells them nothing they did not supply.
        raise HTTPException(status_code=403,
                            detail="You do not have access to that organisation.")


def _require_org_role(principal: Principal, org_id: str, minimum: str) -> None:
    _require_org_access(principal, org_id)
    if principal.is_platform_admin:
        return
    from identity import role_at_least
    if not role_at_least(principal.role, minimum):
        raise HTTPException(status_code=403,
                            detail=f"This action requires the {minimum} role.")
