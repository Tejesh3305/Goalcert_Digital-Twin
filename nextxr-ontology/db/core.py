"""core.py — the ONE relational connection layer (MySQL in prod, SQLite in dev).

WHY THIS EXISTS
---------------
The twin's relational state used to be five separate SQLite files on a mounted
volume. SQLite over NFS/EFS is safe for exactly ONE writer, which pinned the ECS
service at `desired count = 1`: no scale-out, no zero-downtime rolling deploy.

This module replaces those five files with **one RDS MySQL 8 database**
(AWS_DEPLOYMENT.md §7) while keeping SQLite as the local-dev backend, so nothing
about `start.ps1` / offline work changes.

    NXR_DATABASE_URL (or DATABASE_URL) set  →  MySQL, pooled
    unset                                  →  SQLite, one file per store (as before)

The URL is a standard MySQL DSN, e.g.
`mysql://user:pass@host:3306/nextxr` (or `mysql+pymysql://...`).

WRITING SQL THAT RUNS ON BOTH
-----------------------------
Call sites write **SQLite-flavoured** SQL with `?` placeholders. This layer
rewrites `?` → `%s` for PyMySQL (string literals are respected). The remaining
dialect differences are handled by `db/schema.py`, which owns every CREATE TABLE,
and by helpers here:

    Json(obj)     — serialise a dict/list for a JSON (MySQL) or TEXT (SQLite) column
    json_load(v)  — read one back (both backends hand us the JSON as text)

SQLite-flavoured UPSERTS ARE TRANSLATED FOR MySQL, so call sites keep writing one
dialect. `translate()` rewrites, for MySQL only:

    ON CONFLICT (...) DO UPDATE SET x = excluded.x  →  ... ON DUPLICATE KEY UPDATE x = VALUES(x)
    ON CONFLICT (...) DO NOTHING                    →  INSERT IGNORE INTO ... (clause stripped)

`CURRENT_TIMESTAMP` means the same thing in SQLite ≥3.24 and MySQL 8. `datetime('now')`,
`AUTOINCREMENT` and `INSERT OR IGNORE` do NOT — none survive in the stores.

CAVEAT: a literal `%` inside SQL is escaped to `%%` before it reaches PyMySQL.
That is correct for `LIKE '%x%'` patterns written inline, but bind them as
parameters where you can.

CONNECTIONS
-----------
`connect(store)` yields a `Conn` that is also a context manager, matching the old
`with sqlite3.connect(...) as conn:` shape at every call site. On exit it commits
(or rolls back on an exception) and returns the connection to the pool. A
connection that raised a driver-level error is closed instead of pooled, so a
Multi-AZ failover or an RDS Proxy pin-drop costs one request, not the pool.

`store` names the logical store ("twins", "changelog", ...). On MySQL every store
shares one database and the name is only used for diagnostics; on SQLite it picks
the per-store file, which is what keeps existing local `data/*.db` readable.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import sqlite3
import threading
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

from paths import DATA_DIR, data_path

# ── Which store maps to which SQLite file (MySQL ignores this) ───────────
SQLITE_FILES = {
    "twins":       "twins.db",
    "changelog":   "changelog.db",
    "bundles":     "bundles.db",
    "checkpoints": "agent_checkpoints.db",
    "scenes":      "scenes.db",
    "threed":      "threed_jobs.db",
    # Accounts: organisations, users, memberships, sessions, API keys, audit.
    # Its own file locally for the same reason as every other store; on MySQL it
    # shares the one database, so a login and the twin it authorises are in the
    # same transaction domain.
    "identity":    "identity.db",
    "connectors":  "connectors.db",
    "devices":     "devices.db",
    # Telemetry history. In production this is a plain table on the SAME MySQL
    # instance as the stores above (see historian/) — the local SQLite file exists
    # so `npm run dev` needs no server, and is explicitly not sized for real
    # ingest volume.
    "historian":   "historian.db",
}

MYSQL = "mysql"
SQLITE = "sqlite"


def _truthy(val: str | None) -> bool:
    return str(val or "").strip().lower() in ("1", "true", "yes", "on")


def database_url() -> str:
    """The configured MySQL URL, or "" for SQLite mode.

    Accepts `mysql://` and `mysql+pymysql://` (SQLAlchemy-style) DSNs — both are
    parsed the same way by `_mysql_params()`.
    """
    return (os.environ.get("NXR_DATABASE_URL")
            or os.environ.get("DATABASE_URL") or "").strip()


def dialect() -> str:
    return MYSQL if database_url() else SQLITE


def is_mysql() -> bool:
    return dialect() == MYSQL


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
        return "mysql://***"


# ── Placeholder / dialect translation ───────────────────────────────────
_ONCONFLICT_UPDATE = re.compile(
    r"ON\s+CONFLICT\s*(?:\([^)]*\))?\s*DO\s+UPDATE\s+SET", re.IGNORECASE)
_ONCONFLICT_NOTHING = re.compile(
    r"ON\s+CONFLICT\s*(?:\([^)]*\))?\s*DO\s+NOTHING", re.IGNORECASE)
_EXCLUDED = re.compile(r"\bexcluded\.(\w+)", re.IGNORECASE)
_INSERT_INTO = re.compile(r"(\s*)INSERT\s+INTO\b", re.IGNORECASE)

# Column names that are MySQL reserved words and must be back-quoted to be used as
# identifiers. `signal` is the historian's measurement column (SIGNAL/RESIGNAL are
# reserved for stored-program condition handling). The word-boundary match leaves
# substrings like `tenant_signal_ts` untouched, and `(?<!`)…(?!`)` avoids
# double-quoting. SQLite never sees this — it does not go through translate().
_RESERVED_COLS = ("signal",)
_RESERVED_RE = re.compile(
    r"(?<!`)\b(" + "|".join(_RESERVED_COLS) + r")\b(?!`)", re.IGNORECASE)


def _rewrite_upsert(sql: str) -> str:
    """Rewrite SQLite/Postgres `ON CONFLICT` upserts into MySQL's dialect.

    Two shapes are used across the stores:
      • DO NOTHING  → `INSERT IGNORE INTO ...` with the ON CONFLICT clause removed
                      (first-writer-wins, which is what every DO NOTHING site wants).
      • DO UPDATE   → `... ON DUPLICATE KEY UPDATE ...`, with each `excluded.col`
                      reference turned into `VALUES(col)`.
    Leaves SQL without an ON CONFLICT clause untouched.
    """
    if "ON CONFLICT" not in sql.upper():
        return sql
    if _ONCONFLICT_NOTHING.search(sql):
        sql = _ONCONFLICT_NOTHING.sub("", sql)
        sql = _INSERT_INTO.sub(
            lambda m: f"{m.group(1)}INSERT IGNORE INTO", sql, count=1)
        return sql.rstrip()
    if _ONCONFLICT_UPDATE.search(sql):
        sql = _ONCONFLICT_UPDATE.sub("ON DUPLICATE KEY UPDATE", sql)
        sql = _EXCLUDED.sub(lambda m: f"VALUES({m.group(1)})", sql)
    return sql


@functools.lru_cache(maxsize=512)
def translate(sql: str) -> str:
    """Rewrite SQLite-flavoured SQL for PyMySQL: upserts first, then `?` → `%s`,
    `%` → `%%`.

    Quoted string literals are scanned so a `?` inside one is left alone;
    doubled quotes ('') are handled as SQL escapes rather than terminators.
    """
    sql = _rewrite_upsert(sql)
    sql = _RESERVED_RE.sub(r"`\1`", sql)
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
    """Serialise a dict/list for a JSON column, whichever backend is live.

    MySQL columns are `JSON` (queryable, indexable — the reason the architecture
    calls for it) and accept a JSON-text string, which is exactly what SQLite
    keeps in its TEXT column too. So one representation serves both.
    """
    return json.dumps(obj)


def json_load(value: Any) -> Any:
    """Read a JSON column back. Both PyMySQL and sqlite3 hand JSON columns back as
    text, so decode a str/bytes; a dict/list (already decoded) or NULL passes
    through untouched."""
    if value is None or isinstance(value, dict | list):
        return value
    if isinstance(value, bytes | bytearray):
        value = value.decode("utf-8")
    return json.loads(value)


# ── MySQL connection parameters ─────────────────────────────────────────
def _mysql_params() -> dict:
    """Parse NXR_DATABASE_URL into PyMySQL connect kwargs."""
    url = database_url()
    p = urlsplit(url)
    params: dict[str, Any] = {
        "host": p.hostname or "127.0.0.1",
        "port": p.port or 3306,
        "user": unquote(p.username) if p.username else "root",
        "password": unquote(p.password) if p.password else "",
        "database": (p.path or "/nextxr")[1:] or "nextxr",
        "charset": "utf8mb4",
        "connect_timeout": int(os.environ.get("NXR_DB_CONNECT_TIMEOUT", "10")),
    }
    # TLS. RDS terminates TLS: point NXR_DB_SSL_CA at the RDS CA bundle to verify,
    # or set NXR_DB_SSL=1 for TLS without CA pinning. Absent both, connect plain
    # (correct for a private-subnet local/dev instance).
    ssl_ca = os.environ.get("NXR_DB_SSL_CA", "").strip()
    if ssl_ca:
        params["ssl"] = {"ca": ssl_ca}
    elif _truthy(os.environ.get("NXR_DB_SSL")):
        params["ssl"] = {}
    return params


def _mysql_connect():
    import pymysql
    from pymysql.cursors import DictCursor
    return pymysql.connect(cursorclass=DictCursor, autocommit=False,
                           **_mysql_params())


# ── MySQL pool ──────────────────────────────────────────────────────────
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


def _safe_close(conn) -> None:
    try:
        conn.close()
    except Exception:
        pass


class _MySQLPool:
    """A small thread-safe connection pool. PyMySQL ships no pool of its own, and
    the stores construct connections ad hoc (`ChangeLog()` inside a request
    handler); without pooling every construction is a fresh TCP + auth round-trip
    against RDS."""

    def __init__(self, minconn: int, maxconn: int, connect_fn):
        self._connect = connect_fn
        self._max = max(1, maxconn)
        self._idle: list[Any] = []
        self._in_use = 0
        self._cond = threading.Condition(threading.Lock())
        self._closed = False

    def getconn(self, timeout: float = 30.0):
        with self._cond:
            while True:
                if self._closed:
                    raise RuntimeError("MySQL pool is closed")
                if self._idle:
                    self._in_use += 1
                    return self._idle.pop()
                if self._in_use < self._max:
                    self._in_use += 1
                    break
                if not self._cond.wait(timeout=timeout):
                    raise RuntimeError(
                        "timed out waiting for a MySQL connection from the pool")
        try:
            return self._connect()
        except Exception:
            with self._cond:
                self._in_use -= 1
                self._cond.notify()
            raise

    def putconn(self, conn, close: bool = False) -> None:
        with self._cond:
            if self._in_use > 0:
                self._in_use -= 1
            if self._closed or close:
                _safe_close(conn)
            else:
                self._idle.append(conn)
            self._cond.notify()

    def closeall(self) -> None:
        with self._cond:
            self._closed = True
            while self._idle:
                _safe_close(self._idle.pop())
            self._cond.notify_all()


_pool: _MySQLPool | None = None
_pool_lock = threading.Lock()
_pool_url: str = ""


def get_pool() -> _MySQLPool:
    """The process-wide MySQL pool, created on first use."""
    global _pool, _pool_url
    url = database_url()
    if not url:
        raise RuntimeError("get_pool() called with no NXR_DATABASE_URL set")
    if _pool is not None and _pool_url == url:
        return _pool
    with _pool_lock:
        if _pool is not None and _pool_url == url:
            return _pool
        lo, hi = _pool_size()
        _pool = _MySQLPool(lo, hi, _mysql_connect)
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


def _lock_name(name: str) -> str:
    """A MySQL GET_LOCK() name (max 64 chars). Long names are hashed to a stable
    short token so the lock still serialises the right set of writers.

    SHA-256 rather than SHA-1 purely to keep static analysis quiet (bandit B324
    flags SHA-1 wherever it appears). Nothing here is a security boundary — the
    digest namespaces a lock, it does not authenticate anything — but "the weak
    hash is fine, look at the call site" is an argument every reviewer has to
    re-run, and a one-word change ends it permanently.
    """
    if len(name) <= 64:
        return name
    return "nxrlock_" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:24]


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
        self._locks: list[str] = []

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
        SQLite the single-writer file lock already provides it.

        MySQL's GET_LOCK is session- (not transaction-) scoped, so the lock is
        released explicitly at the end of this transaction — see `_release_locks`,
        called from `close()` before the connection returns to the pool."""
        if self.kind != MYSQL:
            return
        lock_name = _lock_name(name)
        timeout = int(os.environ.get("NXR_DB_LOCK_TIMEOUT", "10"))
        row = self.execute("SELECT GET_LOCK(?, ?) AS ok",
                           (lock_name, timeout)).fetchone()
        if not row or not row.get("ok"):
            raise RuntimeError(f"could not acquire lock '{lock_name}' "
                               f"within {timeout}s")
        self._locks.append(lock_name)

    def _release_locks(self) -> None:
        if self.kind != MYSQL or not self._locks:
            return
        for name in self._locks:
            try:
                self._raw.cursor().execute("SELECT RELEASE_LOCK(%s)", (name,))
            except Exception:
                self._broken = True
        self._locks = []

    @contextmanager
    def nested(self, name: str = "nxr_sp") -> Iterator[None]:
        """Scope a statement that is ALLOWED to fail. A no-op on both live
        backends — kept because the call sites are the ones that need it.

        THIS EXISTED FOR POSTGRES, WHICH IS NO LONGER A BACKEND. There, any
        error aborts the WHOLE transaction and every later statement fails with
        "current transaction is aborted", so a schema reconcile probing one
        ALTER, or a migration with a `tolerate` list, would take out the
        statements after it and the ledger row with them. A SAVEPOINT scoped the
        failure.

        Neither backend we now run has that rule. MySQL/InnoDB fails per
        STATEMENT: a rejected ALTER leaves the transaction usable, and the DDL
        these callers issue implicitly commits anyway, so a savepoint around it
        would have nothing to roll back to. SQLite likewise fails per statement.
        In both cases the caller's own `except` is already sufficient.

        So this yields and re-raises, unchanged. It is deliberately NOT deleted:
        `schema.reconcile()` and `db.migrations._execute()` read more honestly
        when the "this may fail, and that is survivable" intent is explicit, and
        keeping the seam means a backend with Postgres' rule can be supported
        again by editing this one method.
        """
        yield

    def _note(self, exc: Exception) -> None:
        """Flag driver-level failures so this connection is closed rather than
        handed back to the pool — a failover leaves sockets that look open."""
        try:
            import pymysql
            if isinstance(exc, pymysql.err.OperationalError
                          | pymysql.err.InterfaceError):
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
            self._release_locks()
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

    `path` forces a specific SQLite file even when MySQL is configured — used by
    throwaway/offline tools that need their own reproducible file.
    """
    if path is None and is_mysql():
        pool = get_pool()
        raw = None
        for _ in range(3):
            cand = pool.getconn()
            try:
                cand.ping(reconnect=True)     # revive/replace a stale socket
                raw = cand
                break
            except Exception:
                pool.putconn(cand, close=True)
                raw = None
        if raw is None:
            raise RuntimeError("no usable MySQL connection in the pool")

        def release(c, broken: bool):
            try:
                pool.putconn(c, close=bool(broken))
            except Exception:
                pass

        return Conn(raw, MYSQL, release)

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
    if is_mysql():
        lo, hi = _pool_size()
        print(f"[db] MySQL - {redacted_url()} (pool {lo}-{hi} per task)",
              flush=True)
    else:
        print(f"[db] SQLite under {DATA_DIR} - single-writer, per-task state. "
              "Fine locally; set NXR_DATABASE_URL to the RDS endpoint before "
              "running more than one task.", flush=True)


def info() -> dict:
    """Backend summary for /health. Includes no credentials."""
    out: dict[str, Any] = {"backend": dialect()}
    if is_mysql():
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
