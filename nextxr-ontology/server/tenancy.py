"""
tenancy.py — the ONE place that decides which tenants a request may touch.

WHY THIS EXISTS
---------------
Tenant isolation used to be enforced in exactly one line of `auth.py`::

    tenant = request.query_params.get("tenant")
    if tenant and not check_tenant_access(key_info, tenant): ...

That check is correct and it is also almost never reached, because the tenant
arrives three different ways and only one of them is a query parameter:

    query param   /api/v1/entities?tenant=acme            <- was checked
    path param    /api/v1/twins/acme/state                 <- was NOT checked
    request body  POST /api/v1/copilot/diagnosis {"tenant": "acme"}
                                                           <- was NOT checked

So a key scoped to `demo-tenant` could read, project, throttle and inject faults
into ANY other tenant's twin through `/api/v1/twins/{tenant}/...`, and could run
every LLM agent against another tenant's live diagnostics. Both of those are
cross-customer data disclosure, which is the one bug that ends an enterprise
deal on the spot.

The fix is deliberately NOT "add the missing checks to the routes that need
them". That is how the hole appeared in the first place: enforcement was
per-route, so a route added later simply never opted in. Instead this module is
installed as a GLOBAL FastAPI dependency (see `server/main.py`), so it runs for
every `/api` request, resolves EVERY tenant identifier the request carries no
matter which of the three carriers it used, and authorizes all of them. A new
route is covered the moment it exists, and a route that carries no tenant at all
is a no-op.

WHY A DEPENDENCY AND NOT MIDDLEWARE
-----------------------------------
Middleware runs BEFORE routing, so `request.path_params` is still empty there —
it cannot see the `{tenant}` in `/api/v1/twins/{tenant}/state`, which is the
exact hole we are closing. A dependency runs after the route is matched and
after FastAPI has buffered the body, so all three carriers are visible. Reading
the body here is safe and cheap: FastAPI has already called `request.body()`, so
`await request.body()` returns its cache rather than touching the socket again.

SCOPE MODEL (and where the real hierarchy lives)
------------------------------------------------
A caller's scope is a set of tenant ids, or `None` meaning "all tenants" (admin).
An API key may declare its scope four ways, in `NXR_API_KEYS`::

    {"tenant": "*"}                      admin - every tenant
    {"tenant": "acme"}                   exactly one tenant (back-compat)
    {"tenants": ["acme", "acme-2"]}      an explicit set
    {"tenant_prefix": "acme-"}           every tenant id under a prefix

`tenants` and `tenant_prefix` are new. They exist because the old model could
not express "this customer owns 50 twins" without handing out `"*"`, which also
grants every OTHER customer's data — so the only way to serve a real enterprise
account was to over-grant. A prefix scope lets one customer hold many twins with
no access to anyone else's.

This is NOT the full Org -> Workspace -> Site -> Twin hierarchy. That lives in
the Integration Hub, which owns login and org membership. This module is the
enforcement point that hierarchy projects onto: the Hub forwards the caller's
org, and `_hub_narrowing()` intersects it with the key's scope. Note the
direction — a forwarded header can only ever NARROW what the API key already
allows, never widen it. Anything else would make an HTTP header a privilege
escalation, since headers are attacker-controlled unless the gateway is the only
possible ingress (and we must not depend on that being true).

FORMER RESIDUALS — BOTH NOW CLOSED
----------------------------------
Two surfaces used to be authenticated but not tenant-authorized. They are
recorded here with their fixes rather than deleted, because "we fixed tenant
isolation" must mean something precise, and the reasoning is what stops them
reopening:

1. AGENT SESSION READS — `GET /api/v1/agents/{twin,bundle,ops,plugin,
   accelerator}/{session_id}`. Keyed by session id, and an id is not a tenant, so
   this module had nothing to authorize and any holder of an id could read
   another tenant's agent transcript. Severity was lower than the path/body hole
   beside it (ids are `uuid4().hex[:12]`, ~48 bits, so they cannot be enumerated)
   but "unguessable" is not an access control.
   FIXED: `authorize_session_state()` below reads the `tenant_id` the state
   already carried and authorizes it. Each of the five routes calls it.

2. THE 3-D PLATFORM SUB-APP at `/api/v1/threed`. `app.mount()` does not pass
   app-level dependencies to the mounted application, so AuthMiddleware covered
   it but this module did not. The argument for leaving it was that its job store
   is keyed by job id and has no tenant column — true, and it held only while
   that stayed true, with nothing enforcing that it would.
   FIXED: `server/main.py` installs `enforce_tenant_scope` on the sub-app's own
   router before mounting it, so a tenant field added to that surface later is
   authorized from the moment it exists.

`POST /api/v1/feed/stop` remains unscoped, but the feed is a single per-process
demo loop rather than tenant state — a cross-tenant nuisance, not a disclosure.
It disappears when the simulated feed is replaced by real ingestion.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# Field names that carry a tenant id. `tenant` is the platform's convention;
# `tenant_id` appears in a few hub-facing payloads and in twin records, so both
# are honoured. Anything else must not be guessed — silently failing to spot a
# tenant identifier is the failure mode this module exists to prevent, so new
# carriers get added HERE and are covered everywhere at once.
TENANT_FIELDS = ("tenant", "tenant_id")

# Bodies larger than this are not scanned for a tenant field. The only routes
# with bodies this big are image uploads (`/build-twin/spec`, `/twin/upload`),
# whose payload is a base64 photo, and re-parsing megabytes of JSON on every
# such call is real CPU for no benefit. They are covered instead by
# `_LARGE_BODY_TENANT_ROUTES` below, which names the few that DO carry a tenant
# so they are still enforced — a size limit must never become a silent bypass.
MAX_BODY_SCAN_BYTES = 256 * 1024


@dataclass(frozen=True)
class Scope:
    """What one caller is allowed to touch.

    `tenants is None` means unrestricted (admin). An empty frozenset means the
    caller may touch NOTHING tenant-scoped, which is the correct answer for a
    key whose configuration we could not understand — failing closed on a
    malformed scope is the whole point.
    """
    tenants: frozenset[str] | None
    prefix: str | None
    role: str
    name: str

    @property
    def is_admin(self) -> bool:
        return self.tenants is None and not self.prefix

    def allows(self, tenant: str) -> bool:
        if not tenant:
            return True                      # nothing to authorize
        if self.tenants is None and not self.prefix:
            return True                      # admin
        if self.prefix and tenant.startswith(self.prefix):
            return True
        return bool(self.tenants) and tenant in self.tenants

    def describe(self) -> str:
        """Human-readable scope for a 403 body. Deliberately does NOT list other
        tenants' ids — an error message must not become an enumeration oracle."""
        if self.is_admin:
            return "all tenants"
        if self.prefix:
            return f"tenants beginning '{self.prefix}'"
        n = len(self.tenants or ())
        return f"{n} assigned tenant{'' if n == 1 else 's'}"


