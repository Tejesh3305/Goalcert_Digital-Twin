"""
schema.py — the telemetry historian's DDL.

WHY A SEPARATE STORE AT ALL
---------------------------
Before this module the platform had nowhere to put a measurement. Neo4j holds the
CURRENT value of a property, which is the right job for a graph and the wrong one
for "give me 90 days of this sensor at 5-minute resolution". The relational store
holds twins, events, bundles and jobs — no measurements. And the live signal
values lived in a Python dict on the `LiveTwin` object, so they died with the
process. The consequence was that the platform could not draw a trend line, could
not replay an incident, could not backfill from a customer's historian, and had
no training data for anything statistical.

WHY TIMESCALEDB
---------------
It is a Postgres EXTENSION, so it runs on the RDS instance §7 of AWS_DEPLOYMENT.md
already provisions. `db/core.py`'s pool, `translate()` and advisory locks keep
working unchanged, and the deployment gains no new backup story, no new security
group, and no second database to keep alive. That property matters more than raw
benchmark numbers: a dedicated TSDB is faster in isolation and strictly worse to
operate, and an architecture review will ask about operations.

Four capabilities are the reason to want it, and all four are configured here:

  hypertable            transparent time partitioning, so a "last hour" query
                        touches one chunk instead of scanning every row
  continuous aggregates incrementally-maintained rollups, so a 90-day chart reads
                        ~2k pre-computed rows rather than aggregating millions
  compression           10-20x on telemetry (columnar + delta-encoded timestamps,
                        which are highly regular). At 5k signals @1Hz that is the
                        difference between ~4GB/day and ~50GB/day of RDS storage.
  retention policies    drop raw after N days, keep rollups for years, declared
                        once instead of maintained as cron jobs

THREE BACKENDS, ONE API
-----------------------
    timescale   full behaviour (production)
    postgres    correct, slower: real table + indexes, rollups computed on the
                fly with date_trunc, no compression or retention
    sqlite      local dev, same SQL surface via db/core.py's translation

The fallbacks are deliberate and they are also a trap, the same one `db`,
`storage` and `bus` already document: everything WORKS, it is just quietly
unsuitable — no compression means the disk fills, no retention means it never
stops. So `NXR_REQUIRE_TIMESCALE=1` makes the extension mandatory and the boot
fail loudly if it is absent, exactly like NXR_REQUIRE_DB/S3/REDIS.

WHY THE ROLLUPS STORE count/sum AND NOT avg
-------------------------------------------
An average cannot be re-averaged: `avg(avg(x))` weights each bucket equally
regardless of how many samples it held, which silently skews every chart drawn
from a coarser rollup. Storing `count` and `sum_value` lets the API compute a
correct mean at any resolution and lets a client safely re-aggregate. It also
keeps the door open to hierarchical aggregates (1h built from 1m rather than from
raw) without having to re-derive history.
"""

from __future__ import annotations

import os
from typing import Optional

import db

# ── Quality codes ───────────────────────────────────────────────────────────
# The classic OPC quality byte, which OPC-UA, every historian and every SCADA
# system already speak. It is on the raw table because a sensor reporting 0 °C
# with a broken wire is NOT the same fact as a sensor reporting 0 °C, and a model
# that cannot tell them apart will confidently learn from garbage. This column
# cannot be added later without reprocessing all of history, which is why it is
# here on day one rather than "when we need it".
QUALITY_BAD = 0
QUALITY_UNCERTAIN = 64
QUALITY_GOOD = 192

RAW_TABLE = "measurements"

# Rollup resolutions. Each is built directly from raw rather than from the tier
# below it: non-hierarchical costs more storage and is unambiguously correct,
# and correctness wins until storage is measured rather than guessed.
BUCKETS: dict[str, str] = {
    "1m": "1 minute",
    "1h": "1 hour",
    "1d": "1 day",
}

# How long each tier is kept. Raw is the expensive one and the least useful after
# an incident is closed; the daily rollup is cheap enough to keep indefinitely
# (None = never drop), which is what makes year-over-year comparison possible.
RETENTION_DAYS: dict[str, Optional[int]] = {
    "raw": 30,
    "1m": 400,
    "1h": 1095,
    "1d": None,
}

