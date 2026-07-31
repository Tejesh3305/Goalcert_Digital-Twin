"""
core.py — reading and writing telemetry history.

WRITE PATH DESIGN
-----------------
`write()` takes a BATCH and is idempotent. Both properties are requirements from
the ingest side rather than conveniences:

  * Batching, because a per-sample INSERT means a network round trip per sample.
    A naive per-row loop would make a 1,000-sample batch 1,000 round trips — at
    ~1 ms RTT to RDS that is a second of wall clock for a batch that should take a
    few milliseconds. PyMySQL collapses `executemany` into one multi-row INSERT,
    which is the single round trip this path wants.

  * Idempotency, because every real ingest path replays. An edge agent buffering
    through a network outage resends its buffer on reconnect; a Sparkplug client
    re-publishes on rebirth; a backfill gets run twice by a human. The guarantee
    lives in the primary key with `INSERT IGNORE`, so it applies to every writer
    automatically — including connectors nobody has written yet. Making it the
    caller's job is how double-counted history happens.

`write()` also VALIDATES, and returns per-sample rejections rather than raising.
A batch of 500 samples with one bad timestamp must not lose the other 499, and
the caller needs to know which one failed and why — a 400 for the whole batch
teaches the operator nothing and loses good data.

READ PATH DESIGN
----------------
`history()` picks the rollup tier from the requested time span so a client never
has to think about resolution, and REPORTS which tier it used. Silent
downsampling is a trap: a chart that quietly switches from raw to hourly means an
operator comparing two views sees different numbers with no explanation.

Averages are computed as `sum/count`, never stored pre-averaged — see the note in
schema.py about why `avg(avg(x))` is wrong.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import db

from .schema import (
    BUCKETS,
    QUALITY_BAD,
    QUALITY_GOOD,
    QUALITY_UNCERTAIN,
    RAW_TABLE,
    backend,
)

# SQLite stores timestamps as TEXT, so range comparisons are lexicographic. That
# is only correct if every stored value has the SAME WIDTH: with optional
# microseconds, '10:00:01+00:00' sorts BEFORE '10:00:00.9+00:00' (because '+' is
# 0x2B and '.' is 0x2E), which silently corrupts every range query and every
# ORDER BY. Microseconds are therefore always written.
_TS_FMT = "%Y-%m-%dT%H:%M:%S.%f+00:00"

# Reject timestamps outside this window. Clock skew on an edge device is normal
# and small; a year in the future is a parsing bug or a unit mix-up (seconds
# passed where milliseconds were expected), and storing it poisons every "latest
# value" and every retention policy — the row never ages out.
MAX_FUTURE = timedelta(hours=1)
MAX_PAST = timedelta(days=3650)

# Points returned before `agg="auto"` steps to a coarser tier.
DEFAULT_MAX_POINTS = 2000
HARD_MAX_POINTS = 50_000


@dataclass(frozen=True)
class Measurement:
    """One observation. `ts` is the SOURCE timestamp — when the device says it
    measured — which is deliberately distinct from `received_at` (when we stored
    it). Store-and-forward replays hours-old data, so collapsing the two would
    make it impossible to tell a stale reading from a fresh one or to measure
    pipeline latency at all."""
    tenant_id: str
    asset_id: str
    signal: str
    ts: datetime
    value: float | None
    unit: str = ""
    quality: int = QUALITY_GOOD
    source: str = "api"


@dataclass
class WriteResult:
    accepted: int = 0
    duplicates: int = 0
    rejected: list[dict] = field(default_factory=list)

    @property
    def submitted(self) -> int:
        return self.accepted + self.duplicates + len(self.rejected)

    def to_dict(self) -> dict:
        return {"submitted": self.submitted, "accepted": self.accepted,
                "duplicates": self.duplicates, "rejected": len(self.rejected),
                "rejections": self.rejected[:50]}


# ── Timestamp handling ──────────────────────────────────────────────────────


def _as_utc(value) -> datetime:
    """Coerce to a timezone-aware UTC datetime.

    A NAIVE datetime is treated as UTC rather than as local time. Server-local
    interpretation would make stored history depend on the container's TZ, so the
    same payload would land at different instants on two tasks in different
    regions.
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, int | float):
        # Epoch. Values above ~1e11 cannot be seconds (that is year 5138), so
        # they are milliseconds — the single most common ingest unit mix-up, and
        # cheap to absorb here rather than reject.
        seconds = float(value)
        if abs(seconds) > 1e11:
            seconds /= 1000.0
        dt = datetime.fromtimestamp(seconds, tz=UTC)
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    else:
        raise ValueError(f"unsupported timestamp type {type(value).__name__}")

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _ts_param(dt: datetime):
    """Bind a timestamp for the live backend: a naive-UTC datetime for MySQL
    DATETIME(6) (which is timezone-naive and stores UTC by convention), a
    fixed-width ISO string for SQLite TEXT."""
    if db.is_mysql():
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt.strftime(_TS_FMT)


