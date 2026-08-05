"""db_admin_routes.py — inspect and apply schema changes on the LIVE database.

WHAT THIS REMOVES, AND WHAT IT HONESTLY CANNOT
-----------------------------------------------
Applying a schema change to RDS used to mean a one-off ECS task (or an
`ecs execute-command` shell) running `python -m db.migrations`. That is a second
deploy-shaped ceremony on top of the image rollout, and it is the step people
skip — which is how a fleet ends up serving queries against a column that is not
there. These routes let an operator do the same thing over HTTP against the
running service: check status, dry-run, apply, and verify.

What it does NOT do is let you change the schema WITHOUT shipping code, and that
limit is deliberate rather than unfinished. `db/schema.py` is the definition of
every table; a change to it is a code change, reviewed and versioned like any
other, and it reaches AWS the way code does. An endpoint that accepted arbitrary
DDL over HTTP would move the definition of your database out of version control
and into whatever the last caller happened to POST — with `DROP TABLE` one
request away and no review, no ledger and no rollback story. So there is no
free-form SQL route here, and adding one would undo the property that makes the
rest of this module safe.

THE PART THAT USUALLY SURPRISES PEOPLE: for most changes there is no migration
to write at all. `schema.ensure()` reconciles — it creates tables that do not
exist AND adds declared columns that an existing table is missing. So:

    add a new table      → declare it in db/schema.py, then POST .../reconcile
    add a column         → declare it in db/schema.py, then POST .../reconcile
    change/remove/rename → write a migration; it needs a data decision

Only the third case needs `db/migrations.py`, because only it can lose data.

OFF BY DEFAULT. Set NXR_DB_ADMIN_API=1 to mount these. They execute DDL, so they
are gated twice: the env flag decides whether the surface exists at all, and an
admin scope decides who may call it.

    GET  /api/v1/admin/db/status      dialect, reachability, applied vs pending
    GET  /api/v1/admin/db/verify      does schema.py agree with the migrations?
    POST /api/v1/admin/db/reconcile   create missing tables + add missing columns
    POST /api/v1/admin/db/migrate     apply pending migrations (dry_run supported)
"""
from __future__ import annotations

import os
import time

from fastapi import APIRouter, HTTPException, Query, Request

from server.tenancy import scope_of

router = APIRouter(prefix="/api/v1/admin/db", tags=["admin", "db"])


def enabled() -> bool:
    """Whether the deployment opted into a DDL surface over HTTP at all."""
    return str(os.environ.get("NXR_DB_ADMIN_API", "")).strip().lower() in (
        "1", "true", "yes", "on")


def _guard(request: Request) -> None:
    """Both gates, in the order that leaks the least.

    The env check comes first and returns 404, not 403: a deployment that never
    enabled this should look like it has no such route, rather than advertising
    an admin DDL endpoint to anyone who probes for it.
    """
    if not enabled():
        raise HTTPException(404, "not found")
    if not scope_of(request).is_admin:
        raise HTTPException(403, "admin scope required for schema operations")


@router.get("/status")
def status(request: Request) -> dict:
    _guard(request)
    from db import core, migrations

    reachable, detail = core.ping()
    applied = migrations.applied_ids() if reachable else set()
    return {
        "backend": core.dialect(),
        "database": core.redacted_url() if core.is_mysql() else "sqlite (local)",
        "reachable": reachable,
        "detail": None if reachable else detail,
        "auto_migrate_on_boot": migrations.auto_migrate_enabled(),
        "migrations": [
            {
                "id": m.migration_id,
                "description": m.description,
                "status": "applied" if m.migration_id in applied else "pending",
            }
            for m in migrations.MIGRATIONS
        ],
        "pending_count": sum(
            1 for m in migrations.MIGRATIONS if m.migration_id not in applied),
    }


@router.get("/verify")
def verify(request: Request) -> dict:
    """Drift check: would a fresh database and an upgraded one come out the same?

    This is the check worth running BEFORE apply, not after. A column added to
    `schema.py` with no matching migration works on every fresh database — every
    developer's, every CI run's — and is missing only on the one that already
    existed, which is production.
    """
    _guard(request)
    from db import migrations

    problems = migrations.verify()
    return {"ok": not problems, "problems": problems}


@router.post("/reconcile")
def reconcile(request: Request) -> dict:
    """Create missing tables and ADD declared-but-missing columns.

    This is the whole answer for additive changes. It is idempotent, so calling
    it when nothing changed is a no-op, and it only ever ADDs — nothing here
    drops, renames or retypes, because each of those needs a data decision and
    belongs in a migration.
    """
    _guard(request)
    from db import core, schema

    reachable, detail = core.ping()
    if not reachable:
        raise HTTPException(503, f"database unreachable: {detail}")

    started = time.monotonic()
    try:
        schema.reset_cache()          # ensure() caches per process; re-read the DB
        schema.ensure_all(strict=True)
    except Exception as e:            # noqa: BLE001
        raise HTTPException(500, f"{type(e).__name__}: {e}") from e
    return {
        "ok": True,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "note": "tables created and missing declared columns added where needed",
    }


@router.post("/migrate")
def migrate(
    request: Request,
    dry_run: bool = Query(
        True,
        description="Default TRUE. Applying schema changes is not something an "
                    "endpoint should do because a parameter was omitted."),
) -> dict:
    _guard(request)
    from db import core, migrations

    reachable, detail = core.ping()
    if not reachable:
        raise HTTPException(503, f"database unreachable: {detail}")

    todo = [m.migration_id for m in migrations.pending()]
    if not todo:
        return {"ok": True, "dry_run": dry_run, "applied": [], "pending": [],
                "note": "up to date"}

    started = time.monotonic()
    try:
        applied = migrations.apply_all(dry_run=dry_run)
    except Exception as e:            # noqa: BLE001
        # Report what is STILL pending, because that is the fact you act on. A
        # partial failure leaves earlier migrations applied — they are recorded,
        # and re-running resumes rather than repeating.
        raise HTTPException(500, {
            "error": f"{type(e).__name__}: {e}",
            "still_pending": [m.migration_id for m in migrations.pending()],
        }) from e

    return {
        "ok": True,
        "dry_run": dry_run,
        "applied": [] if dry_run else applied,
        "would_apply": todo if dry_run else [],
        "duration_ms": int((time.monotonic() - started) * 1000),
    }
