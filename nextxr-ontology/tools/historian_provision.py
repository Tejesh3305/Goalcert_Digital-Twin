#!/usr/bin/env python3
"""
historian_provision.py — create the telemetry historian's schema.

    python -m tools.historian_provision              # create everything
    python -m tools.historian_provision --check      # report, change nothing
    python -m tools.historian_provision --drop --yes # destroy all measurements

WHY THIS IS A SEPARATE STEP FROM BOOT
-------------------------------------
`db/schema.ensure()` provisions the relational tables on boot; the historian is
provisioned here instead so telemetry-table creation is an explicit, one-off
operation per environment rather than something several Fargate tasks race during
a rolling deploy. It IS idempotent, so re-running is safe.

On MySQL this creates one plain table (`measurements`) plus its indexes. There is
no columnar compression and no database-enforced retention — a high-volume
deployment should add an app-level purge (see `historian.retention_days`).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import db  # noqa: E402
import historian  # noqa: E402


def _print_report(report: dict) -> None:
    print(f"  backend           : {report.get('backend')}")
    for item in report.get("created", []):
        print(f"  created           : {item}")
    for item in report.get("skipped", []):
        print(f"  skipped           : {item}")
    for stmt, err in report.get("failures", []):
        print(f"  FAILED            : {stmt}\n                      {err}")


def check() -> int:
    print("Historian status")
    print(f"  relational store  : {db.dialect()} ({db.redacted_url() or 'local file'})")
    print(f"  backend           : {historian.backend()}")
    ok, detail = historian.ping()
    print(f"  usable            : {ok} ({detail})")
    info = historian.info()
    print(f"  compression       : {info.get('compression')}")
    print(f"  rollups           : {info.get('rollups') or 'none'}")
    print()
    print("  NOTE: rollups are computed per request from raw; there is no "
          "compression and no automatic retention. Add an app-level purge before "
          "taking high-volume telemetry.")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="report current state; create nothing")
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
    report = historian.ensure(strict=args.strict)
    _print_report(report)

    return 1 if report.get("failures") else 0


if __name__ == "__main__":
    raise SystemExit(main())