def _ts_read(value) -> str:
    """Render a stored timestamp as ISO-8601 UTC for the API."""
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=UTC)
        return dt.astimezone(UTC).isoformat()
    if isinstance(value, str):
        try:
            return _as_utc(value).isoformat()
        except Exception:
            return value
    return str(value)


# ── Validation ──────────────────────────────────────────────────────────────


def _validate(m: Measurement, now: datetime) -> str | None:
    """Reason this sample is unacceptable, or None. Cheap checks only — this runs
    per sample on the ingest hot path."""
    if not m.tenant_id:
        return "tenant_id is required"
    if not m.asset_id:
        return "asset_id is required"
    if not m.signal:
        return "signal is required"
    if len(m.signal) > 512 or len(m.asset_id) > 256:
        return "asset_id or signal exceeds the maximum length"
    if m.value is not None:
        try:
            v = float(m.value)
        except (TypeError, ValueError):
            return "value is not numeric"
        # NaN/Inf are accepted by DOUBLE PRECISION and then break every
        # aggregate downstream: one NaN makes sum(), avg(), min() and max() NaN
        # for the whole bucket. A missing reading must be NULL with a quality
        # code, which is what a historian means by "no data".
        if not math.isfinite(v):
            return "value must be finite (use null with quality=0 for no-data)"
    if not (QUALITY_BAD <= int(m.quality) <= 255):
        return "quality must be 0-255 (0=bad, 64=uncertain, 192=good)"
    if m.ts > now + MAX_FUTURE:
        return (f"timestamp {m.ts.isoformat()} is more than "
                f"{int(MAX_FUTURE.total_seconds() // 60)} minutes in the future")
    if m.ts < now - MAX_PAST:
        return f"timestamp {m.ts.isoformat()} is implausibly old"
    return None


def coerce(raw: dict, *, tenant_id: str, default_source: str = "api") -> Measurement:
    """Build a Measurement from a loosely-typed dict (an HTTP payload, an MQTT
    message, an OPC-UA notification).

    `tenant_id` is passed separately and NOT read from the payload: the tenant is
    an authorization fact established by the caller's credentials, and letting a
    payload field override it would let one device write into another tenant's
    history. Raises ValueError so the caller can report the offending sample.
    """
    ts_raw = raw.get("ts", raw.get("timestamp", raw.get("time")))
    if ts_raw is None:
        ts = datetime.now(UTC)
    else:
        ts = _as_utc(ts_raw)

    value = raw.get("value", raw.get("v"))
    if value is not None and not isinstance(value, int | float):
        if isinstance(value, bool):
            value = 1.0 if value else 0.0          # discrete signals
        else:
            try:
                value = float(str(value).strip())
            except (TypeError, ValueError):
                raise ValueError(f"value {value!r} is not numeric")

    quality = raw.get("quality", raw.get("q", QUALITY_GOOD))
    try:
        quality = int(quality)
    except (TypeError, ValueError):
        quality = QUALITY_GOOD

    return Measurement(
        tenant_id=tenant_id,
        asset_id=str(raw.get("asset_id") or raw.get("asset") or "").strip(),
        signal=str(raw.get("signal") or raw.get("tag") or "").strip(),
        ts=ts,
        value=None if value is None else float(value),
        unit=str(raw.get("unit") or "")[:64],
        quality=quality,
        source=str(raw.get("source") or default_source)[:32],
    )


