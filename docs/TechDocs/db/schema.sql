-- ============================================================================
-- NextXR Digital Twin Platform — Production Database Schema
-- ============================================================================
--
-- Database:    PostgreSQL 16
-- Extensions:  timescaledb (required in production), pgcrypto,
--              pgvector + postgis (optional, not yet queried)
-- Schema:      public
-- Version:     1.0.0
--
-- This schema defines the 9 relational tables and 3 continuous aggregates the
-- platform owns. Tables are grouped by domain:
--
--   Twin Registry      twins, scene_cache
--   Governance         events                    (per-tenant hash chain)
--   Agents/Capability  published_bundles, checkpoints
--   3-D                threed_jobs
--   Edge / Field       ingest_devices, connectors
--   Historian          measurements (+ measurements_1m / _1h / _1d)
--
-- The authoritative source is nextxr-ontology/db/schema.py (relational) and
-- nextxr-ontology/historian/schema.py (historian). Both render the same DDL for
-- SQLite in local development; this file is the PostgreSQL rendering.
--
-- Provision with:
--     python -m db.schema                     # every relational table
--     python -m db.schema --extensions        # + pgvector / postgis
--     python -m db.schema --check             # report only
--     python -m tools.historian_provision --extension   # timescaledb
--
-- NOTE ON TENANCY. tenant_id is the platform's isolation key and appears on
-- every table. There are deliberately NO cross-tenant foreign keys: isolation is
-- a property of the key and of server/tenancy.py's central enforcement, not of a
-- referential action. Deleting a twin is a scoped delete, not a cascade.
--
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;
-- Required in production. Without it the historian works, never compresses and
-- never expires anything — so the disk fills. NXR_REQUIRE_TIMESCALE=1 turns that
-- silent degradation into a failed boot.
CREATE EXTENSION IF NOT EXISTS timescaledb;
-- Optional. Nothing queries them yet; they are why the architecture specifies
-- PostgreSQL over a KV store (geospatial asset positions, embedding search over
-- the ontology). Creating them needs rds_superuser.
-- CREATE EXTENSION IF NOT EXISTS vector;
-- CREATE EXTENSION IF NOT EXISTS postgis;


-- ============================================================================
-- DOMAIN: Twin Registry
-- ============================================================================

-- ----------------------------------------------------------------------------
-- twins
-- The twin registry. A twin is ONE isolated platform instance keyed by
-- tenant_id. This table holds only metadata — which twins exist, their name,
-- domain template and seed asset. The entities themselves live in Neo4j under
-- the tenant_id and are created EXCLUSIVELY through the Graph Writer
-- (validate -> commit -> changelog -> bus), so a seeded twin honours every
-- platform guarantee the moment it is born.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS twins (
    tenant_id       TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    domain          TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    seed_asset_id   TEXT
);

CREATE INDEX IF NOT EXISTS idx_twins_created
    ON twins (created_at DESC);


-- ----------------------------------------------------------------------------
-- scene_cache
-- BIM scene graphs for the 3-D viewer.
--
-- On disk this was one JSON file per tenant under the data volume, which is
-- per-TASK state: task A builds a twin's scene, task B serves the next request
-- and has never seen it. Moving it here is what lets the 3-D viewer stay correct
-- at desired count > 1.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scene_cache (
    tenant_id       TEXT PRIMARY KEY,
    scene           JSONB       NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);


-- ============================================================================
-- DOMAIN: Governance
-- ============================================================================