# An admin scope used when authentication is not enforced at all (local dev with
# no NXR_API_KEYS and no NXR_REQUIRE_AUTH). It keeps local behaviour exactly as
# it was, and `server/auth.py` is the layer responsible for making sure this
# cannot happen in a real deployment.
_DEV_ADMIN = Scope(tenants=None, prefix=None, role="admin", name="dev-open")


def scope_of(request: Request) -> Scope:
    """The caller's effective scope: their key's scope, narrowed by any trusted
    Hub identity. Cached on `request.state` because the global dependency, the
    list-filtering helpers and the route handlers all ask for it."""
    cached = getattr(request.state, "nxr_scope", None)
    if cached is not None:
        return cached

    # A device-authenticated request is pinned to that device's single tenant with
    # write-only rights. Without this it would fall through to `_DEV_ADMIN` (the
    # local-dev open scope), which would give a plant-room gateway an admin scope
    # over every tenant — the exact over-grant that per-device credentials exist
    # to avoid. The ingest route already ignores any tenant in the body, so this
    # is defence in depth rather than the only control, which is how it should be.
    device = getattr(request.state, "device", None)
    if device is not None:
        scope = Scope(tenants=frozenset({device.tenant_id}), prefix=None,
                      role="write", name=f"device:{device.device_id}")
        request.state.nxr_scope = scope
        return scope

    # A DATABASE-BACKED principal — a signed-in user, or a key issued through
    # `identity/`. Its reach is the set of tenants its ORGANISATION owns, read
    # live from `org_tenants`, intersected with any narrowing on the key itself.
    #
    # This is the change that retired the tenant-prefix convention. A prefix rule
    # answers "does this id start with the right characters", which silently
    # grants any tenant somebody names accordingly and cannot express a twin
    # transferred between customers. An ownership table answers "who owns this",
    # which is the question authorization actually needs.
    principal = getattr(request.state, "principal", None)
    if principal is not None and getattr(principal, "org_id", None):
        scope = _scope_from_principal(principal)
        scope = _hub_narrowing(request, scope)
        request.state.nxr_scope = scope
        return scope

    # A platform admin (our staff) with no org selected still reaches everything.
    if principal is not None and getattr(principal, "is_platform_admin", False):
        scope = _hub_narrowing(
            request, Scope(tenants=None, prefix=None, role=principal.role,
                           name=principal.label or "platform-admin"))
        request.state.nxr_scope = scope
        return scope

    key_info = getattr(request.state, "api_key", None)
    if key_info is not None:
        scope = _scope_from_key(key_info)
    elif _auth_is_enforced():
        # Authenticated as nothing, while authentication is mandatory. This is
        # reached by a principal with neither an org nor platform-admin — a user
        # whose last membership was removed, for instance. They authenticate and
        # reach NO tenant, which is the correct answer and must not fall through
        # to the dev-open admin scope below.
        scope = Scope(tenants=frozenset(), prefix=None, role="read",
                      name=getattr(principal, "label", None) or "unscoped")
    else:
        scope = _DEV_ADMIN
    scope = _hub_narrowing(request, scope)

    request.state.nxr_scope = scope
    return scope