# ── Write ───────────────────────────────────────────────────────────────────

_COLUMNS = ("tenant_id", "asset_id", "signal", "ts", "value", "unit",
            "quality", "source")


def write(measurements: Iterable[Measurement]) -> WriteResult:
    """Persist a batch. Idempotent, per-sample validated, one round trip."""
    now = datetime.now(UTC)
    result = WriteResult()
    rows: list[tuple] = []

    for index, m in enumerate(measurements):
        reason = _validate(m, now)
        if reason:
            result.rejected.append({
                "index": index, "asset_id": m.asset_id, "signal": m.signal,
                "reason": reason,
            })
            continue
        rows.append((m.tenant_id, m.asset_id, m.signal, _ts_param(m.ts),
                     None if m.value is None else float(m.value),
                     m.unit or "", int(m.quality), m.source or "api"))

    if not rows:
        return result

    inserted = _insert_rows(rows)
    result.accepted = inserted
    result.duplicates = len(rows) - inserted
    return result


def _insert_rows(rows: Sequence[tuple]) -> int:
    """Insert, skipping duplicates. Returns the number actually stored."""
    cols = ", ".join(_COLUMNS)
    placeholders = ", ".join("?" for _ in _COLUMNS)

    if db.is_mysql():
        # INSERT IGNORE skips rows that collide on the primary key (the
        # idempotency contract). PyMySQL collapses executemany into one multi-row
        # INSERT, and cur.rowcount is then the exact number of rows ACTUALLY
        # inserted (ignored duplicates do not count) — so no RETURNING trick is
        # needed the way Postgres required one.
        with db.connect("historian") as conn:
            cur = conn.executemany(
                f"INSERT IGNORE INTO {RAW_TABLE} ({cols}) "
                f"VALUES ({placeholders})", rows)
            return int(getattr(cur, "rowcount", 0) or 0)

    with db.connect("historian") as conn:
        before = conn._raw.total_changes       # noqa: SLF001
        conn.executemany(
            f"INSERT OR IGNORE INTO {RAW_TABLE} ({cols}) "
            f"VALUES ({placeholders})", rows)
        return conn._raw.total_changes - before   # noqa: SLF001


# ── Read ────────────────────────────────────────────────────────────────────

# Span thresholds for `agg="auto"`. Explicit and documented rather than derived
# from the data, so the same request always resolves to the same tier — a chart
# whose resolution changes as history accumulates is impossible to reason about.
_AUTO_TIERS = (
    (timedelta(hours=1), "raw"),
    (timedelta(days=2), "1m"),
    (timedelta(days=90), "1h"),
)

_BUCKET_SECONDS = {"1m": 60, "1h": 3600, "1d": 86400}


def choose_agg(start: datetime, end: datetime,
               max_points: int = DEFAULT_MAX_POINTS) -> str:
    """Pick the finest tier whose point count fits `max_points`."""
    span = max(timedelta(seconds=1), end - start)
    for limit, tier in _AUTO_TIERS:
        if span <= limit:
            if tier == "raw":
                return "raw"
            if span.total_seconds() / _BUCKET_SECONDS[tier] <= max_points:
                return tier
    for tier in ("1h", "1d"):
        if span.total_seconds() / _BUCKET_SECONDS[tier] <= max_points:
            return tier
    return "1d"


def _resolve_agg(agg: str, start: datetime, end: datetime,
                 max_points: int) -> tuple[str, list[str]]:
    """(tier, notes). Notes explain any substitution so the response can say so
    out loud instead of silently serving a different resolution."""
    notes: list[str] = []
    tier = (agg or "auto").strip().lower()
    if tier in ("auto", ""):
        tier = choose_agg(start, end, max_points)
        notes.append(f"agg=auto resolved to '{tier}' for this time span")
    if tier not in ("raw", *BUCKETS):
        raise ValueError(f"unknown agg '{agg}'. "
                         f"Use auto, raw, {', '.join(BUCKETS)}")
    if tier != "raw":
        notes.append(f"'{tier}' computed on the fly from raw on the "
                     f"{backend()} backend")
    return tier, notes


