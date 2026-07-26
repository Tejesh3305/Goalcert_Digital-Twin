"""
historian — the telemetry time-series store.

The platform's numeric memory. Neo4j remains the SEMANTIC authority (what a thing
is, what it connects to, what rules govern it); this package is the NUMERIC
authority (what it measured, and when). Neither duplicates the other, and the
join between them is the Neo4j `node_id`, carried here as `asset_id`.

    from historian import Measurement, write, history, latest, signals

Backends, selected automatically and reported by `info()`:

    timescale   TimescaleDB extension on the configured Postgres — hypertable,
                continuous aggregates, compression, retention. Production.
    postgres    plain Postgres — correct, no compression/retention, rollups
                computed per request.
    sqlite      local dev, via db/core.py.

`NXR_REQUIRE_TIMESCALE=1` makes the extension mandatory so a deploy cannot
silently land on a backend with no retention policy and fill its disk. This is
the same guard shape as NXR_REQUIRE_DB / _S3 / _REDIS, and for the same reason:
the fallbacks all WORK, which is exactly why nothing tells you about them.
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
    timescale_required,
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
    timescale_available,
    timescale_version,
)


class HistorianUnavailable(RuntimeError):
    """Raised by `require()` when TimescaleDB is mandatory and absent."""


def require() -> None:
    """Fail fast when the deployment declared Timescale mandatory but it is not
    there. Called from `server.main._preflight()`, so a misconfigured task
    crash-loops with a one-line reason in CloudWatch instead of quietly running
    a historian that never compresses and never expires anything."""
    if not timescale_required():
        return
    if backend() != "timescale":
        raise HistorianUnavailable(
            f"NXR_REQUIRE_TIMESCALE is set but the historian backend resolved to "
            f"'{backend()}'. Without the timescaledb extension there are no "
            f"continuous aggregates, no compression and no retention policy, so "
            f"telemetry would grow without bound and trend queries would scan raw "
            f"rows. Run `CREATE EXTENSION IF NOT EXISTS timescaledb;` on the "
            f"database, then `python -m tools.historian_provision`.")
    ok, detail = ping()
    if not ok:
        raise HistorianUnavailable(
            f"NXR_REQUIRE_TIMESCALE is set but the historian is not usable: "
            f"{detail}")


__all__ = [
    "BUCKETS", "DEFAULT_MAX_POINTS", "HARD_MAX_POINTS", "HistorianUnavailable",
    "Measurement", "QUALITY_BAD", "QUALITY_GOOD", "QUALITY_UNCERTAIN", "Series",
    "WriteResult", "backend", "choose_agg", "coerce", "drop_all", "ensure",
    "history", "info", "latest", "log_posture", "ping", "purge_tenant",
    "require", "reset_cache", "signals", "tenant_stats", "timescale_available",
    "timescale_required", "timescale_version", "write",
]