def _auth_is_enforced() -> bool:
    try:
        from server.auth import auth_required
        return auth_required()
    except Exception:
        return True          # if we cannot tell, assume the strict posture


def _scope_from_principal(principal) -> Scope:
    """Project an `identity.Principal` onto a Scope.

    `tenants_for()` returns None for a platform admin (unrestricted) and an
    explicit frozenset for everyone else — including the EMPTY set, which is the
    right answer for an org that owns no twins yet and must not be confused with
    "unrestricted". That distinction is why this cannot simply pass a falsy
    value through to `Scope(tenants=...)`.
    """
    from identity import tenants_for

    try:
        reachable = tenants_for(principal)
    except Exception:
        # The identity tables are unreachable. Fail CLOSED: a caller whose reach
        # we cannot determine reaches nothing. Returning admin here would turn a
        # database blip into a cross-tenant disclosure.
        return Scope(tenants=frozenset(), prefix=None,
                     role=getattr(principal, "role", "read"),
                     name=getattr(principal, "label", None) or "unresolved")

    if reachable is None:
        return Scope(tenants=None, prefix=None, role=principal.role,
                     name=principal.label or principal.kind)
    return Scope(tenants=reachable, prefix=None, role=principal.role,
                 name=principal.label or principal.kind)


def _scope_from_key(key_info) -> Scope:
    """Project an ApiKeyInfo onto a Scope.

    `ApiKeyInfo` keeps its original single-`tenant` field for back-compat and
    gains optional `tenants` / `tenant_prefix`. Precedence is most-specific
    first, and `"*"` in any position still means admin.
    """
    tenants = getattr(key_info, "tenants", None)
    prefix = getattr(key_info, "tenant_prefix", None) or None
    single = getattr(key_info, "tenant", None)
    role = getattr(key_info, "role", "read")
    name = getattr(key_info, "name", "unknown")

    if single == "*" or (tenants and "*" in tenants):
        return Scope(tenants=None, prefix=None, role=role, name=name)

    allowed: set[str] = set()
    if tenants:
        allowed.update(t for t in tenants if t)
    if single and single != "*":
        allowed.add(single)

    if not allowed and not prefix:
        # A key with no resolvable scope. Fail closed: it authenticates, but it
        # may not reach any tenant-scoped resource.
        return Scope(tenants=frozenset(), prefix=None, role=role, name=name)

    return Scope(tenants=frozenset(allowed), prefix=prefix, role=role, name=name)