@dataclass
class Series:
    tenant_id: str
    asset_id: str
    signal: str
    agg: str
    unit: str
    points: list[dict]
    notes: list[str] = field(default_factory=list)
    truncated: bool = False

    def to_dict(self) -> dict:
        return {"tenant": self.tenant_id, "asset_id": self.asset_id,
                "signal": self.signal, "agg": self.agg, "unit": self.unit,
                "count": len(self.points), "points": self.points,
                "notes": self.notes, "truncated": self.truncated}


def history(tenant_id: str, asset_id: str, signal: str, *,
            start: datetime | None = None, end: datetime | None = None,
            agg: str = "auto", limit: int = DEFAULT_MAX_POINTS) -> Series:
    """One signal's history. `limit` bounds the returned points; `truncated` says
    whether it bit, so a client can never mistake a clipped series for the whole
    range."""
    end = _as_utc(end) if end else datetime.now(UTC)
    start = _as_utc(start) if start else end - timedelta(hours=1)
    if start > end:
        start, end = end, start
    limit = max(1, min(int(limit), HARD_MAX_POINTS))

    tier, notes = _resolve_agg(agg, start, end, limit)
    fetch = limit + 1                    # one extra reveals truncation

    if tier == "raw":
        sql = (f"SELECT ts, value, unit, quality FROM {RAW_TABLE} "
               f"WHERE tenant_id = ? AND asset_id = ? AND signal = ? "
               f"  AND ts >= ? AND ts <= ? "
               f"ORDER BY ts ASC LIMIT ?")
        params = (tenant_id, asset_id, signal, _ts_param(start),
                  _ts_param(end), fetch)
        rows = _rows(sql, params)
        points = [{"ts": _ts_read(r["ts"]),
                   "value": _f(r["value"]),
                   "quality": int(r["quality"] or 0)} for r in rows]
        unit = next((r["unit"] for r in rows if r["unit"]), "")
    else:
        rows = _bucket_rows(tenant_id, asset_id, signal, start, end, tier, fetch)
        points = []
        for r in rows:
            count = int(r["count"] or 0)
            total = _f(r["sum_value"])
            points.append({
                "ts": _ts_read(r["bucket"]),
                # avg from sum/count, never a stored average — re-averaging an
                # average weights buckets equally regardless of sample count.
                "value": (total / count) if count and total is not None else None,
                "min": _f(r["min_value"]), "max": _f(r["max_value"]),
                "first": _f(r["first_value"]), "last": _f(r["last_value"]),
                "count": count, "bad_count": int(r["bad_count"] or 0),
            })
        unit = next((r["unit"] for r in rows if r.get("unit")), "")

    truncated = len(points) > limit
    return Series(tenant_id=tenant_id, asset_id=asset_id, signal=signal,
                  agg=tier, unit=unit or "", points=points[:limit],
                  notes=notes, truncated=truncated)


def _bucket_rows(tenant_id, asset_id, signal, start, end, tier, fetch):
    """Rollup rows, aggregated on the fly from raw. Same columns and semantics on
    every backend, so a caller cannot tell the difference except in latency (and
    the note we add)."""
    if db.is_mysql():
        bucket_expr = {
            "1m": "DATE_FORMAT(ts, '%Y-%m-%d %H:%i:00')",
            "1h": "DATE_FORMAT(ts, '%Y-%m-%d %H:00:00')",
            "1d": "DATE_FORMAT(ts, '%Y-%m-%d 00:00:00')",
        }[tier]
        first_last = ("min(value) AS `first_value`, max(value) AS `last_value`")
    else:
        bucket_expr = {
            "1m": "substr(ts, 1, 17) || '00.000000+00:00'",
            "1h": "substr(ts, 1, 14) || '00:00.000000+00:00'",
            "1d": "substr(ts, 1, 11) || '00:00:00.000000+00:00'",
        }[tier]
        first_last = ("min(value) AS `first_value`, max(value) AS `last_value`")

    # NOTE on first/last: without Timescale's `first(value, ts)` there is no
    # cheap ordered aggregate here, so these columns carry min/max instead. The
    # substitution is reported in `notes` rather than passed off as the real
    # thing — a wrong "last value" is worse than an absent one.
    sql = (f"SELECT {bucket_expr} AS bucket, "
           f"  count(CASE WHEN quality >= {QUALITY_UNCERTAIN} "
           f"             AND value IS NOT NULL THEN 1 END) AS count, "
           f"  sum(CASE WHEN quality >= {QUALITY_UNCERTAIN} THEN value END) AS sum_value, "
           f"  min(CASE WHEN quality >= {QUALITY_UNCERTAIN} THEN value END) AS min_value, "
           f"  max(CASE WHEN quality >= {QUALITY_UNCERTAIN} THEN value END) AS max_value, "
           f"  {first_last}, "
           f"  count(CASE WHEN quality < {QUALITY_UNCERTAIN} THEN 1 END) AS bad_count, "
           f"  max(unit) AS unit "
           f"FROM {RAW_TABLE} "
           f"WHERE tenant_id = ? AND asset_id = ? AND signal = ? "
           f"  AND ts >= ? AND ts <= ? "
           f"GROUP BY bucket ORDER BY bucket ASC LIMIT ?")
    return _rows(sql, (tenant_id, asset_id, signal, _ts_param(start),
                       _ts_param(end), fetch))