# Chunks older than this are compressed. It must be comfortably longer than the
# window that still receives writes: compressed chunks accept inserts on modern
# TimescaleDB but pay for it, and edge store-and-forward legitimately replays
# data hours late.
COMPRESS_AFTER = "7 days"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def retention_days(tier: str) -> Optional[int]:
    """Retention for one tier, overridable per deployment. A customer with a
    regulatory retention floor sets these rather than editing code."""
    default = RETENTION_DAYS.get(tier)
    raw = os.environ.get(f"NXR_HISTORIAN_RETENTION_{tier.upper()}")
    if raw is None:
        return default
    raw = raw.strip().lower()
    if raw in ("", "none", "never", "0", "forever"):
        return None
    try:
        return max(1, int(raw))
    except ValueError:
        return default


# ── Raw table ───────────────────────────────────────────────────────────────

_CREATE_RAW = f"""
CREATE TABLE IF NOT EXISTS {RAW_TABLE} (
    tenant_id   TEXT             NOT NULL,
    asset_id    TEXT             NOT NULL,
    signal      TEXT             NOT NULL,
    ts          TIMESTAMPTZ      NOT NULL,
    value       DOUBLE PRECISION,
    unit        TEXT             NOT NULL DEFAULT '',
    quality     SMALLINT         NOT NULL DEFAULT {QUALITY_GOOD},
    source      TEXT             NOT NULL DEFAULT 'api',
    received_at TIMESTAMPTZ      NOT NULL DEFAULT now()
)
"""

# The primary key IS the idempotency contract: a replayed batch collides and is
# skipped with ON CONFLICT DO NOTHING, so an edge agent that reconnects after a
# network blip and resends its buffer cannot double-count. Doing it in the key
# rather than in application code means every writer gets it, including a bulk
# backfill and a connector nobody has written yet.
#
# On a hypertable the time column must be part of any unique index, hence ts
# last. Order matters for read performance too: tenant → asset → signal → time
# matches how every query filters, so a range scan walks one contiguous run.
_RAW_PK = f"""
CREATE UNIQUE INDEX IF NOT EXISTS {RAW_TABLE}_pk
    ON {RAW_TABLE} (tenant_id, asset_id, signal, ts DESC)
"""

# Serves "what signals does this tenant have, and when was each last seen" —
# the discovery query behind the tag-mapping UI and the staleness check.
_RAW_IDX_SIGNAL = f"""
CREATE INDEX IF NOT EXISTS {RAW_TABLE}_tenant_signal_ts
    ON {RAW_TABLE} (tenant_id, signal, ts DESC)
"""

_SQLITE_CREATE_RAW = f"""
CREATE TABLE IF NOT EXISTS {RAW_TABLE} (
    tenant_id   TEXT    NOT NULL,
    asset_id    TEXT    NOT NULL,
    signal      TEXT    NOT NULL,
    ts          TEXT    NOT NULL,
    value       REAL,
    unit        TEXT    NOT NULL DEFAULT '',
    quality     INTEGER NOT NULL DEFAULT {QUALITY_GOOD},
    source      TEXT    NOT NULL DEFAULT 'api',
    received_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (tenant_id, asset_id, signal, ts)
)
"""


def _cagg_sql(name: str, bucket: str) -> str:
    """A continuous aggregate over raw.

    `count`/`sum_value` rather than `avg` — see the module docstring. BAD-quality
    samples are excluded from the statistics but counted separately, so a chart
    drawn from a rollup shows the real measured signal while the data-quality
    view can still see how much of it was untrustworthy. Collapsing those two
    into one number is how a dashboard ends up quietly averaging in a dead
    sensor's zeros.
    """
    return f"""
CREATE MATERIALIZED VIEW IF NOT EXISTS {RAW_TABLE}_{name}
WITH (timescaledb.continuous) AS
SELECT
    tenant_id,
    asset_id,
    signal,
    time_bucket(INTERVAL '{bucket}', ts)                    AS bucket,
    count(*) FILTER (WHERE quality >= {QUALITY_UNCERTAIN}
                       AND value IS NOT NULL)               AS count,
    sum(value) FILTER (WHERE quality >= {QUALITY_UNCERTAIN}) AS sum_value,
    min(value) FILTER (WHERE quality >= {QUALITY_UNCERTAIN}) AS min_value,
    max(value) FILTER (WHERE quality >= {QUALITY_UNCERTAIN}) AS max_value,
    first(value, ts) FILTER (WHERE quality >= {QUALITY_UNCERTAIN}) AS first_value,
    last(value, ts)  FILTER (WHERE quality >= {QUALITY_UNCERTAIN}) AS last_value,
    count(*) FILTER (WHERE quality < {QUALITY_UNCERTAIN})    AS bad_count,
    max(unit)                                               AS unit
FROM {RAW_TABLE}
GROUP BY tenant_id, asset_id, signal, bucket
WITH NO DATA
"""