def _hub_narrowing(request: Request, scope: Scope) -> Scope:
    """Apply the Integration Hub's forwarded org scoping, NARROWING only.

    The Hub is becoming the platform's entry point: a user signs in there, and
    the Hub calls this API on their behalf with `X-Goalcert-Org` identifying the
    organisation whose data the user may see. Because that is a plain header, it
    is trusted only to REDUCE what the caller's API key already permits. An
    admin key plus a forged org header therefore yields that org's data — which
    the admin key could already read — and never more.

    Org -> tenant resolution is intentionally a lookup, not a string convention,
    so the Hub owns the mapping. Until the twin registry carries an `org`
    column, an org narrows to the tenants whose id begins `<org>-`, which is the
    shape `POST /api/v1/twins` now produces for a scoped caller (see
    `twins_routes.create_twin`). When the column lands, only this function
    changes.
    """
    org = (request.headers.get("X-Goalcert-Org") or "").strip()
    if not org:
        return scope

    org_prefix = f"{_safe_slug(org)}-"

    if scope.is_admin:
        return Scope(tenants=None, prefix=org_prefix, role=scope.role,
                     name=f"{scope.name}@{org}")

    # Already-scoped key: intersect. A prefix scope keeps the more specific of
    # the two; an explicit set keeps only members inside the org.
    if scope.prefix:
        prefix = org_prefix if org_prefix.startswith(scope.prefix) else scope.prefix
    else:
        prefix = None
    tenants = None
    if scope.tenants is not None:
        tenants = frozenset(t for t in scope.tenants if t.startswith(org_prefix)) \
            if not scope.prefix else scope.tenants
    return Scope(tenants=tenants, prefix=prefix, role=scope.role,
                 name=f"{scope.name}@{org}")


def _safe_slug(value: str) -> str:
    """Lowercase, keep [a-z0-9-]. Used on the forwarded org so a hostile header
    cannot smuggle path or query syntax into a prefix comparison."""
    out = []
    for ch in value.lower():
        if ch.isalnum() or ch == "-":
            out.append(ch)
        elif ch in " _.":
            out.append("-")
    return "".join(out).strip("-")


# ── Which requests carry no tenant at all ───────────────────────────────────
#
# These are genuinely tenant-free surfaces: the ontology/schema introspection
# API (the same for every tenant), health, and the two "what can I create"
# listings. Naming them keeps the resolver honest — a path NOT in this set that
# also yields no tenant is worth noticing, and `audit_route_coverage()` reports
# exactly that.
_TENANT_FREE_PREFIXES = (
    "/api/v1/schema",
    "/api/v1/health",
    "/api/v1/copilot/health",
    # The account surface. These carry an ORG, not a tenant, so this dependency
    # has nothing to authorize on them — org-level permission is checked inside
    # `identity/service.py` (`_require_role`), which verifies the actor's role in
    # the org named in the path. Naming the prefix here keeps the coverage audit
    # honest rather than letting it report a silent gap.
    "/api/v1/auth",
    "/api/v1/agents/info",
    "/api/v1/twins/templates",
    "/api/v1/twins/domains",
    "/api/v1/bus/stats",
)

# Routes whose body exceeds MAX_BODY_SCAN_BYTES *and* carries a tenant. Kept as
# an explicit list so the size cap can never silently skip enforcement: if a big
# -body route gains a tenant field it is added here, and the coverage test in
# `tests/test_tenancy_coverage.py` fails until it is.
_LARGE_BODY_TENANT_ROUTES = {
    "/api/v1/agents/twin/upload",
    "/api/v1/agents/twin/build-from-plan",
    "/api/v1/agents/twin/build-from-plan/start",
}


