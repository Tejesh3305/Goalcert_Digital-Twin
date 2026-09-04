"""auth.py — AUTHENTICATION. Who is calling?

This module answers only that question. What they may then TOUCH is
`server/tenancy.py`, and keeping the two apart is deliberate: authentication has
three doors and authorization has one rule, and collapsing them is how the
original cross-tenant hole survived (an authorization check that lived inside the
authentication middleware could only see the query string).

THE THREE DOORS
---------------
    Authorization: Bearer <jwt>   a signed-in HUMAN. Verified by signature, then
                                  the session row is checked so a logout takes
                                  effect at once. See identity/tokens.py.
    X-API-Key: nxr_live_...       a MACHINE. Looked up in the `api_keys` table
                                  (or, legacy, in the NXR_API_KEYS env blob).
    X-Device-Token: ...           a telemetry GATEWAY, on two ingest paths only.

All three produce an `identity.Principal`, stashed on `request.state.principal`.
Everything downstream reads that one object and never re-derives it.

FAIL-CLOSED BY DEFAULT — THE CHANGE THAT MATTERS MOST HERE
-----------------------------------------------------------
This middleware used to serve any request with no credential whenever
`NXR_API_KEYS` was unset. That is the correct default for a laptop and a serious
one for a deployment: the service was open the moment someone forgot an
environment variable, including the `/copilot` endpoints that bill our Anthropic
key. The warning at boot was loud, and a warning is not a control.

The default is now inverted. `auth_required()` returns True unless a deployment
explicitly opts out, so an unconfigured deploy REFUSES requests rather than
serving them. Local dev opts out once, visibly, with `NXR_DEV_MODE=1` (set by
`start.ps1`) — an escape hatch someone has to type, rather than a hole someone
has to remember to close.

LEGACY ENV KEYS STILL WORK
--------------------------
`NXR_API_KEYS` is honoured after the database lookup misses. It is how the
platform was configured before `identity/` existed, it is what the test suite
uses, and it is the break-glass credential when the identity tables are
unreachable. It is no longer the primary mechanism, and `posture()` says so at
boot when it is the only one configured.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response


@dataclass
class ApiKeyInfo:
    """One key configured in the NXR_API_KEYS environment variable.

    LEGACY. Database-backed keys (`identity/store.py`) are the primary mechanism
    and carry their scope as a real `org_tenants` relation rather than the string
    conventions below. This is kept because it still works, the fast test suite
    is built on it, and it is the credential that opens the door when the
    identity tables are down.

    ALL authorization decisions are made in `server/tenancy.py`: this dataclass
    is configuration, not policy.
    """
    key: str
    tenant: str                       # "*" for admin (all tenants)
    role: str                         # "admin", "write", "read"
    name: str                         # human label
    tenants: tuple[str, ...] = ()     # explicit multi-tenant set
    tenant_prefix: str = ""           # every tenant id under this prefix


_key_store: dict[str, ApiKeyInfo] = {}
_keys_loaded = False


def _truthy(val: str | None) -> bool:
    return str(val or "").strip().lower() in ("1", "true", "yes", "on")


def _falsy(val: str | None) -> bool:
    return str(val or "").strip().lower() in ("0", "false", "no", "off")


def _load_keys() -> None:
    """Parse NXR_API_KEYS.

    A malformed blob yields NO keys rather than raising. Raising here happens
    inside middleware construction, which surfaces as HTTP 500 on every request
    with a traceback that does not obviously say "your JSON is broken" — and with
    the fail-closed default, zero keys is already a safe outcome: requests are
    refused, which is what a broken credential configuration should cause.

    There is no default demo key any more. `nxr-demo-key`/`nxr-read-only` used to
    be minted whenever the variable was unset, which meant a published image
    shipped with a known admin credential.
    """
    global _key_store, _keys_loaded
    raw = os.getenv("NXR_API_KEYS")
    keys = []
    if raw:
        try:
            keys = json.loads(raw)
            if not isinstance(keys, list):
                raise ValueError("NXR_API_KEYS must be a JSON array")
        except Exception as e:
            print(f"[auth] !! NXR_API_KEYS could not be parsed ({e}). No "
                  f"environment keys are configured; every request needing one "
                  f"will be rejected.", flush=True)
            keys = []

    _key_store = {
        k["key"]: ApiKeyInfo(
            key=k["key"],
            # Default to "" rather than "*": a key entry that forgets to declare
            # its scope must reach nothing, not everything. The old default meant
            # a typo'd field name silently produced an admin key.
            tenant=k.get("tenant", ""),
            role=k.get("role", "read"),
            name=k.get("name", "unknown"),
            tenants=tuple(k.get("tenants") or ()),
            tenant_prefix=k.get("tenant_prefix", "") or "",
        )
        for k in keys if isinstance(k, dict) and k.get("key")
    }
    _keys_loaded = True


def _resolve_key(api_key: str) -> ApiKeyInfo | None:
    if not _keys_loaded:
        _load_keys()
    return _key_store.get(api_key)


def reload_keys() -> None:
    """Re-read NXR_API_KEYS. For tests that change the variable mid-session."""
    global _keys_loaded
    _keys_loaded = False
    _load_keys()


def auth_required() -> bool:
    """Whether a credential is mandatory. SECURE BY DEFAULT.

    Precedence, most explicit first:

      NXR_REQUIRE_AUTH=0/false   force OFF. An explicit, greppable decision — for
                                 an air-gapped demo box, or a test that needs the
                                 open path.
      NXR_REQUIRE_AUTH=1/true    force ON.
      NXR_DEV_MODE=1             OFF. The local-development escape hatch;
                                 `start.ps1` sets it.
      (nothing set)              ON.

    That last line is the whole point. Previously it meant OFF, so every
    deployment that forgot a variable was open to the internet.
    """
    explicit = os.getenv("NXR_REQUIRE_AUTH")
    if explicit is not None and str(explicit).strip():
        if _falsy(explicit):
            return False
        if _truthy(explicit):
            return True
    if _truthy(os.getenv("NXR_DEV_MODE")):
        return False
    return True


def _identity_enabled() -> bool:
    """Whether to consult the identity tables. On unless explicitly disabled —
    the switch exists so a deployment that has not provisioned the schema yet can
    run on env keys alone without every request paying for a failing lookup."""
    return not _truthy(os.getenv("NXR_IDENTITY_DISABLED"))


def posture() -> list[str]:
    """The auth configuration, as lines for the boot log."""
    lines: list[str] = []
    enforced = auth_required()

    if not enforced:
        why = ("NXR_REQUIRE_AUTH is set to a false value"
               if os.getenv("NXR_REQUIRE_AUTH") else "NXR_DEV_MODE is set")
        lines.append(
            f"[auth] !! AUTHENTICATION IS DISABLED ({why}). Every /api request "
            f"- including the LLM-billing /copilot endpoints - is served without "
            f"a credential. This must never be the posture of a public deployment.")
        return lines

    if not _keys_loaded:
        _load_keys()
    sources = []
    if _identity_enabled():
        sources.append("database (users + api_keys)")
    if _key_store:
        sources.append(f"NXR_API_KEYS ({len(_key_store)} legacy key"
                       f"{'' if len(_key_store) == 1 else 's'})")
    lines.append(f"[auth] enforcement ON - credential sources: "
                 f"{', '.join(sources) if sources else 'NONE CONFIGURED'}")
    if not sources:
        lines.append("[auth] !! No credential source is available, so every /api "
                     "request will be rejected. Provision the identity schema "
                     "(python -m db.schema) and bootstrap an admin, or set "
                     "NXR_API_KEYS.")
    return lines


def log_auth_posture() -> None:
    """Announce the auth posture once at startup so an open deployment cannot
    hide. Fires from AuthMiddleware.__init__ when the app is assembled.

    ASCII only, deliberately. This runs when the middleware stack is built, i.e.
    on the first request — and stdout is a redirected pipe under CloudWatch (and
    under `python -m server.main > log` on Windows). A character the stream's
    encoding cannot represent raised UnicodeEncodeError *inside middleware
    construction*, which surfaced as HTTP 500 on every request. The one line
    warning that the API is open must never be the thing that takes the API down.
    """
    for line in posture():
        print(line, flush=True)


# ── Legacy helpers, retained for back-compat ────────────────────────────


def check_tenant_access(key_info: ApiKeyInfo, requested_tenant: str) -> bool:
    """Whether this env key can access a tenant.

    Delegates to `server/tenancy.py` so there is exactly ONE implementation of
    the rule. Real enforcement — across path, query AND body — is the global
    `enforce_tenant_scope` dependency, because middleware runs before routing and
    therefore cannot see `{tenant}` path parameters at all. That blind spot was
    the original cross-tenant hole.
    """
    from server.tenancy import _scope_from_key
    return _scope_from_key(key_info).allows(requested_tenant)


def check_write_access(key_info: ApiKeyInfo) -> bool:
    """Whether an env key may mutate.

    Ordered against the role ladder rather than a membership test, for the same
    reason as `tenancy.require_write`: a hardcoded `("admin", "write")` refuses
    `owner`, which outranks both.
    """
    from identity import role_at_least
    return role_at_least(key_info.role, "write")


# ── Path policy ─────────────────────────────────────────────────────────

# Non-API paths that need no credential.
_PUBLIC_PATHS = {"/", "/docs", "/openapi.json", "/redoc"}

# API paths that must stay reachable WITHOUT a credential. Platform health probes
# (Render, the Dockerfile HEALTHCHECK, an ECS target group) cannot send one, so
# gating these behind auth makes the orchestrator declare the service unhealthy
# and restart it in a loop — the process is fine, the probe just cannot
# authenticate. The payloads are deliberately non-sensitive.
_PUBLIC_API_PATHS = {
    "/api/v1/health",
    "/api/v1/health/live",
    "/api/v1/health/ready",
    "/api/v1/copilot/health",
}

# The authentication surface itself. These MUST be reachable without a
# credential — they are how a caller obtains one. Everything else under
# /api/v1/auth (me, sessions, orgs, keys) requires authentication and gets it
# through the normal path below.
#
# Each is rate-limited (server/ratelimit.py) precisely because it is
# unauthenticated: /login is a password-guessing surface, /signup an
# account-creation one, and /password/forgot an email-enumeration one.
_PUBLIC_AUTH_PATHS = {
    # Whether a credential is required, and whether signup is open. The sign-in
    # page needs both BEFORE it can hold a credential, and it previously inferred
    # the first by calling a protected endpoint and reading the 401 — a
    # deliberate auth failure on every page load of every deployment.
    "/api/v1/auth/posture",
    "/api/v1/auth/signup",
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/api/v1/auth/logout",
    "/api/v1/auth/password/forgot",
    "/api/v1/auth/password/reset",
    "/api/v1/auth/verify-email",
}

# The Goalcert Hub SSO callback. It MUST be reachable without a credential —
# arriving without one is the entire point, since the Hub's ticket is what the
# visitor is carrying instead.
#
# It needs naming explicitly because of the asymmetry in `_is_public` below: a
# non-API GET already falls through to the SPA rule, but a non-API POST does not,
# and the contract PREFERS the POST form (a token in a query string ends up in
# browser history, proxy logs and Referer headers). Without this entry the Hub's
# preferred delivery would 401 before `server/sso_routes.py` ever ran, and the
# fallback query-param form would appear to work — the worst kind of bug, because
# it only shows up on the safer path.
#
# Kept as a literal rather than imported from `server/sso_routes.py`: that module
# imports `server/auth_routes.py`, which imports this one, so the import would be
# circular. The two must stay in step — `sso_routes.CALLBACK_PATH` is the other
# half.
_PUBLIC_SSO_PATHS = {"/sso/hub/callback"}

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
_DEVICE_TOKEN_PATHS = {
    "/api/v1/ingest/telemetry",
    "/api/v1/ingest/telemetry/bulk",
}

# POSTs that MUTATE NOTHING, and are therefore allowed to a read-only credential.
#
# Each is a pure function of its request body: it touches no store, creates no
# entity, and starts no background work. They are POSTs purely because their
# input is a structured document rather than a handful of query parameters.
#
#   /api/v1/solar/model/evaluate  runs the De Soto model at a given (G, T) and
#                                 returns the I-V curve. No tenant, no telemetry.
#   /api/v1/schema/validate       validates a Turtle fragment against the shapes.
#
# Adding to this list grants every read-only credential access to that path, so
# it takes the same scrutiny as widening a scope.
_READ_SAFE_POST_PATHS = {
    "/api/v1/solar/model/evaluate",
    "/api/v1/schema/validate",
}

# Authenticated /auth routes that are POSTs but are not "writes" in the
# role sense — they act on the caller's OWN session or password, so a read-only
# member must be able to reach them. Without this a `read` user could sign in and
# then be refused their own password change.
_SELF_SERVICE_POST_PATHS = {
    "/api/v1/auth/switch-org",
    "/api/v1/auth/password/change",
    "/api/v1/auth/sessions/revoke-others",
}


def _is_public(path: str, method: str) -> bool:
    """The frontend (SPA) and its assets are public. Only the API surface
    (/api/*) is auth-controlled. Non-API GETs serve the app shell."""
    # Tolerate a trailing slash so /api/v1/health/ isn't accidentally gated.
    normalized = path.rstrip("/") or "/"
    if normalized in _PUBLIC_PATHS or path in _PUBLIC_PATHS:
        return True
    if normalized in _PUBLIC_API_PATHS or normalized in _PUBLIC_AUTH_PATHS:
        return True
    if normalized in _PUBLIC_SSO_PATHS:
        return True
    if path.startswith(("/static", "/assets")):
        return True
    # Any non-API GET is a client-router path -> serve the SPA shell publicly.
    if method == "GET" and not path.startswith("/api"):
        return True
    return False


def _deny(status_code: int, detail: str, **extra) -> JSONResponse:
    """Build an auth-failure response.

    We RETURN this rather than raising HTTPException. FastAPI translates
    HTTPException into a response inside ExceptionMiddleware, which sits *inside*
    the middleware stack (nearer the routes). An exception raised here — in an
    outer BaseHTTPMiddleware — propagates OUTWARD past that handler, so nothing
    converts it: ServerErrorMiddleware catches it and emits a 500 with a
    traceback. That turned every unauthenticated call into "500 Internal Server
    Error" instead of a clean 401, and made the logs look like a server crash.
    """
    payload = {"detail": detail}
    payload.update(extra)
    response = JSONResponse(status_code=status_code, content=payload)
    if status_code == 401:
        # Tells a client WHICH credential to present. A CLI holding an expired
        # token needs to distinguish "refresh me" from "you sent the wrong kind".
        response.headers["WWW-Authenticate"] = 'Bearer realm="nextxr"'
    return response


# ── Middleware ──────────────────────────────────────────────────────────


class AuthMiddleware(BaseHTTPMiddleware):
    """Resolve the caller's credential into a Principal, or refuse the request."""

    def __init__(self, app):
        super().__init__(app)
        log_auth_posture()
        # The deployment posture, all in one place. An operator reading
        # CloudWatch after a rollout needs to see auth, tenancy, identity and
        # every store on adjacent lines — AWS_DEPLOYMENT.md §11 tells them to.
        try:
            from server.tenancy import log_posture as _tenancy_posture
            _tenancy_posture()
        except Exception as e:
            print(f"[tenancy] posture unavailable: {e}", flush=True)

        for mod in ("identity", "db", "storage", "bus", "historian", "sso"):
            try:
                __import__(mod).log_posture()
            except Exception as e:
                # bus.log_posture() re-raises BusUnavailable when Redis is
                # mandatory. That must NOT be swallowed: refusing to start is the
                # entire point of NXR_REQUIRE_REDIS.
                if type(e).__name__ == "BusUnavailable":
                    raise
                print(f"[{mod}] posture unavailable: {e}", flush=True)

        # Schema version. Reported always; APPLIED only when the deployment asked
        # for it (NXR_AUTO_MIGRATE), because several tasks starting together would
        # otherwise queue on the advisory lock and time out their health checks.
        # The recommended shape is a one-off migrate task before the service rolls
        # — this line is how an operator confirms it ran.
        # The posture line is printed EVEN WHEN APPLYING FAILED, and that is the
        # point of the `finally`. Previously a failure raised out of
        # run_at_startup(), skipped log_posture() and printed only
        # "[migrations] posture unavailable: <the same error again>" — so the boot
        # log said what went wrong twice and never said WHICH migrations were
        # still pending, which is the one fact needed to act on it.
        try:
            from db import migrations as _migrations
            try:
                _migrations.run_at_startup()
            finally:
                _migrations.log_posture()
        except Exception as e:
            print(f"[migrations] !! the schema is NOT up to date ({e}). Requests "
                  f"touching a table this build expects to have changed will "
                  f"fail. Run `python -m db.migrations` against this database.",
                  flush=True)

        # Refuse to boot on a configuration that would fail intermittently and
        # unexplainably in a fleet — an ephemeral JWT key while auth is enforced.
        # Better a failed deploy than half the requests 401ing at random.
        try:
            from identity.tokens import require_secret
            require_secret()
        except RuntimeError:
            raise
        except Exception as e:
            print(f"[identity] token posture unavailable: {e}", flush=True)

        # Create the first platform admin if the environment asks for one.
        try:
            from identity import bootstrap_admin
            created = bootstrap_admin()
            if created:
                print(f"[identity] bootstrap platform admin created ({created}). "
                      f"Remove NXR_BOOTSTRAP_ADMIN_* from the task definition now.",
                      flush=True)
        except Exception as e:
            print(f"[identity] bootstrap skipped: {e}", flush=True)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        normalized = path.rstrip("/") or "/"

        # Honor the identity headers the Integration Hub gateway forwards for
        # tenant scoping / audit logging (the browser never holds these). Stash
        # them on request.state so downstream handlers can read them; they never
        # gate access on their own.
        request.state.identity = {
            "user": request.headers.get("X-Goalcert-User"),
            "role": request.headers.get("X-Goalcert-Role"),
            "org": request.headers.get("X-Goalcert-Org"),
        }
        request.state.principal = None

        if _is_public(path, request.method):
            # Public, but still resolve any credential that WAS supplied. A
            # signed-in user hitting /auth/logout or /auth/refresh should be
            # recognised, and the health endpoints report more detail to an
            # authenticated operator.
            await self._try_authenticate(request, path, normalized)
            return await call_next(request)

        # Device credential: authenticate HERE so the ingest hot path does one
        # registry lookup rather than two, and stash the device so
        # tenancy.scope_of() can pin the request to that device's tenant.
        # Note `is not None`, not truthiness. A client that SENDS the header is
        # attempting device authentication, so an empty value must fail as a bad
        # device token rather than falling through to "Missing credential" — two
        # different messages for two malformed credentials is a distinction an
        # attacker can probe, and a confusing mixed signal for an operator.
        device_token = request.headers.get("X-Device-Token")
        if device_token is not None and normalized in _DEVICE_TOKEN_PATHS:
            try:
                from ingest import devices as _devices
                device = _devices.authenticate(device_token)
                request.state.device = device
                from identity import principal_for_device
                request.state.principal = principal_for_device(device)
            except Exception as e:
                # Coarse on purpose: distinguishing "unknown token" from
                # "disabled" from "expired" confirms to whoever is holding stolen
                # hardware that the token is real.
                if type(e).__name__ != "DeviceAuthError":
                    return _deny(503, f"Device registry unavailable: {e}")
                return _deny(401, "Invalid or inactive device token")
            return await call_next(request)

        principal, failure = await self._try_authenticate(request, path, normalized)

        if failure is not None:
            return failure

        if principal is None:
            if not auth_required():
                # Explicitly-opted-out dev mode. tenancy.scope_of() supplies the
                # open admin scope; nothing else in the stack needs to know.
                return await call_next(request)
            return _deny(
                401,
                "Authentication required. Send an 'Authorization: Bearer <token>' "
                "header (sign in at /api/v1/auth/login) or an 'X-API-Key' header.")

        # Write access. "POST implies mutation" is the right default and it is not
        # universally true: a few endpoints are pure FUNCTIONS that read nothing
        # and write nothing, and are POSTs only because their input is a
        # structured body too large or too nested for a query string. Refusing
        # those to a read-only credential is a false negative — an analyst with
        # read access should be able to run the PV model against a datasheet
        # without being handed a credential that can also delete twins.
        #
        # The exemption is a short, explicit ALLOW-list rather than a heuristic,
        # because the failure directions are asymmetric: wrongly exempting a
        # mutating route hands write access to every read credential, while
        # wrongly omitting a pure one costs a 403 that someone reports.
        if request.method in ("POST", "PATCH", "PUT", "DELETE") \
                and normalized not in _READ_SAFE_POST_PATHS \
                and normalized not in _SELF_SERVICE_POST_PATHS \
                and not normalized.startswith("/api/v1/auth/"):
            if not principal.can_write:
                return _deny(403, f"'{principal.label}' has read-only access.")

        return await call_next(request)

    async def _try_authenticate(self, request: Request, path: str,
                                normalized: str):
        """Resolve whichever credential the request carries.

        Returns (principal_or_None, failure_response_or_None). A SUPPLIED but
        INVALID credential is a failure (401) even on a public path — silently
        treating a bad token as anonymous would let a client with an expired
        session believe it was signed in.
        """
        bearer = request.headers.get("Authorization")
        if bearer:
            from identity.tokens import bearer_from_header
            token = bearer_from_header(bearer)
            if token:
                from identity import principal_from_access_token
                principal = principal_from_access_token(token)
                if principal is None:
                    return None, _deny(
                        401, "Session expired or invalid. Sign in again.",
                        code="token_invalid")
                request.state.principal = principal
                # Back-compat: a few handlers still read request.state.api_key.
                request.state.api_key = _compat_key_info(principal)
                return principal, None

        api_key = request.headers.get("X-API-Key")
        if api_key:
            principal = None
            if _identity_enabled():
                try:
                    from identity import principal_from_api_key
                    principal = principal_from_api_key(api_key)
                except Exception as e:
                    # The identity tables are unreachable. Fall through to the
                    # env keys rather than failing every request — that is the
                    # break-glass path, and it is why NXR_API_KEYS still exists.
                    print(f"[auth] identity lookup unavailable, falling back to "
                          f"NXR_API_KEYS: {e}", flush=True)

            if principal is None:
                key_info = _resolve_key(api_key)
                if key_info is not None:
                    principal = _principal_from_env_key(key_info)
                    request.state.api_key = key_info

            if principal is None:
                return None, _deny(401, "Invalid API key.")

            request.state.principal = principal
            if getattr(request.state, "api_key", None) is None:
                request.state.api_key = _compat_key_info(principal)

            # Defence in depth: the query-string tenant check that predates the
            # global dependency. Real enforcement is `enforce_tenant_scope`,
            # which sees path and body too, but a cheap early refusal here costs
            # nothing and closes the window if that dependency is ever removed.
            tenant = request.query_params.get("tenant")
            if tenant:
                from server.tenancy import scope_of
                if not scope_of(request).allows(tenant):
                    return None, _deny(
                        403, f"'{principal.label}' cannot access tenant '{tenant}'.")
            return principal, None

        return None, None


def _principal_from_env_key(key_info: ApiKeyInfo):
    """A Principal for a legacy NXR_API_KEYS entry.

    `tenants=None` means "resolve from the org", which an env key has none of —
    so the scope is carried by `request.state.api_key` and read by
    `tenancy._scope_from_key`, preserving the exact `"*"` / prefix / set
    semantics those keys have always had. Encoding them into a Principal instead
    would mean two implementations of the same rule.
    """
    from identity import Principal
    return Principal(
        kind="api_key",
        key_id=f"env:{key_info.name}",
        org_id=None,
        role=key_info.role,
        tenants=None,
        label=key_info.name,
    )


def _compat_key_info(principal) -> ApiKeyInfo:
    """Shape a Principal like the old ApiKeyInfo for handlers that still read
    `request.state.api_key`. Scope fields are left empty: `tenancy.scope_of()`
    resolves a database-backed principal from `org_tenants`, and a half-filled
    duplicate here would be a second source of truth to disagree with it."""
    return ApiKeyInfo(key="", tenant="", role=principal.role,
                      name=principal.label or principal.kind)
