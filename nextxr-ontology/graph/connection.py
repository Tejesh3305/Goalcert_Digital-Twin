"""
connection.py — Neo4j driver connection manager.

Usage:
    from graph.connection import get_driver, close_driver

    driver = get_driver()          # uses defaults from docker-compose
    driver = get_driver(uri="bolt://somehost:7687", user="neo4j", password="pw")
"""

import logging
import os

from neo4j import GraphDatabase

# Silence benign server notifications (e.g. "relationship type FLAGS does not
# exist" when querying a rel type the young graph hasn't created yet). These
# are informational, not errors, and clutter tool output.
logging.getLogger("neo4j").setLevel(logging.ERROR)

_DEFAULT_URI = "bolt://localhost:7687"
_DEFAULT_USER = "neo4j"

# The local docker-compose password. It is in the repository, so it is PUBLIC —
# treat it as a fixture, never as a credential.
#
# It used to be the fallback whenever NEO4J_PASSWORD was unset, which meant a
# deployment that forgot the variable authenticated to its production graph with
# a password printed in the source. `_default_password()` now hands it out only
# in the local-dev posture; with auth enforced, an unset variable is a hard
# failure at connect time rather than a silent use of a known secret.
_DEV_PASSWORD = "nextxr2026"

_driver = None


def _default_password() -> str:
    configured = os.getenv("NEO4J_PASSWORD")
    if configured:
        return configured

    try:
        from server.auth import auth_required
        production = auth_required()
    except Exception:
        # Imported outside the server (a CLI tool, a test harness). Fall back to
        # the same signal the server uses rather than assuming either posture.
        production = str(os.getenv("NXR_DEV_MODE", "")).strip().lower() \
            not in ("1", "true", "yes", "on")

    if production:
        raise RuntimeError(
            "NEO4J_PASSWORD is not set. Refusing to fall back to the "
            "development password, which is published in this repository. Set "
            "NEO4J_PASSWORD (from Secrets Manager on ECS), or set NXR_DEV_MODE=1 "
            "for local development.")
    return _DEV_PASSWORD


def get_driver(uri=None, user=None, password=None):
    """Return a singleton Neo4j driver (creates on first call)."""
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            uri or os.getenv("NEO4J_URI", _DEFAULT_URI),
            auth=(
                user or os.getenv("NEO4J_USER", _DEFAULT_USER),
                password or _default_password(),
            ),
            # Fail fast when Neo4j is down (e.g. Docker off) so the server still
            # boots and serves the frontend + bus/schema APIs instead of hanging
            # on the default 60s connection-acquisition timeout.
            connection_timeout=float(os.getenv("NEO4J_CONN_TIMEOUT", "4")),
            connection_acquisition_timeout=float(
                os.getenv("NEO4J_ACQ_TIMEOUT", "4")),
            max_transaction_retry_time=float(
                os.getenv("NEO4J_RETRY_TIME", "4")),
        )
    return _driver


def close_driver():
    """Shut down the driver cleanly."""
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None