class TenantForbidden(StarletteHTTPException):
    """403 raised by the global dependency. A distinct type so tests can assert
    on it and so an exception handler can render it consistently."""

    def __init__(self, detail: str):
        super().__init__(status_code=403, detail=detail)


async def _body_tenants(request: Request) -> set[str]:
    """Tenant ids carried in a JSON request body.

    Safe to call: FastAPI has already buffered the body for any route with a
    model, so `request.body()` returns its cache. Non-JSON, empty, malformed and
    non-object bodies all yield nothing rather than raising — a body we cannot
    parse is not a body that can name a tenant, and the route's own validation
    will reject it with a 422 a moment later.
    """
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return set()

    ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype and ctype != "application/json":
        return set()          # multipart / form / turtle carry no tenant field

    try:
        raw = await request.body()
    except Exception:
        return set()
    if not raw:
        return set()

    if len(raw) > MAX_BODY_SCAN_BYTES:
        path = request.url.path.rstrip("/")
        if path not in _LARGE_BODY_TENANT_ROUTES:
            return set()
        # A large body that DOES carry a tenant: scan just enough of the head to
        # find it. These payloads put the base64 image last, so the tenant field
        # is in the first few hundred bytes in practice; if it is not, we fall
        # through to a full parse rather than skip enforcement.
        head = raw[:MAX_BODY_SCAN_BYTES]
        found = _scan_for_tenant(head)
        if found:
            return found

    try:
        parsed = json.loads(raw)
    except Exception:
        return set()
    return _tenants_in(parsed)


def _scan_for_tenant(head: bytes) -> set[str]:
    """Best-effort tenant extraction from a JSON fragment, without a full parse.
    Used only for oversized bodies; returning nothing falls back to a real
    parse, so a miss here costs time and never correctness."""
    import re
    out = set()
    text = head.decode("utf-8", errors="ignore")
    for field in TENANT_FIELDS:
        for m in re.finditer(rf'"{field}"\s*:\s*"([^"\\]{{1,128}})"', text):
            out.add(m.group(1))
    return out


def _tenants_in(obj, depth: int = 0) -> set[str]:
    """Every tenant id anywhere in a decoded JSON body.

    Recurses because a tenant can sit inside a nested object (an agent session
    payload, a batch of ingest samples). Depth- and breadth-capped so a hostile
    deeply-nested body cannot turn authorization into a CPU sink.
    """
    out: set[str] = set()
    if depth > 6:
        return out
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in TENANT_FIELDS and isinstance(value, str) and value:
                out.add(value)
            elif isinstance(value, dict | list):
                out |= _tenants_in(value, depth + 1)
    elif isinstance(obj, list):
        for item in obj[:200]:
            if isinstance(item, dict | list):
                out |= _tenants_in(item, depth + 1)
    return out


def request_tenants(request: Request, body_tenants: Iterable[str] = ()) -> set[str]:
    """Every tenant id in the path and query of this request, plus any supplied
    from the body. Split out from the dependency so tests can exercise the
    resolver directly."""
    found: set[str] = set()

    for key, value in (request.path_params or {}).items():
        if key in TENANT_FIELDS and isinstance(value, str) and value:
            found.add(value)

    for field in TENANT_FIELDS:
        for value in request.query_params.getlist(field):
            if value:
                found.add(value)

    found.update(t for t in body_tenants if t)
    return found


async def enforce_tenant_scope(request: Request) -> None:
    """GLOBAL dependency: authorize every tenant this request names.

    Installed once on the app, so it covers every current and future route.
    Requests that name no tenant pass through untouched.
    """
    path = request.url.path
    if not path.startswith("/api"):
        return                                  # SPA shell, assets, docs
    if path.rstrip("/") in ("/api/v1/health", "/api/v1/copilot/health"):
        return                                  # unauthenticated health probes

    scope = scope_of(request)
    tenants = request_tenants(request, await _body_tenants(request))
    if not tenants:
        return

    denied = sorted(t for t in tenants if not scope.allows(t))
    if denied:
        # Name only the tenant the caller already put on the request — never
        # what they could have reached instead.
        subject = denied[0]
        raise TenantForbidden(
            f"Key '{scope.name}' is scoped to {scope.describe()} and cannot "
            f"access tenant '{subject}'.")

    # Stash the authorized set so handlers can use it without re-resolving.
    request.state.nxr_tenants = tenants


