"""tokens.py — the signed access token, and the secret that signs it.

THE TWO-TOKEN SHAPE, AND WHY
----------------------------
A login issues two credentials with deliberately opposite properties:

  ACCESS TOKEN   a signed JWT, ~15 minutes, stateless. Every API request carries
                 it. Verification is a signature check — no database round trip
                 — which is what keeps per-request latency flat as the fleet
                 grows. The cost of statelessness is that it cannot be revoked
                 before it expires, so its lifetime is short.

  REFRESH TOKEN  an opaque 256-bit random string, ~30 days, stored HASHED in the
                 `sessions` table. It is presented only to /auth/refresh. Because
                 it is a database row, revoking it is immediate and real: "log
                 out everywhere" and "this employee left" both work.

Making the access token stateless and the refresh token stateful is the whole
design. The reverse — a database lookup per request, or a 30-day JWT — is either
slow or unrevocable.

WHY THE ACCESS TOKEN CARRIES ORG AND ROLE
-----------------------------------------
The claims include the org the session is acting for and the role the user holds
there, so `server/tenancy.py` can build a Scope from the token alone. That is a
cache, and like every cache it can go stale: a role revoked mid-token is still
honoured for up to the token's lifetime. That is the explicit trade for the
15-minute expiry, and it is why role CHANGES revoke the user's sessions
(`store.revoke_user_sessions`) rather than relying on the next refresh.

THE SIGNING SECRET
------------------
HS256 with NXR_JWT_SECRET. In production it must be set — `require_secret()` is
called at startup and refuses to boot without it — because the fallback is a
per-process random value, which is correct for local dev (tokens die with the
process, no configuration needed) and catastrophic silently in a fleet: two
tasks would mint tokens the other rejects, so every other request would 401 with
nothing in the logs to explain it.
"""
from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass
from typing import Any

import jwt

ALGORITHM = "HS256"
ISSUER = "nextxr-twin"

# Defaults chosen in the paragraph above; both are overridable so a deployment
# with a stricter posture can shorten them without a code change.
DEFAULT_ACCESS_TTL_SECONDS = 15 * 60
DEFAULT_REFRESH_TTL_SECONDS = 30 * 24 * 3600

_dev_secret: str = ""


class TokenError(Exception):
    """An access token that cannot be trusted. Always a 401, never a 500."""


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default
    return value if value > 0 else default


def access_ttl() -> int:
    return _int_env("NXR_ACCESS_TTL", DEFAULT_ACCESS_TTL_SECONDS)


def refresh_ttl() -> int:
    return _int_env("NXR_REFRESH_TTL", DEFAULT_REFRESH_TTL_SECONDS)


def signing_secret() -> str:
    """The HS256 key. Falls back to a per-process random value for local dev.

    The fallback is generated ONCE per process and memoised, so tokens stay valid
    for the life of a `npm run dev` session. It is never used when
    NXR_JWT_SECRET is set, and `require_secret()` makes a production boot fail
    rather than reach it.
    """
    global _dev_secret
    configured = os.environ.get("NXR_JWT_SECRET", "").strip()
    if configured:
        return configured
    if not _dev_secret:
        _dev_secret = secrets.token_urlsafe(48)
    return _dev_secret


def has_configured_secret() -> bool:
    return bool(os.environ.get("NXR_JWT_SECRET", "").strip())


def require_secret() -> None:
    """Fail the boot when auth is enforced but the signing key is ephemeral.

    Called from the startup posture check. The failure being LOUD and at boot is
    the point: the alternative symptom is intermittent 401s under a load
    balancer, which reads as a client bug and is miserable to trace back to a
    missing environment variable.
    """
    if has_configured_secret():
        return
    from server.auth import auth_required
    if auth_required():
        raise RuntimeError(
            "NXR_JWT_SECRET is not set but authentication is enforced. Each task "
            "would sign tokens with its own random key, so a token minted by one "
            "task is rejected by every other one. Set NXR_JWT_SECRET (32+ random "
            "bytes, from Secrets Manager) in the task definition.")


def secret_is_weak() -> bool:
    """A configured-but-too-short secret. 32 characters is the floor for HS256 to
    carry its nominal strength; shorter is brute-forceable offline from a single
    captured token."""
    configured = os.environ.get("NXR_JWT_SECRET", "").strip()
    return bool(configured) and len(configured) < 32


# ── Access tokens ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class AccessClaims:
    """A verified access token's contents. Constructed ONLY by `decode_access`,
    so holding one of these means the signature has already been checked."""
    user_id: str
    session_id: str
    org_id: str | None
    role: str
    email: str
    is_platform_admin: bool
    expires_at: int

    @property
    def seconds_remaining(self) -> int:
        return max(0, self.expires_at - int(time.time()))


def issue_access(*, user_id: str, session_id: str, org_id: str | None,
                 role: str, email: str = "",
                 is_platform_admin: bool = False,
                 ttl: int | None = None) -> tuple[str, int]:
    """Mint a signed access token. Returns (token, seconds_until_expiry)."""
    lifetime = ttl if ttl is not None else access_ttl()
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": user_id,
        "sid": session_id,
        "org": org_id or "",
        "role": role,
        "email": email,
        "adm": bool(is_platform_admin),
        "iss": ISSUER,
        "iat": now,
        "nbf": now,
        "exp": now + lifetime,
        # A token type claim, checked on decode. Without it a refresh token that
        # was ever signed (they are not, but a future change might) would be
        # accepted as an access token — the classic JWT confused-deputy bug.
        "typ": "access",
    }
    return jwt.encode(payload, signing_secret(), algorithm=ALGORITHM), lifetime


def decode_access(token: str) -> AccessClaims:
    """Verify and decode an access token, or raise TokenError.

    `algorithms` is pinned to a single value. Passing the list the library
    accepts by default would let a token declare `"alg": "none"` and be honoured
    — the original JWT vulnerability, and still the one worth spelling out.
    """
    if not token:
        raise TokenError("missing token")
    try:
        payload = jwt.decode(
            token, signing_secret(), algorithms=[ALGORITHM], issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
    except jwt.ExpiredSignatureError:
        raise TokenError("token expired") from None
    except jwt.InvalidTokenError as e:
        raise TokenError(f"invalid token: {type(e).__name__}") from None

    if payload.get("typ") != "access":
        raise TokenError("wrong token type")

    user_id = str(payload.get("sub") or "")
    if not user_id:
        raise TokenError("token has no subject")

    return AccessClaims(
        user_id=user_id,
        session_id=str(payload.get("sid") or ""),
        org_id=(str(payload.get("org")) or None) if payload.get("org") else None,
        role=str(payload.get("role") or "read"),
        email=str(payload.get("email") or ""),
        is_platform_admin=bool(payload.get("adm")),
        expires_at=int(payload.get("exp") or 0),
    )


def bearer_from_header(value: str | None) -> str:
    """Extract the token from an `Authorization: Bearer <token>` header.

    Returns "" for anything that is not a Bearer credential, so a Basic or
    Negotiate header falls through to the other authenticators rather than being
    reported as a malformed JWT.
    """
    if not value:
        return ""
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


# ── Refresh tokens ──────────────────────────────────────────────────────


def issue_refresh() -> str:
    """A new opaque refresh token. Stored hashed; this plaintext is returned to
    the client once and never recoverable."""
    from .passwords import generate_secret
    return generate_secret("nxr_rt_", nbytes=32)
