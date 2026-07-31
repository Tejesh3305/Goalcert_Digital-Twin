"""
conftest.py — shared fixtures.

Two hard rules for the fast suite, both about making the tests trustworthy
rather than merely green:

1. NO EXTERNAL STORES. Neo4j, Postgres, Redis and S3 are all absent. Every
   route in this platform is already written to degrade gracefully when they
   are (that behaviour is a product requirement — see RUN.md §10), so the
   *authorization* tests below exercise exactly the paths a real caller hits.
   A 403 must be returned before any store is touched, and a test that can
   only pass with a database running would not prove that.

2. AUTH IS CONFIGURED EXPLICITLY. The app is open when `NXR_API_KEYS` is unset,
   so a test that forgot to set it would pass while asserting nothing. The
   `api` fixture always sets keys and always asserts enforcement is live.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ONTOLOGY = ROOT / "nextxr-ontology"
for p in (str(ONTOLOGY), str(ONTOLOGY / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

# ── Redirect on-disk state BEFORE anything imports it ──────────────────────
#
# This runs at conftest MODULE scope, not in a fixture, and that placement is
# load-bearing. `paths.py` resolves `DATA_DIR` from NXR_DATA_DIR at import time
# and every store reads it from there, so a fixture that set the variable later
# would be too late — `paths` would already be bound to the repository's real
# `nextxr-ontology/data/` directory and the suite would read and WRITE the
# developer's actual twins.db, changelog.db and historian.db. That is not a
# hypothetical: an earlier revision of this file set the wrong variable name
# (NXR_STATE_DIR, which nothing reads) and did exactly that silently.
_STATE = Path(tempfile.mkdtemp(prefix="nxr-test-state-"))
os.environ["NXR_DATA_DIR"] = str(_STATE)


# ── The live store URLs, captured BEFORE the fast suite erases them ────────
#
# `_isolate_environment` below POPS NXR_DATABASE_URL / NXR_REDIS_URL / NXR_S3_*
# and overwrites NEO4J_PASSWORD, so the fast suite cannot accidentally reach a
# real store — which is correct, and which also means an integration test that
# read os.environ would find nothing there. By the time any fixture runs the
# variables are already gone, so this dict is captured at IMPORT time: the one
# moment the process still has the operator's real configuration.
#
# Integration tests take the `live_stores` fixture (below) rather than reading
# os.environ, so "the fast suite is hermetic" and "the integration suite talks to
# real servers" stay true at the same time.
LIVE_ENV = {
    var: os.environ.get(var, "")
    for var in ("NXR_DATABASE_URL", "NXR_REDIS_URL",
                "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD",
                "NXR_S3_BUCKET", "NXR_S3_ENDPOINT_URL",
                "NXR_S3_ADDRESSING_STYLE", "NXR_S3_PREFIX",
                "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION")
}


# The three keys every authorization test reasons about.
KEY_ADMIN = "test-admin-key"
KEY_ACME = "test-acme-key"          # single tenant, write
KEY_ACME_MULTI = "test-acme-multi"  # explicit multi-tenant set
KEY_ACME_PREFIX = "test-acme-pfx"   # prefix scope — the enterprise shape
KEY_READER = "test-reader-key"      # read-only, single tenant

API_KEYS = [
    {"key": KEY_ADMIN, "tenant": "*", "role": "admin", "name": "Test Admin"},
    {"key": KEY_ACME, "tenant": "acme", "role": "write", "name": "Acme"},
    {"key": KEY_ACME_MULTI, "tenants": ["acme", "acme-two"], "role": "write",
     "name": "Acme Multi"},
    {"key": KEY_ACME_PREFIX, "tenant_prefix": "acme-", "role": "write",
     "name": "Acme Prefix"},
    {"key": KEY_READER, "tenant": "acme", "role": "read", "name": "Acme Reader"},
]


@pytest.fixture(scope="session", autouse=True)
def _isolate_environment():
    """Configure auth and pin every optional backend off.

    `NXR_DATA_DIR` is NOT set here — it is set at module scope above, because by
    the time a fixture runs an imported module may already have resolved it.
    """
    os.environ.update({
        "NXR_API_KEYS": json.dumps(API_KEYS),
        "NXR_REQUIRE_AUTH": "1",
        "NXR_DATA_DIR": str(_STATE),
        # Identity. A fixed signing key, because the app now REFUSES to boot with
        # an ephemeral one while auth is enforced (identity/tokens.py) — the same
        # guard a real deploy hits, and the test environment is a real deploy as
        # far as that check is concerned. Fixed rather than random so a token
        # minted in one test still verifies in another.
        "NXR_JWT_SECRET": "test-jwt-secret-do-not-use-outside-the-test-suite",
        "NXR_SECRET_PEPPER": "test-pepper",
        # Self-service signup is closed by default once auth is enforced; the
        # identity tests need it open to exercise the flow.
        "NXR_ALLOW_SIGNUP": "1",
        # Plain-HTTP TestClient: a Secure cookie would never come back.
        "NXR_COOKIE_SECURE": "0",
        # Short-circuit the bootstrap admin — a test suite must not depend on
        # environment-provisioned accounts existing.
        "NXR_BOOTSTRAP_ADMIN_EMAIL": "",
        "NXR_BOOTSTRAP_ADMIN_PASSWORD": "",
        # Neo4j is not running here, and `graph/connection.py` now REFUSES to
        # fall back to the repository's published dev password once auth is
        # enforced. Setting it explicitly keeps the connection failure a plain
        # "nothing is listening" (which every route degrades around) instead of
        # a credential-policy error in the middle of unrelated assertions.
        "NEO4J_PASSWORD": "test-neo4j-password",
        # Every TestClient enters the app's lifespan, which probes Neo4j. With
        # nothing listening the default 4s acquisition timeout is paid on each
        # one, and the identity suite builds a fresh client per test — minutes of
        # the run spent waiting for a connection that will never succeed.
        "NEO4J_CONN_TIMEOUT": "0.3",
        "NEO4J_ACQ_TIMEOUT": "0.3",
        "NEO4J_RETRY_TIME": "0.3",
        # Keep the bus in-process and silent: these tests assert on HTTP status
        # codes, not on fan-out, and a Redis probe would add seconds per test.
        "NXR_BUS_DISABLED": "1",
        # Rate limiting OFF for the shared fixture. The authorization sweep walks
        # every tenant-carrying route in the app and would trip the copilot
        # bucket partway through, turning 403 assertions into 429s — a real
        # limiter doing its job, but it would mean the isolation suite stopped
        # testing isolation. `test_ratelimit.py` builds its own client with the
        # limiter ON and asserts on it directly.
        "NXR_RATELIMIT_DISABLED": "1",
        # Never let a test reach a real LLM. Absent keys make copilot/ return
        # its documented fallback rather than billing anyone.
        "ANTHROPIC_API_KEY": "",
        "NXR_CORS_ORIGINS": "*",
    })
    for var in ("NXR_DATABASE_URL", "NXR_S3_BUCKET", "NXR_REDIS_URL",
                "NXR_REQUIRE_DB", "NXR_REQUIRE_S3", "NXR_REQUIRE_REDIS"):
        os.environ.pop(var, None)
    yield _STATE


@pytest.fixture(scope="session")
def app(_isolate_environment):
    """The real FastAPI app, assembled exactly as production assembles it.

    Deliberately NOT a stripped-down test app: the bug this suite exists to
    prevent was a *missing global dependency*, which a hand-built app would
    reproduce incorrectly.
    """
    from server.main import app as real_app
    return real_app


@pytest.fixture(scope="session")
def api(app):
    """A TestClient plus a live assertion that auth is actually enforced."""
    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as client:
        probe = client.get("/api/v1/stats", params={"tenant": "acme"})
        assert probe.status_code == 401, (
            "auth is not being enforced in the test environment — every "
            "authorization assertion below would pass vacuously. "
            f"got {probe.status_code}: {probe.text[:200]}")
        yield client


def hdr(key: str, **extra: str) -> dict:
    """Request headers for an API key, plus any extras (e.g. a hub org)."""
    return {"X-API-Key": key, **extra}


# ── Integration fixtures ───────────────────────────────────────────────────
#
# Everything below is used only by tests marked `@pytest.mark.integration`,
# which the default CI run deselects. They restore the real store URLs captured
# in LIVE_ENV for the duration of one test, then put the hermetic values back.


@contextmanager
def _restored(*names: str):
    """Temporarily restore the operator's real value for these variables."""
    saved = {n: os.environ.get(n) for n in names}
    try:
        for n in names:
            value = LIVE_ENV.get(n, "")
            if value:
                os.environ[n] = value
            else:
                os.environ.pop(n, None)
        yield
    finally:
        for n, old in saved.items():
            if old is None:
                os.environ.pop(n, None)
            else:
                os.environ[n] = old