# ── Helpers for routes that LIST across tenants ─────────────────────────────
#
# The dependency above authorizes tenants the request NAMES. A listing endpoint
# names none — `GET /api/v1/twins` returned every registered twin to any key,
# which is the same disclosure by a different route. Listings must therefore
# filter, and these helpers are how.


def visible_tenants(request: Request, candidates: Iterable[str]) -> list[str]:
    """Subset of `candidates` this caller may see, order preserved."""
    scope = scope_of(request)
    return [t for t in candidates if scope.allows(t)]


def filter_by_tenant(request: Request, rows: Iterable[dict],
                     field: str = "tenant_id") -> list[dict]:
    """Filter dict rows (twin records, findings, jobs) by the caller's scope."""
    scope = scope_of(request)
    return [r for r in rows if scope.allows(str(r.get(field) or ""))]


def require_write(request: Request) -> Scope:
    """Assert the caller may mutate, and return their scope.

    `auth.py` already rejects mutating verbs for a `read` key. This exists for
    handlers that mutate under a non-obvious verb (a POST that only reads) and
    for provisioning paths that need the scope object anyway.

    THE COMPARISON IS ORDERED, NOT A MEMBERSHIP TEST. This read
    `scope.role not in ("admin", "write")`, which was correct for the three roles
    that existed before `identity/` and silently wrong afterwards: `owner` is the
    MOST privileged role and was refused by it, so an organisation's owner could
    not create a twin. Using the ladder means a role added later is ranked rather
    than accidentally excluded.
    """
    from identity import role_at_least

    scope = scope_of(request)
    if not role_at_least(scope.role, "write"):
        raise TenantForbidden(f"Key '{scope.name}' has read-only access.")
    return scope


def new_tenant_id(request: Request, slug: str) -> str:
    """The tenant id a caller is allowed to CREATE for a new twin.

    Provisioning is the one operation where the caller does not yet name a
    tenant — so the scope has to decide the id rather than check it. An admin
    gets the plain slug. A prefix-scoped caller gets the prefix applied, so the
    twin it creates lands inside its own scope and stays reachable. A caller
    with a fixed set of tenants cannot create at all: there is no id it could be
    given that its static scope would subsequently allow, and inventing one
    would produce a twin its own key cannot read. That gap is exactly what the
    Hub's Org -> Workspace hierarchy resolves, so this raises with that reason
    rather than pretending to succeed.
    """
    scope = require_write(request)

    # An identity-backed caller: the new twin is CLAIMED by their organisation,
    # which is what makes it reachable to them and to nobody else. This is the
    # case the old code could not serve at all — a caller with a fixed tenant set
    # had no id it could be given that its own scope would subsequently allow, so
    # provisioning raised. Ownership is a relation now, so the twin can simply be
    # recorded as theirs.
    principal = getattr(request.state, "principal", None)
    org_id = getattr(principal, "org_id", None) if principal else None
    if org_id:
        from identity import store as _identity_store

        org = _identity_store.get_org(org_id)
        # Namespace the tenant under the org unless the caller already did. Two
        # customers both creating "solar-farm" must not collide, and the prefix
        # is what keeps ids readable while staying unique.
        prefix = (org.tenant_prefix if org else f"{org_id}-") or f"{org_id}-"
        tenant_id = slug if slug.startswith(prefix) else f"{prefix}{slug}"

        owner = _identity_store.tenant_owner(tenant_id)
        if owner and owner != org_id:
            raise TenantForbidden(
                f"Tenant '{tenant_id}' already belongs to another organisation.")
        _identity_store.claim_tenant(tenant_id, org_id)
        _identity_store.audit("tenant.created", org_id=org_id,
                              actor_user=principal.user_id or "",
                              actor_key=principal.key_id or "",
                              target_type="tenant", target_id=tenant_id)
        return tenant_id

    if scope.is_admin:
        return slug
    if scope.prefix:
        return slug if slug.startswith(scope.prefix) else f"{scope.prefix}{slug}"
    raise TenantForbidden(
        f"Key '{scope.name}' is scoped to {scope.describe()} and cannot create "
        f"new twins. Twin provisioning requires an admin key, a prefix-scoped "
        f"key (\"tenant_prefix\"), or a credential belonging to an organisation "
        f"— so the new twin lands inside the caller's own scope.")


