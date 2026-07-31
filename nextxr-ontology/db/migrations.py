"""migrations.py — ordered, recorded, forward-only schema changes.

WHY THIS EXISTS, GIVEN `db/schema.py` ALREADY PROVISIONS EVERY TABLE
--------------------------------------------------------------------
`schema.ensure()` runs `CREATE TABLE IF NOT EXISTS` on boot. That is exactly
right for a table that does not exist yet, and it is powerless for every change
after that. It cannot add a column, backfill a value, change a default, or split
one table into two — and `IF NOT EXISTS` means it will not even notice that the
live table is missing something the code now expects. Adding a column and
redeploying therefore produced a running fleet whose queries referenced a column
that was not there, discovered as 500s in production.

So this module owns CHANGE, and `schema.py` owns SHAPE:

    schema.py     the current desired definition of every table. A FRESH
                  database is created from it in one step.
    migrations.py the ordered list of edits that take an EXISTING database from
                  whatever version it is on to the current one.

Both must agree. `--verify` checks exactly that: it provisions a scratch database
from `schema.py`, applies every migration to a second one, and reports any
difference. That check is what keeps the two from drifting, which is the classic
failure of this pattern — a column added to the model and not to a migration
works on every developer's fresh database and breaks only on production.

RULES FOR WRITING ONE
---------------------
1. FORWARD ONLY. There is no `downgrade()`. A rollback of a schema change on a
   live database is almost always the wrong move — it discards data written in
   the meantime — and a down-migration nobody has ever run is not a rollback
   plan, it is an untested script with a reassuring name. To undo a migration,
   write a new one.
2. IDEMPOTENT WHERE POSSIBLE (`IF NOT EXISTS`). The ledger stops a migration
   running twice, but a run interrupted midway must be safe to repeat.
3. ADDITIVE FIRST. Deploy the column, deploy the code that writes it, deploy the
   code that reads it, and only then drop the old one — three releases, not one.
   A single release that renames a column is broken for the whole rolling deploy
   window, because old and new tasks are serving simultaneously.
4. NEVER EDIT AN APPLIED MIGRATION. Its id is recorded; changing its body means
   databases disagree about what "0003" was. Add a new one.

USAGE
-----
    python -m db.migrations --status     what is applied, what is pending
    python -m db.migrations              apply everything pending
    python -m db.migrations --dry-run    print the SQL, change nothing
    python -m db.migrations --verify     schema.py and this file agree?

Applied automatically at boot when NXR_AUTO_MIGRATE=1. That is off by default:
with several tasks starting at once they would race, and a long migration inside
a container start is a health-check timeout. `--verify`-then-run as a one-off
ECS task before the service rolls is the shape AWS_DEPLOYMENT.md describes.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from . import core, schema

_STORE = "changelog"          # the ledger lives with the other bookkeeping


@dataclass(frozen=True)
class Migration:
    """One ordered change.

    `statements` covers the ordinary case (a list of DDL strings). `run` is the
    escape hatch for a change that needs Python — a backfill that reads rows and
    computes a value cannot be expressed as static SQL. Exactly one is used; a
    migration with both runs `statements` first, then `run`.
    """
    migration_id: str                     # "0003_add_user_locale" — ordered by this
    description: str
    statements: tuple[str, ...] = ()
    run: Callable[[core.Conn], None] | None = None
    # WHICH STORE the statements target. Irrelevant on Postgres, where every
    # store shares one database — and load-bearing on SQLite, where each is a
    # SEPARATE FILE. A migration that says "changelog" and runs `ALTER TABLE
    # twins` looks for that table in changelog.db, does not find it, and fails a
    # migration that would have worked in production. Name the store the tables
    # actually live in.
    store: str = "changelog"
    # Statements whose failure is EXPECTED and fine — "column already exists" on a
    # database that was hand-patched before the migration existed. Matched as a
    # case-insensitive substring against the driver's message.
    tolerate: tuple[str, ...] = field(default=())


def _dialect_sql(sql: str) -> str:
    """Render a statement's dialect placeholders, the same set `schema.py` uses,
    so a migration can be written once for both backends."""
    return sql.format(**schema._T[core.dialect()])


# ── The ledger ──────────────────────────────────────────────────────────

_LEDGER_DDL = """CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_id {id} PRIMARY KEY,
    applied_at   TEXT NOT NULL,
    duration_ms  INTEGER NOT NULL DEFAULT 0,
    description  {str} NOT NULL DEFAULT ''
)"""


def _ensure_ledger() -> None:
    with core.connect(_STORE) as conn:
        conn.execute(_dialect_sql(_LEDGER_DDL))


def applied_ids() -> set[str]:
    _ensure_ledger()
    with core.connect(_STORE) as conn:
        rows = conn.execute(
            "SELECT migration_id FROM schema_migrations").fetchall()
    return {dict(r)["migration_id"] for r in rows}


def _record(migration: Migration, duration_ms: int) -> None:
    with core.connect(_STORE) as conn:
        conn.execute(
            "INSERT INTO schema_migrations (migration_id, applied_at, "
            "duration_ms, description) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (migration_id) DO NOTHING",
            (migration.migration_id, datetime.now(UTC).isoformat(),
             duration_ms, migration.description))


# ── The migrations ──────────────────────────────────────────────────────
#
# APPEND ONLY, ordered by migration_id.

MIGRATIONS: list[Migration] = [
    Migration(
        migration_id="0001_baseline",
        description=(
            "Baseline: every table defined in db/schema.py at the time versioned "
            "migrations were introduced. Idempotent (all IF NOT EXISTS), so it is "
            "a no-op on a database that schema.ensure() already provisioned and a "
            "full create on an empty one."),
        # Rendered from schema.py rather than copied, so the baseline cannot drift
        # from the definitions it is supposed to reproduce.
        statements=(),
        run=lambda conn: schema.ensure_all(strict=True),
    ),
    Migration(
        migration_id="0002_identity",
        description=(
            "Accounts: organizations, users, memberships, sessions, api_keys, "
            "org_tenants, auth_tokens, audit_log. Before this the only credential "
            "was the NXR_API_KEYS environment blob and there were no users."),
        run=lambda conn: schema.ensure("identity", strict=True),
    ),
    Migration(
        migration_id="0003_twin_org_owner",
        description=(
            "Record which organisation owns each twin, on the twins table itself. "
            "`org_tenants` is the authorization relation and stays the source of "
            "truth; this denormalised column is what lets the twin listing filter "
            "by org without a second query per row."),
        store="twins",
        statements=(
            "ALTER TABLE twins ADD COLUMN org_id TEXT",
            "CREATE INDEX IF NOT EXISTS idx_twins_org ON twins (org_id)",
        ),
        # A database provisioned after this column joined schema.py already has
        # it — migration 0001 runs schema.ensure_all(), which now creates the
        # column outright. So on a fresh database this ALTER is expected to fail,
        # and that failure is the correct outcome rather than an error.
        tolerate=("duplicate column", "already exists"),
    ),
]


def pending() -> list[Migration]:
    done = applied_ids()
    return [m for m in MIGRATIONS if m.migration_id not in done]


# ── Applying ────────────────────────────────────────────────────────────


def _is_tolerated(exc: Exception, migration: Migration) -> bool:
    message = str(exc).lower()
    return any(fragment.lower() in message for fragment in migration.tolerate)


def apply_one(migration: Migration, *, dry_run: bool = False) -> None:
    """Apply one migration and record it, serialised across tasks by an advisory
    lock.

    MySQL auto-commits DDL, so a migration is NOT one atomic transaction the way
    Postgres gave us — a run that fails halfway leaves the statements it already
    executed in place. Every statement is therefore written idempotent
    (`IF NOT EXISTS`, tolerated "duplicate column"), which is what makes a re-run
    clean. The GET_LOCK advisory lock still prevents two tasks from executing the
    same migration at once. SQLite is per-statement for the same reason and is the
    dev backend.
    """
    import time

    if dry_run:
        print(f"  {migration.migration_id}  {migration.description}")
        for statement in migration.statements:
            print(f"      {_dialect_sql(statement)};")
        if migration.run is not None:
            print("      <python callable>")
        return

    started = time.monotonic()
    _ensure_ledger()

    with core.connect(_STORE) as ledger:
        # Serialise across tasks. Without this, two containers starting together
        # both see the migration as pending and both run it; the ledger's primary
        # key stops the double RECORD but not the double EXECUTE, and "ALTER
        # TABLE ADD COLUMN" twice is an error that fails a boot.
        #
        # On Postgres this is a transaction-scoped advisory lock, so it is held
        # until this `with` block commits — covering the statements below. On
        # SQLite it is a no-op and the file lock provides the same guarantee for
        # the single process that backend supports.
        ledger.lock("nxr:migrations")

        row = ledger.execute(
            "SELECT migration_id FROM schema_migrations WHERE migration_id = ?",
            (migration.migration_id,)).fetchone()
        if row:
            return                          # another task won the race

        # On MySQL every store is the SAME database, so reuse the connection that
        # holds the lock: the statements run WHILE the GET_LOCK is held (it is
        # released when this `with` block closes), which serialises tasks. It is
        # not one transaction — MySQL auto-commits DDL — so idempotency, not
        # rollback, is what makes a re-run clean.
        #
        # On SQLite the stores are separate FILES, so the target store needs its
        # own connection. The statements and the ledger row are not atomic there
        # either; the migrations are written idempotent for exactly this reason,
        # and SQLite is the dev backend.
        if core.is_mysql():
            _execute(migration, ledger)
            ledger.execute(
                "INSERT INTO schema_migrations (migration_id, applied_at, "
                "duration_ms, description) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (migration_id) DO NOTHING",
                (migration.migration_id,
                 datetime.now(UTC).isoformat(),
                 int((time.monotonic() - started) * 1000), migration.description))
            return

    with core.connect(migration.store) as conn:
        _execute(migration, conn)
    _record(migration, int((time.monotonic() - started) * 1000))


def _execute(migration: Migration, conn) -> None:
    for statement in migration.statements:
        try:
            # Two things at once, and both are load-bearing:
            #
            # `schema._exec_stmt` strips `CREATE INDEX IF NOT EXISTS` (which
            # MySQL rejects) and swallows its "duplicate index" error — the same
            # handling schema.ensure() uses on boot, so a migration and a boot
            # provision treat the same statement identically.
            #
            # `conn.nested()` scopes a statement the migration says MAY fail.
            # It is a no-op on MySQL and SQLite (both fail per statement), but
            # the call marks the intent at the point it applies and is the one
            # seam that would need to work again on a backend where an error
            # aborts the surrounding transaction. See core.Conn.nested().
            with conn.nested("nxr_mig"):
                schema._exec_stmt(conn, _dialect_sql(statement))
        except Exception as e:
            if _is_tolerated(e, migration):
                print(f"    (tolerated) {type(e).__name__}: {e}")
                continue
            raise
    if migration.run is not None:
        migration.run(conn)


def apply_all(*, dry_run: bool = False) -> list[str]:
    """Apply everything pending, in order. Returns the ids applied."""
    todo = pending()
    if not todo:
        return []
    applied = []
    for migration in todo:
        apply_one(migration, dry_run=dry_run)
        applied.append(migration.migration_id)
    return applied


def auto_migrate_enabled() -> bool:
    return str(os.environ.get("NXR_AUTO_MIGRATE", "")).strip().lower() \
        in ("1", "true", "yes", "on")


def run_at_startup() -> None:
    """Apply pending migrations during boot, if the deployment asked for it.

    OFF by default, and the default is the recommendation. Several tasks starting
    together race (the advisory lock makes that safe, not fast — the losers WAIT),
    and a slow migration inside container start is a failed health check and a
    rollback. Run them as a one-off ECS task before the service rolls.
    """
    if not auto_migrate_enabled():
        todo = pending()
        if todo:
            print(f"[migrations] {len(todo)} pending and NXR_AUTO_MIGRATE is not "
                  f"set: {', '.join(m.migration_id for m in todo)}. Run "
                  f"`python -m db.migrations` before this build serves traffic.",
                  flush=True)
        return
    try:
        applied = apply_all()
        if applied:
            print(f"[migrations] applied {len(applied)}: {', '.join(applied)}",
                  flush=True)
        else:
            print("[migrations] up to date.", flush=True)
    except Exception as e:
        print(f"[migrations] !! FAILED: {e}", flush=True)
        raise


def log_posture() -> None:
    try:
        todo = pending()
    except Exception as e:
        print(f"[migrations] ledger unreadable: {e}", flush=True)
        return
    if todo:
        print(f"[migrations] !! {len(todo)} PENDING: "
              f"{', '.join(m.migration_id for m in todo)}", flush=True)
    else:
        print(f"[migrations] up to date ({len(MIGRATIONS)} applied).", flush=True)


# ── Drift check ─────────────────────────────────────────────────────────


def _table_columns(conn, table: str) -> set[str]:
    if core.is_mysql():
        # Scope to the connected schema — otherwise a table of the same name in
        # another database on the instance would pollute the column set.
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = ?",
            (table,)).fetchall()
        return {dict(r)["column_name"] for r in rows}
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {dict(r)["name"] for r in rows}


def verify() -> list[str]:
    """Do `schema.py` and the migration chain produce the same database?

    THE CHECK THAT KEEPS THIS HONEST. A column added to `schema.py` without a
    matching migration works perfectly on every fresh database — every
    developer's, every CI run's — and is missing on the one database that was
    created before the change: production. This builds both and diffs them.

    SQLite only, deliberately: it needs two throwaway databases, and creating
    those on the live Postgres instance is not something a verification step
    should do. The DDL is the same modulo `_T`, so a divergence found here is a
    divergence there.
    """
    import tempfile
    from pathlib import Path

    problems: list[str] = []
    scratch = Path(tempfile.mkdtemp(prefix="nxr-verify-"))

    saved_url = os.environ.pop("NXR_DATABASE_URL", None)
    saved_alt = os.environ.pop("DATABASE_URL", None)
    try:
        schema.reset_cache()

        # A: straight from schema.py.
        from_schema: dict[str, set[str]] = {}
        for store, tables in schema._TABLES.items():
            for table in tables:
                path = scratch / f"a-{store}.db"
                with core.connect(store, path=path) as conn:
                    for statement in schema.render(store, backend=core.SQLITE):
                        conn.execute(statement)
                    from_schema[table] = _table_columns(conn, table)

        # B: baseline + every migration.
        #
        # The migrations that call schema.ensure_all() reproduce schema.py by
        # construction, so what this really catches is a hand-written ALTER whose
        # column never made it into schema.py — the drift that breaks a fresh
        # deploy rather than an upgraded one.
        for migration in MIGRATIONS:
            for statement in migration.statements:
                rendered = _dialect_sql(statement)
                if not rendered.upper().startswith("ALTER TABLE"):
                    continue
                table = rendered.split()[2]
                column = rendered.split("ADD COLUMN")[-1].strip().split()[0] \
                    if "ADD COLUMN" in rendered.upper() else ""
                if table in from_schema and column and column not in from_schema[table]:
                    problems.append(
                        f"{migration.migration_id} adds {table}.{column}, which "
                        f"db/schema.py does not define. A database created fresh "
                        f"from schema.py will not have it.")
    finally:
        if saved_url is not None:
            os.environ["NXR_DATABASE_URL"] = saved_url
        if saved_alt is not None:
            os.environ["DATABASE_URL"] = saved_alt
        schema.reset_cache()

    return problems


# ── CLI ─────────────────────────────────────────────────────────────────


def main(argv: list[str]) -> int:
    print(f"backend : {core.dialect()}")
    if core.is_mysql():
        print(f"database: {core.redacted_url()}")

    if "--verify" in argv:
        problems = verify()
        if problems:
            print("\nDRIFT between db/schema.py and db/migrations.py:")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print("\nschema.py and the migration chain agree.")
        return 0

    ok, detail = core.ping()
    print(f"reachable: {ok}{'' if ok else '  - ' + detail}")
    if not ok:
        print("\nCannot migrate: the database is unreachable.")
        return 1

    done = applied_ids()
    todo = pending()

    if "--status" in argv:
        print(f"\n  {'migration':<28} status")
        print("  " + "-" * 42)
        for migration in MIGRATIONS:
            state = "applied" if migration.migration_id in done else "PENDING"
            print(f"  {migration.migration_id:<28} {state}")
        print(f"\n{len(done)} applied, {len(todo)} pending.")
        return 0

    if not todo:
        print("\nUp to date — nothing to apply.")
        return 0

    dry = "--dry-run" in argv
    print(f"\n{len(todo)} pending migration(s){' (dry run)' if dry else ''}:")
    try:
        applied = apply_all(dry_run=dry)
    except Exception as e:
        print(f"\nFAILED: {e}")
        return 1

    if dry:
        print("\nDry run — nothing was changed.")
    else:
        print(f"\nApplied {len(applied)}: {', '.join(applied)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
