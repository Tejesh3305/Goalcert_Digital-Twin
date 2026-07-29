"""core.py — the ONE relational connection layer (Postgres in prod, SQLite in dev).

WHY THIS EXISTS
---------------
The twin's relational state used to be five separate SQLite files on a mounted
volume. SQLite over NFS/EFS is safe for exactly ONE writer, which pinned the ECS
service at `desired count = 1`: no scale-out, no zero-downtime rolling deploy.

This module replaces those five files with **one RDS PostgreSQL 16 database**
(AWS_DEPLOYMENT.md §7) while keeping SQLite as the local-dev backend, so nothing
about `start.ps1` / offline work changes.

    NXR_DATABASE_URL (or DATABASE_URL) set  →  PostgreSQL, pooled
    unset                                  →  SQLite, one file per store (as before)

WRITING SQL THAT RUNS ON BOTH
-----------------------------
Call sites write **SQLite-flavoured** SQL with `?` placeholders. This layer
rewrites `?` → `%s` for psycopg2 (string literals are respected). The remaining
dialect differences are handled by `db/schema.py`, which owns every CREATE TABLE,
and by two helpers here:

    Json(obj)     — bind a dict/list to a JSONB (Postgres) or TEXT (SQLite) column
    json_load(v)  — read one back (psycopg2 already parses JSONB into a dict)

Portable-by-construction SQL used by the stores: `ON CONFLICT (...) DO UPDATE SET
... excluded.x`, `ON CONFLICT (...) DO NOTHING` and `CURRENT_TIMESTAMP` all mean
the same thing in SQLite ≥3.24 and Postgres. `datetime('now')`, `AUTOINCREMENT`
and `INSERT OR IGNORE` do NOT — none survive in the stores.

CAVEAT: a literal `%` inside SQL is escaped to `%%` before it reaches psycopg2.
That is correct for `LIKE '%x%'` patterns written inline, but bind them as
parameters where you can.

CONNECTIONS
-----------
`connect(store)` yields a `Conn` that is also a context manager, matching the old
`with sqlite3.connect(...) as conn:` shape at every call site. On exit it commits
(or rolls back on an exception) and returns the connection to the pool. A
connection that raised a driver-level error is closed instead of pooled, so a
Multi-AZ failover or an RDS Proxy pin-drop costs one request, not the pool.

`store` names the logical store ("twins", "changelog", ...). On Postgres every
store shares one database and the name is only used for diagnostics; on SQLite it
picks the per-store file, which is what keeps existing local `data/*.db` readable.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from paths import DATA_DIR, data_path

# ── Which store maps to which SQLite file (Postgres ignores this) ────────
SQLITE_FILES = {
    "twins":       "twins.db",
    "changelog":   "changelog.db",
    "bundles":     "bundles.db",
    "checkpoints": "agent_checkpoints.db",
    "scenes":      "scenes.db",
    "threed":      "threed_jobs.db",
    # Accounts: organisations, users, memberships, sessions, API keys, audit.
    # Its own file locally for the same reason as every other store; on Postgres
    # it shares the one database, so a login and the twin it authorises are in
    # the same transaction domain.
    "identity":    "identity.db",
    "connectors":  "connectors.db",
    "devices":     "devices.db",
    # Telemetry history. In production this is a TimescaleDB hypertable on the
    # SAME Postgres instance as the stores above (see historian/) — the local
    # SQLite file exists so `npm run dev` needs no server, and is explicitly not
    # sized for real ingest volume.
    "historian":   "historian.db",
}

POSTGRES = "postgres"
SQLITE = "sqlite"


def database_url() -> str:
    """The configured Postgres URL, or "" for SQLite mode.

    `postgres://` is normalised to `postgresql://` — RDS consoles and several
    deploy tools still emit the former, which psycopg2 rejects outright.
    """
    url = (os.environ.get("NXR_DATABASE_URL")
           or os.environ.get("DATABASE_URL") or "").strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def dialect() -> str:
    return POSTGRES if database_url() else SQLITE


def is_postgres() -> bool:
    return dialect() == POSTGRES


def redacted_url() -> str:
    """The URL with the password removed — safe for logs and /health."""
    url = database_url()
    if not url:
        return ""
    try:
        p = urlsplit(url)
        host = p.hostname or ""
        if p.port:
            host = f"{host}:{p.port}"
        netloc = f"{p.username}:***@{host}" if p.username else host
        return urlunsplit((p.scheme, netloc, p.path, "", ""))
    except Exception:
        return "postgresql://***"


# ── Placeholder / dialect translation ───────────────────────────────────
@functools.lru_cache(maxsize=512)
def translate(sql: str) -> str:
    """Rewrite SQLite-flavoured SQL for psycopg2: `?` → `%s`, `%` → `%%`.

    Quoted string literals are scanned so a `?` inside one is left alone;
    doubled quotes ('') are handled as SQL escapes rather than terminators.
    """
    out: list[str] = []
    in_str = False
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if in_str:
            if c == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    out.append("''")
                    i += 2
                    continue
                in_str = False
            out.append("%%" if c == "%" else c)
        elif c == "'":
            in_str = True
            out.append(c)
        elif c == "?":
            out.append("%s")
        elif c == "%":
            out.append("%%")
        else:
            out.append(c)
        i += 1
    return "".join(out)


def Json(obj: Any):
    """Bind a dict/list to a JSON column, whichever backend is live.

    Postgres columns are JSONB (queryable, indexable — the reason the
    architecture calls for it); SQLite keeps the same value as TEXT.
    """
    if is_postgres():
        from psycopg2.extras import Json as _PgJson
        return _PgJson(obj)
    return json.dumps(obj)


def json_load(value: Any) -> Any:
    """Read a JSON column back. psycopg2 already parses JSONB into Python, so
    only the SQLite TEXT case needs decoding — and a NULL stays None."""
    if value is None or isinstance(value, dict | list):
        return value
    if isinstance(value, bytes | bytearray):
        value = value.decode("utf-8")
    return json.loads(value)


def advisory_key(name: str) -> int:
    """A stable signed 64-bit key for pg_advisory_xact_lock().

    Derived in Python rather than via Postgres' undocumented `hashtext()`, so
    the value can't shift under us across server versions.
    """
    return int.from_bytes(hashlib.sha1(name.encode("utf-8")).digest()[:8],
                          "big", signed=True)


# ── Postgres pool ───────────────────────────────────────────────────────
_pool = None
_pool_lock = threading.Lock()
_pool_url: str = ""


def _pool_size() -> tuple[int, int]:
    """Per-TASK pool bounds. Keep max modest: with N Fargate tasks the real
    server-side number is N × max, and RDS Proxy multiplexes on top."""
    try:
        lo = max(0, int(os.environ.get("NXR_DB_POOL_MIN", "1")))
    except ValueError:
        lo = 1
    try:
        hi = max(1, int(os.environ.get("NXR_DB_POOL_MAX", "10")))
    except ValueError:
        hi = 10
    return lo, max(lo, hi)


def _connect_kwargs() -> dict:
    kw: dict[str, Any] = {
        # Named in pg_stat_activity / RDS Performance Insights, so a stuck
        # query is attributable to this service rather than "python".
        "application_name": os.environ.get("NXR_DB_APP_NAME", "nextxr-twin"),
        "connect_timeout": int(os.environ.get("NXR_DB_CONNECT_TIMEOUT", "10")),
    }
    sslmode = os.environ.get("NXR_DB_SSLMODE", "").strip()
    if sslmode:
        kw["sslmode"] = sslmode
    return kw


def get_pool():
    """The process-wide psycopg2 pool, created on first use.

    Stores are constructed ad hoc all over this codebase (`ChangeLog()` inside
    request handlers, for instance). One shared pool is what keeps that pattern
    from opening a TCP connection per object.
    """
    global _pool, _pool_url
    url = database_url()
    if not url:
        raise RuntimeError("get_pool() called with no NXR_DATABASE_URL set")
    if _pool is not None and _pool_url == url:
        return _pool
    with _pool_lock:
        if _pool is not None and _pool_url == url:
            return _pool
        from psycopg2.pool import ThreadedConnectionPool
        lo, hi = _pool_size()
        _pool = ThreadedConnectionPool(lo, hi, url, **_connect_kwargs())
        _pool_url = url
        return _pool


def close_pool() -> None:
    """Drop every pooled connection (shutdown, or after a config change)."""
    global _pool, _pool_url
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.closeall()
            except Exception:
                pass
        _pool = None
        _pool_url = ""


# ── Connection wrapper ──────────────────────────────────────────────────
class Conn:
    """One checked-out connection, shaped like the `sqlite3.Connection` the
    stores were written against: `conn.execute(sql, params)` returns a cursor
    whose rows support `row["column"]`."""

    def __init__(self, raw, kind: str, release):
        self._raw = raw
        self.kind = kind
        self._release = release
        self._broken = False

    # -- statements ------------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] = ()):
        if self.kind == SQLITE:
            return self._raw.execute(sql, tuple(params))
        cur = self._raw.cursor()
        try:
            cur.execute(translate(sql), tuple(params))
        except Exception as e:
            self._note(e)
            raise
        return cur

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]):
        if self.kind == SQLITE:
            return self._raw.executemany(sql, [tuple(r) for r in rows])
        cur = self._raw.cursor()
        try:
            cur.executemany(translate(sql), [tuple(r) for r in rows])
        except Exception as e:
            self._note(e)
            raise
        return cur

    def lock(self, name: str) -> None:
        """Serialise writers across ALL tasks for the duration of this
        transaction. This is what makes the change log's read-last-hash →
        append sequence safe once the service runs more than one task; on
        SQLite the single-writer file lock already provides it."""
        if self.kind == POSTGRES:
            self.execute("SELECT pg_advisory_xact_lock(?)",
                         (advisory_key(name),))

    def _note(self, exc: Exception) -> None:
        """Flag driver-level failures so this connection is closed rather than
        handed back to the pool — a failover leaves sockets that look open."""
        try:
            import psycopg2
            if isinstance(exc, psycopg2.OperationalError | psycopg2.InterfaceError):
                self._broken = True
        except Exception:
            pass

    # -- transaction -----------------------------------------------------
    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        try:
            self._raw.rollback()
        except Exception:
            self._broken = True

    def close(self) -> None:
        if self._release is not None:
            self._release(self._raw, self._broken)
            self._release = None

    def __enter__(self) -> Conn:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()
        return False


def _sqlite_path(store: str, path: Path | None) -> Path:
    if path is not None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    return data_path(SQLITE_FILES.get(store, f"{store}.db"))


def connect(store: str, *, path: Path | None = None) -> Conn:
    """Check out a connection for `store`.

    `path` forces a specific SQLite file even when Postgres is configured —
    used by throwaway/offline tools that need their own reproducible file.
    """
    if path is None and is_postgres():
        pool = get_pool()
        from psycopg2.extras import RealDictCursor

        raw = None
        for _ in range(3):
            raw = pool.getconn()
            if getattr(raw, "closed", 0) == 0:
                break
            pool.putconn(raw, close=True)     # stale — the server hung up
            raw = None
        if raw is None:
            raise RuntimeError("no usable Postgres connection in the pool")

        raw.cursor_factory = RealDictCursor

        def release(c, broken: bool):
            try:
                pool.putconn(c, close=bool(broken))
            except Exception:
                pass

        return Conn(raw, POSTGRES, release)

    raw = sqlite3.connect(_sqlite_path(store, path), timeout=30.0)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA journal_mode=WAL")

    def release_sqlite(c, broken: bool):
        try:
            c.close()
        except Exception:
            pass

    return Conn(raw, SQLITE, release_sqlite)


# ── Diagnostics (served by /api/v1/health) ──────────────────────────────
def ping() -> tuple[bool, str]:
    """(reachable, detail). Never raises — the health endpoint must not 503."""
    try:
        with connect("changelog") as conn:
            conn.execute("SELECT 1")
        return True, "connected"
    except Exception as e:
        return False, str(e)


def log_posture() -> None:
    """Announce the storage posture once at startup, next to the `[auth]` line.

    An unset NXR_DATABASE_URL is not an error — it is the local-dev default —
    which is exactly why a deploy can end up on per-task SQLite files without
    anything failing. The twins would be there, they would just be a different
    set on each task. This makes that visible in CloudWatch on the first boot.
    """
    if is_postgres():
        lo, hi = _pool_size()
        print(f"[db] PostgreSQL - {redacted_url()} (pool {lo}-{hi} per task)",
              flush=True)
    else:
        print(f"[db] SQLite under {DATA_DIR} - single-writer, per-task state. "
              "Fine locally; set NXR_DATABASE_URL to the RDS endpoint before "
              "running more than one task.", flush=True)


def info() -> dict:
    """Backend summary for /health. Includes no credentials."""
    out: dict[str, Any] = {"backend": dialect()}
    if is_postgres():
        lo, hi = _pool_size()
        out["url"] = redacted_url()
        out["pool"] = {"min": lo, "max": hi,
                       "open": _pool is not None}
    else:
        out["data_dir"] = str(DATA_DIR)
        out["files"] = sorted(SQLITE_FILES.values())
    ok, detail = ping()
    out["status"] = "connected" if ok else "unreachable"
    if not ok:
        out["detail"] = detail
    return out
