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

TWO BACKENDS, ONE API
---------------------
    mysql     production: one plain table on the same RDS MySQL instance as the
              other stores. `db/core.py`'s pool and `translate()` keep working,
              so the deployment gains no second database. Rollups are computed on
              the fly from raw (date-bucketed GROUP BY) — correct, and cheaper to
              operate than a dedicated TSDB.
    sqlite    local dev, same SQL surface via db/core.py's translation.

TRADE-OFF, STATED PLAINLY (the same trap `db`/`storage`/`bus` document): a plain
table has no columnar compression and no automatic retention, so raw telemetry
grows without bound. That is acceptable for an MVP and for moderate ingest; a
high-volume deployment should add an app-level purge (see `retention_days`) or a
partitioned/columnar store later.

WHY THE ROLLUPS COMPUTE count/sum AND NOT avg
---------------------------------------------
An average cannot be re-averaged: `avg(avg(x))` weights each bucket equally
regardless of how many samples it held, which silently skews every chart drawn
from a coarser rollup. Reporting `count` and `sum` lets the API compute a correct
mean at any resolution and lets a client safely re-aggregate.
"""

from __future__ import annotations

import os

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

# Rollup resolutions, computed on the fly from raw (see core.py `_bucket_rows`).
BUCKETS: dict[str, str] = {
    "1m": "1 minute",
    "1h": "1 hour",
    "1d": "1 day",
}

# How long each tier is worth keeping. Informational on MySQL — there is no
# database-enforced retention here — but kept so an app-level purge cron can read
# a single, overridable policy rather than hard-coding one.
RETENTION_DAYS: dict[str, int | None] = {
    "raw": 30,
    "1m": 400,
    "1h": 1095,
    "1d": None,
}


def retention_days(tier: str) -> int | None:
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
#
# The primary key IS the idempotency contract: a replayed batch collides and is
# skipped with INSERT IGNORE, so an edge agent that reconnects after a network
# blip and resends its buffer cannot double-count. Doing it in the key rather than
# in application code means every writer gets it, including a bulk backfill and a
# connector nobody has written yet.
#
# Key columns are `CHARACTER SET ascii`: tag/asset/tenant identifiers are ASCII,
# and utf8mb4 (4 bytes/char) on a VARCHAR(512) signal would push the composite PK
# past InnoDB's 3072-byte index limit. `unit` stays utf8mb4 (it can hold '°C').
_MYSQL_CREATE_RAW = f"""
CREATE TABLE IF NOT EXISTS {RAW_TABLE} (
    tenant_id   VARCHAR(191) CHARACTER SET ascii NOT NULL,
    asset_id    VARCHAR(256) CHARACTER SET ascii NOT NULL,
    signal      VARCHAR(512) CHARACTER SET ascii NOT NULL,
    ts          DATETIME(6)  NOT NULL,
    value       DOUBLE,
    unit        VARCHAR(64)  NOT NULL DEFAULT '',
    quality     SMALLINT     NOT NULL DEFAULT {QUALITY_GOOD},
    source      VARCHAR(64)  CHARACTER SET ascii NOT NULL DEFAULT 'api',
    received_at DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (tenant_id, asset_id, signal, ts)
)
"""

# Serves "what signals does this tenant have, and when was each last seen" — the
# discovery query behind the tag-mapping UI and the staleness check.
_MYSQL_IDX_SIGNAL = (f"CREATE INDEX {RAW_TABLE}_tenant_signal_ts "
                     f"ON {RAW_TABLE} (tenant_id, signal, ts DESC)")

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


def reset_cache() -> None:
    """No-op retained for API compatibility (was the timescaledb probe cache)."""
    return None


def backend() -> str:
    """'mysql' | 'sqlite'."""
    return "mysql" if db.is_mysql() else "sqlite"


# ── Provisioning ────────────────────────────────────────────────────────────


def _create_index_mysql(conn, stmt: str) -> None:
    """MySQL has no `CREATE INDEX IF NOT EXISTS`; run it and ignore the
    "duplicate key name" error so provisioning stays idempotent."""
    try:
        conn.execute(stmt)
    except Exception as e:
        msg = str(e).lower()
        if "1061" not in msg and "duplicate key name" not in msg:
            raise


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
        if db.is_mysql():
            with db.connect("historian") as conn:
                conn.execute(_MYSQL_CREATE_RAW)
                _create_index_mysql(conn, _MYSQL_IDX_SIGNAL)
            report["created"].append(f"{RAW_TABLE} (mysql)")
            report["skipped"].append(
                "compression / automatic retention — not available on a plain "
                "MySQL table; rollups are computed per request from raw.")
        else:
            with db.connect("historian") as conn:
                conn.execute(_SQLITE_CREATE_RAW)
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {RAW_TABLE}_tenant_signal_ts "
                    f"ON {RAW_TABLE} (tenant_id, signal, ts DESC)")
            report["created"].append(f"{RAW_TABLE} (sqlite)")
            report["skipped"].append(
                "rollups computed per request — SQLite backend")
    except Exception as e:
        report["failures"].append(("create raw table", str(e)[:300]))
        if strict:
            raise
    return report


def drop_all() -> None:
    """Tear the historian down. Test-suite and local-reset only — it destroys
    every measurement, so it is never called from application code."""
    with db.connect("historian") as conn:
        conn.execute(f"DROP TABLE IF EXISTS {RAW_TABLE}")
    reset_cache()
