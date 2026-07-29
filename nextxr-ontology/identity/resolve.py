"""resolve.py — credential → Principal.

Both doors into the API end here. A browser arrives with `Authorization: Bearer
<jwt>`; a machine arrives with `X-API-Key: nxr_live_...`. Each is turned into the
SAME `Principal`, so `server/tenancy.py` builds a Scope without knowing which was
used and no route has to ask.

WHY THE API-KEY LOOKUP IS CACHED AND THE TOKEN CHECK IS NOT
-----------------------------------------------------------
An access token verifies with a signature check — no I/O, so there is nothing to
cache. An API key is a database row, and looking it up on every request would put
an RDS round trip in front of every call including the hot ingest path. So key
resolution is memoised for a few seconds.

That TTL is the revocation delay, and it is bounded deliberately: revoking a key
takes effect within `_CACHE_TTL` seconds everywhere, on every task, with no
cross-task invalidation machinery. Five seconds is short enough that "revoke this
leaked key" is honest and long enough to absorb a burst from one client. The
cache stores only ACTIVE keys — a miss (unknown, revoked, expired) is never
cached, so a revoked key is never served from memory after its entry ages out,
and an attacker cannot use a flood of bad keys to evict the good ones.
"""
from __future__ import annotations

import threading
import time

from . import store, tokens
from .models import ROLE_ADMIN, ROLE_READ, Principal

_CACHE_TTL = 5.0
_key_cache: dict[str, tuple[float, Principal]] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    """Drop the API-key cache. Called by tests, and by the route that revokes a
    key so the revocation is instant on at least the task that served it."""
    with _cache_lock:
        _key_cache.clear()


def principal_from_api_key(secret: str) -> Principal | None:
    """Resolve an `X-API-Key` value, or None if it is not a valid live key."""
    if not secret:
        return None

    now = time.monotonic()
    with _cache_lock:
        hit = _key_cache.get(secret)
        if hit is not None and hit[0] > now:
            return hit[1]

    record = store.find_api_key(secret)
    if record is None:
        return None

    org = store.get_org(record.org_id)
    if org is not None and not org.is_active:
        # A suspended organisation's keys stop working. Billing and offboarding
        # both need this, and doing it here means it applies to every route at
        # once rather than being a check each one could forget.
        return None

    # A key with an explicit tenant list is narrowed to it. With none, it reaches
    # everything its org owns — resolved live rather than frozen into the record,
    # so a twin created tomorrow is reachable by a key issued today.
    tenant_scope = record.tenants or None

    principal = Principal(
        kind="api_key",
        key_id=record.key_id,
        org_id=record.org_id,
        role=record.role,
        tenants=tenant_scope,
        label=record.name or record.prefix,
    )

    with _cache_lock:
        _key_cache[secret] = (now + _CACHE_TTL, principal)
        # Bound the cache. An unauthenticated caller cannot grow it — only
        # successful lookups are stored — but a large fleet of legitimate keys
        # should not grow it without limit either.
        if len(_key_cache) > 2048:
            cutoff = time.monotonic()
            for cached_key, (expiry, _) in list(_key_cache.items()):
                if expiry <= cutoff:
                    _key_cache.pop(cached_key, None)

    store.touch_api_key(record.key_id)
    return principal


def principal_from_access_token(token: str) -> Principal | None:
    """Resolve a Bearer access token, or None if it does not verify.

    The claims are trusted for the token's lifetime — see the note in `tokens.py`
    on why role changes revoke sessions rather than being re-read here. The one
    thing NOT taken on trust is the session: a stateless token whose session was
    revoked (logout, password change, reuse detection) must stop working
    immediately, so the session row is checked. That is one indexed lookup, and
    it is what makes "sign out everywhere" mean anything.
    """
    if not token:
        return None
    try:
        claims = tokens.decode_access(token)
    except tokens.TokenError:
        return None

    if claims.session_id:
        session = store.get_session(claims.session_id)
        if not store.session_is_valid(session):
            return None

    return Principal(
        kind="user",
        user_id=claims.user_id,
        session_id=claims.session_id,
        org_id=claims.org_id,
        role=claims.role,
        email=claims.email,
        is_platform_admin=claims.is_platform_admin,
        tenants=None,
        label=claims.email or claims.user_id,
    )


def tenants_for(principal: Principal) -> frozenset[str] | None:
    """The tenant set a principal may reach, or None for unrestricted.

    THE resolution that replaced the tenant-prefix string convention. A platform
    admin gets None (everything). Everyone else gets exactly the tenants their
    organisation owns, intersected with any narrowing on their key — so a tenant
    absent from `org_tenants` is unreachable, whatever it is named.
    """
    if principal.is_platform_admin:
        return None

    if not principal.org_id:
        return frozenset()                 # authenticated, member of nothing

    owned = frozenset(store.org_tenants(principal.org_id))
    if principal.tenants is None:
        return owned
    return frozenset(principal.tenants) & owned


def principal_for_device(device) -> Principal:
    """A telemetry gateway. Write-only, pinned to its own single tenant — the
    posture `ingest/devices.py` describes, expressed as a Principal so it flows
    through the same authorization path as everything else."""
    return Principal(
        kind="device",
        key_id=getattr(device, "device_id", ""),
        org_id=store.tenant_owner(getattr(device, "tenant_id", "")),
        role="write",
        tenants=(getattr(device, "tenant_id", ""),),
        label=f"device:{getattr(device, 'device_id', '')}",
    )


def platform_admin_principal(label: str = "bootstrap") -> Principal:
    """An unrestricted principal, used only where the process itself is acting
    (schema provisioning, the CLI). Never constructed from a request."""
    return Principal(kind="user", role=ROLE_ADMIN, is_platform_admin=True,
                     label=label)


def anonymous_principal() -> Principal:
    return Principal(kind="anonymous", role=ROLE_READ, tenants=(),
                     label="anonymous")
