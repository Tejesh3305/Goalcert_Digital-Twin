"""schema.py — every relational table the twin owns, in ONE place.

The five SQLite files this replaces each created their own tables inline, which
meant five places to keep in step with the Postgres deploy. Here each store
declares its DDL once and `ensure(store)` renders it for whichever backend is
live. Stores still call `ensure()` from their constructor, so a fresh database
self-provisions on first use — but the result is cached per process, because
`ChangeLog()` is constructed inside request handlers and a DDL round-trip per
construction would be a real cost against RDS.

Provision explicitly before a deploy (idempotent, safe to re-run):

    python -m db.schema                 # create every table
    python -m db.schema --extensions    # + pgvector / PostGIS (needs rds_superuser)
    python -m db.schema --check         # report what exists, create nothing

Dialect differences live only in `_T`. Everything else is SQL that means the
same thing in SQLite ≥3.24 and Postgres 16.
"""
from __future__ import annotations

import sys
import threading

from . import core

# Type/DDL fragments that genuinely differ between the two backends.
_T = {
    core.POSTGRES: {
        "serial_pk": "BIGSERIAL PRIMARY KEY",
        "json":      "JSONB",
        "ts":        "TIMESTAMPTZ",
        "ts_now":    "TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "float":     "DOUBLE PRECISION",
    },
    core.SQLITE: {
        "serial_pk": "INTEGER PRIMARY KEY AUTOINCREMENT",
        "json":      "TEXT",
        "ts":        "TEXT",
        "ts_now":    "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "float":     "REAL",
    },
}

