"""test_ratelimit.py — the bound on what one caller can spend.

WHY THIS SUITE BUILDS ITS OWN CLIENT
------------------------------------
`conftest` sets `NXR_RATELIMIT_DISABLED=1` for the shared `api` fixture, because
the tenant-isolation sweep walks every tenant-carrying route in the app and would
trip the copilot bucket partway through — turning 403 assertions into 429s. That
is a real limiter doing its job, but it would mean the isolation suite stopped
testing isolation.

So the limiter is off there and ON here, and these tests exercise the buckets
directly rather than through HTTP wherever the property is about arithmetic. A
token bucket is a small amount of maths; going through the whole middleware stack
to check it refills correctly is slow and tests the wrong thing.

WHAT MATTERS MOST
-----------------
`test_login_is_limited`. Per-account lockout (identity/store.py) stops eight
guesses against ONE account and does nothing about one guess against each of ten
thousand accounts — which is what credential stuffing actually is. That is a
per-IP problem and this is the only thing solving it.
"""

from __future__ import annotations

import pytest
from server import ratelimit
from server.ratelimit import Limit


@pytest.fixture(autouse=True)
def fresh_buckets():
    """Empty the in-process buckets around every test. They are module-level, so
    one test's consumption otherwise sets the starting point for the next."""
    ratelimit.reset()
    yield
    ratelimit.reset()