def authorize_session_state(request: Request, state) -> None:
    """Authorize a read of an agent session, using the tenant INSIDE its state.

    CLOSES A DOCUMENTED RESIDUAL. `GET /api/v1/agents/{flow}/{session_id}` is
    keyed by session id, and a session id is not a tenant — so the global
    dependency had nothing to authorize and every one of these routes returned
    another tenant's agent transcript to anyone holding the id.

    It was never as severe as the path-parameter hole it sat beside: ids are
    `uuid4().hex[:12]` (~48 bits), so they cannot be enumerated, and an attacker
    had to already possess a specific one. But "cannot be guessed" is not an
    access control — ids leak through logs, screenshots, browser history and
    support tickets — and the fix is small because the state already carries
    `tenant_id`. It just was not read.

    `state` is any agent state object or dict with a `tenant_id`. A state with
    no tenant is allowed through: some flows (bundle authoring from a template)
    are genuinely tenant-free, and refusing those would break them for their
    legitimate owner.
    """
    tenant = ""
    if state is not None:
        tenant = (getattr(state, "tenant_id", None)
                  or (state.get("tenant_id") if isinstance(state, dict) else "")
                  or "")
    if not tenant:
        return

    scope = scope_of(request)
    if not scope.allows(str(tenant)):
        # Deliberately the same message shape as a 404 would give away less, but
        # the caller supplied the session id, so confirming it exists tells them
        # nothing new. What must NOT be disclosed is the owning tenant's id.
        raise TenantForbidden(
            f"Session '{getattr(state, 'session_id', '')}' belongs to another "
            f"tenant.")


def forbidden_handler(request: Request, exc: TenantForbidden) -> JSONResponse:
    """Render TenantForbidden consistently, and never leak the scope internals."""
    return JSONResponse(status_code=403, content={"detail": exc.detail})


# ── Coverage audit (used by the test suite) ─────────────────────────────────


def _body_model(param):
    """The Pydantic model behind a FastAPI body parameter.

    FastAPI moved this between versions: older `ModelField` exposed `.type_`,
    the Pydantic-v2 compat shim exposes `.field_info.annotation`. Both are read
    so the audit does not silently report zero body carriers — a false "no
    tenant here" is precisely the blind spot this ledger exists to catch.
    """
    direct = getattr(param, "type_", None)
    if direct is not None:
        return direct
    return getattr(getattr(param, "field_info", None), "annotation", None)


def _model_tenant_fields(model, depth: int = 0) -> set[str]:
    """Tenant field names declared on a Pydantic model, including inherited and
    nested ones (an agent payload can carry the tenant on a submodel)."""
    found: set[str] = set()
    fields = getattr(model, "model_fields", None)
    if not fields or depth > 4:
        return found
    for name, info in fields.items():
        if name in TENANT_FIELDS:
            found.add(name)
            continue
        nested = getattr(info, "annotation", None)
        if getattr(nested, "model_fields", None):
            found |= _model_tenant_fields(nested, depth + 1)
    return found


