"""db — the twin's relational store.

RDS PostgreSQL 16 in production, SQLite locally. One import surface:

    from db import connect, Json, json_load, schema

    schema.ensure("twins")
    with connect("twins") as conn:
        row = conn.execute("SELECT * FROM twins WHERE tenant_id = ?", (t,)).fetchone()

See `db/core.py` for the dialect rules and `db/schema.py` for every table.
Use `python -m db.schema` to provision and `python -m db.migrate` to copy an
existing SQLite deployment into Postgres.
"""
from __future__ import annotations

from . import schema  # noqa: F401  (re-exported: `from db import schema`)
from .core import (  # noqa: F401
    POSTGRES,
    SQLITE,
    Conn,
    Json,
    advisory_key,
    close_pool,
    connect,
    database_url,
    dialect,
    info,
    is_postgres,
    json_load,
    log_posture,
    ping,
    redacted_url,
)

__all__ = [
    "POSTGRES", "SQLITE", "Conn", "Json", "advisory_key", "close_pool",
    "connect", "database_url", "dialect", "info", "is_postgres", "json_load",
    "log_posture", "ping", "redacted_url", "schema",
]
