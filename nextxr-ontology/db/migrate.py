"""migrate.py — RETIRED.

This was a one-shot importer that copied a local SQLite deployment into RDS
PostgreSQL at cutover. The platform's relational backend is now MySQL, and this
tool's Postgres-specific machinery (sequence reset via
`setval(pg_get_serial_sequence(...))`, `ON CONFLICT` upserts, JSONB adapters) has
no MySQL equivalent, so it is retired rather than ported.

Provisioning a fresh MySQL database and applying ordered schema changes is done
with the two tools that remain:

    python -m db.schema        # create every table on the configured database
    python -m db.migrations    # apply ordered, recorded schema changes

If you ever need to lift data from an old SQLite dev instance into MySQL, do it
with a purpose-built export/import (e.g. mysqldump-compatible INSERTs or a small
row-copy script) rather than this file — there is no supported SQLite→MySQL
cutover path in the standalone deploy.
"""
from __future__ import annotations

import sys


def main() -> int:
    print(__doc__)
    print("This command is retired. Use `python -m db.schema` and "
          "`python -m db.migrations`.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