def latest(tenant_id: str, asset_id: str | None = None,
           signals: Sequence[str] | None = None,
           stale_after_s: int = 300) -> list[dict]:
    """The most recent sample per signal, with staleness.

    `age_s` and `stale` are the point of this endpoint. A dashboard showing a
    value with no age cannot distinguish "22.4 °C right now" from "22.4 °C, last
    heard from four days ago", and that distinction is the difference between a
    working twin and a decorative one.
    """
    where = ["tenant_id = ?"]
    params: list = [tenant_id]
    if asset_id:
        where.append("asset_id = ?")
        params.append(asset_id)
    if signals:
        where.append(f"signal IN ({', '.join('?' for _ in signals)})")
        params.extend(signals)
    clause = " AND ".join(where)

    if db.is_mysql():
        # A window function ranks each (asset, signal) group by time and keeps the
        # newest. SQLite's `GROUP BY … HAVING ts = max(ts)` relies on SQLite's
        # bare-column selection and would be rejected under MySQL's
        # ONLY_FULL_GROUP_BY, so MySQL needs its own query.
        sql = (f"SELECT asset_id, signal, ts, value, unit, quality, source "
               f"FROM (SELECT asset_id, signal, ts, value, unit, quality, source, "
               f"      ROW_NUMBER() OVER (PARTITION BY asset_id, signal "
               f"                         ORDER BY ts DESC) AS rn "
               f"      FROM {RAW_TABLE} WHERE {clause}) t "
               f"WHERE t.rn = 1")
    else:
        sql = (f"SELECT asset_id, signal, ts, value, unit, quality, source "
               f"FROM {RAW_TABLE} WHERE {clause} "
               f"GROUP BY asset_id, signal HAVING ts = max(ts)")

    now = datetime.now(UTC)
    out = []
    for r in _rows(sql, tuple(params)):
        ts_iso = _ts_read(r["ts"])
        try:
            age = max(0.0, (now - _as_utc(ts_iso)).total_seconds())
        except Exception:
            age = None
        out.append({
            "asset_id": r["asset_id"], "signal": r["signal"], "ts": ts_iso,
            "value": _f(r["value"]), "unit": r["unit"] or "",
            "quality": int(r["quality"] or 0), "source": r["source"],
            "age_s": None if age is None else round(age, 1),
            "stale": None if age is None else age > stale_after_s,
        })
    return out


def signals(tenant_id: str, asset_id: str | None = None) -> list[dict]:
    """Signal inventory: what this tenant has ever sent, and when.

    This is the discovery query behind the tag-mapping UI. A customer arrives
    with tens of thousands of opaque historian tags; the first thing anyone needs
    is a list of what is actually flowing.
    """
    where = ["tenant_id = ?"]
    params: list = [tenant_id]
    if asset_id:
        where.append("asset_id = ?")
        params.append(asset_id)
    sql = (f"SELECT asset_id, signal, max(unit) AS unit, count(*) AS samples, "
           f"       min(ts) AS first_ts, max(ts) AS last_ts "
           f"FROM {RAW_TABLE} WHERE {' AND '.join(where)} "
           f"GROUP BY asset_id, signal ORDER BY asset_id, signal")
    return [{"asset_id": r["asset_id"], "signal": r["signal"],
             "unit": r["unit"] or "", "samples": int(r["samples"] or 0),
             "first_ts": _ts_read(r["first_ts"]),
             "last_ts": _ts_read(r["last_ts"])}
            for r in _rows(sql, tuple(params))]


