"""observability.py — structured logs, request ids, and access logging.

WHAT WAS WRONG
--------------
The server logged with `print()`. On a laptop that is readable; in CloudWatch it
is a wall of unstructured lines with no timestamp, no severity, no request
correlation and no way to answer "show me every 5xx for tenant acme in the last
hour" except by eye. Three specific consequences:

  * NO REQUEST CORRELATION. A single API call touches auth, tenancy, the graph
    and the physics runtime. When one fails there was nothing tying its log lines
    together, so diagnosing a report of "it broke at 14:32" meant reading every
    line from 14:32.
  * NO SEVERITY. Everything was stdout, so an alarm could not distinguish a
    posture note at boot from a failed database write.
  * NOT MACHINE-READABLE. CloudWatch Logs Insights, and every log pipeline, wants
    JSON. Text means no metric filters and no structured queries.

WHAT THIS DOES
--------------
JSON to stdout in production, colourless human-readable text locally. One
`request_id` per request, taken from the inbound `X-Request-ID` if present (so an
ALB/API-Gateway trace id survives) and echoed on the response. Every log line
emitted while handling a request carries that id, the tenant and the principal —
without any call site passing them, because they live in a `ContextVar` the
formatter reads.

WHAT IT DELIBERATELY DOES NOT LOG
---------------------------------
Bodies, query strings, `Authorization` / `X-API-Key` / `X-Device-Token` headers,
and cookies. Logs get shipped to third-party aggregators and read by people who
do not have production database access; a credential or a customer's telemetry in
there is a disclosure that outlives the request by years. Paths ARE logged, and
paths contain tenant ids — that is intentional and is the reason `Referrer-Policy`
is set in `security.py`.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

# Per-request context. A ContextVar rather than a thread-local because the app is
# async: one thread serves many concurrent requests, so a thread-local would
# attribute log lines to whichever request happened to run last.
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "nxr_request_id", default="")
_principal_label: contextvars.ContextVar[str] = contextvars.ContextVar(
    "nxr_principal", default="")
_tenant: contextvars.ContextVar[str] = contextvars.ContextVar(
    "nxr_tenant", default="")

REQUEST_ID_HEADER = "X-Request-ID"

# Header names never written to a log, in any form.
_REDACTED_HEADERS = {
    "authorization", "x-api-key", "x-device-token", "cookie", "set-cookie",
    "proxy-authorization",
}


def current_request_id() -> str:
    return _request_id.get()


def json_logs_enabled() -> bool:
    """JSON when the deployment is a deployment. Explicit override first, then
    the same signal everything else uses: enforced auth means production."""
    explicit = os.environ.get("NXR_LOG_JSON")
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip().lower() in ("1", "true", "yes", "on")
    from server.auth import auth_required
    return auth_required()


def log_level() -> int:
    name = (os.environ.get("NXR_LOG_LEVEL") or "INFO").strip().upper()
    return getattr(logging, name, logging.INFO)


class JsonFormatter(logging.Formatter):
    """One JSON object per line — the shape CloudWatch Logs Insights parses."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                  + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = _request_id.get()
        if request_id:
            payload["request_id"] = request_id
        principal = _principal_label.get()
        if principal:
            payload["principal"] = principal
        tenant = _tenant.get()
        if tenant:
            payload["tenant"] = tenant

        # Anything a call site attached via `extra={...}`.
        for key, value in getattr(record, "__dict__", {}).items():
            if key.startswith("nxr_") and key not in ("nxr_request_id",):
                payload[key[4:]] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # `default=str` so a stray datetime or Path in `extra` cannot make the
        # logger itself raise — a logging call that throws inside an exception
        # handler loses the original error, which is the worst possible time.
        return json.dumps(payload, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Human-readable, for a terminal. Includes a short request id so
    correlation still works while developing."""

    def format(self, record: logging.LogRecord) -> str:
        stamp = time.strftime("%H:%M:%S", time.localtime(record.created))
        request_id = _request_id.get()
        suffix = f"  [{request_id[:8]}]" if request_id else ""
        base = (f"{stamp} {record.levelname:<7} {record.name:<22} "
                f"{record.getMessage()}{suffix}")
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


_configured = False


def configure() -> None:
    """Install the root handler. Idempotent — uvicorn may import this twice."""
    global _configured
    if _configured:
        return
    _configured = True

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_logs_enabled() else TextFormatter())

    root = logging.getLogger()
    # Replace rather than add. uvicorn installs its own handlers, and leaving
    # them produces every line twice — once structured, once not.
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(log_level())

    # uvicorn's access log duplicates AccessLogMiddleware below, without the
    # request id or the principal. Silence it and keep the richer one.
    logging.getLogger("uvicorn.access").handlers = []
    logging.getLogger("uvicorn.access").propagate = False
    for noisy, level in (("uvicorn.error", logging.INFO),
                         ("neo4j", logging.ERROR),
                         ("botocore", logging.WARNING),
                         ("boto3", logging.WARNING),
                         ("urllib3", logging.WARNING),
                         ("httpx", logging.WARNING)):
        logging.getLogger(noisy).setLevel(level)


def get_logger(name: str) -> logging.Logger:
    configure()
    return logging.getLogger(name)


log = get_logger("nxr.http")


# ── Middleware ──────────────────────────────────────────────────────────


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request id, bind the log context, and emit one access line.

    Added OUTERMOST in `server/main.py` so the id exists before any other
    middleware can log, and so the access line's duration covers the whole
    stack — including time spent in the rate limiter and auth, which is exactly
    where a latency regression tends to hide.
    """

    def __init__(self, app):
        super().__init__(app)
        configure()
        log.info("logging configured",
                 extra={"nxr_format": "json" if json_logs_enabled() else "text",
                        "nxr_level": logging.getLevelName(log_level())})

    async def dispatch(self, request: Request,
                       call_next: RequestResponseEndpoint) -> Response:
        # Trust an inbound id so a trace started at the ALB or by the Integration
        # Hub carries through. Bounded and sanitised: it is echoed in a response
        # header and written to logs, so an unbounded attacker-controlled string
        # is both a log-injection vector and a way to bloat every line.
        inbound = (request.headers.get(REQUEST_ID_HEADER) or "").strip()
        request_id = "".join(c for c in inbound if c.isalnum() or c in "-_")[:64] \
            or uuid.uuid4().hex

        id_token = _request_id.set(request_id)
        principal_token = _principal_label.set("")
        tenant_token = _tenant.set("")
        request.state.request_id = request_id

        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)

            # Bind late: auth runs INSIDE this middleware, so the principal is
            # only known now. Every line logged during the request already had
            # the request id, which is what correlation needs; the principal
            # enriches the access line.
            principal = getattr(request.state, "principal", None)
            if principal is not None:
                _principal_label.set(getattr(principal, "label", "") or
                                     getattr(principal, "kind", ""))
            tenants = getattr(request.state, "nxr_tenants", None)
            if tenants:
                _tenant.set(sorted(tenants)[0])

            path = request.url.path
            # Health probes fire every few seconds from every task; logging them
            # at INFO buries everything else and costs real money in ingestion.
            level = logging.DEBUG if path.startswith("/api/v1/health") else (
                logging.WARNING if status >= 500 else
                logging.INFO if status < 400 else logging.WARNING)

            log.log(level, f"{request.method} {path} {status}",
                    extra={
                        "nxr_method": request.method,
                        "nxr_path": path,
                        "nxr_status": status,
                        "nxr_duration_ms": duration_ms,
                        # Query strings can carry tenant ids and filters, but not
                        # credentials — those are headers. The KEYS are logged so
                        # a slow query shape is identifiable; the values are not.
                        "nxr_query_keys": sorted(request.query_params.keys()) or None,
                    })

            _request_id.reset(id_token)
            _principal_label.reset(principal_token)
            _tenant.reset(tenant_token)


def safe_headers(headers) -> dict:
    """Headers with every credential removed. For a debug endpoint or an error
    report — never call this on the hot path."""
    return {k: ("<redacted>" if k.lower() in _REDACTED_HEADERS else v)
            for k, v in dict(headers).items()}