def _refresh_policy_sql(name: str, bucket: str) -> str:
    """Keep a rollup fresh.

    `start_offset` bounds how far back a refresh looks: without it every run
    rescans all history, which turns into the historian's dominant cost as the
    table grows. It must still be wide enough to absorb late-arriving data — an
    edge agent replaying a buffer — or those samples land in a bucket that is
    never recomputed and silently never appear in a chart. `end_offset` keeps the
    refresh off the newest bucket, which is still being written.
    """
    start = {"1m": "3 hours", "1h": "3 days", "1d": "30 days"}[name]
    end = {"1m": "1 minute", "1h": "1 hour", "1d": "1 hour"}[name]
    every = {"1m": "1 minute", "1h": "10 minutes", "1d": "1 hour"}[name]
    return f"""
SELECT add_continuous_aggregate_policy('{RAW_TABLE}_{name}',
    start_offset => INTERVAL '{start}',
    end_offset   => INTERVAL '{end}',
    schedule_interval => INTERVAL '{every}',
    if_not_exists => TRUE)
"""


# ── Capability detection ────────────────────────────────────────────────────

_TIMESCALE_CACHE: Optional[bool] = None


def timescale_available(force: bool = False) -> bool:
    """Is the timescaledb extension installed and usable?

    Cached: this is consulted on every query to pick a rollup source, and a
    catalogue round-trip per request would be a silly cost. `force=True`
    re-probes, which the provisioning tool needs after CREATE EXTENSION.
    """
    global _TIMESCALE_CACHE
    if _TIMESCALE_CACHE is not None and not force:
        return _TIMESCALE_CACHE
    if not db.is_postgres():
        _TIMESCALE_CACHE = False
        return False
    try:
        with db.connect("historian") as conn:
            row = conn.execute(
                "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'"
            ).fetchone()
        _TIMESCALE_CACHE = row is not None
    except Exception:
        _TIMESCALE_CACHE = False
    return _TIMESCALE_CACHE


def timescale_version() -> str:
    if not db.is_postgres():
        return ""
    try:
        with db.connect("historian") as conn:
            row = conn.execute(
                "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'"
            ).fetchone()
        return (row["extversion"] if row else "") or ""
    except Exception:
        return ""


def reset_cache() -> None:
    global _TIMESCALE_CACHE
    _TIMESCALE_CACHE = None


def backend() -> str:
    """'timescale' | 'postgres' | 'sqlite'."""
    if not db.is_postgres():
        return "sqlite"
    return "timescale" if timescale_available() else "postgres"


# ── Provisioning ────────────────────────────────────────────────────────────


def _autocommit_exec(statements: list[str]) -> list[tuple[str, str]]:
    """Run DDL that CANNOT be inside a transaction block.

    TimescaleDB refuses `CREATE MATERIALIZED VIEW ... WITH (timescaledb.continuous)`
    and the policy helpers inside a transaction, and `db.Conn` always opens one.
    So this reaches past the wrapper for the connection's autocommit flag —
    narrowly, only for these statements, and it returns per-statement failures
    instead of raising so one unsupported policy on an older Timescale cannot
    abort the whole provisioning run.
    """
    failures: list[tuple[str, str]] = []
    conn = db.connect("historian")
    raw = conn._raw                       # noqa: SLF001 - see docstring
    previous = getattr(raw, "autocommit", None)
    try:
        raw.autocommit = True
        for sql in statements:
            cur = raw.cursor()
            try:
                cur.execute(sql)
            except Exception as e:
                failures.append((sql.strip().splitlines()[0][:80], str(e)[:200]))
            finally:
                try:
                    cur.close()
                except Exception:
                    pass
    finally:
        try:
            if previous is not None:
                raw.autocommit = previous
        except Exception:
            pass
        conn.close()
    return failures


