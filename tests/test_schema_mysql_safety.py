"""test_schema_mysql_safety.py — the DDL must be valid on the backend that ships.

Production is RDS MySQL 8; the fast test suite and local dev are SQLite. That
asymmetry hides a whole class of bug, because SQLite accepts DDL that MySQL
rejects outright — and the failure does not surface until a deploy, where
`schema.ensure_all(strict=True)` in migration 0001 turns one bad index into "the
entire migration chain cannot run".

Both bugs these tests were written for were exactly that shape:

  * `hub_sso_jti(expires_at)` indexed a bare TEXT column. MySQL errno 1170.
    Shipped, and blocked every MySQL migration from the moment hub SSO landed.
  * `tasks(org_id, status)` and the UNIQUE `xp_ledger(user_id, task_id, reason)`
    named `{str}` columns, which render VARCHAR(1024). At utf8mb4's 4 bytes per
    character that is 4096 bytes for one column against InnoDB's 3072-byte limit
    for the whole key. MySQL errno 1071.

The second one mattered beyond provisioning: that UNIQUE index IS the guard that
stops XP being awarded twice (`store.add_xp` relies on it for its ON CONFLICT).
Without it the constraint silently would not exist on the production backend.

These tests render the DDL for MySQL and check it statically, so they run in the
fast suite with no MySQL present.
"""

from __future__ import annotations

import re

import pytest
from db import core, schema

#: InnoDB's maximum index key length, DYNAMIC row format.
MAX_KEY_BYTES = 3072
#: utf8mb4 — the charset docker-compose.yml pins the server to.
BYTES_PER_CHAR = 4


def _column_types(ddl: list[str]) -> dict[str, dict[str, str]]:
    """table -> {column: rendered type}, parsed from the CREATE TABLE statements."""
    tables: dict[str, dict[str, str]] = {}
    for stmt in ddl:
        m = re.match(r"\s*CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*)\)\s*$", stmt, re.S)
        if not m:
            continue
        cols: dict[str, str] = {}
        for line in m.group(2).split(","):
            parts = line.strip().split()
            if (len(parts) >= 2 and parts[0].isidentifier()
                    and parts[0].upper() not in ("PRIMARY", "UNIQUE", "KEY",
                                                 "CONSTRAINT", "FOREIGN")):
                cols[parts[0]] = parts[1]
        tables[m.group(1)] = cols
    return tables


def _key_bytes(col_type: str) -> int:
    """Index-key cost of one column on MySQL. TEXT/JSON are unindexable."""
    m = re.match(r"VARCHAR\((\d+)\)", col_type or "", re.I)
    if m:
        return int(m.group(1)) * BYTES_PER_CHAR
    if (col_type or "").upper() in ("TEXT", "JSON", "BLOB"):
        return 10 ** 6            # sentinel: cannot be indexed without a prefix
    return 8                       # numeric / datetime


def _indexes(ddl: list[str]):
    """(name, table, [columns]) for every CREATE INDEX and composite PRIMARY KEY."""
    out = []
    for stmt in ddl:
        m = re.match(r"\s*CREATE (?:UNIQUE )?INDEX (?:IF NOT EXISTS )?(\w+) "
                     r"ON (\w+) \(([^)]*)\)", stmt, re.I)
        if m:
            cols = [c.strip().split()[0] for c in m.group(3).split(",")]
            out.append((m.group(1), m.group(2), cols))
            continue
        tm = re.match(r"\s*CREATE TABLE IF NOT EXISTS (\w+)", stmt)
        pk = re.search(r"PRIMARY KEY \(([^)]+)\)", stmt)
        if tm and pk:
            cols = [c.strip() for c in pk.group(1).split(",")]
            out.append((f"{tm.group(1)}_PRIMARY", tm.group(1), cols))
    return out


ALL_STORES = sorted(schema.DDL.keys())


@pytest.mark.parametrize("store", ALL_STORES)
def test_no_index_exceeds_the_mysql_key_limit(store):
    ddl = schema.render(store, backend=core.MYSQL)
    types = _column_types(ddl)

    problems = []
    for name, table, cols in _indexes(ddl):
        total = sum(_key_bytes(types.get(table, {}).get(c, "")) for c in cols)
        if total > MAX_KEY_BYTES:
            detail = ", ".join(f"{c}={types.get(table, {}).get(c, '?')}" for c in cols)
            problems.append(f"{table}.{name}({detail}) = {total} bytes")

    assert not problems, (
        "index key too long for MySQL (InnoDB limit "
        f"{MAX_KEY_BYTES} bytes, utf8mb4):\n  " + "\n  ".join(problems)
        + "\n\nUse the {id} or {code} token for any column named in an index; "
          "{str} renders VARCHAR(1024) and cannot fit.")


@pytest.mark.parametrize("store", ALL_STORES)
def test_no_index_names_an_unindexable_column(store):
    """MySQL refuses TEXT/BLOB/JSON in a key without an explicit prefix length.

    Split from the length check because the failure mode is different (errno
    1170 rather than 1071) and so is the fix: a TEXT column in an index is
    always a declaration mistake, never something to shorten.
    """
    ddl = schema.render(store, backend=core.MYSQL)
    types = _column_types(ddl)

    problems = []
    for name, table, cols in _indexes(ddl):
        for c in cols:
            declared = types.get(table, {}).get(c, "")
            if declared.upper() in ("TEXT", "JSON", "BLOB"):
                problems.append(f"{table}.{name} indexes {c} declared {declared}")

    assert not problems, (
        "MySQL cannot index TEXT/JSON/BLOB without a prefix length:\n  "
        + "\n  ".join(problems)
        + "\n\nDeclare the column {id} (or {code} for a short vocabulary).")


def test_the_xp_uniqueness_guard_exists_on_both_backends():
    """`store.add_xp` leans on this index for its ON CONFLICT DO NOTHING.

    Asserted by name and by column set, on BOTH renderings: if it ever stops
    being created on MySQL, double-award protection quietly disappears there
    while every SQLite test keeps passing.
    """
    for backend in (core.SQLITE, core.MYSQL):
        ddl = schema.render("work", backend=backend)
        found = [ix for ix in _indexes(ddl) if ix[0] == "idx_xp_task_reason"]
        assert found, f"idx_xp_task_reason missing on {backend}"
        _, table, cols = found[0]
        assert table == "xp_ledger"
        assert cols == ["user_id", "task_id", "reason"]

        unique = any("UNIQUE" in s.upper() and "idx_xp_task_reason" in s for s in ddl)
        assert unique, f"idx_xp_task_reason is not UNIQUE on {backend}"


def test_every_work_table_is_declared_for_both_backends():
    """A store that renders on one backend and not the other is a deploy bug."""
    expected = {"teams", "team_members", "tasks", "task_events", "xp_ledger",
                "work_rules", "scenario_runs"}
    for backend in (core.SQLITE, core.MYSQL):
        rendered = _column_types(schema.render("work", backend=backend))
        assert expected <= set(rendered), (
            f"missing on {backend}: {sorted(expected - set(rendered))}")


def test_no_ddl_token_is_left_unrendered():
    """An unknown `{token}` would reach the database as a literal brace.

    `render()` formats with `_T[backend]`, so a typo like `{cod}` raises KeyError
    there — but a token that renders to an empty string, or a stray brace in a
    comment, would sail through and produce invalid SQL at deploy time.
    """
    for store in ALL_STORES:
        for backend in (core.SQLITE, core.MYSQL):
            for stmt in schema.render(store, backend=backend):
                assert "{" not in stmt and "}" not in stmt, (
                    f"unrendered token in {store} ({backend}): {stmt[:120]}")
