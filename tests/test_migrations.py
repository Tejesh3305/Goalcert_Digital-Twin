"""test_migrations.py — ordered, recorded, forward-only schema change.

WHAT THIS PREVENTS
------------------
`schema.ensure()` runs `CREATE TABLE IF NOT EXISTS` at boot. That is exactly
right for a table that does not exist and powerless for every change after it:
adding a column and redeploying produced a running fleet whose queries referenced
a column that was not there, discovered as 500s in production.

The properties that make `db/migrations.py` trustworthy, each with the failure it
prevents:

  idempotent      a re-run must be a no-op, or a restart re-applies everything
  recorded        the ledger is what makes "which version is this database" answerable
  ordered         0003 must not run before 0002
  right store     SQLite keeps each store in its own FILE, so a migration that
                  names the wrong one fails on the dev backend only
  no drift        a column in a migration but not in schema.py works on every
                  fresh database and is missing on the one that matters
"""

from __future__ import annotations

import pytest
from db import core, migrations, schema


@pytest.fixture
def scratch_db(tmp_path, monkeypatch):
    """A throwaway SQLite database, with the provisioning caches cleared.

    Both caches matter. `schema._done` remembers what it has created per
    (backend, store) and `migrations` reads its ledger from the live database —
    without clearing the first, a fresh directory would be reported as already
    provisioned and no DDL would run.
    """
    monkeypatch.setenv("NXR_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("NXR_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    import paths
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(core, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(core, "data_path", lambda name: tmp_path / name)

    schema.reset_cache()
    yield tmp_path
    schema.reset_cache()


# ── The ledger ──────────────────────────────────────────────────────────


def test_a_fresh_database_has_everything_pending(scratch_db):
    assert migrations.applied_ids() == set()
    assert len(migrations.pending()) == len(migrations.MIGRATIONS)


def test_applying_records_every_migration(scratch_db):
    applied = migrations.apply_all()

    assert applied == [m.migration_id for m in migrations.MIGRATIONS]
    assert migrations.applied_ids() == {m.migration_id for m in migrations.MIGRATIONS}
    assert migrations.pending() == []


def test_re_applying_is_a_no_op(scratch_db):
    """A restart must not re-run anything. `ALTER TABLE ADD COLUMN` twice is an
    error that fails a boot."""
    migrations.apply_all()
    assert migrations.apply_all() == []


def test_a_dry_run_changes_nothing(scratch_db):
    migrations.apply_all(dry_run=True)
    assert migrations.applied_ids() == set()


def test_migrations_are_ordered_by_id():
    ids = [m.migration_id for m in migrations.MIGRATIONS]
    assert ids == sorted(ids), "migrations must be appended in id order"


def test_migration_ids_are_unique():
    ids = [m.migration_id for m in migrations.MIGRATIONS]
    assert len(ids) == len(set(ids))


def test_every_migration_has_a_description():
    """The ledger is read by whoever is debugging a schema question at 2am. A
    migration called `0007_fix` with no description is not an answer."""
    for m in migrations.MIGRATIONS:
        assert len(m.description) > 20, f"{m.migration_id} needs a real description"


def test_every_migration_names_a_real_store():
    """On SQLite each store is a separate FILE, so a migration that says
    'changelog' and runs `ALTER TABLE twins` looks for that table in
    changelog.db, does not find it, and fails a migration that would have worked
    against Postgres."""
    known = set(schema.DDL) | set(core.SQLITE_FILES)
    for m in migrations.MIGRATIONS:
        assert m.store in known, f"{m.migration_id} targets unknown store {m.store!r}"


# ── The result ──────────────────────────────────────────────────────────


def test_every_table_exists_afterwards(scratch_db):
    migrations.apply_all()

    for store, tables in schema._TABLES.items():
        with core.connect(store) as conn:
            for table in tables:
                conn.execute(f"SELECT COUNT(*) FROM {table}")   # raises if missing


def test_the_identity_tables_are_created(scratch_db):
    """The subsystem that did not exist before. Named explicitly because a
    missing one is not a crash — it is a login that fails with a confusing
    error."""
    migrations.apply_all()

    with core.connect("identity") as conn:
        for table in ("organizations", "users", "memberships", "sessions",
                      "api_keys", "org_tenants", "auth_tokens", "audit_log"):
            conn.execute(f"SELECT COUNT(*) FROM {table}")


def test_the_org_column_lands_on_twins(scratch_db):
    """Migration 0003. Present on a FRESH database (schema.py defines it) and
    added by the ALTER on an existing one — the two paths must agree."""
    migrations.apply_all()

    with core.connect("twins") as conn:
        columns = {dict(r)["name"]
                   for r in conn.execute("PRAGMA table_info(twins)").fetchall()}
    assert "org_id" in columns


def test_a_tolerated_failure_does_not_abort(scratch_db):
    """0003's ALTER is EXPECTED to fail on a fresh database — 0001 already
    created the column via schema.ensure_all(). That failure is the correct
    outcome, not an error, which is what `tolerate` encodes."""
    applied = migrations.apply_all()
    assert "0003_twin_org_owner" in applied


# ── Upgrading a database that PREDATES a column ─────────────────────────
#
# The regression these cover took every existing deployment down to an
# unmigratable state, and every test above passed throughout — because they all
# start from an empty directory, and the bug only exists on a database that was
# created BEFORE a column joined schema.py.


def _legacy_twins_table(directory) -> None:
    """A `twins` table as it existed before `org_id` was declared: the real
    starting state of every database created before that change."""
    import sqlite3

    conn = sqlite3.connect(directory / "twins.db")
    conn.execute("""CREATE TABLE twins (
                        tenant_id     TEXT PRIMARY KEY,
                        name          TEXT NOT NULL,
                        domain        TEXT NOT NULL,
                        description   TEXT NOT NULL DEFAULT '',
                        created_at    TEXT NOT NULL,
                        seed_asset_id TEXT
                    )""")
    conn.execute("INSERT INTO twins (tenant_id, name, domain, created_at) "
                 "VALUES ('legacy-plant', 'Legacy Plant', 'hvac', '2026-01-01')")
    conn.commit()
    conn.close()


def test_a_database_predating_a_column_can_still_migrate(scratch_db):
    """THE REGRESSION.

    `CREATE TABLE IF NOT EXISTS` cannot add a column, so on a table that predates
    `org_id` the CREATE INDEX on that column raised "no such column: org_id".
    Migration 0001 runs `ensure_all(strict=True)`, so the whole chain aborted
    there — BEFORE 0003, the migration that adds the column. The database was
    then permanently stuck: every boot printed the same failure and no migration
    could ever be applied.
    """
    _legacy_twins_table(scratch_db)

    applied = migrations.apply_all()

    assert applied == [m.migration_id for m in migrations.MIGRATIONS]
    with core.connect("twins") as conn:
        columns = {dict(r)["name"]
                   for r in conn.execute("PRAGMA table_info(twins)").fetchall()}
    assert "org_id" in columns


def test_upgrading_in_place_keeps_the_existing_rows(scratch_db):
    """Reconciling ADDs; it must never re-create the table. A create-copy-swap
    that lost a row would be a far worse bug than the one being fixed."""
    _legacy_twins_table(scratch_db)

    migrations.apply_all()

    with core.connect("twins") as conn:
        row = dict(conn.execute(
            "SELECT name, org_id FROM twins WHERE tenant_id = 'legacy-plant'"
        ).fetchone())
    assert row["name"] == "Legacy Plant"
    assert row["org_id"] is None        # added, not invented


def test_ensure_reconciles_without_migrations(scratch_db):
    """A boot with NXR_AUTO_MIGRATE unset must also self-heal. `ensure()` is what
    runs on every start; if only the migration path could add the column, a
    deployment that runs migrations as a separate task would still serve requests
    against a table missing it."""
    _legacy_twins_table(scratch_db)

    schema.ensure("twins")

    with core.connect("twins") as conn:
        columns = {dict(r)["name"]
                   for r in conn.execute("PRAGMA table_info(twins)").fetchall()}
        indexes = {dict(r)["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()}
    assert "org_id" in columns
    assert "idx_twins_org" in indexes


def test_reconciling_is_idempotent(scratch_db):
    """The second boot must not try to add the column again — `ALTER TABLE ADD
    COLUMN` twice is an error, and this runs on every start."""
    _legacy_twins_table(scratch_db)

    schema.ensure("twins")
    schema.reset_cache()
    schema.ensure("twins")              # must not raise

    with core.connect("twins") as conn:
        names = [dict(r)["name"]
                 for r in conn.execute("PRAGMA table_info(twins)").fetchall()]
    assert names.count("org_id") == 1


def test_declared_columns_ignores_table_constraints():
    """`PRIMARY KEY (org_id, user_id)` is a table constraint. Read as a column it
    would produce `ALTER TABLE memberships ADD COLUMN PRIMARY ...`."""
    table, columns = schema.declared_columns(
        schema.render("identity")[3])   # the memberships CREATE TABLE

    assert table == "memberships"
    # `persona` (the operational role: supervisor / frontline) joined this table
    # in migration 0005; `role` remains the data ladder. See identity/models.py
    # for why the two axes are separate columns.
    assert set(columns) == {"org_id", "user_id", "role", "persona", "created_at"}
    assert "primary" not in {c.lower() for c in columns}


def test_declared_columns_skips_non_table_statements():
    for statement in schema.render("twins"):
        if statement.upper().startswith("CREATE INDEX"):
            assert schema.declared_columns(statement) == ("", {})


def test_a_column_that_cannot_be_added_in_place_is_reported(scratch_db, capsys):
    """ADD COLUMN cannot express UNIQUE on an existing SQLite table, or NOT NULL
    without a default on a populated one. Those need a real migration — and the
    operator has to be TOLD, rather than the store silently staying unprovisioned.
    """
    _legacy_twins_table(scratch_db)
    with core.connect("twins") as conn:
        added = schema.reconcile(conn, """CREATE TABLE IF NOT EXISTS twins (
                                              tenant_id TEXT PRIMARY KEY,
                                              serial    TEXT NOT NULL UNIQUE
                                          )""")

    assert added == []
    assert "serial" in capsys.readouterr().err


# ── Drift ───────────────────────────────────────────────────────────────


def test_schema_and_migrations_agree(scratch_db):
    """THE CHECK THAT KEEPS THIS HONEST.

    A column added to `schema.py` without a matching migration works perfectly on
    every fresh database — every developer's, every CI run's — and is missing on
    the one database created before the change: production. And the reverse, a
    migration adding a column `schema.py` never learned about, breaks the next
    fresh deploy instead.
    """
    assert migrations.verify() == []


def test_the_verifier_notices_a_missing_column(scratch_db, monkeypatch):
    """The drift check has to be able to FAIL, or it is decorative."""
    drifted = list(migrations.MIGRATIONS) + [
        migrations.Migration(
            migration_id="9999_drift",
            description="A column that db/schema.py does not define — the exact "
                        "mistake verify() exists to catch.",
            store="twins",
            statements=("ALTER TABLE twins ADD COLUMN not_in_schema_py TEXT",),
        )
    ]
    monkeypatch.setattr(migrations, "MIGRATIONS", drifted)

    problems = migrations.verify()
    assert any("not_in_schema_py" in p for p in problems)


# ── Startup behaviour ───────────────────────────────────────────────────


def test_auto_migrate_is_off_by_default(monkeypatch):
    """Several tasks starting together would queue on the advisory lock, and the
    ones that wait fail their health check — a failed deploy caused by the
    migration mechanism rather than by the migration."""
    monkeypatch.delenv("NXR_AUTO_MIGRATE", raising=False)
    assert migrations.auto_migrate_enabled() is False


def test_auto_migrate_can_be_enabled(monkeypatch):
    monkeypatch.setenv("NXR_AUTO_MIGRATE", "1")
    assert migrations.auto_migrate_enabled() is True


def test_startup_does_not_migrate_when_disabled(scratch_db, monkeypatch, capsys):
    """It must still SAY so — a pending migration nobody mentions is discovered
    as a 500 referencing a missing column."""
    monkeypatch.delenv("NXR_AUTO_MIGRATE", raising=False)

    migrations.run_at_startup()

    assert migrations.applied_ids() == set()
    assert "pending" in capsys.readouterr().out.lower()


def test_startup_migrates_when_enabled(scratch_db, monkeypatch):
    monkeypatch.setenv("NXR_AUTO_MIGRATE", "1")

    migrations.run_at_startup()

    assert migrations.pending() == []


# ── Rendering ───────────────────────────────────────────────────────────


def test_dialect_placeholders_render_for_both_backends():
    """The templates are shared, so a `{json}` that only renders on one backend
    is a migration that fails on the other — discovered in production, since
    local dev is the other one."""
    for m in migrations.MIGRATIONS:
        for statement in m.statements:
            for backend in (core.SQLITE, core.MYSQL):
                rendered = statement.format(**schema._T[backend])
                assert "{" not in rendered, (
                    f"{m.migration_id} has an unrendered placeholder on {backend}")
