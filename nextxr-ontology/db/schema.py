"""schema.py — every relational table the twin owns, in ONE place.

The five SQLite files this replaces each created their own tables inline, which
meant five places to keep in step with the MySQL deploy. Here each store declares
its DDL once and `ensure(store)` renders it for whichever backend is live. Stores
still call `ensure()` from their constructor, so a fresh database self-provisions
on first use — but the result is cached per process, because `ChangeLog()` is
constructed inside request handlers and a DDL round-trip per construction would be
a real cost against RDS.

Provision explicitly before a deploy (idempotent, safe to re-run):

    python -m db.schema                 # create every table
    python -m db.schema --check         # report what exists, create nothing

Dialect differences live only in `_T`. Everything else is SQL that means the
same thing in SQLite ≥3.24 and MySQL 8, with one exception handled in `ensure()`:
MySQL has no `CREATE INDEX IF NOT EXISTS`, so index creation strips the clause and
ignores the "duplicate index" error.

WHY `{id}` INSTEAD OF `TEXT` ON KEY COLUMNS
-------------------------------------------
MySQL cannot use a `TEXT`/`BLOB` column as a PRIMARY KEY, in a UNIQUE constraint,
or in an ordinary index without a prefix length. Every column that is a key or is
named in a `CREATE INDEX` therefore uses the `{id}` token (`VARCHAR(255)` on MySQL,
`TEXT` on SQLite). Plain, non-indexed text stays `TEXT`.

`CREATE TABLE IF NOT EXISTS` CANNOT ADD A COLUMN, WHICH IS WHY `ensure()`
RECONCILES
--------------------------------------------------------------------------
Declaring a new column here and an index on it used to break every database that
already existed: the CREATE TABLE was a no-op (the table is there), so the column
never appeared, and the CREATE INDEX that followed failed with "no such column".
That failure took `db/migrations.py` down with it — migration 0001 calls
`ensure_all(strict=True)`, so the whole chain aborted BEFORE reaching the
migration that would have added the column. The database could then never be
upgraded, on SQLite or on MySQL.

So `ensure()` does three things per store, in this order: create the tables,
ADD any declared column an existing table is missing, then create the indexes.
The middle step is what makes the first paragraph's promise ("a fresh database is
created from schema.py in one step") also true of a database that already exists.
It only ever ADDs — nothing here drops, renames or retypes a column, because
those need a data decision and belong in a migration.
"""
from __future__ import annotations

import re
import sys
import threading

from . import core

# Type/DDL fragments that genuinely differ between the two backends.
_T = {
    core.MYSQL: {
        "serial_pk": "BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY",
        "json":      "JSON",
        "ts":        "DATETIME",
        "ts_now":    "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "float":     "DOUBLE",
        # Key/indexed identifier columns — MySQL cannot index bare TEXT.
        "id":        "VARCHAR(255)",
        # Short string columns that carry a DEFAULT — MySQL forbids a DEFAULT on
        # TEXT/BLOB, so these are VARCHAR. Not indexed, so length is generous.
        "str":       "VARCHAR(1024)",
        # A short CLOSED VOCABULARY that is also INDEXED — a status, a severity,
        # a reason code. `{str}` cannot be used for these: at VARCHAR(1024) a
        # single such column costs 4096 bytes of index key (utf8mb4 is 4 bytes
        # per character) and InnoDB's limit for the whole key is 3072, so the
        # CREATE INDEX fails outright with errno 1071. That is not a theoretical
        # cap — it is why `tasks(org_id, status)` and the UNIQUE
        # `xp_ledger(user_id, task_id, reason)` could not be created on MySQL at
        # all, which took the whole `work` store's provisioning down with them.
        #
        # 64 characters is far more than any vocabulary in this schema needs
        # ("in_progress" is the longest at 11) and leaves the three-column XP
        # index at 2296 bytes, comfortably inside the limit with room for the
        # index to gain a column later.
        "code":      "VARCHAR(64)",
    },
    core.SQLITE: {
        "serial_pk": "INTEGER PRIMARY KEY AUTOINCREMENT",
        "json":      "TEXT",
        "ts":        "TEXT",
        "ts_now":    "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "float":     "REAL",
        "id":        "TEXT",
        "str":       "TEXT",
        "code":      "TEXT",
    },
}

