"""security.py — the transport-level defaults: CORS, response headers, body size.

Three controls that are each a one-liner and each prevent a distinct class of
problem. They live together because they share a property: all three were
previously either absent or set to the most permissive value, and all three are
the kind of thing a vendor security questionnaire asks about by name.

CORS
----
`allow_origins` defaulted to `["*"]`. With the API also serving the SPA that is
not the disaster it sounds like — the browser's same-origin policy is not what
protects this API, the `Authorization` header is (a cross-site request cannot set
one, and cookies alone do not authenticate anything here). But `*` still means
any site can read any response the browser is willing to make, and it is
indefensible on a questionnaire. `cors_origins()` now REFUSES to return `*` when
authentication is enforced unless someone explicitly asks for it.

RESPONSE HEADERS
----------------
The headers below are what a browser needs in order to defend a user against
things the server cannot control: clickjacking, MIME sniffing, and referrer
leakage of tenant ids into third-party analytics. HSTS is included only when the
deployment says it terminates TLS, because sending it over plain HTTP either does
nothing or, if the browser honours it against localhost, breaks local dev in a
way that persists in the browser's HSTS cache long after the header is removed.

BODY SIZE
---------
There was no limit. `enforce_tenant_scope` reads request bodies to find tenant
identifiers and the 3-D endpoints accept base64 images, so an unbounded body is
memory an anonymous caller allocates on our task. The cap is generous (32 MB,
above the largest legitimate plan image) and returns 413 rather than dying.
"""
from __future__ import annotations

import os

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

DEFAULT_MAX_BODY_BYTES = 32 * 1024 * 1024


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


# ── CORS ────────────────────────────────────────────────────────────────


def cors_origins() -> list[str]:
    """The allowed origins, and a refusal to be wide open in production.

    Precedence:
      NXR_CORS_ORIGINS set    the comma-separated allow-list, used verbatim.
                              `*` is honoured here — someone typed it.
      auth not enforced       `*` (local dev; the SPA runs on a different port).
      auth enforced, unset    SAME-ORIGIN ONLY (an empty list). The container
                              serves the SPA and the API from one origin, so this
                              is the correct default and needs no configuration.
                              A separate CloudFront domain must be named
                              explicitly, which is a deliberate act rather than
                              an accident.
    """
    configured = os.getenv("NXR_CORS_ORIGINS", "").strip()
    if configured:
        return [o.strip() for o in configured.split(",") if o.strip()]

    from server.auth import auth_required
    if not auth_required():
        return ["*"]
    return []


def cors_posture() -> str:
    origins = cors_origins()
    if origins == ["*"]:
        return ("[cors] !! allow_origins is '*' - any website can read API "
                "responses on behalf of a signed-in user's browser. Set "
                "NXR_CORS_ORIGINS to your frontend's origin(s).")
    if not origins:
        return ("[cors] same-origin only. The container serves the SPA and the "
                "API together, so nothing else is needed unless the frontend is "
                "on its own domain (then set NXR_CORS_ORIGINS).")
    return f"[cors] {len(origins)} allowed origin(s): {', '.join(origins)}"


def cors_allow_credentials() -> bool:
    """Whether to echo `Access-Control-Allow-Credentials`.

    Only meaningful with an explicit origin list — the CORS spec forbids
    combining it with `*`, and Starlette silently drops one of the two if you
    try, which is a confusing way to find out. The refresh cookie is the reason
    it is wanted at all, and that cookie is scoped to /api/v1/auth.
    """
    origins = cors_origins()
    return bool(origins) and origins != ["*"]


# ── Response headers ────────────────────────────────────────────────────


def _hsts_enabled() -> bool:
    """Only when the deployment says TLS terminates in front of it."""
    if os.getenv("NXR_HSTS") is not None:
        return _truthy(os.getenv("NXR_HSTS"))
    return _truthy(os.getenv("NXR_TRUST_PROXY"))


# The Content-Security-Policy applied to the SPA.
#
# 'unsafe-inline' for styles is present because the 3-D viewer and several chart
# components set inline styles, and removing it would break the UI rather than
# harden it — an accurate policy that is enforced beats an aspirational one that
# gets switched off. Scripts do NOT get 'unsafe-inline': that is the one that
# actually stops XSS, and the bundle has no inline scripts.
#
# `connect-src` includes blob: and data: because the viewer streams generated
# GLBs from object URLs.
_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self' blob: data:; "
    "worker-src 'self' blob:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach the browser-facing defence headers to every response."""

    def __init__(self, app):
        super().__init__(app)
        print(cors_posture(), flush=True)
        if not _hsts_enabled():
            print("[security] HSTS off (no TLS terminator declared). Set "
                  "NXR_HSTS=1 behind an HTTPS load balancer.", flush=True)

    async def dispatch(self, request: Request,
                       call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)

        # Never let a browser guess a content type. The 3-D endpoints return
        # user-supplied bytes, and a sniffed text/html there is stored XSS.
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        # Tenant ids appear in URLs. Without this they travel in the Referer
        # header to any third-party resource the page loads.
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=(), payment=(), usb=()")

        # The CSP applies to documents, not to JSON. Putting it on API responses
        # is harmless but noisy, and it makes the /docs Swagger UI (which does
        # use inline scripts) fail to render.
        path = request.url.path
        if not path.startswith("/api") and path not in ("/docs", "/redoc"):
            response.headers.setdefault("Content-Security-Policy", _CSP)

        if _hsts_enabled():
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains")

        # Credentials must never sit in a shared cache.
        if path.startswith("/api/v1/auth"):
            response.headers["Cache-Control"] = "no-store"

        return response


# ── Body size ───────────────────────────────────────────────────────────


def max_body_bytes() -> int:
    try:
        value = int(os.environ.get("NXR_MAX_BODY_BYTES", "")
                    or DEFAULT_MAX_BODY_BYTES)
    except ValueError:
        return DEFAULT_MAX_BODY_BYTES
    return value if value > 0 else DEFAULT_MAX_BODY_BYTES


class BodyLimitMiddleware(BaseHTTPMiddleware):
    """Refuse oversized requests before they are buffered.

    Checks `Content-Length` only. A chunked request without that header slips
    past, which is a known and accepted gap: closing it means wrapping the
    receive channel and counting bytes, and the real backstop for that case is
    the load balancer's own limit (the ALB caps at 1 MB for Lambda targets and
    streams otherwise). This stops the ordinary accident — a client posting a
    200 MB file at an endpoint expecting 2 MB — at zero cost.
    """

    async def dispatch(self, request: Request,
                       call_next: RequestResponseEndpoint) -> Response:
        raw = request.headers.get("content-length")
        if raw:
            try:
                length = int(raw)
            except ValueError:
                return JSONResponse(status_code=400,
                                    content={"detail": "Malformed Content-Length."})
            cap = max_body_bytes()
            if length > cap:
                return JSONResponse(
                    status_code=413,
                    content={"detail": f"Request body exceeds the {cap} byte limit.",
                             "limit_bytes": cap})
        return await call_next(request)