-- ----------------------------------------------------------------------------
-- events  (the Change Log)
-- A per-tenant, tamper-evident hash chain of every graph mutation.
--
--   prev_event_hash -> wm_hash is the tamper-evident linkage: change any stored
--   field of an old event and every wm_hash after it stops matching.
--
--   `seq` orders a tenant's chain. Gaps from rolled-back transactions are fine
--   because verification walks the hash linkage, not the numbers.
--
--   Appends take a PostgreSQL ADVISORY LOCK per tenant for the length of the
--   transaction, so a tenant's chain cannot fork under concurrent writers.
--   Tenants never block each other; a single tenant's write throughput is
--   bounded by that lock, which is the correct trade for a ledger.
--
--   field_changes is JSONB so the log is queryable in place rather than opaque
--   text.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events (
    seq               BIGSERIAL PRIMARY KEY,
    event_id          TEXT  NOT NULL UNIQUE,   -- ULID: time-ordered, sortable
    tenant_id         TEXT  NOT NULL,
    entity_id         TEXT  NOT NULL,
    entity_type       TEXT  NOT NULL,          -- canonical OWL class IRI
    actor             TEXT  NOT NULL,          -- user | agent | connector | runtime
    action            TEXT  NOT NULL,          -- create | update | delete
    field_changes     JSONB NOT NULL,          -- { field: {old, new} }
    ts                TEXT  NOT NULL,          -- ISO-8601 UTC
    prev_event_hash   TEXT  NOT NULL,
    wm_hash           TEXT  NOT NULL           -- sha256 over canonical content
);

CREATE INDEX IF NOT EXISTS idx_events_tenant
    ON events (tenant_id, seq);

CREATE INDEX IF NOT EXISTS idx_events_entity
    ON events (tenant_id, entity_id, seq);


-- ============================================================================
-- DOMAIN: Agents & Capability
-- ============================================================================

-- ----------------------------------------------------------------------------
-- published_bundles
-- Capability bundles authored by the Bundle Author meta-agent. A bundle packages
-- a vertical: the ontology classes it provides, ready-made ENTITY TEMPLATES, and
-- the Tier-C rules it ships.
--
-- Persisting here is what closes the loop: the Capability Composer can load the
-- very bundle the Bundle Author just published — and on Postgres, a bundle
-- published by one task is immediately visible to the rest of the fleet.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS published_bundles (
    bundle_id       TEXT PRIMARY KEY,
    name            TEXT  NOT NULL,
    domains         JSONB NOT NULL,            -- keywords the Composer matches
    payload         JSONB NOT NULL,            -- entity/relationship templates + rules
    tenant_id       TEXT,                      -- NULL = platform-wide
    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);


-- ----------------------------------------------------------------------------
-- checkpoints
-- Agent graph checkpoints (LangGraph-compatible CheckpointSaver).
--
-- Because state lives here rather than in a task's memory, a run interrupted for
-- HUMAN APPROVAL on one task resumes on another — which is why the ECS service
-- does not need sticky sessions.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id       TEXT PRIMARY KEY,
    graph_name      TEXT  NOT NULL,            -- twin | bundle | ops | plugin | accelerator
    state           JSONB NOT NULL,
    resume_at       TEXT,                      -- node to resume from
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);


-- ============================================================================
-- DOMAIN: 3-D Reconstruction
-- ============================================================================

-- ----------------------------------------------------------------------------
-- threed_jobs
-- Photo -> GLB reconstruction job records.
--
-- The record was job.json on the task's own disk, so a browser polling job
-- status through the load balancer got a 404 whenever the poll landed on a task
-- that had not run the job. The heavy outputs live in the blob store (S3); this
-- is only the record.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS threed_jobs (
    job_id          TEXT PRIMARY KEY,
    status          TEXT NOT NULL,             -- queued | running | completed | failed
    stage           TEXT,
    filename        TEXT,
    fields          JSONB,
    stages          JSONB,                     -- per-stage status + artifact keys
    state           JSONB,
    error           TEXT,
    created         DOUBLE PRECISION NOT NULL, -- epoch seconds
    updated         DOUBLE PRECISION NOT NULL  -- epoch seconds
);

CREATE INDEX IF NOT EXISTS idx_threed_updated
    ON threed_jobs (updated DESC);


-- ============================================================================
-- DOMAIN: Edge / Field
-- ============================================================================