@pytest.fixture
def live_mysql():
    """A provisioned MySQL, or skip.

    Skips rather than fails when NXR_DATABASE_URL is unset, so `pytest -m
    integration` on a laptop with nothing running reports "skipped" instead of a
    wall of connection errors. CI sets the variable, so a genuinely broken MySQL
    path fails there — see the `integration` job in .github/workflows/ci.yml.
    """
    url = LIVE_ENV.get("NXR_DATABASE_URL", "")
    if not url:
        pytest.skip("NXR_DATABASE_URL is not set — start MySQL with "
                    "`docker compose up -d mysql` and export the URL")

    import db
    from db import schema as db_schema

    with _restored("NXR_DATABASE_URL"):
        # The dialect is read from the environment on every call, but the
        # connection pool and the "already provisioned" set are process-global
        # and were populated while SQLite was live. Both have to go, or the test
        # silently exercises the SQLite path under a MySQL name.
        db.close_pool()
        db_schema.reset_cache()
        assert db.is_mysql(), (
            f"expected the MySQL dialect, got {db.dialect()!r} — "
            f"NXR_DATABASE_URL={db.redacted_url()!r}")
        try:
            yield db
        finally:
            db.close_pool()
            db_schema.reset_cache()


@pytest.fixture
def live_redis():
    """A reachable Redis URL, or skip."""
    url = LIVE_ENV.get("NXR_REDIS_URL", "")
    if not url:
        pytest.skip("NXR_REDIS_URL is not set — start Redis with "
                    "`docker compose up -d redis`")
    # The fast suite sets NXR_BUS_DISABLED=1 so no test pays for a Redis probe.
    # Left in place it pins the bus to the null backend, and an integration test
    # asserting "the bus is Redis" would fail for a reason that has nothing to do
    # with Redis.
    disabled = os.environ.pop("NXR_BUS_DISABLED", None)
    try:
        with _restored("NXR_REDIS_URL"):
            yield url
    finally:
        if disabled is not None:
            os.environ["NXR_BUS_DISABLED"] = disabled


@pytest.fixture
def live_neo4j():
    """A reachable Neo4j driver, or skip."""
    if not LIVE_ENV.get("NEO4J_URI"):
        pytest.skip("NEO4J_URI is not set — start Neo4j with "
                    "`docker compose up -d neo4j`")
    with _restored("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"):
        try:
            from graph import connection
        except Exception as e:                       # pragma: no cover
            pytest.skip(f"graph.connection unavailable: {e}")
        connection.close_driver()
        try:
            yield connection
        finally:
            connection.close_driver()
