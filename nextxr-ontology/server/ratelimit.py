"""ratelimit.py — a bound on what one caller can spend.

WHY THIS EXISTS
---------------
Three surfaces on this API cost real money or real access, and none of them had a
ceiling:

  * `/api/v1/auth/login` is a password-guessing oracle. Per-account lockout
    (identity/store.py) stops eight guesses against ONE account; it does nothing
    about one guess against each of ten thousand accounts, which is what
    credential stuffing actually looks like. That is a per-IP problem.
  * `/api/v1/copilot/*` bills our Anthropic key per call. `copilot/spend.py` caps
    the tokens a TENANT may spend; this caps the rate at which anyone can try,
    including callers who have not authenticated yet.
  * Everything else is a database and a graph, and an unbounded client is a
    denial-of-service against every other customer on the same task.

TOKEN BUCKET, NOT FIXED WINDOW
------------------------------
A fixed window ("100 requests per minute") lets a caller send 100 at 11:59:59 and
100 more at 12:00:00 — 200 in one second, which is the burst the limit existed to
prevent. A token bucket refills continuously, so the sustained rate and the burst
size are separate, stated numbers.

TWO BACKENDS, AND WHY THE FALLBACK IS HONEST ABOUT ITSELF
----------------------------------------------------------
With Redis configured the bucket is shared across tasks, so "60 logins per
minute" means 60 across the fleet. Without it each task keeps its own counter, so
the real limit is N x the configured number. That is a genuine weakening rather
than a technicality, so it is stated at boot rather than hidden — and the Redis
path fails OPEN on a connection error, because a rate limiter that takes the API
down when its cache blips has caused a worse outage than the one it prevents.

WHAT IS NOT RATE-LIMITED
------------------------
Health probes (an orchestrator polls them by design) and the SPA's static assets.
Both are listed explicitly below rather than pattern-matched, so nothing joins
them by accident.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response


@dataclass(frozen=True)
class Limit:
    """`burst` requests immediately, then `rate` per second sustained."""
    rate: float
    burst: int

    @property
    def per_minute(self) -> int:
        return int(self.rate * 60)


# Per-route-class limits. The unauthenticated surfaces are tightest because they
# are the ones an anonymous caller can reach.
#
# LOGIN: 10/minute with a burst of 5 per IP. A human signing in uses one or two;
# a stuffing run needs thousands, and this makes that take days instead of
# minutes. Deliberately NOT tighter — offices and mobile carriers put many
# legitimate users behind one address, and locking out a whole building to slow
# an attacker is a bad trade.
LIMITS: dict[str, Limit] = {
    "auth_login":   Limit(rate=10 / 60, burst=5),
    "auth_signup":  Limit(rate=5 / 60, burst=3),
    "auth_forgot":  Limit(rate=5 / 60, burst=3),
    "auth_refresh": Limit(rate=60 / 60, burst=20),
    # The LLM surface. Costs money per call, so the burst is small even for an
    # authenticated caller; `copilot/spend.py` enforces the token budget on top.
    "copilot":      Limit(rate=30 / 60, burst=10),
    # Telemetry ingest is machine traffic and legitimately bursty — a gateway
    # flushes a buffered minute at once. High ceiling, still a ceiling.
    "ingest":       Limit(rate=600 / 60, burst=300),
    "default":      Limit(rate=300 / 60, burst=120),
}

# Paths that are never limited.
_EXEMPT = (
    "/api/v1/health",
    "/api/v1/copilot/health",
    "/static",
    "/assets",
)


def _classify(path: str, method: str) -> str | None:
    """Which limit applies, or None for exempt."""
    normalized = path.rstrip("/") or "/"
    if normalized.startswith(_EXEMPT):
        return None
    if not path.startswith("/api"):
        return None                      # SPA shell and client-router paths

    if normalized == "/api/v1/auth/login":
        return "auth_login"
    if normalized == "/api/v1/auth/signup":
        return "auth_signup"
    if normalized in ("/api/v1/auth/password/forgot",
                      "/api/v1/auth/password/reset"):
        return "auth_forgot"
    if normalized == "/api/v1/auth/refresh":
        return "auth_refresh"
    if normalized.startswith("/api/v1/copilot"):
        return "copilot"
    if normalized.startswith("/api/v1/ingest"):
        return "ingest"
    return "default"


def enabled() -> bool:
    return str(os.environ.get("NXR_RATELIMIT_DISABLED", "")).strip().lower() \
        not in ("1", "true", "yes", "on")


def _scale() -> float:
    """A multiplier on every limit, so a deployment can loosen or tighten the
    whole table without editing per-route numbers."""
    try:
        value = float(os.environ.get("NXR_RATELIMIT_SCALE", "1") or 1)
    except ValueError:
        return 1.0
    return value if value > 0 else 1.0


# ── In-process bucket ───────────────────────────────────────────────────


class _LocalBuckets:
    """Per-task token buckets. Correct for one task; N times looser for N."""

    def __init__(self) -> None:
        self._buckets: dict[str, tuple[float, float]] = {}   # key -> (tokens, ts)
        self._lock = threading.Lock()
        self._last_sweep = time.monotonic()

    def take(self, key: str, limit: Limit) -> tuple[bool, float]:
        """Consume one token. Returns (allowed, retry_after_seconds)."""
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (float(limit.burst), now))
            tokens = min(float(limit.burst), tokens + (now - last) * limit.rate)
            if tokens >= 1.0:
                self._buckets[key] = (tokens - 1.0, now)
                allowed, retry = True, 0.0
            else:
                self._buckets[key] = (tokens, now)
                allowed, retry = False, (1.0 - tokens) / limit.rate

            # Periodic sweep. Without it every distinct client address is a
            # permanent dictionary entry, which is an unbounded memory leak an
            # anonymous caller controls the size of.
            if now - self._last_sweep > 300:
                self._last_sweep = now
                cutoff = now - 900
                for bucket_key, (_, stamp) in list(self._buckets.items()):
                    if stamp < cutoff:
                        self._buckets.pop(bucket_key, None)
        return allowed, retry

    def clear(self) -> None:
        with self._lock:
            self._buckets.clear()


_local = _LocalBuckets()


# ── Redis bucket (shared across tasks) ──────────────────────────────────

# Atomic token bucket in one round trip. It MUST be atomic: read-then-write from
# the application would let concurrent requests on different tasks each read the
# same token count and all be allowed, which is precisely the burst the limiter
# exists to prevent.
_LUA = """
local key    = KEYS[1]
local rate   = tonumber(ARGV[1])
local burst  = tonumber(ARGV[2])
local now    = tonumber(ARGV[3])
local state  = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts     = tonumber(state[2])
if tokens == nil then tokens = burst; ts = now end
tokens = math.min(burst, tokens + (now - ts) * rate)
local allowed = 0
local retry = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  retry = (1 - tokens) / rate
end
redis.call('HMSET', key, 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', key, math.ceil(burst / rate) + 60)
return {allowed, tostring(retry)}
"""

_redis_client = None
_redis_script = None
_redis_lock = threading.Lock()
_redis_failed_at = 0.0


def _redis():
    """The shared Redis client, or None. Reuses the bus's URL — the limiter and
    the event bus want the same ElastiCache cluster, and a second variable is a
    second thing to get wrong."""
    global _redis_client, _redis_script, _redis_failed_at

    url = (os.environ.get("NXR_REDIS_URL")
           or os.environ.get("REDIS_URL") or "").strip()
    if not url:
        return None

    # After a failure, stop trying for a while. Retrying a dead Redis on every
    # request adds its connection timeout to every request's latency — the
    # limiter would become the outage.
    if _redis_failed_at and time.monotonic() - _redis_failed_at < 30:
        return None

    if _redis_client is not None:
        return _redis_client

    with _redis_lock:
        if _redis_client is not None:
            return _redis_client
        try:
            import redis as _redis_lib
            client = _redis_lib.Redis.from_url(
                url, socket_timeout=0.25, socket_connect_timeout=0.25,
                decode_responses=True)
            client.ping()
            _redis_script = client.register_script(_LUA)
            _redis_client = client
            _redis_failed_at = 0.0
            return _redis_client
        except Exception:
            _redis_failed_at = time.monotonic()
            return None


def _take_redis(key: str, limit: Limit) -> tuple[bool, float] | None:
    """Consume a token from the shared bucket, or None if Redis is unavailable."""
    global _redis_client, _redis_failed_at
    client = _redis()
    if client is None or _redis_script is None:
        return None
    try:
        allowed, retry = _redis_script(
            keys=[f"nxr:rl:{key}"],
            args=[limit.rate, limit.burst, time.time()])
        return bool(int(allowed)), float(retry)
    except Exception:
        # Fail OPEN, and stop using Redis for a while. A limiter that 500s when
        # its cache blips is a worse outage than the abuse it prevents.
        with _redis_lock:
            _redis_client = None
            _redis_failed_at = time.monotonic()
        return None


def shared_backend_active() -> bool:
    return _redis() is not None


def reset() -> None:
    """Drop local state. For tests."""
    _local.clear()


# ── Identity of the caller being limited ────────────────────────────────


def _client_ip(request: Request) -> str:
    """The address to charge. `X-Forwarded-For` is read only when
    NXR_TRUST_PROXY says a proxy is in front — otherwise any client could forge
    the header and get a fresh bucket per request, which turns the limiter off
    for exactly the callers it is meant to stop."""
    if str(os.environ.get("NXR_TRUST_PROXY", "")).strip().lower() in ("1", "true", "yes", "on"):
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


def _bucket_key(request: Request, category: str) -> str:
    """Who is being limited.

    An authenticated caller is charged by CREDENTIAL, not by address: one
    customer behind a corporate NAT must not consume the whole office's budget,
    and a key that misbehaves should be throttled wherever it is used from.
    Anonymous callers are charged by address, which is all we know about them.
    """
    principal = getattr(request.state, "principal", None)
    if principal is not None:
        if getattr(principal, "key_id", None):
            return f"{category}:key:{principal.key_id}"
        if getattr(principal, "user_id", None):
            return f"{category}:user:{principal.user_id}"
    return f"{category}:ip:{_client_ip(request)}"


# ── Middleware ──────────────────────────────────────────────────────────


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Bound the request rate per caller.

    ORDERING MATTERS. This is added to the app BEFORE AuthMiddleware in
    `server/main.py`, which — because Starlette wraps middleware in reverse — puts
    it OUTSIDE auth, so it runs FIRST. That is deliberate: the login endpoint has
    to be limited for callers who have not authenticated and never will, and a
    limiter that runs after authentication cannot see them. The cost is that
    `request.state.principal` is not set yet on the first pass, so unauthenticated
    traffic is charged by IP — which is exactly right for it.
    """

    def __init__(self, app):
        super().__init__(app)
        if not enabled():
            print("[ratelimit] !! DISABLED via NXR_RATELIMIT_DISABLED. Login is "
                  "an unbounded password-guessing surface and /copilot is an "
                  "unbounded bill.", flush=True)
        elif shared_backend_active():
            print("[ratelimit] Redis-backed - limits are shared across tasks.",
                  flush=True)
        else:
            print("[ratelimit] in-process buckets - each task counts separately, "
                  "so the effective fleet-wide limit is N x the configured value. "
                  "Set NXR_REDIS_URL to share them.", flush=True)

    async def dispatch(self, request: Request,
                       call_next: RequestResponseEndpoint) -> Response:
        if not enabled():
            return await call_next(request)

        category = _classify(request.url.path, request.method)
        if category is None:
            return await call_next(request)

        limit = LIMITS.get(category, LIMITS["default"])
        scale = _scale()
        if scale != 1.0:
            limit = Limit(rate=limit.rate * scale,
                          burst=max(1, int(limit.burst * scale)))

        key = _bucket_key(request, category)

        result = _take_redis(key, limit)
        if result is None:
            result = _local.take(key, limit)
        allowed, retry_after = result

        if not allowed:
            seconds = max(1, int(retry_after + 0.999))
            response = JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many requests. Slow down and try again.",
                    "retry_after_seconds": seconds,
                },
            )
            response.headers["Retry-After"] = str(seconds)
            response.headers["X-RateLimit-Limit"] = str(limit.per_minute)
            response.headers["X-RateLimit-Policy"] = (
                f"{limit.per_minute};w=60;burst={limit.burst}")
            return response

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit.per_minute)
        return response