-- ----------------------------------------------------------------------------
-- ingest_devices
-- The identities that push telemetry.
--
-- A device MUST NOT authenticate with a tenant API key. Those keys are read and
-- write credentials for the whole twin: a gateway sitting in a plant room,
-- flashed onto hardware an electrician can unscrew, would then be able to read
-- every asset, drive the physics runtime and bill the LLM endpoints. A device
-- credential instead grants exactly one verb (append telemetry) in exactly one
-- tenant, and can be revoked on its own without rotating anything a human uses.
--
-- Only the token HASH is stored. A registry an operator can read back is a
-- registry an attacker can read once, so the plaintext is returned exactly once
-- at creation and is never recoverable — the same posture as an SSH
-- authorized_keys file or a GitHub PAT.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingest_devices (
    device_id       TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    name            TEXT NOT NULL DEFAULT '',
    token_hash      TEXT NOT NULL,
    asset_prefix    TEXT NOT NULL DEFAULT '',  -- narrows the device to a subtree
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL DEFAULT '',
    expires_at      TEXT,
    last_seen_at    TEXT,
    last_seen_ip    TEXT,
    samples_total   DOUBLE PRECISION NOT NULL DEFAULT 0,
    rejected_total  DOUBLE PRECISION NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_devices_tenant
    ON ingest_devices (tenant_id);

-- Authentication looks a device up BY HASH, because the presented token is all
-- we have — there is no device_id on the wire to narrow it first. Without this
-- index every ingest request is a full table scan.
CREATE UNIQUE INDEX IF NOT EXISTS idx_devices_token
    ON ingest_devices (token_hash);


-- ----------------------------------------------------------------------------
-- connectors
-- Field-protocol connectors (Modbus TCP/RTU, OPC-UA, MQTT + Sparkplug B).
--
-- Durable configuration, not runtime state: a site commissions dozens of these
-- and expects them polling after a redeploy. The whole config — including the
-- point map — is one JSON column rather than a normalised point table, because a
-- point map is read and written as a WHOLE document (validated as a unit,
-- versioned as a unit, uploaded as a unit) and is never queried by point. A
-- 40-row join to reconstruct one device's map would buy nothing.
--
-- Credentials live in this column too, which is why ConnectorConfig.redacted()
-- is ALLOW-listed rather than deny-listed: a customer's PLC password must never
-- reach an API response because someone added a field and forgot to hide it.
--
-- Exactly one task polls a given connector, via a Redis ownership lease. Three
-- tasks polling one inverter triples the load on a small microcontroller, and
-- three masters on an RS-485 segment corrupt frames rather than merely
-- duplicating them.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS connectors (
    connector_id    TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    protocol        TEXT NOT NULL,             -- modbus_tcp | modbus_rtu | opcua | mqtt
    name            TEXT NOT NULL DEFAULT '',
    enabled         INTEGER NOT NULL DEFAULT 1,
    config          JSONB NOT NULL,            -- endpoint, interval, point map, credentials
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_connectors_tenant
    ON connectors (tenant_id, enabled);


-- ============================================================================
-- DOMAIN: Historian  (TimescaleDB)
-- ============================================================================
--
-- WHY A SEPARATE STORE AT ALL
--   Neo4j holds the CURRENT value of a property, which is the right job for a
--   graph and the wrong one for "give me 90 days of this sensor at 5-minute
--   resolution". The relational tables above hold twins, events, bundles and
--   jobs — no measurements.
--
-- WHY TIMESCALEDB
--   It is a Postgres EXTENSION, so it runs on the RDS instance already
--   provisioned: same pool, same backups, no new security group, no second
--   database to keep alive. That property matters more than raw benchmark
--   numbers — a dedicated TSDB is faster in isolation and strictly worse to
--   operate.
--
--   hypertable            time partitioning: a "last hour" query touches one chunk
--   continuous aggregates incremental rollups: a 90-day chart reads ~2k rows
--   compression           10-20x on telemetry (columnar + delta-encoded ts)
--   retention policies    declared once, not maintained as cron jobs
-- ----------------------------------------------------------------------------

-- Quality is the classic OPC quality byte, which OPC-UA, every historian and
-- every SCADA system already speak: 0 = BAD, 64 = UNCERTAIN, 192 = GOOD.
-- It is on the RAW table because a sensor reporting 0 °C with a broken wire is
-- NOT the same fact as a sensor reporting 0 °C, and a model that cannot tell
-- them apart will confidently learn from garbage. This column cannot be added
-- later without reprocessing all of history, which is why it is here on day one.
CREATE TABLE IF NOT EXISTS measurements (
    tenant_id       TEXT             NOT NULL,
    asset_id        TEXT             NOT NULL,
    signal          TEXT             NOT NULL,
    ts              TIMESTAMPTZ      NOT NULL,
    value           DOUBLE PRECISION,
    unit            TEXT             NOT NULL DEFAULT '',
    quality         SMALLINT         NOT NULL DEFAULT 192,
    source          TEXT             NOT NULL DEFAULT 'api',
    received_at     TIMESTAMPTZ      NOT NULL DEFAULT now()
);

-- The primary key IS the idempotency contract: a replayed batch collides and is
-- skipped with ON CONFLICT DO NOTHING, so an edge agent that reconnects after a
-- network blip and resends its buffer cannot double-count. Doing it in the key
-- rather than in application code means every writer gets it, including a bulk
-- backfill and a connector nobody has written yet.
--
-- On a hypertable the time column must be part of any unique index, hence ts
-- last. Order matters for read performance too: tenant -> asset -> signal -> time
-- matches how every query filters, so a range scan walks one contiguous run.
CREATE UNIQUE INDEX IF NOT EXISTS measurements_pk
    ON measurements (tenant_id, asset_id, signal, ts DESC);

-- Serves "what signals does this tenant have, and when was each last seen" — the
-- discovery query behind the tag-mapping UI and the staleness check.
CREATE INDEX IF NOT EXISTS measurements_tenant_signal_ts
    ON measurements (tenant_id, signal, ts DESC);

-- migrate_data converts an existing plain table in place, which is what makes
-- this safe to run on a deployment that already collected data on plain Postgres.
SELECT create_hypertable('measurements', 'ts',
       chunk_time_interval => INTERVAL '1 day',
       migrate_data        => TRUE,
       if_not_exists       => TRUE);


-- ----------------------------------------------------------------------------
-- Continuous aggregates: measurements_1m / _1h / _1d
--
-- WHY count/sum AND NOT avg
--   An average cannot be re-averaged: avg(avg(x)) weights each bucket equally
--   regardless of how many samples it held, which silently skews every chart
--   drawn from a coarser rollup. Storing count and sum_value lets the API compute
--   a correct mean at any resolution and lets a client safely re-aggregate.
--
--   BAD-quality samples are excluded from the statistics but counted separately
--   in bad_count, so a chart drawn from a rollup shows the real measured signal
--   while the data-quality view can still see how much of it was untrustworthy.
--
--   Each tier is built directly from RAW rather than from the tier below it:
--   non-hierarchical costs more storage and is unambiguously correct.
-- ----------------------------------------------------------------------------

CREATE MATERIALIZED VIEW IF NOT EXISTS measurements_1m
WITH (timescaledb.continuous) AS
SELECT
    tenant_id,
    asset_id,
    signal,
    time_bucket(INTERVAL '1 minute', ts)                       AS bucket,
    count(*)     FILTER (WHERE quality >= 64 AND value IS NOT NULL) AS count,
    sum(value)   FILTER (WHERE quality >= 64)                  AS sum_value,
    min(value)   FILTER (WHERE quality >= 64)                  AS min_value,
    max(value)   FILTER (WHERE quality >= 64)                  AS max_value,
    first(value, ts) FILTER (WHERE quality >= 64)              AS first_value,
    last(value, ts)  FILTER (WHERE quality >= 64)              AS last_value,
    count(*)     FILTER (WHERE quality <  64)                  AS bad_count,
    max(unit)                                                  AS unit
FROM measurements
GROUP BY tenant_id, asset_id, signal, bucket
WITH NO DATA;

CREATE MATERIALIZED VIEW IF NOT EXISTS measurements_1h
WITH (timescaledb.continuous) AS
SELECT
    tenant_id,
    asset_id,
    signal,
    time_bucket(INTERVAL '1 hour', ts)                         AS bucket,
    count(*)     FILTER (WHERE quality >= 64 AND value IS NOT NULL) AS count,
    sum(value)   FILTER (WHERE quality >= 64)                  AS sum_value,
    min(value)   FILTER (WHERE quality >= 64)                  AS min_value,
    max(value)   FILTER (WHERE quality >= 64)                  AS max_value,
    first(value, ts) FILTER (WHERE quality >= 64)              AS first_value,
    last(value, ts)  FILTER (WHERE quality >= 64)              AS last_value,
    count(*)     FILTER (WHERE quality <  64)                  AS bad_count,
    max(unit)                                                  AS unit
FROM measurements
GROUP BY tenant_id, asset_id, signal, bucket
WITH NO DATA;

CREATE MATERIALIZED VIEW IF NOT EXISTS measurements_1d
WITH (timescaledb.continuous) AS
SELECT
    tenant_id,
    asset_id,
    signal,
    time_bucket(INTERVAL '1 day', ts)                          AS bucket,
    count(*)     FILTER (WHERE quality >= 64 AND value IS NOT NULL) AS count,
    sum(value)   FILTER (WHERE quality >= 64)                  AS sum_value,
    min(value)   FILTER (WHERE quality >= 64)                  AS min_value,
    max(value)   FILTER (WHERE quality >= 64)                  AS max_value,
    first(value, ts) FILTER (WHERE quality >= 64)              AS first_value,
    last(value, ts)  FILTER (WHERE quality >= 64)              AS last_value,
    count(*)     FILTER (WHERE quality <  64)                  AS bad_count,
    max(unit)                                                  AS unit
FROM measurements
GROUP BY tenant_id, asset_id, signal, bucket
WITH NO DATA;


-- ----------------------------------------------------------------------------
-- Refresh policies
--
-- start_offset bounds how far back a refresh looks: without it every run
-- rescans all history, which becomes the historian's dominant cost as the table
-- grows. It must still be wide enough to absorb late-arriving data — an edge
-- agent replaying a buffer — or those samples land in a bucket that is never
-- recomputed and silently never appear in a chart. end_offset keeps the refresh
-- off the newest bucket, which is still being written.
-- ----------------------------------------------------------------------------
SELECT add_continuous_aggregate_policy('measurements_1m',
       start_offset      => INTERVAL '3 hours',
       end_offset        => INTERVAL '1 minute',
       schedule_interval => INTERVAL '1 minute',
       if_not_exists     => TRUE);

SELECT add_continuous_aggregate_policy('measurements_1h',
       start_offset      => INTERVAL '3 days',
       end_offset        => INTERVAL '1 hour',
       schedule_interval => INTERVAL '10 minutes',
       if_not_exists     => TRUE);

SELECT add_continuous_aggregate_policy('measurements_1d',
       start_offset      => INTERVAL '30 days',
       end_offset        => INTERVAL '1 hour',
       schedule_interval => INTERVAL '1 hour',
       if_not_exists     => TRUE);


-- ----------------------------------------------------------------------------
-- Compression
--
-- segmentby groups the columns a query filters on so a compressed chunk can skip
-- whole segments; orderby matches the natural write order, which is what makes
-- timestamp delta-encoding effective. 7 days is comfortably longer than the
-- window that still receives writes: compressed chunks accept inserts on modern
-- TimescaleDB but pay for it, and edge store-and-forward legitimately replays
-- data hours late.
--
-- At 5,000 signals @ 1 Hz this is the difference between ~4 GB/day and
-- ~50 GB/day of RDS storage.
-- ----------------------------------------------------------------------------
ALTER TABLE measurements SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'tenant_id, asset_id, signal',
    timescaledb.compress_orderby   = 'ts DESC'
);

SELECT add_compression_policy('measurements', INTERVAL '7 days',
       if_not_exists => TRUE);


-- ----------------------------------------------------------------------------
-- Retention
--
-- Raw is the expensive tier and the least useful after an incident is closed;
-- the daily rollup is cheap enough to keep indefinitely, which is what makes
-- year-over-year comparison possible. Override per deployment with
-- NXR_HISTORIAN_RETENTION_RAW / _1M / _1H / _1D (a regulatory retention floor).
-- ----------------------------------------------------------------------------
SELECT add_retention_policy('measurements',    INTERVAL '30 days',   if_not_exists => TRUE);
SELECT add_retention_policy('measurements_1m', INTERVAL '400 days',  if_not_exists => TRUE);
SELECT add_retention_policy('measurements_1h', INTERVAL '1095 days', if_not_exists => TRUE);
-- measurements_1d: no retention policy — kept forever by design.


-- ============================================================================
-- APPENDIX A — Neo4j schema (Cypher)
-- ============================================================================
--
-- Applied by `python -m graph.schema`, which PARSES THE ONTOLOGY, discovers the
-- taxonomy categories and emits one constraint + one index per category. The
-- graph schema therefore always matches the ontology it was derived from.
--
-- The ten categories are FIXED at platform version v3 (platform/nxr-taxonomy.ttl)
-- and a pack may never introduce an eleventh — enforced by
-- nxr:TaxonomyClosureShape over the ontology itself.
--
--   CREATE CONSTRAINT uniq_physicalasset_tenant_id IF NOT EXISTS
--       FOR (n:PhysicalAsset) REQUIRE (n.tenantId, n.id) IS UNIQUE;
--   CREATE INDEX idx_physicalasset_updated_at IF NOT EXISTS
--       FOR (n:PhysicalAsset) ON (n.updatedAt);
--
--   ... repeated for: MobileAsset, Actor, Location, Process, Observation,
--                     Finding, Incident, Document, Capability
--
--   -- the eleventh label: the graph-side change log
--   CREATE CONSTRAINT uniq_changelog_tenant_id IF NOT EXISTS
--       FOR (n:ChangeLog) REQUIRE (n.tenantId, n.id) IS UNIQUE;
--   CREATE INDEX idx_changelog_seq IF NOT EXISTS
--       FOR (n:ChangeLog) ON (n.tenantId, n.seq);
--   CREATE INDEX idx_changelog_entity IF NOT EXISTS
--       FOR (n:ChangeLog) ON (n.tenantId, n.entityId);
--
-- Node properties: tenantId, id, canonicalType (the OWL class IRI), displayName,
-- status, updatedAt, plus the class's observable properties written as live
-- signal values by the machine-twin runtime's persist().
--
-- Relationships are ontology object properties written as CURIEs and resolved
-- through graph/writer.py PREFIXES (e.g. hvac:servesSpace, railway:feedsSection,
-- solar:combinesString). An unregistered prefix is REJECTED rather than silently
-- creating an untyped edge.
--
-- ============================================================================
-- APPENDIX B — verification queries
-- ============================================================================
--
-- Tables and row counts (what `python -m db.schema --check` reports):
--   SELECT 'twins' t, count(*) FROM twins
--   UNION ALL SELECT 'events',            count(*) FROM events
--   UNION ALL SELECT 'published_bundles', count(*) FROM published_bundles
--   UNION ALL SELECT 'checkpoints',       count(*) FROM checkpoints
--   UNION ALL SELECT 'scene_cache',       count(*) FROM scene_cache
--   UNION ALL SELECT 'threed_jobs',       count(*) FROM threed_jobs
--   UNION ALL SELECT 'ingest_devices',    count(*) FROM ingest_devices
--   UNION ALL SELECT 'connectors',        count(*) FROM connectors;
--
-- Is TimescaleDB actually installed (the check behind health.historian.backend)?
--   SELECT extversion FROM pg_extension WHERE extname = 'timescaledb';
--
-- Is measurements really a hypertable, and is compression on?
--   SELECT hypertable_name, compression_enabled
--     FROM timescaledb_information.hypertables;
--
-- Verify one tenant's change-log chain is intact (should return zero rows):
--   SELECT a.seq
--     FROM events a
--     JOIN events b ON b.tenant_id = a.tenant_id AND b.seq = a.seq - 1
--    WHERE a.tenant_id = :tenant
--      AND a.prev_event_hash <> b.wm_hash;
--
-- ============================================================================