# store -> list of DDL templates, applied in order. `{json}` etc. are filled
# from _T. Every statement is IF NOT EXISTS: ensure() is run on every boot.
DDL: dict[str, list[str]] = {
    "twins": [
        # `org_id` is denormalised from `org_tenants`, which stays the source of
        # truth for AUTHORIZATION — a twin is reachable because its org owns it
        # there, never because of this column. It is here so the twin listing can
        # one day filter by organisation in one query instead of one lookup per
        # row. See migration 0003_twin_org_owner for the upgrade path.
        #
        # NOT POPULATED YET, and stated here so nobody builds a query on it: the
        # listing in server/twins_routes.py filters through `Scope.allows()`,
        # which reads `org_tenants`. Every row's org_id is NULL, so a
        # `WHERE org_id = ?` would return nothing at all — write the column on
        # creation and backfill the existing rows before relying on it.
        """CREATE TABLE IF NOT EXISTS twins (
               tenant_id     {id} PRIMARY KEY,
               name          TEXT NOT NULL,
               domain        TEXT NOT NULL,
               description   {str} NOT NULL DEFAULT '',
               created_at    {id} NOT NULL,
               seed_asset_id TEXT,
               org_id        {id}
           )""",
        "CREATE INDEX IF NOT EXISTS idx_twins_created ON twins (created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_twins_org ON twins (org_id)",
    ],
    "changelog": [
        # `seq` orders a tenant's hash chain. MySQL gets a real AUTO_INCREMENT;
        # gaps from rolled-back transactions are fine — verification walks the
        # prev_event_hash linkage, not the numbers.
        """CREATE TABLE IF NOT EXISTS events (
               seq             {serial_pk},
               event_id        {id} NOT NULL UNIQUE,
               tenant_id       {id} NOT NULL,
               entity_id       {id} NOT NULL,
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
               bundle_id   {id} PRIMARY KEY,
               name        TEXT NOT NULL,
               domains     {json} NOT NULL,
               payload     {json} NOT NULL,
               tenant_id   TEXT,
               created_at  {ts_now}
           )""",
    ],
    "checkpoints": [
        """CREATE TABLE IF NOT EXISTS checkpoints (
               thread_id   {id} PRIMARY KEY,
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
               tenant_id   {id} PRIMARY KEY,
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
               job_id    {id} PRIMARY KEY,
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
               device_id     {id} PRIMARY KEY,
               tenant_id     {id} NOT NULL,
               name          {str} NOT NULL DEFAULT '',
               token_hash    {id} NOT NULL,
               asset_prefix  {str} NOT NULL DEFAULT '',
               enabled       INTEGER NOT NULL DEFAULT 1,
               created_at    TEXT NOT NULL,
               created_by    {str} NOT NULL DEFAULT '',
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
               org_id        {id} PRIMARY KEY,
               name          TEXT NOT NULL,
               plan          {str} NOT NULL DEFAULT 'trial',
               status        {str} NOT NULL DEFAULT 'active',
               tenant_prefix TEXT NOT NULL,
               settings      {json},
               created_at    TEXT NOT NULL,
               updated_at    TEXT NOT NULL
           )""",
        # Email is the login identifier, so it is stored lowercased and carries a
        # UNIQUE index rather than a UNIQUE column: the index is what makes
        # "is this address taken" a lookup instead of a scan, and both are needed.
        """CREATE TABLE IF NOT EXISTS users (
               user_id           {id} PRIMARY KEY,
               email             {id} NOT NULL,
               password_hash     TEXT NOT NULL,
               name              {str} NOT NULL DEFAULT '',
               status            {str} NOT NULL DEFAULT 'active',
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
        # `role` is the DATA ladder (owner/admin/write/read): what you may read
        # and write. `persona` is the OPERATIONAL role (supervisor/frontline):
        # what job you do. They are separate columns because they answer separate
        # questions and collapsing them loses one of the answers — a supervisor
        # who may only READ the twin is a perfectly ordinary account, and so is a
        # frontline operator who may write telemetry back. ensure() ADDs `persona`
        # to an existing memberships table, so this is safe on a live database.
        """CREATE TABLE IF NOT EXISTS memberships (
               org_id     {id} NOT NULL,
               user_id    {id} NOT NULL,
               role       {str} NOT NULL DEFAULT 'read',
               persona    {str} NOT NULL DEFAULT 'frontline',
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
               session_id   {id} PRIMARY KEY,
               user_id      {id} NOT NULL,
               org_id       TEXT,
               refresh_hash {id} NOT NULL,
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
               key_id       {id} PRIMARY KEY,
               org_id       {id} NOT NULL,
               key_hash     {id} NOT NULL,
               prefix       TEXT NOT NULL,
               name         {str} NOT NULL DEFAULT '',
               role         {str} NOT NULL DEFAULT 'read',
               tenants      {json},
               created_by   {str} NOT NULL DEFAULT '',
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
               tenant_id  {id} PRIMARY KEY,
               org_id     {id} NOT NULL,
               created_at TEXT NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_org_tenants_org ON org_tenants (org_id)",
        # Single-use, short-lived tokens for email verification and password
        # reset. Hashed like everything else, and `used_at` makes them one-shot
        # so a reset link in a mailbox is not a standing credential.
        """CREATE TABLE IF NOT EXISTS auth_tokens (
               token_hash {id} PRIMARY KEY,
               user_id    {id} NOT NULL,
               purpose    {id} NOT NULL,
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
               ts           {id} NOT NULL,
               org_id       {id},
               actor_user   {id},
               actor_key    TEXT,
               action       TEXT NOT NULL,
               target_type  {str} NOT NULL DEFAULT '',
               target_id    {str} NOT NULL DEFAULT '',
               outcome      {str} NOT NULL DEFAULT 'ok',
               ip           TEXT,
               user_agent   TEXT,
               detail       {json}
           )""",
        "CREATE INDEX IF NOT EXISTS idx_audit_org ON audit_log (org_id, ts DESC)",
        "CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log (actor_user, ts DESC)",
        # ── Goalcert Hub SSO (see the `sso/` package) ──────────────────
        # WHICH HUB USER IS WHICH LOCAL USER. `hub_sub` is the Hub's subject
        # claim and the PERMANENT key: emails get reassigned when someone leaves
        # and their address is handed on, so a system that re-matched on email
        # every visit would eventually hand the newcomer the leaver's twin. Email
        # is the bootstrap for the first visit only.
        #
        # `user_id` is UNIQUE, not just indexed. One local account maps to at
        # most one Hub identity, so a second Hub subject cannot quietly claim a
        # user that is already linked — the resolver refuses that case rather
        # than guessing, and this constraint is what makes the refusal true even
        # under a race.
        """CREATE TABLE IF NOT EXISTS hub_identities (
               hub_sub      {id} PRIMARY KEY,
               user_id      {id} NOT NULL,
               hub_iss      {str} NOT NULL DEFAULT '',
               linked_at    TEXT NOT NULL,
               last_seen_at TEXT,
               linked_by    {str} NOT NULL DEFAULT ''
           )""",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_hub_identities_user "
        "ON hub_identities (user_id)",
        # SPENT TICKETS. A Hub ticket is single-use; this is what makes that
        # true. It is a TABLE rather than an in-process cache because a cache is
        # per-worker, and a replay landing on a second Uvicorn worker or a second
        # ECS task would find it empty and be accepted. Rows are purged on the
        # sign-in path (sso/store.py), so it stays bounded without a cron.
        # `expires_at` is {id}, NOT TEXT, because the index below names it and
        # MySQL cannot index a TEXT column without a prefix length (errno 1170).
        # As TEXT this CREATE INDEX failed on MySQL, and because migration 0001
        # provisions every store with ensure_all(strict=True) that single
        # statement blocked the WHOLE migration chain on MySQL — not just hub
        # SSO. It has always worked on SQLite, where {id} and TEXT are the same
        # type, which is why it went unnoticed. Values are ISO-8601 strings
        # compared lexicographically (sso/store.py), so the narrower type is a
        # drop-in.
        """CREATE TABLE IF NOT EXISTS hub_sso_jti (
               jti        {id} PRIMARY KEY,
               seen_at    TEXT NOT NULL,
               expires_at {id} NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_hub_sso_jti_expires "
        "ON hub_sso_jti (expires_at)",
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
               connector_id {id} PRIMARY KEY,
               tenant_id    {id} NOT NULL,
               protocol     TEXT NOT NULL,
               name         {str} NOT NULL DEFAULT '',
               enabled      INTEGER NOT NULL DEFAULT 1,
               config       {json} NOT NULL,
               created_at   TEXT NOT NULL,
               updated_at   TEXT NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_connectors_tenant "
        "ON connectors (tenant_id, enabled)",
    ],

    # ── Work: the fault -> supervisor -> operator loop ──────────────────
    #
    # The twin already detects faults; a Finding node is the detection. What it
    # could not express is DISPATCH — that a person owes a fix to a specific
    # fault, that a supervisor decided who, and that the operator learned
    # something by closing it. These tables are that missing half.
    #
    #     Finding (graph)  ->  task (unassigned)  ->  supervisor assigns
    #                      ->  operator fixes via a scenario run
    #                      ->  xp_ledger entry + task closed
    #
    # Findings stay in the GRAPH: this is deliberately not a copy of them. A task
    # points at its finding by node id (`finding_node_id`) and the graph remains
    # the single source of truth for what is wrong with the plant. Dropping every
    # row here would lose the dispatch history and not one byte of twin state.
    "work": [
        # A supervisor is authoritative over their own people, not the whole org.
        # Teams are what bound that authority — without them "supervisor" would
        # mean "may assign to anyone in the tenant", which is not a role anyone
        # asked for.
        """CREATE TABLE IF NOT EXISTS teams (
               team_id       {id} PRIMARY KEY,
               org_id        {id} NOT NULL,
               name          {str} NOT NULL DEFAULT '',
               site          {str} NOT NULL DEFAULT '',
               shift         {str} NOT NULL DEFAULT 'A',
               supervisor_id {id},
               created_at    TEXT NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_teams_org ON teams (org_id)",
        "CREATE INDEX IF NOT EXISTS idx_teams_supervisor ON teams (supervisor_id)",

        """CREATE TABLE IF NOT EXISTS team_members (
               team_id    {id} NOT NULL,
               user_id    {id} NOT NULL,
               org_id     {id} NOT NULL,
               created_at TEXT NOT NULL,
               PRIMARY KEY (team_id, user_id)
           )""",
        "CREATE INDEX IF NOT EXISTS idx_team_members_user ON team_members (user_id)",

        # The task itself: one row per fault that someone must fix.
        #
        # `finding_node_id` is the join back to the graph. It is unique among
        # LIVE tasks (enforced in the store, not by a UNIQUE index here, because
        # a closed task must not stop the same fault raising work when it
        # recurs next month).
        #
        # `scenario_id` is how "fix the issue" knows WHICH procedure to teach.
        # It is resolved once, when the task is created, from the finding's
        # behaviour — so the operator's page needs no lookup of its own.
        """CREATE TABLE IF NOT EXISTS tasks (
               task_id         {id} PRIMARY KEY,
               org_id          {id} NOT NULL,
               tenant_id       {id} NOT NULL,
               code            {str} NOT NULL DEFAULT '',
               title           TEXT NOT NULL,
               detail          TEXT NOT NULL DEFAULT '',
               finding_node_id {id} NOT NULL DEFAULT '',
               asset_node_id   {id} NOT NULL DEFAULT '',
               asset_name      {str} NOT NULL DEFAULT '',
               behavior_id     {str} NOT NULL DEFAULT '',
               severity        {str} NOT NULL DEFAULT 'warning',
               scenario_id     {str} NOT NULL DEFAULT '',
               status          {code} NOT NULL DEFAULT 'open',
               priority        {str} NOT NULL DEFAULT 'normal',
               assignee_id     {id},
               assigned_by     {id},
               team_id         {id},
               source          {str} NOT NULL DEFAULT 'twin_finding',
               score           {float},
               xp_awarded      INTEGER NOT NULL DEFAULT 0,
               resolution      TEXT NOT NULL DEFAULT '',
               changelog_ref   {str} NOT NULL DEFAULT '',
               created_at      TEXT NOT NULL,
               assigned_at     TEXT,
               started_at      TEXT,
               completed_at    TEXT
           )""",
        # `status` is {code}, not {str}: it is named in the two indexes below and
        # a VARCHAR(1024) column cannot be part of a MySQL index key. See _T.
        "CREATE INDEX IF NOT EXISTS idx_tasks_org_status ON tasks (org_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks (assignee_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_finding ON tasks (finding_node_id)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_tenant ON tasks (tenant_id)",

        # Every transition, append-only. This is what makes "who assigned this,
        # when, and what happened next" answerable after the fact — a status
        # column alone remembers only the present.
        """CREATE TABLE IF NOT EXISTS task_events (
               event_id   {id} PRIMARY KEY,
               task_id    {id} NOT NULL,
               org_id     {id} NOT NULL,
               kind       {str} NOT NULL DEFAULT '',
               actor_id   {id} NOT NULL DEFAULT '',
               actor_name {str} NOT NULL DEFAULT '',
               summary    TEXT NOT NULL DEFAULT '',
               payload    {json},
               created_at TEXT NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events (task_id)",

        # XP is a LEDGER, not a counter on the user row. A total that can only be
        # incremented cannot be explained, audited or corrected; a ledger answers
        # "why do I have 340 XP" with the rows that produced it, and the total is
        # a SUM. The unique index on (user, task, reason) is what makes a double
        # award impossible rather than merely unlikely — a retried request that
        # gets as far as the insert is refused by the database.
        """CREATE TABLE IF NOT EXISTS xp_ledger (
               entry_id   {id} PRIMARY KEY,
               org_id     {id} NOT NULL,
               user_id    {id} NOT NULL,
               task_id    {id} NOT NULL DEFAULT '',
               reason     {code} NOT NULL DEFAULT '',
               points     INTEGER NOT NULL DEFAULT 0,
               detail     TEXT NOT NULL DEFAULT '',
               created_at TEXT NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_xp_user ON xp_ledger (user_id)",
        # THE UNIQUE INDEX IS THE DOUBLE-AWARD GUARD, so it has to exist on both
        # backends. `reason` is {code} for that reason — as {str} this index was
        # 6136 bytes and MySQL refused to create it, which would have left the
        # `ON CONFLICT DO NOTHING` in store.add_xp with nothing to conflict on.
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_xp_task_reason "
        "ON xp_ledger (user_id, task_id, reason)",

        # Which findings become tasks. Data rather than code, so raising the bar
        # from "critical only" to "warnings too" is a row edit, not a deploy.
        """CREATE TABLE IF NOT EXISTS work_rules (
               rule_id       {id} PRIMARY KEY,
               org_id        {id} NOT NULL,
               tenant_id     {str} NOT NULL DEFAULT '',
               behavior_glob {str} NOT NULL DEFAULT '*',
               min_severity  {str} NOT NULL DEFAULT 'critical',
               priority      {str} NOT NULL DEFAULT 'high',
               scenario_id   {str} NOT NULL DEFAULT '',
               enabled       INTEGER NOT NULL DEFAULT 1,
               seq           INTEGER NOT NULL DEFAULT 0,
               created_at    TEXT NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_work_rules_org ON work_rules (org_id, seq)",

        # One row per operator per scenario run. A run is deterministic and
        # recomputable from (scenario, answers), so what is stored is the
        # OUTCOME — score, how far they got, where they went wrong — not a
        # replay of every keystroke.
        """CREATE TABLE IF NOT EXISTS scenario_runs (
               run_id       {id} PRIMARY KEY,
               org_id       {id} NOT NULL,
               user_id      {id} NOT NULL,
               task_id      {id} NOT NULL DEFAULT '',
               scenario_id  {str} NOT NULL DEFAULT '',
               mode         {str} NOT NULL DEFAULT 'guided',
               status       {str} NOT NULL DEFAULT 'in_progress',
               step         INTEGER NOT NULL DEFAULT 0,
               total_steps  INTEGER NOT NULL DEFAULT 0,
               score        {float},
               passed       INTEGER NOT NULL DEFAULT 0,
               hints_used   INTEGER NOT NULL DEFAULT 0,
               wrong_steps  INTEGER NOT NULL DEFAULT 0,
               transcript   {json},
               started_at   TEXT NOT NULL,
               completed_at TEXT
           )""",
        "CREATE INDEX IF NOT EXISTS idx_runs_user ON scenario_runs (user_id)",
        "CREATE INDEX IF NOT EXISTS idx_runs_task ON scenario_runs (task_id)",
    ],
}

_done: set[tuple[str, str]] = set()
_warned: set[tuple[str, str]] = set()
_lock = threading.Lock()

# MySQL has no `CREATE INDEX IF NOT EXISTS`; strip the clause and ignore the
# "duplicate key name" error (errno 1061) so ensure() stays idempotent on boot.
_CREATE_INDEX_INE = re.compile(
    r"^(\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+)IF\s+NOT\s+EXISTS\s+", re.IGNORECASE)


def render(store: str, backend: str | None = None) -> list[str]:
    """`store`'s DDL statements for a backend (default: the live one)."""
    return [stmt.format(**_T[backend or core.dialect()]) for stmt in DDL[store]]


def _exec_stmt(conn, stmt: str) -> None:
    """Execute one DDL statement, adapting the one construct MySQL lacks."""
    if core.is_mysql() and _CREATE_INDEX_INE.match(stmt):
        stmt = _CREATE_INDEX_INE.sub(r"\1", stmt, count=1)
        try:
            conn.execute(stmt)
        except Exception as e:
            msg = str(e).lower()
            if "1061" in msg or "duplicate key name" in msg:
                return                             # index already exists — fine
            raise
    else:
        conn.execute(stmt)


# ── Reconciling an EXISTING table with its declaration ──────────────────

_CREATE_TABLE = re.compile(
    r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*"
    r"\((?P<body>.*)\)\s*;?\s*$",
    re.IGNORECASE | re.DOTALL)

# A fragment starting with one of these is a TABLE constraint, not a column —
# `PRIMARY KEY (org_id, user_id)` must not be read as a column called "PRIMARY".
_TABLE_CONSTRAINTS = ("primary", "unique", "foreign", "check", "constraint",
                      "exclude")


def _split_columns(body: str) -> list[str]:
    """Split a CREATE TABLE body on its top-level commas.

    Depth-aware because a column definition can contain its own parentheses —
    `NUMERIC(10, 2)`, `CHECK (n > 0)` — and splitting on every comma would tear
    those in half.
    """
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def declared_columns(create_stmt: str) -> tuple[str, dict[str, str]]:
    """(table, {column: definition}) for a rendered CREATE TABLE statement.

    Returns ("", {}) for anything that is not one — the DDL lists interleave
    CREATE TABLE and CREATE INDEX, and only the former declares columns.
    """
    match = _CREATE_TABLE.match(create_stmt)
    if not match:
        return "", {}
    columns: dict[str, str] = {}
    for fragment in _split_columns(match.group("body")):
        head, _, rest = fragment.partition(" ")
        if head.lower() in _TABLE_CONSTRAINTS:
            continue
        columns[head.strip('"')] = rest.strip()
    return match.group(1), columns


def _live_columns(conn, table: str) -> set[str]:
    """The columns a table ACTUALLY has, or an empty set if it has none/does not
    exist. Called immediately after CREATE TABLE IF NOT EXISTS, so empty means
    "unreadable", which the caller treats as "nothing to reconcile"."""
    if core.is_mysql():
        # DATABASE() scopes this to the schema the connection is actually using,
        # which matters on a server hosting more than one: information_schema is
        # server-wide, so an unscoped query would happily report the columns of a
        # same-named table in someone else's database.
        rows = conn.execute(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_NAME = ? AND TABLE_SCHEMA = DATABASE()",
            (table,)).fetchall()
        # MySQL returns the column label in the case it was selected in; the
        # SQLite path below yields lowercase "name", so normalise here.
        return {str(dict(r)["COLUMN_NAME"]) for r in rows}
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {dict(r)["name"] for r in rows}


def _is_addable(definition: str) -> bool:
    """Whether ALTER TABLE ADD COLUMN can express this definition at all.

    A PRIMARY KEY / UNIQUE / REFERENCES column cannot be bolted onto an existing
    SQLite table, and NOT NULL without a DEFAULT is rejected by both backends the
    moment the table has a row. Those need a real migration (create-copy-swap, or
    a backfill between the ADD and the constraint), so this reports them instead
    of emitting DDL that is guaranteed to fail.
    """
    upper = definition.upper()
    if any(token in upper for token in
           ("PRIMARY KEY", "UNIQUE", "REFERENCES", "GENERATED")):
        return False
    return "NOT NULL" not in upper or "DEFAULT" in upper


def reconcile(conn, create_stmt: str) -> list[str]:
    """ADD every column `create_stmt` declares that the live table lacks.

    Returns the columns added, for the boot log — a column appearing on a
    production table is not something to do silently.

    Each ALTER is wrapped in `conn.nested()` so that one un-addable column
    cannot turn into a completely unprovisioned store. On MySQL and SQLite that
    wrapper is a no-op, because both fail per statement and the `except` below
    is what actually contains the failure; it stays because it marks which
    statement is permitted to fail. See core.Conn.nested().
    """
    table, declared = declared_columns(create_stmt)
    if not table or not declared:
        return []

    live = _live_columns(conn, table)
    if not live:
        return []                      # brand-new table (or unreadable): nothing to do

    added: list[str] = []
    for column, definition in declared.items():
        if column in live:
            continue
        if not _is_addable(definition):
            print(f"[db] !! {table}.{column} is declared in db/schema.py and "
                  f"missing from the live table, and cannot be added in place "
                  f"({definition}). This needs a migration in db/migrations.py.",
                  file=sys.stderr)
            continue
        try:
            with conn.nested():
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        except Exception as e:
            # Two tasks reconciling at once: one wins, the other sees "duplicate
            # column", which is the desired end state rather than a problem.
            message = str(e).lower()
            if "duplicate column" in message or "already exists" in message:
                continue
            # SQLite refuses a non-constant DEFAULT (CURRENT_TIMESTAMP) in ADD
            # COLUMN, so the statement below is the starting point rather than a
            # guaranteed fix — a column like that needs the value backfilled in a
            # migration. Either way the operator gets the table, the column and
            # the driver's own reason, instead of a store that silently stays
            # unprovisioned.
            print(f"[db] !! could not add {table}.{column}: {e}\n"
                  f"     needs a migration in db/migrations.py; the change is "
                  f"ALTER TABLE {table} ADD COLUMN {column} {definition};",
                  file=sys.stderr)
            continue
        added.append(column)
    return added


def ensure(store: str, *, strict: bool = False) -> None:
    """Bring `store`'s tables to the shape declared above: create what is
    missing, then ADD any declared column an existing table does not have, then
    create the indexes. Cached per (backend, store), so the common path —
    re-constructing a store object — costs nothing.

    THE ORDER IS LOAD-BEARING. An index on a newly declared column is the exact
    case that used to fail: `CREATE TABLE IF NOT EXISTS` will not add the column,
    so `CREATE INDEX ... (new_column)` raised "no such column" on every database
    that predated the declaration. Reconciling between the two is what makes a
    new column-plus-index a normal deploy instead of a broken one.

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
        added: list[str] = []
        try:
            with core.connect(store) as conn:
                for stmt in render(store):
                    _exec_stmt(conn, stmt)
                    # Reconcile right after the table it belongs to, so the
                    # indexes further down the list see the columns they need.
                    added += [f"{declared_columns(stmt)[0]}.{c}"
                              for c in reconcile(conn, stmt)]
        except Exception as e:
            if strict:
                raise
            if key not in _warned:
                _warned.add(key)
                print(f"[db] schema for '{store}' not provisioned yet: {e}",
                      file=sys.stderr)
            return
        if added:
            print(f"[db] schema updated: added {', '.join(added)}", flush=True)
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
    """No database extensions are required on MySQL. Kept as a stable CLI entry
    point (was pgvector / PostGIS on Postgres); returns a skip notice."""
    return [("none", "skipped (mysql needs no extensions)")]


# ── CLI ─────────────────────────────────────────────────────────────────
_TABLES = {"twins": ["twins"], "changelog": ["events"],
           "bundles": ["published_bundles"], "checkpoints": ["checkpoints"],
           "scenes": ["scene_cache"], "threed": ["threed_jobs"],
           "devices": ["ingest_devices"], "connectors": ["connectors"],
           "identity": ["organizations", "users", "memberships", "sessions",
                        "api_keys", "org_tenants", "auth_tokens", "audit_log",
                        "hub_identities", "hub_sso_jti"],
           "work": ["teams", "team_members", "tasks", "task_events",
                    "xp_ledger", "work_rules", "scenario_runs"]}


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
    if core.is_mysql():
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
