"""db — the twin's relational store.

RDS MySQL 8 in production, SQLite locally. One import surface:

    from db import connect, Json, json_load, schema

    schema.ensure("twins")
    with connect("twins") as conn:
        row = conn.execute("SELECT * FROM twins WHERE tenant_id = ?", (t,)).fetchone()

See `db/core.py` for the dialect rules and `db/schema.py` for every table.
Use `python -m db.schema` to provision and `python -m db.migrations` to apply
ordered schema changes.
"""
from __future__ import annotations

from . import schema  # noqa: F401  (re-exported: `from db import schema`)
from .core import (  # noqa: F401
    MYSQL,
    SQLITE,
    Conn,
    Json,
    close_pool,
    connect,
    database_url,
    dialect,
    info,
    is_mysql,
    json_load,
    log_posture,
    ping,
    redacted_url,
)

__all__ = [
    "MYSQL", "SQLITE", "Conn", "Json", "close_pool",
    "connect", "database_url", "dialect", "info", "is_mysql", "json_load",
    "log_posture", "ping", "redacted_url", "schema",
]
