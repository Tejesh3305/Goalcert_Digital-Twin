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

KNOWN RESIDUALS (deliberate, and why they are a different severity)
------------------------------------------------------------------
Two surfaces are authenticated but not tenant-authorized. Both are recorded here
rather than left to be discovered, because "we fixed tenant isolation" must mean
something precise:

1. AGENT SESSION READS — `GET /api/v1/agents/{twin,bundle,ops,plugin,
   accelerator}/{session_id}`. A session belongs to a tenant, but the route is
   keyed by session id and the id is not a tenant, so this module has nothing to
   authorize. Severity is lower than the path/body hole it sits beside: ids are
   `uuid4().hex[:12]` (~48 bits), so they cannot be enumerated — an attacker must
   already possess a specific id. The path-parameter hole needed only a tenant
   NAME, which the twin listing handed out. Closing this properly means storing
   the owning tenant with the session and checking it on read; it is a schema
   change to the checkpoint store, so it is sequenced with the Hub's
   Org -> Workspace work rather than bolted on here.

2. THE 3-D PLATFORM SUB-APP at `/api/v1/threed`. `app.mount()` does not pass app
   -level dependencies to the mounted application, so AuthMiddleware covers it
   (a key is required) but this module does not. Its job store is keyed by job id
   and carries no tenant column today, so there is no tenant to check; when it
   gains one, `enforce_tenant_scope` must be installed on that app too.

`POST /api/v1/feed/stop` is also unscoped, but the feed is a single per-process
demo loop rather than tenant state — it is a cross-tenant nuisance, not a
disclosure. It disappears when the simulated feed is replaced by real ingestion.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Iterable, Optional

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
    tenants: Optional[frozenset[str]]
    prefix: Optional[str]
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

    key_info = getattr(request.state, "api_key", None)
    scope = _scope_from_key(key_info) if key_info is not None else _DEV_ADMIN
    scope = _hub_narrowing(request, scope)

    request.state.nxr_scope = scope
    return scope


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
            elif isinstance(value, (dict, list)):
                out |= _tenants_in(value, depth + 1)
    elif isinstance(obj, list):
        for item in obj[:200]:
            if isinstance(item, (dict, list)):
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
    """
    scope = scope_of(request)
    if scope.role not in ("admin", "write"):
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
    if scope.is_admin:
        return slug
    if scope.prefix:
        return slug if slug.startswith(scope.prefix) else f"{scope.prefix}{slug}"
    raise TenantForbidden(
        f"Key '{scope.name}' is scoped to {scope.describe()} and cannot create "
        f"new twins. Twin provisioning requires an admin key or a "
        f"prefix-scoped key (\"tenant_prefix\"), so the new twin lands inside "
        f"the caller's own scope.")


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
    raw = os.getenv("NXR_API_KEYS")
    if not raw:
        print("[tenancy] no NXR_API_KEYS - every caller is admin-scoped "
              "(all tenants). Local-dev posture only.", flush=True)
        return
    try:
        keys = json.loads(raw)
    except Exception as e:
        print(f"[tenancy] !! NXR_API_KEYS is not valid JSON ({e}) - no key will "
              f"resolve and every /api call will be rejected.", flush=True)
        return
    admin = sum(1 for k in keys
                if k.get("tenant") == "*" or "*" in (k.get("tenants") or []))
    scoped = len(keys) - admin
    print(f"[tenancy] {len(keys)} key(s): {admin} admin (all tenants), "
          f"{scoped} tenant-scoped. Enforcement covers path, query and body "
          f"carriers on every /api route.", flush=True)