def ensure(*, strict: bool = False) -> dict:
    """Create the historian schema. Idempotent — safe to run on every boot and
    from the provisioning tool.

    Returns a report rather than printing, so `tools/historian_provision.py` can
    show it and the health endpoint can summarise it. With `strict=True` a
    failure raises, which is what the provisioning tool wants and a best-effort
    boot does not.
    """
    report: dict = {"backend": backend(), "created": [], "skipped": [],
                    "failures": []}

    try:
        if db.is_postgres():
            with db.connect("historian") as conn:
                conn.execute(_CREATE_RAW)
                conn.execute(_RAW_PK)
                conn.execute(_RAW_IDX_SIGNAL)
            report["created"].append(RAW_TABLE)
        else:
            with db.connect("historian") as conn:
                conn.execute(_SQLITE_CREATE_RAW)
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {RAW_TABLE}_tenant_signal_ts "
                    f"ON {RAW_TABLE} (tenant_id, signal, ts DESC)")
            report["created"].append(f"{RAW_TABLE} (sqlite)")
            report["skipped"].append(
                "hypertable/rollups/compression/retention — SQLite backend")
            return report
    except Exception as e:
        report["failures"].append(("create raw table", str(e)[:300]))
        if strict:
            raise
        return report

    if not timescale_available(force=True):
        # Plain Postgres: the table and indexes above are the whole story.
        # Rollups are computed on the fly (see core.py), which is correct and
        # slower. Naming that here keeps it from looking like it worked fully.
        report["skipped"].append(
            "hypertable/rollups/compression/retention — timescaledb extension "
            "not installed. Run CREATE EXTENSION timescaledb, then re-run.")
        return report

    # Hypertable. `migrate_data` converts an existing plain table in place, which
    # is what makes this safe to run on a deployment that already collected data
    # on plain Postgres.
    ddl: list[str] = [
        f"""SELECT create_hypertable('{RAW_TABLE}', 'ts',
                chunk_time_interval => INTERVAL '1 day',
                migrate_data => TRUE,
                if_not_exists => TRUE)""",
    ]
    for name, bucket in BUCKETS.items():
        ddl.append(_cagg_sql(name, bucket))
        ddl.append(_refresh_policy_sql(name, bucket))

    # Compression. segmentby groups the columns a query filters on so the
    # compressed chunk can skip whole segments; orderby matches the natural write
    # order, which is what makes timestamp delta-encoding effective.
    ddl.append(f"""
        ALTER TABLE {RAW_TABLE} SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'tenant_id, asset_id, signal',
            timescaledb.compress_orderby   = 'ts DESC')
    """)
    ddl.append(f"""SELECT add_compression_policy('{RAW_TABLE}',
                    INTERVAL '{COMPRESS_AFTER}', if_not_exists => TRUE)""")

    raw_keep = retention_days("raw")
    if raw_keep:
        ddl.append(f"""SELECT add_retention_policy('{RAW_TABLE}',
                        INTERVAL '{raw_keep} days', if_not_exists => TRUE)""")
    for name in BUCKETS:
        keep = retention_days(name)
        if keep:
            ddl.append(f"""SELECT add_retention_policy('{RAW_TABLE}_{name}',
                            INTERVAL '{keep} days', if_not_exists => TRUE)""")

    failures = _autocommit_exec(ddl)

    # `if_not_exists => TRUE` still errors on a policy that exists with different
    # parameters, and re-running ensure() must not look broken because of it.
    benign = ("already exists", "already has", "duplicate")
    for stmt, err in failures:
        if any(b in err.lower() for b in benign):
            report["skipped"].append(f"{stmt} — {err[:80]}")
        else:
            report["failures"].append((stmt, err))

    report["created"].extend(
        ["hypertable"] + [f"{RAW_TABLE}_{n}" for n in BUCKETS]
        + ["compression", "retention"])
    report["timescale_version"] = timescale_version()

    if strict and report["failures"]:
        raise RuntimeError(f"historian provisioning failed: {report['failures']}")
    return report


def drop_all() -> None:
    """Tear the historian down. Test-suite and local-reset only — it destroys
    every measurement, so it is never called from application code."""
    if db.is_postgres():
        stmts = [f"DROP MATERIALIZED VIEW IF EXISTS {RAW_TABLE}_{n} CASCADE"
                 for n in BUCKETS]
        stmts.append(f"DROP TABLE IF EXISTS {RAW_TABLE} CASCADE")
        _autocommit_exec(stmts)
    else:
        with db.connect("historian") as conn:
            conn.execute(f"DROP TABLE IF EXISTS {RAW_TABLE}")
    reset_cache()
