"""migrate.py — copy an existing SQLite deployment into RDS Postgres.

One-shot, idempotent, read-only against the source. Run it once when cutting a
running twin over to RDS; re-running it is safe (every row upserts on its
primary key), so a failed run can simply be repeated.

    # point at the target, then:
    export NXR_DATABASE_URL='postgresql://nxr:...@twin.xxxx.rds.amazonaws.com/nextxr?sslmode=require'
    python -m db.migrate --dry-run     # report what would move, write nothing
    python -m db.migrate               # do it
    python -m db.migrate --source /data --truncate

`--source` is the directory holding the .db files (default: NXR_DATA_DIR, i.e.
whatever the running service uses). `--truncate` empties each target table
first — use it to re-run cleanly after a partial import, NOT against a database
already taking live writes.

THE CHANGE LOG IS COPIED VERBATIM, `seq` included. Its per-tenant hash chain is
a linked list of content hashes: re-generating event ids, timestamps or ordering
would break `verify_chain()` for every tenant and destroy the tamper-evidence
that is the point of the ledger. Chain integrity is verified after the copy and
reported per tenant.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from . import core, schema

# store -> (sqlite file, table, columns, conflict key)
PLAN = [
    ("twins", "twins.db", "twins",
     ["tenant_id", "name", "domain", "description", "created_at",
      "seed_asset_id"], "tenant_id", []),
    ("changelog", "changelog.db", "events",
     ["seq", "event_id", "tenant_id", "entity_id", "entity_type", "actor",
      "action", "field_changes", "ts", "prev_event_hash", "wm_hash"],
     "event_id", ["field_changes"]),
    ("bundles", "bundles.db", "published_bundles",
     ["bundle_id", "name", "domains", "payload", "tenant_id"],
     "bundle_id", ["domains", "payload"]),
    ("checkpoints", "agent_checkpoints.db", "checkpoints",
     ["thread_id", "graph_name", "state", "resume_at"],
     "thread_id", ["state"]),
]

BATCH = 500


def _read_sqlite(path: Path, table: str, cols: list[str]) -> list[tuple]:
    if not path.exists():
        return []
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        # A store may predate a column; select what the file actually has.
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not have:
            return []
        use = [c for c in cols if c in have]
        rows = conn.execute(f"SELECT {', '.join(use)} FROM {table}").fetchall()
        return [tuple(r[c] if c in use else None for c in cols) for r in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _upsert(store: str, table: str, cols: list[str], key: str,
            json_cols: list[str], rows: list[tuple]) -> int:
    """Insert rows, leaving any that already exist untouched.

    DO NOTHING rather than DO UPDATE: this is a cutover, not a sync. If the
    service has already taken a live write for a key, the live row is the newer
    truth and must win over the snapshot in the SQLite file.
    """
    if not rows:
        return 0
    placeholders = ",".join("?" for _ in cols)
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
           f"ON CONFLICT ({key}) DO NOTHING")
    idx = [cols.index(c) for c in json_cols]
    written = 0
    for start in range(0, len(rows), BATCH):
        chunk = rows[start:start + BATCH]
        prepared = []
        for r in chunk:
            r = list(r)
            for i in idx:
                # SQLite held these as TEXT; Postgres wants real JSONB. A value
                # that will not parse is kept as a JSON string rather than
                # dropped — losing an audited payload is worse than an odd one.
                if isinstance(r[i], str):
                    try:
                        r[i] = core.Json(json.loads(r[i]))
                    except (ValueError, TypeError):
                        r[i] = core.Json(r[i])
                elif r[i] is None:
                    r[i] = core.Json(None)
            prepared.append(tuple(r))
        with core.connect(store) as conn:
            conn.executemany(sql, prepared)
        written += len(chunk)
    return written


def _count(store: str, table: str) -> int:
    with core.connect(store) as conn:
        return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]


def _fix_events_sequence() -> None:
    """Re-point the `events.seq` identity past the copied rows.

    Explicit seq values do NOT advance a BIGSERIAL's sequence, so the first
    append after a migration would collide on seq 1 and keep colliding. This is
    the single thing most likely to be missed in a hand-rolled cutover.
    """
    with core.connect("changelog") as conn:
        conn.execute(
            "SELECT setval(pg_get_serial_sequence('events','seq'), "
            "COALESCE((SELECT MAX(seq) FROM events), 1), true)")


def _migrate_scenes(source: Path, dry: bool) -> int:
    """Load data/scenes/*.json into the scene_cache table."""
    d = source / "scenes"
    if not d.is_dir():
        return 0
    files = sorted(d.glob("*.json"))
    if dry or not files:
        return len(files)
    n = 0
    for f in files:
        try:
            scene = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        with core.connect("scenes") as conn:
            conn.execute(
                "INSERT INTO scene_cache (tenant_id, scene) VALUES (?,?) "
                "ON CONFLICT (tenant_id) DO NOTHING", (f.stem, core.Json(scene)))
        n += 1
    return n


def _verify_chains() -> tuple[int, list[str]]:
    """Re-verify every tenant's hash chain in the TARGET database."""
    from changelog.service import ChangeLog
    cl = ChangeLog()
    with core.connect("changelog") as conn:
        tenants = [r["tenant_id"] for r in conn.execute(
            "SELECT DISTINCT tenant_id FROM events").fetchall()]
    broken = []
    for t in tenants:
        ok, bad = cl.verify_chain(t)
        if not ok:
            broken.append(f"{t} (first bad event: {bad})")
    return len(tenants), broken


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    truncate = "--truncate" in argv

    source = None
    if "--source" in argv:
        source = Path(argv[argv.index("--source") + 1]).resolve()
    if source is None:
        from paths import DATA_DIR
        source = DATA_DIR

    print("=" * 62)
    print("  SQLite -> Postgres migration" + ("  (DRY RUN)" if dry else ""))
    print("=" * 62)
    print(f"source : {source}")

    if not core.is_postgres():
        print("\nNXR_DATABASE_URL is not set - there is no Postgres to migrate "
              "INTO.\nSet it to the RDS endpoint and re-run.")
        return 1
    print(f"target : {core.redacted_url()}")

    ok, detail = core.ping()
    if not ok:
        print(f"\nTarget unreachable: {detail}")
        return 1

    if not source.is_dir():
        print(f"\nSource directory does not exist: {source}")
        return 1

    if not dry:
        schema.ensure_all(strict=True)

    if truncate and not dry:
        print("\n--truncate: emptying target tables first")
        for store, _f, table, *_ in PLAN:
            with core.connect(store) as conn:
                conn.execute(f"DELETE FROM {table}")
        with core.connect("scenes") as conn:
            conn.execute("DELETE FROM scene_cache")

    print(f"\n  {'table':<20} {'read':>7} {'written':>8} {'target now':>11}")
    print("  " + "-" * 50)
    total = 0
    for store, filename, table, cols, key, json_cols in PLAN:
        rows = _read_sqlite(source / filename, table, cols)
        written = 0 if dry else _upsert(store, table, cols, key, json_cols, rows)
        now = "-" if dry else _count(store, table)
        print(f"  {table:<20} {len(rows):>7} {written:>8} {str(now):>11}")
        total += written

    scenes = _migrate_scenes(source, dry)
    print(f"  {'scene_cache':<20} {scenes:>7} {'-' if dry else scenes:>8}")

    if dry:
        print("\nDry run - nothing was written.")
        return 0

    _fix_events_sequence()
    print("\nevents.seq sequence advanced past the copied rows.")

    n_tenants, broken = _verify_chains()
    if broken:
        print(f"\n!! CHANGE LOG: {len(broken)}/{n_tenants} chains FAILED "
              f"verification:")
        for b in broken:
            print(f"    {b}")
        print("  The copy is complete but tamper-evidence is not intact for "
              "those tenants.\n  Investigate before decommissioning the SQLite "
              "files — do not delete them.")
        return 2
    print(f"change log: all {n_tenants} tenant chain(s) verify.")
    print(f"\nMigrated {total} rows. Keep the SQLite files until the "
          "deployment is confirmed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