def audit_route_coverage(app) -> dict:
    """Report which routes name a tenant, and which name none.

    The global dependency means enforcement cannot be forgotten, so this is not
    a guard — it is a LEDGER. Its job is to catch the opposite mistake: a route
    that quietly stops carrying a tenant identifier (renamed field, tenant moved
    into a nested object under a new key) and therefore silently becomes
    unscoped. `tests/test_tenancy_coverage.py` pins the tenant-free list, so
    adding a route to it is a deliberate, reviewed act.
    """
    scoped, free = [], []
    for route in getattr(app, "routes", []):
        path = getattr(route, "path", "")
        if not path.startswith("/api"):
            continue
        methods = sorted(getattr(route, "methods", None) or [])
        names = set()

        for field in TENANT_FIELDS:
            if f"{{{field}}}" in path:
                names.add(f"path:{field}")

        for dep in getattr(getattr(route, "dependant", None), "query_params", []):
            if getattr(dep, "name", None) in TENANT_FIELDS:
                names.add(f"query:{dep.name}")

        for param in getattr(getattr(route, "dependant", None), "body_params", None) or []:
            for field in _model_tenant_fields(_body_model(param)):
                names.add(f"body:{field}")

        entry = {"path": path, "methods": methods, "carriers": sorted(names)}
        (scoped if names else free).append(entry)

    return {
        "scoped": scoped,
        "tenant_free": free,
        "declared_tenant_free_prefixes": list(_TENANT_FREE_PREFIXES),
    }


def log_posture() -> None:
    """Announce the tenancy posture at boot, next to the auth/db/storage/bus
    lines an operator is told to read after every rollout."""
    # The primary path is now database-backed: a caller's reach is the set of
    # tenants their organisation owns (`org_tenants`), not a string convention.
    try:
        from identity import store as _identity_store
        orgs = _identity_store.list_orgs(limit=1000)
        owned = sum(len(_identity_store.org_tenants(o.org_id)) for o in orgs)
        print(f"[tenancy] identity-backed: {len(orgs)} organisation(s) owning "
              f"{owned} tenant(s). Enforcement covers path, query and body "
              f"carriers on every /api route.", flush=True)
        _log_unowned_tenants(owned)
    except Exception as e:
        print(f"[tenancy] identity tables not readable ({e}); scoping falls back "
              f"to NXR_API_KEYS only.", flush=True)

    raw = os.getenv("NXR_API_KEYS")
    if not raw:
        if _auth_is_enforced():
            print("[tenancy] no legacy NXR_API_KEYS configured (fine - database "
                  "credentials are the primary mechanism).", flush=True)
        else:
            print("[tenancy] !! authentication is DISABLED, so every caller is "
                  "admin-scoped (all tenants). Local-dev posture only.",
                  flush=True)
        return
    try:
        keys = json.loads(raw)
    except Exception as e:
        print(f"[tenancy] !! NXR_API_KEYS is not valid JSON ({e}) - no legacy key "
              f"will resolve.", flush=True)
        return
    admin = sum(1 for k in keys
                if k.get("tenant") == "*" or "*" in (k.get("tenants") or []))
    scoped = len(keys) - admin
    print(f"[tenancy] {len(keys)} legacy env key(s): {admin} admin (all tenants), "
          f"{scoped} tenant-scoped.", flush=True)


def _log_unowned_tenants(owned: int) -> None:
    """Warn about twins that NO organisation owns.

    `org_tenants` is the authorization relation, so a twin missing from it is
    unreachable by every signed-in user — including our own staff unless they are
    a platform admin. That is the correct rule and a confusing symptom: the twin
    is in the registry, the API returns 200, and the app looks empty. It is the
    normal state of any registry that predates `identity/` — every twin there was
    created when a tenant PREFIX, not a row, conferred access — and nothing in the
    boot log said so.

    Reported, never fixed automatically. Which customer owns an existing twin is
    not a default the platform gets to pick, and the plausible-looking guess —
    hand them to whichever organisation exists — is a cross-tenant disclosure the
    first time there are two.
    """
    try:
        from db import connect
        with connect("twins") as conn:
            total = dict(conn.execute(
                "SELECT COUNT(*) AS n FROM twins").fetchone())["n"]
    except Exception:
        return                             # registry not provisioned yet

    unowned = max(0, total - owned)
    if not unowned:
        return
    print(f"[tenancy] !! {unowned} of {total} twin(s) are owned by no "
          f"organisation, so no signed-in user can see them (a platform admin "
          f"can). Assign them with `python -m identity.tenants --org <ORG_ID> "
          f"--adopt-unowned`.", flush=True)