# store -> list of DDL templates, applied in order. `{json}` etc. are filled
# from _T. Every statement is IF NOT EXISTS: ensure() is run on every boot.
DDL: dict[str, list[str]] = {
    "twins": [
        # `org_id` is denormalised from `org_tenants`, which stays the source of
        # truth for AUTHORIZATION — a twin is reachable because its org owns it
        # there, never because of this column. It exists so the twin listing can
        # filter by organisation in one query instead of one lookup per row.
        # See migration 0003_twin_org_owner for the upgrade path.
        """CREATE TABLE IF NOT EXISTS twins (
               tenant_id     TEXT PRIMARY KEY,
               name          TEXT NOT NULL,
               domain        TEXT NOT NULL,
               description   TEXT NOT NULL DEFAULT '',
               created_at    TEXT NOT NULL,
               seed_asset_id TEXT,
               org_id        TEXT
           )""",
        "CREATE INDEX IF NOT EXISTS idx_twins_created ON twins (created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_twins_org ON twins (org_id)",
    ],
    "changelog": [
        # `seq` orders a tenant's hash chain. Postgres gets a real sequence;
        # gaps from rolled-back transactions are fine — verification walks the
        # prev_event_hash linkage, not the numbers.
        """CREATE TABLE IF NOT EXISTS events (
               seq             {serial_pk},
               event_id        TEXT NOT NULL UNIQUE,
               tenant_id       TEXT NOT NULL,
               entity_id       TEXT NOT NULL,
               entity_type     TEXT NOT NULL,
               actor           TEXT NOT NULL,
               action          TEXT NOT NULL,
               field_changes   {json} NOT NULL,
               ts              TEXT NOT NULL,
               prev_event_hash TEXT NOT NULL,
               wm_hash         TEXT NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_events_tenant ON events (tenant_id, seq)",
        "CREATE INDEX IF NOT EXISTS idx_events_entity "
        "ON events (tenant_id, entity_id, seq)",
    ],
    "bundles": [
        """CREATE TABLE IF NOT EXISTS published_bundles (
               bundle_id   TEXT PRIMARY KEY,
               name        TEXT NOT NULL,
               domains     {json} NOT NULL,
               payload     {json} NOT NULL,
               tenant_id   TEXT,
               created_at  {ts_now}
           )""",
    ],
    "checkpoints": [
        """CREATE TABLE IF NOT EXISTS checkpoints (
               thread_id   TEXT PRIMARY KEY,
               graph_name  TEXT NOT NULL,
               state       {json} NOT NULL,
               resume_at   TEXT,
               updated_at  {ts_now}
           )""",
    ],
    # The BIM scene cache. On disk this was one JSON file per tenant under the
    # data volume, which is per-TASK state: task A builds a twin's scene, task B
    # serves the next request and has never seen it. Moving it here is what lets
    # the 3-D viewer stay correct at desired count > 1.
    "scenes": [
        """CREATE TABLE IF NOT EXISTS scene_cache (
               tenant_id   TEXT PRIMARY KEY,
               scene       {json} NOT NULL,
               updated_at  {ts_now}
           )""",
    ],
    # 3-D reconstruction jobs (photo -> GLB). The record was job.json on the
    # task's own disk, so a browser polling job status through the load balancer
    # got a 404 whenever the poll landed on a task that had not run the job. The
    # heavy outputs live in the blob store (storage/); this is only the record.
    "threed": [
        """CREATE TABLE IF NOT EXISTS threed_jobs (
               job_id    TEXT PRIMARY KEY,
               status    TEXT NOT NULL,
               stage     TEXT,
               filename  TEXT,
               fields    {json},
               stages    {json},
               state     {json},
               error     TEXT,
               created   {float} NOT NULL,
               updated   {float} NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_threed_updated "
        "ON threed_jobs (updated DESC)",
    ],
    # Ingest devices — the identities that push telemetry.
    #
    # A device MUST NOT authenticate with a tenant API key. Those keys are read
    # and write credentials for the whole twin: a gateway sitting in a plant room,
    # flashed onto hardware an electrician can unscrew, would then be able to read
    # every asset, drive the physics runtime and bill the LLM endpoints. A device
    # credential instead grants exactly one verb (append telemetry) in exactly
    # one tenant, and can be revoked on its own without rotating anything a human
    # uses.
    #
    # Only the token HASH is stored. A registry an operator can read back is a
    # registry an attacker can read once, so the plaintext is returned exactly
    # once at creation and never recoverable — the same posture as an SSH
    # authorized_keys file or a GitHub PAT.
    "devices": [
        """CREATE TABLE IF NOT EXISTS ingest_devices (
               device_id     TEXT PRIMARY KEY,
               tenant_id     TEXT NOT NULL,
               name          TEXT NOT NULL DEFAULT '',
               token_hash    TEXT NOT NULL,
               asset_prefix  TEXT NOT NULL DEFAULT '',
               enabled       INTEGER NOT NULL DEFAULT 1,
               created_at    TEXT NOT NULL,
               created_by    TEXT NOT NULL DEFAULT '',
               expires_at    TEXT,
               last_seen_at  TEXT,
               last_seen_ip  TEXT,
               samples_total {float} NOT NULL DEFAULT 0,
               rejected_total {float} NOT NULL DEFAULT 0
           )""",
        "CREATE INDEX IF NOT EXISTS idx_devices_tenant "
        "ON ingest_devices (tenant_id)",
        # Authentication looks a device up BY HASH, because the presented token is
        # all we have — there is no device_id on the wire to narrow it first.
        # Without this index every ingest request is a full table scan.
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_devices_token "
        "ON ingest_devices (token_hash)",
    ],
    # ── IDENTITY ────────────────────────────────────────────────────────
    #
    # Who the platform's users are, which organisation they belong to, and what
    # that organisation owns. Before this existed the ONLY credential was a JSON
    # blob in the NXR_API_KEYS environment variable: adding a customer meant
    # editing an env var and rolling the fleet, revoking one meant the same, and
    # the change log's `actor` column pointed at nothing. There was no login.
    #
    # The organisation — not the user — is the unit that owns twins. A user
    # reaches a tenant only through a membership in the org that owns it
    # (`org_tenants`), which is what lets one person belong to two customers
    # without either seeing the other's data. `server/tenancy.py` is still the
    # single enforcement point; these tables are what it now resolves against
    # instead of an environment variable.
    "identity": [
        """CREATE TABLE IF NOT EXISTS organizations (
               org_id        TEXT PRIMARY KEY,
               name          TEXT NOT NULL,
               plan          TEXT NOT NULL DEFAULT 'trial',
               status        TEXT NOT NULL DEFAULT 'active',
               tenant_prefix TEXT NOT NULL,
               settings      {json},
               created_at    TEXT NOT NULL,
               updated_at    TEXT NOT NULL
           )""",
        # Email is the login identifier, so it is stored lowercased and carries a
        # UNIQUE index rather than a UNIQUE column: the index is what makes
        # "is this address taken" a lookup instead of a scan, and both are needed.
        """CREATE TABLE IF NOT EXISTS users (
               user_id           TEXT PRIMARY KEY,
               email             TEXT NOT NULL,
               password_hash     TEXT NOT NULL,
               name              TEXT NOT NULL DEFAULT '',
               status            TEXT NOT NULL DEFAULT 'active',
               is_platform_admin INTEGER NOT NULL DEFAULT 0,
               email_verified_at TEXT,
               created_at        TEXT NOT NULL,
               updated_at        TEXT NOT NULL,
               last_login_at     TEXT,
               failed_logins     INTEGER NOT NULL DEFAULT 0,
               locked_until      TEXT,
               mfa_secret        TEXT
           )""",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users (email)",
        # A user's role is per-organisation. Someone can be an owner at their own
        # company and a read-only guest at a partner's, and one column on `users`
        # could not express that.
        """CREATE TABLE IF NOT EXISTS memberships (
               org_id     TEXT NOT NULL,
               user_id    TEXT NOT NULL,
               role       TEXT NOT NULL DEFAULT 'read',
               created_at TEXT NOT NULL,
               PRIMARY KEY (org_id, user_id)
           )""",
        "CREATE INDEX IF NOT EXISTS idx_memberships_user ON memberships (user_id)",
        # Refresh-token sessions. Only the HASH is stored, for the same reason as
        # device tokens: a table an operator can read back is a table an attacker
        # can read once. `rotated_from` is what makes reuse detection possible —
        # presenting a refresh token that has already been rotated means the token
        # was stolen, and the whole family is revoked rather than just that one.
        """CREATE TABLE IF NOT EXISTS sessions (
               session_id   TEXT PRIMARY KEY,
               user_id      TEXT NOT NULL,
               org_id       TEXT,
               refresh_hash TEXT NOT NULL,
               issued_at    TEXT NOT NULL,
               expires_at   TEXT NOT NULL,
               revoked_at   TEXT,
               rotated_from TEXT,
               ip           TEXT,
               user_agent   TEXT
           )""",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_refresh "
        "ON sessions (refresh_hash)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id)",
        # Machine credentials, moved out of NXR_API_KEYS. `key_hash` is the only
        # copy of the secret; `prefix` is the first few displayable characters so
        # the UI can say WHICH key without being able to reconstruct it — the
        # same shape as a GitHub PAT listing.
        """CREATE TABLE IF NOT EXISTS api_keys (
               key_id       TEXT PRIMARY KEY,
               org_id       TEXT NOT NULL,
               key_hash     TEXT NOT NULL,
               prefix       TEXT NOT NULL,
               name         TEXT NOT NULL DEFAULT '',
               role         TEXT NOT NULL DEFAULT 'read',
               tenants      {json},
               created_by   TEXT NOT NULL DEFAULT '',
               created_at   TEXT NOT NULL,
               expires_at   TEXT,
               revoked_at   TEXT,
               last_used_at TEXT
           )""",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys (key_hash)",
        "CREATE INDEX IF NOT EXISTS idx_api_keys_org ON api_keys (org_id)",
        # A tenant belongs to exactly ONE organisation — hence tenant_id, not a
        # composite, as the primary key. This is the join that turns "which org is
        # this user in" into "which twins may they see", and it is deliberately a
        # lookup rather than the old string-prefix convention: a prefix rule
        # cannot express a twin transferred between orgs, and silently grants
        # access to any tenant someone names with the right leading characters.
        """CREATE TABLE IF NOT EXISTS org_tenants (
               tenant_id  TEXT PRIMARY KEY,
               org_id     TEXT NOT NULL,
               created_at TEXT NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_org_tenants_org ON org_tenants (org_id)",
        # Single-use, short-lived tokens for email verification and password
        # reset. Hashed like everything else, and `used_at` makes them one-shot
        # so a reset link in a mailbox is not a standing credential.
        """CREATE TABLE IF NOT EXISTS auth_tokens (
               token_hash TEXT PRIMARY KEY,
               user_id    TEXT NOT NULL,
               purpose    TEXT NOT NULL,
               created_at TEXT NOT NULL,
               expires_at TEXT NOT NULL,
               used_at    TEXT
           )""",
        "CREATE INDEX IF NOT EXISTS idx_auth_tokens_user "
        "ON auth_tokens (user_id, purpose)",
        # WHO DID WHAT. The change log next door is the tamper-evident record of
        # what happened to the GRAPH; this is the record of what happened to the
        # ACCOUNT — logins, key issuance, role changes, revocations. Both actor
        # columns are nullable because an action has exactly one of them: a human
        # session or a machine key.
        """CREATE TABLE IF NOT EXISTS audit_log (
               audit_id     {serial_pk},
               ts           TEXT NOT NULL,
               org_id       TEXT,
               actor_user   TEXT,
               actor_key    TEXT,
               action       TEXT NOT NULL,
               target_type  TEXT NOT NULL DEFAULT '',
               target_id    TEXT NOT NULL DEFAULT '',
               outcome      TEXT NOT NULL DEFAULT 'ok',
               ip           TEXT,
               user_agent   TEXT,
               detail       {json}
           )""",
        "CREATE INDEX IF NOT EXISTS idx_audit_org ON audit_log (org_id, ts DESC)",
        "CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log (actor_user, ts DESC)",
    ],
    # Field-protocol connectors (Modbus / OPC-UA / MQTT).
    #
    # Durable configuration, not runtime state: a site commissions dozens of these
    # and expects them polling after a redeploy. The whole config — including the
    # point map — is one JSON column rather than a normalised point table, because
    # a point map is read and written as a WHOLE document (validated as a unit,
    # versioned as a unit, uploaded as a unit) and is never queried by point. A
    # 40-row join to reconstruct one device's map would buy nothing.
    #
    # Credentials live in this column too, which is why `ConnectorConfig.redacted()`
    # is allow-listed rather than deny-listed: a customer's PLC password must never
    # reach an API response because someone added a field and forgot to hide it.
    "connectors": [
        """CREATE TABLE IF NOT EXISTS connectors (
               connector_id TEXT PRIMARY KEY,
               tenant_id    TEXT NOT NULL,
               protocol     TEXT NOT NULL,
               name         TEXT NOT NULL DEFAULT '',
               enabled      INTEGER NOT NULL DEFAULT 1,
               config       {json} NOT NULL,
               created_at   TEXT NOT NULL,
               updated_at   TEXT NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_connectors_tenant "
        "ON connectors (tenant_id, enabled)",
    ],
}

# Optional Postgres extensions. Not used by any query today; they are the
# reason the architecture specifies Postgres over a plain KV store (geospatial
# station/charger positions, embedding search over the ontology). Creating them
# needs rds_superuser, so this is opt-in and non-fatal.
EXTENSIONS = ["vector", "postgis"]

_done: set[tuple[str, str]] = set()
_warned: set[tuple[str, str]] = set()
_lock = threading.Lock()


def render(store: str, backend: str | None = None) -> list[str]:
    """`store`'s DDL statements for a backend (default: the live one)."""
    return [stmt.format(**_T[backend or core.dialect()]) for stmt in DDL[store]]


def ensure(store: str, *, strict: bool = False) -> None:
    """Create `store`'s tables if missing. Cached per (backend, store), so the
    common path — re-constructing a store object — costs nothing.

    Not strict by default, and deliberately so: several agent graphs build their
    checkpointer at MODULE IMPORT time. Raising here when RDS is momentarily
    unreachable would turn a database blip into "the container will not start",
    which is strictly worse than the existing posture (the app boots, /health
    reports the component as unreachable, and reads degrade). The failure is
    logged once and the DDL is retried on the next call, so a database that
    comes up later self-provisions without a restart.
    """
    key = (core.dialect(), store)
    if key in _done:
        return
    with _lock:
        if key in _done:
            return
        try:
            with core.connect(store) as conn:
                for stmt in render(store):
                    conn.execute(stmt)
        except Exception as e:
            if strict:
                raise
            if key not in _warned:
                _warned.add(key)
                print(f"[db] schema for '{store}' not provisioned yet: {e}",
                      file=sys.stderr)
            return
        _done.add(key)


def ensure_all(*, strict: bool = False) -> None:
    for store in DDL:
        ensure(store, strict=strict)


def reset_cache() -> None:
    """Forget what has been provisioned — for tests that switch backends."""
    with _lock:
        _done.clear()
        _warned.clear()


def create_extensions() -> list[tuple[str, str]]:
    """Enable pgvector / PostGIS. Returns [(name, "created"|error)]."""
    if not core.is_postgres():
        return [(e, "skipped (sqlite)") for e in EXTENSIONS]
    out = []
    for ext in EXTENSIONS:
        try:
            with core.connect("changelog") as conn:
                conn.execute(f'CREATE EXTENSION IF NOT EXISTS "{ext}"')
            out.append((ext, "created"))
        except Exception as e:
            out.append((ext, f"skipped: {e}"))
    return out


# ── CLI ─────────────────────────────────────────────────────────────────
_TABLES = {"twins": ["twins"], "changelog": ["events"],
           "bundles": ["published_bundles"], "checkpoints": ["checkpoints"],
           "scenes": ["scene_cache"], "threed": ["threed_jobs"],
           "devices": ["ingest_devices"], "connectors": ["connectors"],
           "identity": ["organizations", "users", "memberships", "sessions",
                        "api_keys", "org_tenants", "auth_tokens", "audit_log"]}


def _row_count(store: str, table: str):
    try:
        with core.connect(store) as conn:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        return row["n"]
    except Exception:
        return None


def main(argv: list[str]) -> int:
    check_only = "--check" in argv
    print(f"backend : {core.dialect()}")
    if core.is_postgres():
        print(f"database: {core.redacted_url()}")
    else:
        from paths import DATA_DIR
        print(f"data dir: {DATA_DIR}")

    ok, detail = core.ping()
    print(f"reachable: {ok}{'' if ok else '  - ' + detail}")
    if not ok and not check_only:
        print("\nCannot provision: the database is unreachable.")
        return 1

    if not check_only:
        if "--extensions" in argv:
            for name, status in create_extensions():
                print(f"  extension {name:10s} {status}")
        ensure_all(strict=True)   # provisioning explicitly: surface DDL errors

    print("\n  table                rows")
    print("  " + "-" * 30)
    for store, tables in _TABLES.items():
        for table in tables:
            n = _row_count(store, table)
            print(f"  {table:<20} {'-  (missing)' if n is None else n}")
    print("\nSchema OK." if not check_only else "\nCheck complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
