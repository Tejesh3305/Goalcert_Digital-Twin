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
        # Keep the bus in-process and silent: these tests assert on HTTP status
        # codes, not on fan-out, and a Redis probe would add seconds per test.
        "NXR_BUS_DISABLED": "1",
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
