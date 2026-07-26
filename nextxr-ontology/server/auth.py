"""
auth.py — API key authentication and tenant-scoped RBAC.

Every request must include an X-API-Key header. Keys are mapped to tenants
and roles. The dashboard (served at /) is exempt from auth.

Tenant isolation: a key scoped to tenant "acme" cannot read or write
data in tenant "globex". Admin keys can access all tenants.

Roles:
  - admin:  full access to all tenants and operations
  - write:  read + write within their tenant
  - read:   read-only within their tenant

Configuration is via environment variable NXR_API_KEYS (JSON) or defaults
to a demo key for development.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response


@dataclass
class ApiKeyInfo:
    """One configured API key and what it may reach.

    `tenant` is the original single-tenant field and still works. `tenants` and
    `tenant_prefix` were added because the single field could not express "this
    customer owns 50 twins" without granting `"*"` — which also grants every
    OTHER customer's data. Serving a real enterprise account therefore required
    over-granting. See `server/tenancy.py` for how these project onto a Scope,
    and note that ALL authorization decisions are made there: this dataclass is
    configuration, not policy.
    """
    key: str
    tenant: str                       # "*" for admin (all tenants)
    role: str                         # "admin", "write", "read"
    name: str                         # human label
    tenants: tuple[str, ...] = ()     # explicit multi-tenant set
    tenant_prefix: str = ""           # every tenant id under this prefix


# Default keys for development — override with NXR_API_KEYS env var
_DEFAULT_KEYS = [
    {"key": "nxr-demo-key", "tenant": "*", "role": "admin", "name": "Demo Admin"},
    {"key": "nxr-read-only", "tenant": "demo-tenant", "role": "read", "name": "Demo Reader"},
]

_key_store: dict[str, ApiKeyInfo] = {}


def _load_keys():
    global _key_store
    raw = os.getenv("NXR_API_KEYS")
    if raw:
        keys = json.loads(raw)
    else:
        keys = _DEFAULT_KEYS

    _key_store = {
        k["key"]: ApiKeyInfo(
            key=k["key"],
            # Default to "" rather than "*": a key entry that forgets to declare
            # its scope must reach nothing, not everything. The old default
            # meant a typo'd field name silently produced an admin key.
            tenant=k.get("tenant", ""),
            role=k.get("role", "read"),
            name=k.get("name", "unknown"),
            tenants=tuple(k.get("tenants") or ()),
            tenant_prefix=k.get("tenant_prefix", "") or "",
        )
        for k in keys
    }


def _resolve_key(api_key: str) -> Optional[ApiKeyInfo]:
    if not _key_store:
        _load_keys()
    return _key_store.get(api_key)


def _truthy(val: Optional[str]) -> bool:
    return str(val or "").strip().lower() in ("1", "true", "yes", "on")


def _require_auth() -> bool:
    """Production posture. When NXR_REQUIRE_AUTH is set, a missing/blank X-API-Key
    is always rejected — even if NXR_API_KEYS is not configured. This closes the
    dev-mode fail-open bypass for a real deployment without changing the local
    default (open), so exposing the service to the internet is an explicit opt-in
    to enforcement rather than a silent default-open."""
    return _truthy(os.getenv("NXR_REQUIRE_AUTH"))


def log_auth_posture() -> None:
    """Announce the auth posture once at startup so an open deployment cannot hide.
    Fires from AuthMiddleware.__init__ when the app is assembled."""
    if os.getenv("NXR_API_KEYS"):
        print("[auth] API key enforcement ON - NXR_API_KEYS configured.", flush=True)
    elif _require_auth():
        print("[auth] API key enforcement ON - NXR_REQUIRE_AUTH set but NXR_API_KEYS "
              "is empty, so every /api call will be rejected until keys are configured.",
              flush=True)
    else:
        # ASCII only, deliberately. This runs when the middleware stack is built,
        # i.e. on the first request — and stdout is a redirected pipe under
        # CloudWatch (and under `python -m server.main > log` on Windows). A
        # character the stream's encoding can't represent raised UnicodeEncodeError
        # *inside middleware construction*, which surfaced as HTTP 500 on every
        # request. The one line warning that the API is open must never be the
        # thing that takes the API down.
        print("[auth] !! API IS OPEN - no NXR_API_KEYS set and NXR_REQUIRE_AUTH unset. "
              "Every /api request (including LLM-billing /copilot endpoints) is served "
              "without a key. Set NXR_API_KEYS before exposing this service publicly.",
              flush=True)


def check_tenant_access(key_info: ApiKeyInfo, requested_tenant: str) -> bool:
    """Check if this key can access the requested tenant.

    Delegates to `server/tenancy.py` so there is exactly ONE implementation of
    the rule. This used to compare `key_info.tenant == requested_tenant`
    directly, which is now wrong twice over: it cannot see a `tenants` set or a
    `tenant_prefix`, so a correctly-configured multi-twin enterprise key would
    be refused its own data.

    The middleware still calls this on the query-string tenant as
    defence-in-depth. Real enforcement — across path, query AND body — is the
    global `enforce_tenant_scope` dependency, because middleware runs before
    routing and therefore cannot see `{tenant}` path parameters at all. That
    blind spot was the original cross-tenant hole.
    """
    from server.tenancy import _scope_from_key
    return _scope_from_key(key_info).allows(requested_tenant)


def check_write_access(key_info: ApiKeyInfo) -> bool:
    """Check if this key can perform write operations."""
    return key_info.role in ("admin", "write")


# Paths that don't require auth
_PUBLIC_PATHS = {"/", "/docs", "/openapi.json", "/redoc"}

# Paths where an `X-Device-Token` is an acceptable credential INSTEAD of an
# X-API-Key. Deliberately a tiny, explicit allow-list of append-only telemetry
# endpoints.
#
# A telemetry gateway cannot hold a tenant API key: that key reads every asset,
# drives the physics runtime, deletes entities and bills the LLM endpoints, and
# the gateway is hardware in a plant room that anyone with a screwdriver can walk
# off with (see ingest/devices.py). So it authenticates as itself — but the
# middleware previously demanded an X-API-Key on every /api path, which rejected
# the device before its own route could ever authenticate it.
#
# Everything about that stays narrow on purpose: a device token is accepted ONLY
# for these two paths, and `server/tenancy.py` pins the resulting scope to that
# device's single tenant with write-only rights, so a device that somehow reached
# another route still could not read across the fleet.
_DEVICE_TOKEN_PATHS = {
    "/api/v1/ingest/telemetry",
    "/api/v1/ingest/telemetry/bulk",
}

# POSTs that MUTATE NOTHING, and are therefore allowed to a read-only key.
#
# Each is a pure function of its request body: it touches no store, creates no
# entity, and starts no background work. They are POSTs purely because their input
# is a structured document rather than a handful of query parameters.
#
#   /api/v1/solar/model/evaluate  runs the De Soto model at a given (G, T) and
#                                 returns the I-V curve. No tenant, no telemetry.
#   /api/v1/schema/validate       validates a Turtle fragment against the shapes
#                                 and returns the violations.
#
# Adding to this list grants every read-only key access to that path, so it takes
# the same scrutiny as widening a scope.
_READ_SAFE_POST_PATHS = {
    "/api/v1/solar/model/evaluate",
    "/api/v1/schema/validate",
}

# API paths that must stay reachable WITHOUT a key. Platform health probes
# (Render, the Dockerfile HEALTHCHECK, an ECS target group) cannot send an
# X-API-Key, so gating these behind auth makes the orchestrator declare the
# service unhealthy and restart it in a loop — the process is fine, the probe
# just can't authenticate. The health payload is deliberately non-sensitive
# (up/degraded + component status).
_PUBLIC_API_PATHS = {"/api/v1/health", "/api/v1/copilot/health"}


def _is_public(path: str, method: str) -> bool:
    """The frontend (SPA) and its assets are public. Only the API surface
    (/api/*) is auth-controlled. Non-API GETs serve the app shell."""
    # Tolerate a trailing slash so /api/v1/health/ isn't accidentally gated.
    normalized = path.rstrip("/") or "/"
    if normalized in _PUBLIC_PATHS or path in _PUBLIC_PATHS:
        return True
    if normalized in _PUBLIC_API_PATHS:
        return True
    if path.startswith(("/static", "/assets")):
        return True
    # Any non-API GET is a client-router path -> serve the SPA shell publicly.
    if method == "GET" and not path.startswith("/api"):
        return True
    return False


def _deny(status_code: int, detail: str) -> JSONResponse:
    """Build an auth-failure response.

    We RETURN this rather than raising HTTPException. FastAPI translates
    HTTPException into a response inside ExceptionMiddleware, which sits *inside*
    the middleware stack (nearer the routes). An exception raised here — in an
    outer BaseHTTPMiddleware — propagates OUTWARD past that handler, so nothing
    converts it: ServerErrorMiddleware catches it and emits a 500 with a
    traceback. That turned every unauthenticated call into "500 Internal Server
    Error" instead of a clean 401, and made the logs look like a server crash.
    """
    return JSONResponse(status_code=status_code, content={"detail": detail})


class AuthMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that enforces API key authentication."""

    def __init__(self, app):
        super().__init__(app)
        log_auth_posture()
        # Tenant-scope posture sits next to the auth line: "enforcement ON" and
        # "every key is admin-scoped" are different deployments, and an operator
        # reading CloudWatch after a rollout needs to see both.
        try:
            from server.tenancy import log_posture as _tenancy_posture
            _tenancy_posture()
        except Exception as e:
            print(f"[tenancy] posture unavailable: {e}", flush=True)
        # Same reasoning as the auth line: a misconfigured deploy must announce
        # itself at boot rather than be discovered when twins go missing, models
        # 404 on half the tasks, or live updates quietly stop for some users.
        # These four lines are the deployment posture, and AWS_DEPLOYMENT.md §11
        # tells operators to read them in CloudWatch after every rollout.
        for mod in ("db", "storage", "bus", "historian"):
            try:
                __import__(mod).log_posture()
            except Exception as e:
                # bus.log_posture() re-raises BusUnavailable when Redis is
                # mandatory. That must NOT be swallowed: refusing to start is the
                # entire point of NXR_REQUIRE_REDIS.
                if type(e).__name__ == "BusUnavailable":
                    raise
                print(f"[{mod}] posture unavailable: {e}", flush=True)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        # Honor the identity headers the Integration Hub gateway forwards for
        # tenant scoping / audit logging (the browser never holds these). Stash
        # them on request.state so downstream handlers can read them; they never
        # gate access on their own (the X-API-Key does that).
        request.state.identity = {
            "user": request.headers.get("X-Goalcert-User"),
            "role": request.headers.get("X-Goalcert-Role"),
            "org": request.headers.get("X-Goalcert-Org"),
        }

        # Public paths (frontend app, assets, docs)
        if _is_public(path, request.method):
            return await call_next(request)

        # Device credential: authenticate HERE so the ingest hot path does one
        # registry lookup rather than two, and stash the device so
        # tenancy.scope_of() can pin the request to that device's tenant.
        # Note `is not None`, not truthiness. A client that SENDS the header is
        # attempting device authentication, so an empty value must fail as a bad
        # device token rather than falling through to "Missing X-API-Key header" —
        # two different messages for two malformed credentials is a distinction an
        # attacker can probe, and a confusing mixed signal for an operator.
        device_token = request.headers.get("X-Device-Token")
        if device_token is not None and path.rstrip("/") in _DEVICE_TOKEN_PATHS:
            try:
                from ingest import devices as _devices
                request.state.device = _devices.authenticate(device_token)
            except Exception as e:
                # Coarse on purpose: distinguishing "unknown token" from
                # "disabled" from "expired" confirms to whoever is holding stolen
                # hardware that the token is real.
                if type(e).__name__ != "DeviceAuthError":
                    return _deny(503, f"Device registry unavailable: {e}")
                return _deny(401, "Invalid or inactive device token")
            return await call_next(request)

        # Require API key
        api_key = request.headers.get("X-API-Key")
        if not api_key:
            # No key on the request. In dev (no NXR_API_KEYS configured) we let it
            # through — UNLESS auth is explicitly required (NXR_REQUIRE_AUTH), the
            # production posture. That closes the fail-open hole for a real deploy
            # while leaving local-dev behaviour (open) unchanged.
            if not os.getenv("NXR_API_KEYS") and not _require_auth():
                return await call_next(request)
            return _deny(401, "Missing X-API-Key header")

        key_info = _resolve_key(api_key)
        if key_info is None:
            return _deny(401, "Invalid API key")

        # Check tenant access
        tenant = request.query_params.get("tenant")
        if tenant and not check_tenant_access(key_info, tenant):
            return _deny(
                403,
                f"Key '{key_info.name}' cannot access tenant '{tenant}'",
            )

        # Check write access for mutations.
        #
        # "POST implies mutation" is the right default and it is not universally
        # true: a few endpoints are pure FUNCTIONS that read nothing and write
        # nothing, and are POSTs only because their input is a structured body too
        # large or too nested for a query string. Refusing those to a read-only key
        # is a false negative — an analyst with read access should be able to run
        # the PV model against a datasheet, or validate a Turtle fragment, without
        # being handed a credential that can also delete twins.
        #
        # The exemption is a short, explicit ALLOW-list rather than a heuristic,
        # because the failure directions are asymmetric: wrongly exempting a
        # mutating route hands write access to every read key, while wrongly
        # omitting a pure one costs a 403 that someone reports.
        if (request.method in ("POST", "PATCH", "PUT", "DELETE")
                and path.rstrip("/") not in _READ_SAFE_POST_PATHS):
            if not check_write_access(key_info):
                return _deny(403, f"Key '{key_info.name}' has read-only access")

        # Attach key info to request state for downstream use
        request.state.api_key = key_info
        return await call_next(request)
