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
    key: str
    tenant: str      # "*" for admin (all tenants)
    role: str         # "admin", "write", "read"
    name: str         # human label


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
            tenant=k.get("tenant", "*"),
            role=k.get("role", "read"),
            name=k.get("name", "unknown"),
        )
        for k in keys
    }


def _resolve_key(api_key: str) -> Optional[ApiKeyInfo]:
    if not _key_store:
        _load_keys()
    return _key_store.get(api_key)


def check_tenant_access(key_info: ApiKeyInfo, requested_tenant: str) -> bool:
    """Check if this key can access the requested tenant."""
    if key_info.tenant == "*":
        return True
    return key_info.tenant == requested_tenant


def check_write_access(key_info: ApiKeyInfo) -> bool:
    """Check if this key can perform write operations."""
    return key_info.role in ("admin", "write")


# Paths that don't require auth
_PUBLIC_PATHS = {"/", "/docs", "/openapi.json", "/redoc"}

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

        # Require API key
        api_key = request.headers.get("X-API-Key")
        if not api_key:
            # Allow requests without key in dev mode (no NXR_API_KEYS env set)
            if not os.getenv("NXR_API_KEYS"):
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

        # Check write access for mutations
        if request.method in ("POST", "PATCH", "PUT", "DELETE"):
            if not check_write_access(key_info):
                return _deny(403, f"Key '{key_info.name}' has read-only access")

        # Attach key info to request state for downstream use
        request.state.api_key = key_info
        return await call_next(request)
