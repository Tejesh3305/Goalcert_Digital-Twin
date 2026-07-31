"""
test_integration_stores.py — the tests that need a real server running.

WHY THIS FILE EXISTS
--------------------
The `integration` marker was declared in pyproject.toml and used by NOTHING. CI
ran `pytest -m integration`, selected 0 of 366 tests, and pytest exited 5 ("no
tests collected") — so the job that was supposed to prove the production backend
works had been failing for its entire existence while proving nothing. The fast
suite pins every store OFF (see tests/conftest.py), which is correct and also
means the MySQL/Redis/Neo4j code paths were the ones nothing covered.

WHAT BELONGS HERE
-----------------
Only things that CANNOT be true on the SQLite/in-memory fallbacks:

  * the MySQL dialect rewrites in `db.core.translate()` — upserts and the
    back-quoted reserved word `signal` are no-ops on SQLite, so a mistake there
    is invisible until it reaches RDS;
  * `GET_LOCK` advisory locking, which on SQLite is the file lock instead;
  * the historian's window-function queries, which are why MySQL 8 (not 5.7,
    not MariaDB) is the stated floor;
  * schema reconcile against a live information_schema;
  * the migration ledger against a real database.

Everything here skips — not fails — when a store is absent, so `pytest -m
integration` on a laptop with nothing running is quiet. CI sets the URLs, so a
genuine breakage is red there. See the `integration` job in
.github/workflows/ci.yml.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.integration


def _tenant() -> str:
    """A unique tenant per test, so a re-run never collides with its own rows."""
    return f"it-{uuid.uuid4().hex[:12]}"


# ── The relational backend really is MySQL ─────────────────────────────────

def test_dialect_is_mysql_and_server_is_8_or_newer(live_mysql):
    """MySQL 8.0+ is a hard floor, not a preference: the historian's `latest()`
    and rollup queries use `ROW_NUMBER() OVER (...)`, which 5.7 and MariaDB do
    not have. A deploy onto 5.7 would provision cleanly and then fail on the
    first history read, so assert the version where it is cheap to see."""
    db = live_mysql
    assert db.is_mysql()
    assert db.dialect() == db.MYSQL

    with db.connect("twins") as conn:
        version = str(dict(conn.execute("SELECT VERSION() AS v").fetchone())["v"])

    major = int(version.split(".", 1)[0])
    assert "mariadb" not in version.lower(), (
        f"MariaDB is not supported — window functions differ. Got {version!r}")
    assert major >= 8, f"MySQL 8.0+ required, got {version!r}"


def test_every_declared_store_provisions(live_mysql):
    """`db.schema.ensure()` must bring every store up on a real MySQL. This is
    the check that catches a `TEXT` column being used as a key — legal on SQLite,
    rejected by InnoDB, and therefore never seen by the fast suite."""
    db = live_mysql
    from db import schema

    for store in schema.DDL:
        schema.ensure(store, strict=True)      # raises if the DDL is not valid

    with db.connect("twins") as conn:
        rows = conn.execute(
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE()").fetchall()
    live = {str(dict(r)["TABLE_NAME"]).lower() for r in rows}

    # A representative table from several different stores, rather than all of
    # them: this asserts the loop above actually created things, without
    # duplicating schema.py's contents into the test.
    #
    # `measurements` is deliberately NOT in this list. The historian provisions
    # itself (`historian.schema.ensure()`, via `python -m tools.historian_provision`)
    # rather than through db/schema.py, so a deploy needs both steps — which is
    # exactly why they are separate lines in AWS_DEPLOYMENT.md §7.1.
    for table in ("twins", "events", "users"):
        assert table in live, f"{table} missing after ensure(); have {sorted(live)}"


def test_ensure_is_idempotent_and_reconcile_finds_nothing_new(live_mysql):
    """Re-provisioning a database that is already current must add no columns.

    `reconcile()` ADDs any declared column a live table lacks. Run against a
    freshly-ensured schema it should find nothing — if it reports a column, then
    schema.py declares something the CREATE TABLE path did not produce, which is
    the drift that used to break every existing database."""
    from db import schema

    schema.ensure("twins", strict=True)
    schema.reset_cache()
    schema.ensure("twins", strict=True)        # second pass: must be a no-op

    import db
    with db.connect("twins") as conn:
        for stmt in schema.render("twins"):
            added = schema.reconcile(conn, stmt)
            assert added == [], (
                f"reconcile added {added} to an already-current schema — "
                f"db/schema.py and the live table disagree")


# ── The dialect rewrites, which are invisible on SQLite ────────────────────

def test_upsert_rewrites_and_round_trips(live_mysql):
    """`ON CONFLICT ... DO UPDATE` is SQLite syntax; MySQL needs `ON DUPLICATE
    KEY UPDATE`. `translate()` rewrites it centrally so no call site was edited
    during the migration — which means a bug in that rewrite would surface as
    "every write in the platform fails", and only against MySQL."""
    db = live_mysql
    from db import schema

    schema.ensure("twins", strict=True)
    tenant = _tenant()
    sql = ("INSERT INTO twins (tenant_id, name, domain, created_at) "
           "VALUES (?, ?, ?, ?) "
           "ON CONFLICT (tenant_id) DO UPDATE SET name = excluded.name")

    with db.connect("twins") as conn:
        conn.execute(sql, (tenant, "first", "test",
                           datetime.now(UTC).isoformat()))
        conn.commit()

        # The same statement again with a different name: the row must be
        # UPDATED, not duplicated and not rejected.
        conn.execute(sql, (tenant, "second", "test",
                           datetime.now(UTC).isoformat()))
        conn.commit()

        rows = conn.execute(
            "SELECT name FROM twins WHERE tenant_id = ?", (tenant,)).fetchall()

    assert len(rows) == 1, "upsert inserted a duplicate instead of updating"
    assert dict(rows[0])["name"] == "second"


def test_insert_ignore_form_round_trips(live_mysql):
    """The other upsert shape: `ON CONFLICT ... DO NOTHING` -> `INSERT IGNORE`.
    It must not raise on the second insert and must not change the first row."""
    db = live_mysql
    from db import schema

    schema.ensure("twins", strict=True)
    tenant = _tenant()
    sql = ("INSERT INTO twins (tenant_id, name, domain, created_at) "
           "VALUES (?, ?, ?, ?) ON CONFLICT (tenant_id) DO NOTHING")

    with db.connect("twins") as conn:
        conn.execute(sql, (tenant, "original", "test",
                           datetime.now(UTC).isoformat()))
        conn.commit()
        conn.execute(sql, (tenant, "ignored", "test",
                           datetime.now(UTC).isoformat()))
        conn.commit()
        rows = conn.execute(
            "SELECT name FROM twins WHERE tenant_id = ?", (tenant,)).fetchall()

    assert len(rows) == 1
    assert dict(rows[0])["name"] == "original", "DO NOTHING overwrote the row"


def test_json_column_round_trips_as_a_structure(live_mysql):
    """`field_changes`/`payload`/`state` are real `JSON` columns. The point of
    the type is that the value comes back as a structure, not as text — a
    silently stringified column would still "work" until something queried into
    it."""
    db = live_mysql
    from db import Json, json_load, schema

    schema.ensure("changelog", strict=True)
    tenant = _tenant()
    payload = {"nested": {"n": 1}, "list": [1, 2, 3], "unicode": "°C ✓"}

    with db.connect("changelog") as conn:
        conn.execute(
            "INSERT INTO events (event_id, tenant_id, entity_id, entity_type, "
            "actor, action, field_changes, ts, prev_event_hash, wm_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"evt_{uuid.uuid4().hex[:12]}", tenant, "ent-1", "Asset",
             "test", "update", Json(payload),
             datetime.now(UTC).isoformat(), "genesis", "wm"))
        conn.commit()
        row = conn.execute(
            "SELECT field_changes FROM events WHERE tenant_id = ?",
            (tenant,)).fetchone()

    assert json_load(dict(row)["field_changes"]) == payload


def test_reserved_word_signal_is_quoted(live_mysql):
    """`signal` is reserved in MySQL 8. `translate()` back-quotes it centrally;
    without that every historian query is a syntax error. Written as a plain
    unquoted identifier here on purpose — that is how the call sites write it."""
    db = live_mysql
    import historian
    from historian import schema as hschema

    hschema.ensure()
    tenant = _tenant()

    with db.connect("historian") as conn:
        rows = conn.execute(
            f"SELECT signal FROM {hschema.RAW_TABLE} WHERE tenant_id = ? "
            f"AND signal = ? LIMIT 1", (tenant, "nope")).fetchall()
    assert rows == [] or rows is not None
    assert historian.backend() == "mysql"


def test_advisory_lock_is_acquired_and_released(live_mysql):
    """`GET_LOCK` is what serialises change-log writers across tasks. It is
    session-scoped, so the release path matters as much as the acquire: a lock
    leaked back into the pool would deadlock the next writer to reuse that
    connection."""
    db = live_mysql
    name = f"nxr-test-{uuid.uuid4().hex[:8]}"

    with db.connect("changelog") as conn:
        conn.lock(name)
        held = conn.execute(
            "SELECT IS_USED_LOCK(?) IS NOT NULL AS held", (name,)).fetchone()
        assert dict(held)["held"], "GET_LOCK did not take the lock"

    # Out of the `with`, the connection went back to the pool and the lock must
    # have been released with it.
    with db.connect("changelog") as conn:
        free = conn.execute(
            "SELECT IS_FREE_LOCK(?) AS free", (name,)).fetchone()
        assert dict(free)["free"], (
            "the advisory lock survived the connection returning to the pool — "
            "the next writer on this connection would block")


def test_long_lock_names_are_hashed_under_the_64_char_limit(live_mysql):
    """MySQL rejects a lock name over 64 characters. A tenant id long enough to
    trip that is a plausible customer, not a hypothetical."""
    db = live_mysql
    from db.core import _lock_name

    long_name = "changelog:" + ("t" * 200)
    short = _lock_name(long_name)
    assert len(short) <= 64
    assert _lock_name(long_name) == short, "the hash must be stable"

    with db.connect("changelog") as conn:
        conn.lock(long_name)                   # must not raise


# ── The historian, which is the reason MySQL 8 is the floor ────────────────

def test_historian_write_then_read_back(live_mysql):
    """The full cycle against a real table: write a batch, read the latest
    value, and pull the history. `latest()` uses `ROW_NUMBER() OVER (...)`."""
    import historian
    from historian import schema as hschema

    hschema.ensure()
    tenant, asset = _tenant(), "pump-1"
    now = datetime.now(UTC).replace(microsecond=0)

    batch = [
        historian.Measurement(tenant_id=tenant, asset_id=asset,
                              signal="temperature", ts=now - timedelta(minutes=i),
                              value=20.0 + i, unit="DEG_C")
        for i in range(5)
    ]
    result = historian.write(batch)
    assert result.accepted == 5, f"rejected: {result.rejected}"

    latest = historian.latest(tenant, asset)
    assert len(latest) == 1, latest
    assert latest[0]["signal"] == "temperature"
    assert float(latest[0]["value"]) == pytest.approx(20.0), (
        "latest() returned something other than the newest sample")

    series = historian.history(tenant, asset, "temperature",
                               start=now - timedelta(hours=1), end=now)
    assert len(series.points) >= 1

    stats = historian.tenant_stats(tenant)
    assert stats["samples"] == 5
    assert stats["assets"] == 1

    inventory = historian.signals(tenant)
    assert any(s["signal"] == "temperature" for s in inventory)


def test_historian_rejects_a_bad_sample_without_losing_the_batch(live_mysql):
    """One malformed reading must not discard the good ones alongside it — a
    device sending a single NaN would otherwise drop a whole batch."""
    import historian
    from historian import schema as hschema

    hschema.ensure()
    tenant = _tenant()
    now = datetime.now(UTC)

    result = historian.write([
        historian.Measurement(tenant_id=tenant, asset_id="a", signal="good",
                              ts=now, value=1.0, unit="X"),
        historian.Measurement(tenant_id="", asset_id="a", signal="bad",
                              ts=now, value=2.0, unit="X"),
    ])
    assert result.accepted == 1
    assert len(result.rejected) == 1


def test_historian_isolates_tenants(live_mysql):
    """Two tenants writing the same asset and signal names must not see each
    other. This is the store-level half of the isolation the API enforces."""
    import historian
    from historian import schema as hschema

    hschema.ensure()
    a, b = _tenant(), _tenant()
    now = datetime.now(UTC)

    historian.write([
        historian.Measurement(tenant_id=a, asset_id="shared", signal="v",
                              ts=now, value=1.0),
        historian.Measurement(tenant_id=b, asset_id="shared", signal="v",
                              ts=now, value=2.0),
    ])

    only_a = historian.latest(a, "shared")
    assert len(only_a) == 1
    assert float(only_a[0]["value"]) == pytest.approx(1.0), (
        "tenant A read tenant B's measurement")


# ── Migrations against a real ledger ───────────────────────────────────────

def test_migrations_apply_and_are_idempotent(live_mysql):
    """Apply the chain, then apply it again. The second run must be a no-op —
    a migration that re-runs on every deploy is one that will eventually run
    against data it did not expect."""
    from db import migrations

    migrations.apply_all()
    assert migrations.pending() == [], (
        f"still pending after apply_all: {[m.migration_id for m in migrations.pending()]}")

    applied_again = migrations.apply_all()
    assert applied_again == [], f"re-applied {applied_again} on a current database"


def test_migrations_verify_agrees_with_schema(live_mysql):
    """`--verify` compares db/schema.py against what the chain actually built.
    A disagreement means a fresh database and a migrated one differ, which is
    the bug class that only ever appears in production."""
    from db import migrations

    migrations.apply_all()
    problems = migrations.verify()
    assert problems == [], f"schema.py and the migration chain disagree: {problems}"


# ── The other two stores ───────────────────────────────────────────────────

def test_redis_bus_publishes_and_reads_back(live_redis):
    """With NXR_REDIS_URL set the bus must resolve to Redis Streams, not the
    in-memory fallback. The fallback is per-process, so a deploy that lands on it
    silently stops delivering live updates to every task but one."""
    from bus import event_bus

    event_bus.reset_event_bus()
    try:
        bus = event_bus.get_event_bus()
        backend = getattr(bus, "backend", None) or type(bus).__name__
        assert "redis" in str(backend).lower(), (
            f"NXR_REDIS_URL is set but the bus resolved to {backend!r} — "
            f"this is the silent per-task fallback")

        tenant = _tenant()
        event = event_bus.BusEvent(
            event_id=f"evt_{uuid.uuid4().hex[:12]}", tenant_id=tenant,
            entity_id="ent-1", entity_type="Asset", label="probe",
            action="update", actor="integration-test",
            ts=datetime.now(UTC).isoformat(),
            field_changes={"n": 1})

        msg_id = bus.publish(event)
        assert msg_id, "publish() returned no message id — the event was skipped"

        received = bus.read(tenant, count=10)
        assert received, "published to Redis but read nothing back"
        ids = [ev.event_id for _mid, ev in received]
        assert event.event_id in ids, f"{event.event_id} not in {ids}"

        # The round trip must preserve the nested structure, not stringify it —
        # to_wire() JSON-encodes it and from_wire() has to undo that.
        delivered = next(ev for _mid, ev in received
                         if ev.event_id == event.event_id)
        assert delivered.field_changes == {"n": 1}
    finally:
        event_bus.reset_event_bus()


def test_neo4j_round_trips_a_node(live_neo4j):
    """Neo4j is the twin's graph and is required, not optional. A write and a
    read-back proves credentials and connectivity together."""
    connection = live_neo4j
    driver = connection.get_driver()
    marker = f"it-{uuid.uuid4().hex[:12]}"

    with driver.session() as session:
        session.run(
            "CREATE (n:IntegrationProbe {marker: $marker, at: timestamp()})",
            marker=marker)
        try:
            found = session.run(
                "MATCH (n:IntegrationProbe {marker: $marker}) RETURN count(n) AS n",
                marker=marker).single()
            assert found["n"] == 1
        finally:
            session.run(
                "MATCH (n:IntegrationProbe {marker: $marker}) DELETE n",
                marker=marker)
