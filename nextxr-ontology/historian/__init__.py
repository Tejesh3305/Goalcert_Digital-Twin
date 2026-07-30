"""
historian — the telemetry time-series store.

The platform's numeric memory. Neo4j remains the SEMANTIC authority (what a thing
is, what it connects to, what rules govern it); this package is the NUMERIC
authority (what it measured, and when). Neither duplicates the other, and the
join between them is the Neo4j `node_id`, carried here as `asset_id`.

    from historian import Measurement, write, history, latest, signals

Backends, selected automatically and reported by `info()`:

    mysql     production: one plain table on the configured RDS MySQL instance.
              Rollups are computed on the fly from raw. No columnar compression
              and no automatic retention, so raw grows without bound — fine for
              an MVP/moderate ingest; add an app-level purge for high volume.
    sqlite    local dev, via db/core.py.
"""

from __future__ import annotations

from .core import (
    DEFAULT_MAX_POINTS,
    HARD_MAX_POINTS,
    Measurement,
    Series,
    WriteResult,
    choose_agg,
    coerce,
    history,
    info,
    latest,
    log_posture,
    ping,
    purge_tenant,
    signals,
    tenant_stats,
    write,
)
from .schema import (
    BUCKETS,
    QUALITY_BAD,
    QUALITY_GOOD,
    QUALITY_UNCERTAIN,
    backend,
    drop_all,
    ensure,
    reset_cache,
    retention_days,
)


class HistorianUnavailable(RuntimeError):
    """Raised by `require()` when the historian is mandatory but unusable."""


def require() -> None:
    """Fail fast when the deployment declared the historian mandatory but it is
    not usable. Called from `server.main._preflight()`, so a misconfigured task
    crash-loops with a one-line reason in CloudWatch instead of quietly serving a
    twin with no telemetry history.

    Opt in with `NXR_REQUIRE_HISTORIAN=1` (parallel to NXR_REQUIRE_DB/_S3/_REDIS).
    Off by default: the SQLite/plain-table fallbacks WORK, so most deployments do
    not need to make this fatal.
    """
    import os
    if str(os.environ.get("NXR_REQUIRE_HISTORIAN") or "").strip().lower() \
            not in ("1", "true", "yes", "on"):
        return
    ok, detail = ping()
    if not ok:
        raise HistorianUnavailable(
            f"NXR_REQUIRE_HISTORIAN is set but the historian is not usable: "
            f"{detail}")


__all__ = [
    "BUCKETS", "DEFAULT_MAX_POINTS", "HARD_MAX_POINTS", "HistorianUnavailable",
    "Measurement", "QUALITY_BAD", "QUALITY_GOOD", "QUALITY_UNCERTAIN", "Series",
    "WriteResult", "backend", "choose_agg", "coerce", "drop_all", "ensure",
    "history", "info", "latest", "log_posture", "ping", "purge_tenant",
    "require", "reset_cache", "retention_days", "signals", "tenant_stats",
    "write",
]
