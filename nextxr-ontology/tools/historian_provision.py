#!/usr/bin/env python3
"""
historian_provision.py — create the telemetry historian's schema.

    python -m tools.historian_provision              # create everything
    python -m tools.historian_provision --check      # report, change nothing
    python -m tools.historian_provision --extension  # + CREATE EXTENSION timescaledb
    python -m tools.historian_provision --drop --yes # destroy all measurements

WHY THIS IS A SEPARATE STEP FROM BOOT
-------------------------------------
The app never provisions the historian on startup, unlike `db/schema.ensure()`.
Three reasons, all learned from the rest of this codebase:

  * `CREATE EXTENSION timescaledb` needs elevated rights (rds_superuser). The
    task role deliberately does not have them, so a boot-time attempt would fail
    on every start and log noise forever.

  * Creating a hypertable can MIGRATE existing rows. That is a data operation, and
    a data operation must be a decision someone makes, not something six Fargate
    tasks race each other to perform during a rolling deploy.

  * Continuous aggregates and their policies cannot run inside a transaction, so
    they need their own connection handling (see historian/schema.py). Boot-time
    DDL is expected to be cheap and idempotent; this is neither.

Run it once per environment before the first deploy, and again after upgrading
TimescaleDB. It IS idempotent, so re-running is safe.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import db                                          # noqa: E402
import historian                                   # noqa: E402
from historian import schema as hschema            # noqa: E402


def _print_report(report: dict) -> None:
    print(f"  backend           : {report.get('backend')}")
    if report.get("timescale_version"):
        print(f"  timescale version : {report['timescale_version']}")
    for item in report.get("created", []):
        print(f"  created           : {item}")
    for item in report.get("skipped", []):
        print(f"  skipped           : {item}")
    for stmt, err in report.get("failures", []):
        print(f"  FAILED            : {stmt}\n                      {err}")


def _create_extension() -> bool:
    """`CREATE EXTENSION IF NOT EXISTS timescaledb`. Needs rds_superuser."""
    if not db.is_postgres():
        print("  extension         : skipped (SQLite backend)")
        return False
    try:
        with db.connect("historian") as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")
        hschema.reset_cache()
        print("  extension         : timescaledb enabled")
        return True
    except Exception as e:
        print(f"  extension         : FAILED — {str(e)[:300]}")
        print("                      This needs rds_superuser. On RDS, connect as "
              "the master user and run it there; on self-managed Postgres the "
              "shared_preload_libraries entry must also be set.")
        return False


def check() -> int:
    print("Historian status")
    print(f"  relational store  : {db.dialect()} ({db.redacted_url() or 'local file'})")
    print(f"  backend           : {historian.backend()}")
    print(f"  timescale present : {historian.timescale_available(force=True)}")
    if historian.timescale_version():
        print(f"  timescale version : {historian.timescale_version()}")
    print(f"  required by config: {historian.timescale_required()}")
    ok, detail = historian.ping()
    print(f"  usable            : {ok} ({detail})")
    info = historian.info()
    print(f"  compression       : {info.get('compression')}")
    print(f"  rollups           : {info.get('rollups') or 'none'}")
    if info.get("retention_days"):
        print(f"  retention (days)  : {info['retention_days']}")
    if not info.get("compression"):
        print()
        print("  WARNING: no compression and no retention policy. Telemetry will "
              "grow without bound and trend queries will scan raw rows. Run this "
              "tool with --extension against a superuser connection.")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="report current state; create nothing")
    parser.add_argument("--extension", action="store_true",
                        help="CREATE EXTENSION timescaledb first (needs superuser)")
    parser.add_argument("--drop", action="store_true",
                        help="DESTROY every measurement (requires --yes)")
    parser.add_argument("--yes", action="store_true",
                        help="confirm a destructive operation")
    parser.add_argument("--strict", action="store_true",
                        help="exit non-zero on any provisioning failure")
    args = parser.parse_args()

    if args.check:
        return check()

    if args.drop:
        if not args.yes:
            print("Refusing to drop the historian without --yes.")
            print(f"This would DELETE every measurement in "
                  f"{db.redacted_url() or 'the local SQLite file'}.")
            return 2
        print(f"Dropping historian tables in "
              f"{db.redacted_url() or 'the local SQLite file'} ...")
        historian.drop_all()
        print("  dropped.")
        return 0

    print(f"Provisioning historian in {db.redacted_url() or 'the local SQLite file'}")
    if args.extension:
        _create_extension()

    report = historian.ensure(strict=args.strict)
    _print_report(report)

    if report.get("failures"):
        return 1
    if historian.backend() != "timescale" and db.is_postgres():
        print()
        print("Done, but WITHOUT TimescaleDB: no hypertable, no continuous "
              "aggregates, no compression, no retention. Re-run with --extension "
              "as a superuser to get them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