@pytest.fixture
def limited(app, monkeypatch):
    """A client with the limiter ON, and tight limits so a test can exhaust a
    bucket in a handful of requests rather than hundreds."""
    from fastapi.testclient import TestClient

    monkeypatch.delenv("NXR_RATELIMIT_DISABLED", raising=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


# ── The bucket itself ───────────────────────────────────────────────────


def test_burst_is_allowed_then_refused():
    limit = Limit(rate=1.0, burst=3)

    for i in range(3):
        allowed, _ = ratelimit._local.take("k", limit)
        assert allowed, f"request {i + 1} of the burst was refused"

    allowed, retry_after = ratelimit._local.take("k", limit)
    assert not allowed
    assert retry_after > 0


def test_the_bucket_refills_over_time(monkeypatch):
    """A token bucket, not a fixed window. A fixed window lets a caller send the
    whole allowance at 11:59:59 and again at 12:00:00 — double the burst in one
    second, which is what the limit existed to prevent."""
    import server.ratelimit as rl

    now = [1000.0]
    monkeypatch.setattr(rl.time, "monotonic", lambda: now[0])

    limit = Limit(rate=2.0, burst=2)          # 2/second

    assert rl._local.take("refill", limit)[0]
    assert rl._local.take("refill", limit)[0]
    assert not rl._local.take("refill", limit)[0]

    now[0] += 0.5                              # one token's worth
    assert rl._local.take("refill", limit)[0]
    assert not rl._local.take("refill", limit)[0]


def test_a_bucket_never_exceeds_its_burst(monkeypatch):
    """Idle time must not accumulate an unbounded allowance — otherwise a client
    that waits an hour gets to send an hour's worth at once, which is exactly the
    spike the limiter exists to absorb."""
    import server.ratelimit as rl

    now = [1000.0]
    monkeypatch.setattr(rl.time, "monotonic", lambda: now[0])

    limit = Limit(rate=1.0, burst=3)
    rl._local.take("cap", limit)

    now[0] += 3600                             # an hour idle

    allowed = sum(1 for _ in range(10) if rl._local.take("cap", limit)[0])
    assert allowed == 3


def test_callers_have_separate_buckets():
    limit = Limit(rate=1.0, burst=2)

    for _ in range(2):
        ratelimit._local.take("caller-a", limit)
    assert not ratelimit._local.take("caller-a", limit)[0]

    assert ratelimit._local.take("caller-b", limit)[0]


# ── Classification ──────────────────────────────────────────────────────


@pytest.mark.parametrize("path,expected", [
    ("/api/v1/auth/login", "auth_login"),
    ("/api/v1/auth/signup", "auth_signup"),
    ("/api/v1/auth/password/forgot", "auth_forgot"),
    ("/api/v1/auth/refresh", "auth_refresh"),
    ("/api/v1/copilot/diagnosis", "copilot"),
    ("/api/v1/ingest/telemetry", "ingest"),
    ("/api/v1/twins", "default"),
])
def test_routes_map_to_their_limit(path, expected):
    assert ratelimit._classify(path, "POST") == expected


@pytest.mark.parametrize("path", [
    "/api/v1/health",
    "/api/v1/copilot/health",
    "/static/app.js",
    "/assets/index.css",
    "/",
    "/some/client/route",
])
def test_exempt_paths_are_never_limited(path):
    """An orchestrator polls health by design, and throttling the SPA's own
    assets would make the app fail to load under its own traffic."""
    assert ratelimit._classify(path, "GET") is None


def test_the_login_limit_is_tighter_than_the_default():
    assert ratelimit.LIMITS["auth_login"].burst < ratelimit.LIMITS["default"].burst
    assert ratelimit.LIMITS["auth_login"].rate < ratelimit.LIMITS["default"].rate


def test_the_llm_surface_is_limited_tightly():
    """Every call bills our Anthropic key."""
    assert ratelimit.LIMITS["copilot"].burst <= 20


def test_ingest_is_allowed_to_burst():
    """Machine traffic is legitimately bursty — a gateway flushes a buffered
    minute at once. A ceiling, but a high one."""
    assert ratelimit.LIMITS["ingest"].burst > ratelimit.LIMITS["default"].burst


# ── Through HTTP ────────────────────────────────────────────────────────


def test_login_is_limited(limited):
    """THE ONE THAT MATTERS. Without this, an attacker can try one password
    against every account in a breach corpus and the per-account lockout never
    triggers."""
    limit = ratelimit.LIMITS["auth_login"]

    statuses = [
        limited.post("/api/v1/auth/login",
                     json={"email": f"probe{i}@example.com",
                           "password": "guessing-a-password"}).status_code
        for i in range(limit.burst + 4)
    ]

    assert 429 in statuses, (
        f"{len(statuses)} login attempts and none were throttled — the login "
        f"endpoint is an unbounded password-guessing surface")


def test_a_429_says_when_to_retry(limited):
    limit = ratelimit.LIMITS["auth_login"]

    response = None
    for i in range(limit.burst + 6):
        response = limited.post("/api/v1/auth/login",
                                json={"email": f"x{i}@example.com",
                                      "password": "guessing-a-password"})
        if response.status_code == 429:
            break

    assert response.status_code == 429
    # A bare "too many requests" reads as a bug; a number reads as a rule.
    assert int(response.headers["Retry-After"]) >= 1
    assert response.json()["retry_after_seconds"] >= 1
    assert "X-RateLimit-Limit" in response.headers


def test_health_survives_a_flood(limited):
    """An orchestrator probing every few seconds from every task must never be
    throttled — that is a self-inflicted outage."""
    statuses = {limited.get("/api/v1/health/live").status_code for _ in range(120)}
    assert statuses == {200}


def test_the_limiter_can_be_disabled(app, monkeypatch):
    """The switch exists for the test suite and for an air-gapped demo. It must
    announce itself at boot, which `RateLimitMiddleware.__init__` does."""
    monkeypatch.setenv("NXR_RATELIMIT_DISABLED", "1")
    assert ratelimit.enabled() is False

    monkeypatch.delenv("NXR_RATELIMIT_DISABLED")
    assert ratelimit.enabled() is True


def test_the_scale_factor_applies(monkeypatch):
    monkeypatch.setenv("NXR_RATELIMIT_SCALE", "2")
    assert ratelimit._scale() == 2.0

    # A nonsensical value must not turn the limiter off.
    monkeypatch.setenv("NXR_RATELIMIT_SCALE", "not-a-number")
    assert ratelimit._scale() == 1.0

    monkeypatch.setenv("NXR_RATELIMIT_SCALE", "-5")
    assert ratelimit._scale() == 1.0


# ── Bucket keys ─────────────────────────────────────────────────────────


def test_an_authenticated_caller_is_charged_by_credential():
    """Not by address: one customer behind a corporate NAT must not consume the
    whole office's budget, and a misbehaving key should be throttled wherever it
    is used from."""
    from types import SimpleNamespace

    request = SimpleNamespace(
        state=SimpleNamespace(principal=SimpleNamespace(
            key_id="key_abc", user_id=None)),
        headers={},
        client=SimpleNamespace(host="10.0.0.1"),
    )
    assert ratelimit._bucket_key(request, "default") == "default:key:key_abc"


def test_an_anonymous_caller_is_charged_by_address():
    from types import SimpleNamespace

    request = SimpleNamespace(
        state=SimpleNamespace(principal=None),
        headers={},
        client=SimpleNamespace(host="203.0.113.9"),
    )
    assert ratelimit._bucket_key(request, "auth_login") == "auth_login:ip:203.0.113.9"


def test_a_forged_forwarded_header_is_ignored_without_a_proxy(monkeypatch):
    """Reading X-Forwarded-For unconditionally lets any client get a fresh bucket
    per request, which turns the limiter off for exactly the callers it exists to
    stop."""
    from types import SimpleNamespace

    monkeypatch.delenv("NXR_TRUST_PROXY", raising=False)

    request = SimpleNamespace(
        state=SimpleNamespace(principal=None),
        headers={"X-Forwarded-For": "1.2.3.4"},
        client=SimpleNamespace(host="10.0.0.1"),
    )
    assert ratelimit._client_ip(request) == "10.0.0.1"

    monkeypatch.setenv("NXR_TRUST_PROXY", "1")
    assert ratelimit._client_ip(request) == "1.2.3.4"


def test_only_the_leftmost_forwarded_address_is_used(monkeypatch):
    """The rest of the chain is proxies. Taking the last entry charges the ALB
    for every request in the fleet."""
    from types import SimpleNamespace

    monkeypatch.setenv("NXR_TRUST_PROXY", "1")
    request = SimpleNamespace(
        state=SimpleNamespace(principal=None),
        headers={"X-Forwarded-For": "203.0.113.9, 10.0.0.5, 10.0.0.6"},
        client=SimpleNamespace(host="10.0.0.1"),
    )
    assert ratelimit._client_ip(request) == "203.0.113.9"