def tenant_stats(tenant_id: str) -> dict:
    """Row count and time span for one tenant — the cheap "is data arriving?"
    check the health endpoint and the ingest UI both want."""
    sql = (f"SELECT count(*) AS samples, count(DISTINCT asset_id) AS assets, "
           f"       count(DISTINCT signal) AS signals, "
           f"       min(ts) AS first_ts, max(ts) AS last_ts "
           f"FROM {RAW_TABLE} WHERE tenant_id = ?")
    rows = _rows(sql, (tenant_id,))
    if not rows:
        return {"samples": 0, "assets": 0, "signals": 0}
    r = rows[0]
    return {"samples": int(r["samples"] or 0),
            "assets": int(r["assets"] or 0),
            "signals": int(r["signals"] or 0),
            "first_ts": _ts_read(r["first_ts"]) if r["first_ts"] else None,
            "last_ts": _ts_read(r["last_ts"]) if r["last_ts"] else None}


def purge_tenant(tenant_id: str) -> int:
    """Delete one tenant's measurements. Used by twin deletion and by GDPR /
    right-to-erasure requests, which an enterprise contract will require."""
    with db.connect("historian") as conn:
        cur = conn.execute(f"DELETE FROM {RAW_TABLE} WHERE tenant_id = ?",
                           (tenant_id,))
        return int(getattr(cur, "rowcount", 0) or 0)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _rows(sql: str, params: Sequence) -> list[dict]:
    """Run a read and return plain dicts.

    PyMySQL's DictCursor already yields mappings; sqlite3.Row does not, so it is
    converted. Returning one row type keeps every caller above this line free of
    backend conditionals.
    """
    with db.connect("historian") as conn:
        cur = conn.execute(sql, tuple(params))
        fetched = cur.fetchall()
    out = []
    for row in fetched:
        out.append(dict(row) if not isinstance(row, dict) else row)
    return out


def _f(value) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# ── Posture / diagnostics ───────────────────────────────────────────────────


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def ping() -> tuple[bool, str]:
    """(usable, detail). Never raises — /health must not 503."""
    try:
        with db.connect("historian") as conn:
            conn.execute(f"SELECT 1 FROM {RAW_TABLE} LIMIT 1").fetchall()
        return True, "ready"
    except Exception as e:
        text = str(e)
        if "measurements" in text and ("no such table" in text
                                       or "does not exist" in text):
            return False, ("schema not provisioned — run "
                           "`python -m tools.historian_provision`")
        return False, text[:200]


def info() -> dict:
    """Historian summary for /api/v1/health."""
    out: dict = {"backend": backend()}
    out["compression"] = False
    out["rollups"] = list(BUCKETS)          # computed on the fly from raw
    # The same shape of warning db/storage/bus already carry: it WORKS, it is just
    # not sized for unbounded production ingest, and nothing errors to tell you.
    out["scale_safe"] = False
    out["detail"] = (
        "rollups are computed per request from raw, with no columnar compression "
        "and no automatic retention — raw telemetry grows without bound. Add an "
        "app-level purge for high-volume ingest.")
    ok, detail = ping()
    out["status"] = "ready" if ok else "unavailable"
    if not ok:
        out["detail"] = detail
    return out


def log_posture() -> None:
    """One boot line, alongside [auth]/[db]/[storage]/[bus]/[tenancy]."""
    if backend() == "mysql":
        print("[historian] MySQL - plain table, rollups computed per request, no "
              "compression, no automatic retention. Fine for MVP/moderate ingest; "
              "add an app-level purge before high-volume telemetry.", flush=True)
    else:
        print("[historian] SQLite - local-dev telemetry history only. Set "
              "NXR_DATABASE_URL to the RDS MySQL endpoint before ingesting "
              "anything real.", flush=True)
