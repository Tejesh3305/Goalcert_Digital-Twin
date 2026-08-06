# AWS Deployment — NextXR Digital Twin

The authoritative runbook for deploying the twin to AWS. Section numbers are
stable — other files reference them (the `Dockerfile`, `.env.example §5.2`,
`paths.py §7`, and the auth notes at §10).

The system is a **single-container monolith**: one FastAPI process serves the
REST API **and** the built React UI, with the GPU reconstruction step offloaded
to an external RunPod endpoint. It is deliberately close to Fargate-ready — the
image is non-root, `$PORT`-driven, healthchecked, and serves its own frontend.

---

## 0. Read this first — what changed

Everything below §1 remains accurate as the *explanation* of the topology. Two
things about the *mechanics* have changed, and they change what you actually run.

### Deployment is manual, and §12 is the procedure

There is no Terraform and no deploy pipeline. Both were removed deliberately:
the infrastructure is provisioned once and changes rarely, and a generated plan
nobody reads is worse than a checklist somebody follows. **§12 is the runbook** —
ordered steps, each ending in something you can check.

Two consequences you have to own by hand, because nothing else will:

1. **Run the migrations BEFORE you roll the service** (§14). This is the step a
   pipeline existed to make unforgettable. Adding a column and redeploying
   without it produces a fleet whose queries reference a column that does not
   exist. Run `python -m db.migrations` as a one-off task, confirm it exits 0,
   *then* update the service.
2. **Tag images with the commit SHA, never `latest`** (§12.1). An immutable tag
   is what makes a rollback a task-definition change rather than a rebuild, and
   it is the only thing that answers "which build is running".

`NXR_AUTO_MIGRATE` stays `0` regardless: several tasks starting at once would
queue on the advisory lock and the ones that wait would fail their health check
(§14).

### Applying a schema change without a second deploy

Most schema changes need no migration at all. `db/schema.py` is the definition of
every table and `schema.ensure()` **reconciles**: it creates tables that do not
exist and `ALTER TABLE ... ADD COLUMN`s any declared column an existing table is
missing. So:

| Change | What it takes |
| ------ | ------------- |
| Add a table | declare it in `db/schema.py` → reconcile |
| Add a column | declare it in `db/schema.py` → reconcile |
| Drop / rename / retype a column | write a migration — it needs a data decision |
| Backfill a value | write a migration with a `run=` callable |

Reconcile only ever ADDs. Nothing in it drops, renames or retypes, because each
of those can lose data and belongs in a reviewed, ledgered migration.

`NXR_DB_ADMIN_API=1` mounts an admin-scoped surface that runs both against the
**live** database, so the apply step no longer needs a one-off ECS task or an
`ecs execute-command` shell:

```bash
BASE=https://<your-host>/api/v1/admin/db
curl -H "X-API-Key: $ADMIN_KEY" $BASE/status        # applied vs pending
curl -H "X-API-Key: $ADMIN_KEY" $BASE/verify        # schema.py vs migrations drift
curl -X POST -H "X-API-Key: $ADMIN_KEY" $BASE/reconcile          # add tables/columns
curl -X POST -H "X-API-Key: $ADMIN_KEY" "$BASE/migrate"          # dry run (default)
curl -X POST -H "X-API-Key: $ADMIN_KEY" "$BASE/migrate?dry_run=false"
```

Gated twice: the env flag decides whether the routes exist (404 otherwise), and
an admin scope decides who may call them. `migrate` defaults to `dry_run=true` —
applying schema changes because a parameter was omitted is not a behaviour worth
having.

**What this does not remove.** The table definitions live in `db/schema.py`,
which is code, so *defining* a new table still ships with an image. What you no
longer need is the separate migration ceremony after the rollout. There is
deliberately no free-form-SQL route: that would move your schema out of version
control and put `DROP TABLE` one HTTP call from anyone holding an admin key.

### The API is closed by default, and it has users

The two changes that matter most for a deploy:

**1. Authentication no longer fails open.** It used to be served without a
credential whenever `NXR_API_KEYS` was unset — so a deployment that forgot one
variable was open to the internet, `/copilot` LLM billing included. The default
is now REQUIRED; `NXR_DEV_MODE=1` is the explicit local opt-out.

**2. There are accounts.** Users, organisations, memberships, sessions,
database-backed API keys, and an audit log — see `identity/` and §13. People sign
in at `/login`; machines use keys issued from the UI. Revoking either is
immediate and needs no redeploy.

Consequences for the task definition:

| Variable | | |
|---|---|---|
| `NXR_JWT_SECRET` | **required** | The app refuses to boot without it while auth is enforced. Each task would otherwise sign tokens the others reject — intermittent 401s that look like a client bug. |
| `NXR_SECRET_PEPPER` | recommended | Peppers API-key and refresh-token hashes. Rotating it invalidates every key and session. |
| `NXR_BOOTSTRAP_ADMIN_EMAIL` / `_PASSWORD` | first deploy only | Creates the first platform admin. **Remove after it runs.** |
| `NXR_AUTO_MIGRATE` | `0` | Migrations run as a one-off task, not at container start — several tasks would race the advisory lock and fail their health checks. |

### The health check moved

| Endpoint | 503s? | Point this at |
|---|---|---|
| `/api/v1/health/live` | never | ECS/Docker **liveness**, and the Dockerfile HEALTHCHECK |
| `/api/v1/health/ready` | **yes** | The **ALB target group** |
| `/api/v1/health` | never | Dashboards and CloudWatch alarms (unchanged) |

The target group previously pointed at `/api/v1/health`, which never fails — so a
task with no database stayed in rotation serving errors, and a rolling deploy
shifted all traffic to new tasks before they could answer.

---

## 1. Runtime topology

```
Route 53 ─▶ CloudFront (optional) ─▶ ALB ─▶ ECS Fargate service
                                              └─ N× task: nextxr-twin container (:8080)
                                                   env      ← task definition
                                                   secrets  ← Secrets Manager
                                              DESIRED COUNT = 2+  (see §9)
                                              │   THE TASK IS STATELESS
                  ┌───────────────┬───────────┴──┬──────────────┬───────────────────┐
                  ▼               ▼              ▼              ▼                    ▼
          RDS MySQL 8          S3        ElastiCache      Neo4j Aura        RunPod serverless
            (Multi-AZ)        (blobs:      Redis 7          (or EC2)         GPU (TRELLIS) —
          via RDS Proxy       GLBs +      (event bus)                            external
                             artifacts)
```

Four stores, each with one job:

| Store | Holds | §  |
|---|---|---|
| **RDS MySQL 8** | records: twin registry, change log, identity/auth, agent bundles, checkpoints, scene cache, 3-D job records, telemetry historian | §7 |
| **S3** | blobs: generated GLBs and 3-D job artifacts | §7.3 |
| **ElastiCache Redis 7** | the live event bus (one stream per tenant) | §9 |
| **Neo4j** | the twin's graph: entities, relationships, findings | §6 |

Everything else — the API process, the LLM, the GPU worker — is stateless or
external. **The ECS task itself holds nothing durable**, which is what makes
desired count > 1 and rolling deploys safe.

> **Changed from the SQLite/EFS design.** Relational state used to be five SQLite
> files on an EFS mount and generated models were local files. SQLite over
> NFS/EFS is single-writer and local files are per-task, so the service was
> pinned at one task. Moving records to RDS and blobs to S3 is what lifted that.
> Each store still has a task-local fallback for offline dev — and a required-flag
> so a deploy cannot land on one by accident (§9).

---

## 2. Build & image

`docker build -t nextxr-twin .` — the `Dockerfile` is multi-stage:

1. `node:20-slim` builds `frontend/dist`.
2. `python:3.12-slim` installs `requirements.txt`, copies `nextxr-ontology/` and the
   built `dist`, runs as non-root **uid/gid 10001**, exposes `:8080`, and
   healthchecks `GET /api/v1/health/live` — liveness only, so a slow RDS or S3
   cannot time the probe out and get a working process killed (§0).

The container serves the UI at `/`, so a first deploy needs **no S3/CloudFront** —
this is the true one-URL deploy. Add CloudFront later only if you federate the UI
into the Goalcert hub (then set `VITE_REMOTE_BASE` at build time to the CloudFront
origin, or hub-embedded assets 404).

---

## 3. AWS resources (prerequisites)

| Resource | Purpose |
|---|---|
| ECR repository | holds the built image |
| ECS cluster + Fargate service + task def | runs the container (desired count **2+**) |
| Application Load Balancer + target group | fronts `:8080`; health check path `/api/v1/health/ready` (§0) |
| **RDS MySQL 8** (Multi-AZ), private subnets | the relational store (§7) |
| **S3 bucket** | blobs: generated GLBs + 3-D artifacts (§7.3) |
| **ElastiCache Redis 7** (Multi-AZ), private subnets | the event bus (§9) |
| **RDS Proxy** (optional but recommended) | connection pooling across tasks (§7.5) |
| Secrets Manager secrets | API keys, DB URL/password (§5.2, §10) |
| Neo4j Aura instance (or Neo4j on EC2) | the graph DB (§6) |
| RunPod serverless endpoint | GPU reconstruction (§8) — external to AWS |
| EFS filesystem + access point (uid/gid **10001**) | **optional** once S3 is set (§7.4) |

Security groups: RDS (3306) and ElastiCache (6379) accept traffic **only** from
the ECS task security group (and RDS Proxy), and live in the isolated/private
subnets — neither is ever publicly addressable. Add an **S3 gateway VPC
endpoint** so blob traffic skips the NAT gateway's per-GB charge.

---

## 4. First-deploy checklist (minimum to be safe & durable)

Full step-by-step with commands is §12; rehearse it locally first with §11. The
short version — the things whose absence is silent:

1. **Point at all three shared stores**: `NXR_DATABASE_URL` (a *secret*, §7),
   `NXR_S3_BUCKET` (§7.3), `NXR_REDIS_URL` (§9). Any one left unset falls back to
   task-local storage and **nothing fails** — you get a fleet that serves
   different twins, 404s its own models, or stops sending live updates to half
   the users.
2. **Set the three guards**: `NXR_REQUIRE_DB=1`, `NXR_REQUIRE_S3=1`,
   `NXR_REQUIRE_REDIS=1` (§9). This is what converts each of those into a loud
   startup failure. Do not skip it because "the URLs are set" — that is exactly
   the assumption the guards exist to check.
3. **Provision the schema**: `python -m db.schema` (§7.1). The app self-provisions
   on first use, but running it explicitly turns a permissions problem into a
   clear error before traffic arrives.
4. **Set `NXR_API_KEYS`** (+ `NXR_REQUIRE_AUTH=1`) — closes the fail-open hole
   (§10). Without it the API is public and unauthenticated.
5. **Move every key to Secrets Manager** (§5.2) — nothing in `.env` on the task.
6. **Stand up managed Neo4j** and rotate off the `nextxr2026` default (§6).
7. **Restrict CORS** with `NXR_CORS_ORIGINS` (§10).
8. **Provision the database** on the fresh RDS MySQL: `python -m db.schema`,
   `python -m db.migrations`, `python -m tools.historian_provision` (§7.1). There
   is no SQLite→MySQL data cutover (§7.2).
9. **Read the four posture lines** in CloudWatch after the rollout (§12.9):
   `[auth]`, `[db]`, `[blobs]`, `[bus]`. They state the posture in plain words
   and take ten seconds to check.

### 4.1 What you must supply — the operator's input list

Everything the repository cannot know. Nothing here is in version control, and
each line is something a deploy stalls on if it is missing.

**You do NOT need to supply a DB schema.** This is the most common assumption
and it is wrong: `db/schema.py` *is* the schema — 16 tables of declarative DDL —
and `python -m db.schema` creates all of them against an empty database. What
you supply is an **empty MySQL 8 database and a user that can `CREATE TABLE` in
it**. Do not hand-write SQL, and do not import a dump.

| # | You provide | Where it goes | Notes |
|---|---|---|---|
| 1 | **RDS MySQL 8 endpoint + user + password + empty DB name** | `NXR_DATABASE_URL` secret | `mysql://user:pass@host:3306/nextxr`. **MySQL 8.0+, not 5.7/MariaDB** — the historian uses `ROW_NUMBER() OVER` (§7) |
| 2 | **S3 bucket name** (Block Public Access ON) | `NXR_S3_BUCKET` env | plus a task role with object read/write (§7.3). No access keys |
| 3 | **ElastiCache Redis 7 endpoint** | `NXR_REDIS_URL` env | `redis://host:6379/0` (§9) |
| 4 | **Neo4j Aura URI + password** | `NEO4J_URI` env, `NEO4J_PASSWORD` secret | rotate off the `nextxr2026` dev default (§6) |
| 5 | **`NXR_JWT_SECRET`** | secret | **32+ random bytes.** The app will not boot without it. `python -c "import secrets;print(secrets.token_urlsafe(48))"` |
| 6 | **`NXR_SECRET_PEPPER`** | secret | same generator. Set it once and never rotate casually (§13) |
| 7 | **First admin email + password** | `NXR_BOOTSTRAP_ADMIN_EMAIL` / `_PASSWORD` | **first deploy only, then delete both** (§13) |
| 8 | **`ANTHROPIC_API_KEY`** | secret | only if `/copilot` is used |
| 9 | **`RUNPOD_API_KEY` + `RUNPOD_ENDPOINT_ID`** | secrets | only if photo→3-D is used; unset stubs the step (§8) |
| 10 | **Public hostname + ACM certificate** | ALB listener, `NXR_CORS_ORIGINS` | |
| 11 | **VPC, 2 AZs, subnets, security groups** | §12.2 | |

**There is no `.env` file on AWS.** `.env` is gitignored and local-only;
`.env.example` is the annotated catalogue of every variable and is the file to
read, but on ECS these values are task-definition `environment:` entries and
Secrets Manager `secrets:` references (§5.2). Nothing is baked into the image —
`.dockerignore` excludes `.env` for exactly this reason.

---

## 5. Deployment shape (ECS Fargate)

Fargate tasks built from the `Dockerfile`, behind an ALB, **desired count 2+**
across two AZs (see §9).

The ALB target-group health check uses **`GET /api/v1/health/ready`**, which
*is* allowed to 503 — that is the whole point of it. A task that cannot reach a
configured MySQL, or that is running on the in-memory bus while
`NXR_REQUIRE_REDIS=1`, drops out of rotation without being killed, and rejoins
on its own when the dependency recovers.

Readiness is deliberately narrower than health: **Neo4j being down is not
unready.** The twin registry, schema API, auth and the SPA all still serve, and
the documented product behaviour is to degrade rather than disappear — draining
every task for a graph outage would be a self-inflicted outage larger than the
fault. So still add a CloudWatch alarm on `/api/v1/health`'s `degraded` status
(§12.10): that is the only signal that reports a sick Neo4j.

### 5.1 Task definition (essentials)

- **Container port** 8080; ALB → target group → 8080.
- **User** uid/gid 10001 (baked into the image).
- **Task role** with S3 object read/write on the blob bucket (§7.3) — this is the
  app's own identity, distinct from the execution role that injects secrets.
- **No volume required.** Mount EFS only if you want 3-D scratch to survive a
  task replacement (§7.4); if you do, the access point must use uid/gid **10001**
  or the first write to `/data` fails.
- **Logging** to CloudWatch (`awslogs` driver). `PYTHONUNBUFFERED=1` is already set,
  so stdout — the `[auth]`, `[db]`, `[blobs]` and `[bus]` posture lines — reaches
  CloudWatch.

### 5.2 Environment & secrets

Plain **environment** (task-def `environment:`):

| Var | Value | Notes |
|---|---|---|
| `PORT` | `8080` | ALB target port |
| `NEO4J_URI` | `neo4j+s://<aura-id>.databases.neo4j.io` | managed Neo4j (§6) |
| `NEO4J_USER` | `neo4j` | |
| `NXR_S3_BUCKET` | `nextxr-twin-blobs` | blob store (§7.3); access via the task role |
| `NXR_S3_PREFIX` | `prod` | optional — share one bucket across environments |
| `NXR_REDIS_URL` | `redis://…cache.amazonaws.com:6379/0` | event bus (§9) |
| `NXR_REQUIRE_DB` | `1` | refuse to start on the SQLite fallback (§9) |
| `NXR_REQUIRE_S3` | `1` | refuse to start on local-disk blobs (§9) |
| `NXR_REQUIRE_REDIS` | `1` | refuse to start on the in-memory bus (§9) |
| `NXR_DB_POOL_MAX` | `10` | per-task pool ceiling; server-side total is tasks × this (§7.5) |
| `NXR_DB_SSL_CA` | `/path/rds-ca.pem` | verify RDS TLS (or `NXR_DB_SSL=1` for TLS without CA pinning) (§7) |
| `NXR_REQUIRE_AUTH` | `1` | enforce auth even before keys load (§10) |
| `NXR_CORS_ORIGINS` | `https://app…,https://hub…` | allow-list (§10) |
| `AWS_REGION` | `ap-southeast-2` | region for the S3 client; must match the bucket |
| `NXR_TRUST_PROXY` | `1` | honour `X-Forwarded-For` behind the ALB, or every audit-log entry and rate-limit bucket keys off the ALB's IP (§10) |
| `NXR_HSTS` | `1` | emit `Strict-Transport-Security`; set only once HTTPS is terminated at the ALB |
| `NXR_AUTO_MIGRATE` | `0` | migrations run as a one-off task, never at container start (§14) |
| `NXR_DB_ADMIN_API` | unset | set `1` **only** to mount `/api/v1/admin/db` (§0); 404 otherwise |
| `NXR_DATA_DIR` | `/data` | only if you mount EFS (§7.4); scratch, not durable state |
| `DATA_DIR` | `/data/threed` | ditto — the 3-D platform's own working dir |

**Secrets** (task-def `secrets:` → Secrets Manager / SSM — never in `environment`):

| Secret | Required? | Why it is a secret |
|---|---|---|
| `NXR_DATABASE_URL` | **yes** | embeds the RDS password |
| `NXR_JWT_SECRET` | **yes** | **32+ random bytes.** `identity/tokens.require_secret()` refuses to boot without it while auth is enforced — otherwise each task signs with its own random key and a token minted by one task is rejected by every other (intermittent 401s that look like a client bug) |
| `NXR_API_KEYS` | yes | break-glass machine credentials (§10) |
| `NEO4J_PASSWORD` | **yes** | the driver refuses to connect without it once auth is enforced (§6) |
| `NXR_SECRET_PEPPER` | recommended | peppers API-key and refresh-token hashes; rotating it invalidates every key and session (§13) |
| `ANTHROPIC_API_KEY` | if `/copilot` is used | LLM billing |
| `RUNPOD_API_KEY` / `RUNPOD_ENDPOINT_ID` | if 3-D is used | GPU reconstruction (§8); unset stubs the step |
| `REPLICATE_API_TOKEN` | optional | alternate 3-D backend |
| `NXR_BOOTSTRAP_ADMIN_PASSWORD` | **first deploy only** | creates the first admin — **remove afterwards** (§13) |

`NXR_DATABASE_URL` is a secret because it embeds the password. If you prefer to
keep the URL in plain `environment`, use RDS **IAM authentication** or store only
the password in Secrets Manager and assemble the URL in an entrypoint — do not
put a password-bearing URL in `environment:`, where it shows in the console and
in `describe-task-definition`.

> Note the historical config sprawl: the 3-D platform reads its own
> `threed_platform/.env` locally. On ECS, inject `RUNPOD_*`/`REPLICATE_*` as task
> secrets so there is one source of truth.

---

## 6. Neo4j (graph database)

No AWS-managed Neo4j is wired up by default. Two options:

- **Neo4j Aura** (recommended, least ops) — set `NEO4J_URI` to the `neo4j+s://…`
  endpoint and put the password in Secrets Manager.
- **Neo4j on EC2/ECS** with an EBS volume — more control, more ops.

Either way, **rotate off the `nextxr2026` default** baked into `graph/connection.py`
and `docker-compose.yml` (both now read `NEO4J_PASSWORD` and only fall back to that
value for local dev). Reads degrade to empty and writes 503 while Neo4j is
unreachable — a deploy is not "done" while `/api/v1/health` reports `degraded`.

---

## 7. State: RDS MySQL 8

All relational state lives in one MySQL database, reached via `NXR_DATABASE_URL`.
**MySQL 8.0+ is required** (not 5.7 / MariaDB): the historian's "latest value" and
rollup queries use window functions (`ROW_NUMBER() OVER …`) that only exist in 8.

`db/schema.py` declares **16 tables** and `python -m tools.historian_provision`
adds a 17th (`measurements`). The full set — this is what `--check` verifies:

| Table | Holds | Was |
|---|---|---|
| `twins` | the twin registry — every twin a user has created | `twins.db` |
| `events` | the governance change log (per-tenant hash chain) | `changelog.db` |
| `published_bundles` | agent-authored capability bundles | `bundles.db` |
| `checkpoints` | agent graph checkpoints (human-in-the-loop resume) | `agent_checkpoints.db` |
| `scene_cache` | BIM scene graphs for the 3-D viewer | `data/scenes/*.json` |
| `threed_jobs` | photo→GLB job records (the artifacts themselves go to S3, §7.3) | local JSON |
| `ingest_devices` | registered telemetry devices / connectivity credentials | — |
| `connectors` | external system connector configurations | — |
| `organizations` | tenant-owning organisations | `identity.db` |
| `users` | accounts, password hashes, `is_platform_admin` | `identity.db` |
| `memberships` | user↔org role grants (`owner`/`admin`/`write`/`read`, §13) | `identity.db` |
| `sessions` | refresh-token session families (reuse detection, §13) | `identity.db` |
| `api_keys` | database-backed machine credentials (peppered hashes) | `identity.db` |
| `org_tenants` | **the ownership table** `server/tenancy.py` enforces against | `identity.db` |
| `auth_tokens` | password-reset / invite tokens | `identity.db` |
| `audit_log` | who did what, incl. `session.reuse_detected` (§13) | `identity.db` |
| `measurements` | the telemetry historian — **provisioned separately** (§7.1) | `historian.db` |

`measurements` is the only one not in `db/schema.py`; it is created by
`python -m tools.historian_provision`, which is why that command is a distinct
step in §7.1 and §12.6 rather than something `db.schema` covers.

`field_changes`, `payload`, `domains`, `state` and `scene` are **`JSON`** columns
(MySQL has no `JSONB`; `JSON` is queryable in place), so the change log and scene
graphs are not opaque text. Auto-increment keys are `BIGINT AUTO_INCREMENT` (no
sequences). Identifier/indexed columns are `VARCHAR` (MySQL cannot index bare
`TEXT`).

**Instance shape.** `db.t4g.medium` + gp3 is a fine starting point — the records
and audit log are small (live physics state is in-process and on Redis; the graph
is in Neo4j). The historian's `measurements` table is the one that grows: it is a
plain table with no columnar compression and no automatic retention, so size the
instance for your telemetry volume, or add an app-level purge (see
`historian.retention_days`) / partitioning if ingest is heavy. Multi-AZ for
failover; enable automated backups (the change log is an audit record and PITR is
the point).

**Extensions.** None. MySQL needs no `CREATE EXTENSION` step (there is no pgvector
/ PostGIS equivalent, and nothing queried them). `python -m db.schema` alone
provisions everything.

### 7.1 Provisioning

```bash
# TLS: append the RDS CA via NXR_DB_SSL_CA, or set NXR_DB_SSL=1 (see §5.2).
export NXR_DATABASE_URL='mysql://nextxr:PASS@twin.xxxx.rds.amazonaws.com:3306/nextxr'
python -m db.schema              # create every table (idempotent)
python -m db.schema --check      # report only, create nothing
python -m db.migrations          # apply ordered schema changes
python -m tools.historian_provision   # create the measurements table
```

Run it from a bastion/ECS-exec session inside the VPC — RDS is not publicly
reachable. The app also self-provisions the relational tables on first use, so
this is a fail-early check rather than a hard prerequisite.

### 7.2 Fresh database only — no SQLite cutover

The standalone deploy starts from a **fresh MySQL database**; there is no
supported SQLite→MySQL data import (the old `db.migrate` importer was
Postgres-specific and is retired). Provision with the commands in §7.1. If you
must lift data from an old SQLite dev instance, do it with a purpose-built
export/import rather than an in-app tool.

### 7.3 Blobs — S3 (`NXR_S3_BUCKET`)

Generated GLBs and 3-D job artifacts go to S3, keyed rather than pathed:

```
s3://<bucket>/<prefix>/threed/jobs/<job_id>/input/<original>
s3://<bucket>/<prefix>/threed/jobs/<job_id>/artifacts/<stage>/model.glb
```

Unset, the same code writes under `NXR_DATA_DIR/blobs/`. That is correct offline
and **wrong on a multi-task deploy**: the task that ran the reconstruction has
the GLB and every other task 404s it — for a model the user just watched being
generated. Set `NXR_REQUIRE_S3=1` so that misconfiguration fails at boot.

- **Access:** grant the ECS **task role** `s3:GetObject`, `s3:PutObject`,
  `s3:DeleteObject` on `arn:aws:s3:::<bucket>/*` and `s3:ListBucket` on the
  bucket. No access keys in the environment.
- **Health checks the write path**, not just reachability: `/api/v1/health`
  puts, reads back and deletes a probe object, because a bucket you can list but
  not write to is the usual IAM mistake and reads fine right up until the first
  upload.
- **`GET /api/jobs/{id}/result` redirects to a presigned URL** when the backend
  is S3, so a 20 MB GLB streams from S3 instead of occupying an API worker.
- **Bucket settings:** Block Public Access ON (presigned URLs still work),
  default encryption (SSE-S3 is enough), versioning optional. A lifecycle rule
  expiring `threed/jobs/*/artifacts/` after 30–90 days is worth adding — the
  intermediate stage images are debugging aids, not product data.

Mid-pipeline scratch still lands on local disk (`DATA_DIR`) while a stage runs;
finished artifacts are published to S3 as each stage completes. That is why
stages can keep using `trimesh.export(path)` unchanged.

### 7.4 EFS — optional once S3 is configured

With `NXR_S3_BUCKET` set, nothing durable is left on the volume: relational state
is in RDS and blobs are in S3, so **the task is stateless** and you can drop the
EFS mount entirely. Keep it only if you want the 3-D pipeline's scratch to
survive a mid-job task replacement (it does not resume anyway, so this is
rarely worth it).

If you do mount it, use an **EFS access point** at `/data` with uid/gid
**10001** — the image runs as that user and the first write fails otherwise.

### 7.5 Connection pooling

Each task holds its own pool (`NXR_DB_POOL_MIN`/`NXR_DB_POOL_MAX`, default 1–10),
so the server-side total is `tasks × NXR_DB_POOL_MAX`. Put **RDS Proxy** in front
and point `NXR_DATABASE_URL` at the proxy endpoint: it multiplexes those
connections, survives failover without the fleet reconnecting in lockstep, and
can hold IAM credentials. Size `NXR_DB_POOL_MAX` against the instance's
`max_connections`, remembering that migrations and bastion sessions need headroom
too.

---

## 8. GPU reconstruction (RunPod)

`apps/trellis-worker/` is a RunPod **serverless GPU** container (CUDA 12.1, 24 GB
VRAM). It is deployed **on RunPod, not AWS**; the API is a thin HTTPS client
(`threed_platform/app/stages/reconstruct.py`). Set `RUNPOD_API_KEY` and
`RUNPOD_ENDPOINT_ID` (secrets). Keep GPU off AWS unless you specifically want
ECS-GPU / `g5` instances (expensive). If both RunPod vars are unset the pipeline
stubs the reconstruction step.

---

## 9. Scaling & known constraints

- **Multi-task is now legal.** With relational state in RDS (§7), desired count
  2+ across two AZs and rolling deploys are safe. That was the single biggest
  architectural constraint and it is gone.
- **The three per-task fallbacks are the whole risk, and they are now guarded.**
  Each shared store silently degrades to something task-local when unconfigured.
  All three are correct at one task and wrong at two, and none of them *errors* —
  which is why each has a required-flag that turns the mistake into a failed boot:

  | Unset | Silent failure at 2+ tasks | Guard |
  |---|---|---|
  | `NXR_DATABASE_URL` | per-task SQLite; each task serves different twins | `NXR_REQUIRE_DB=1` |
  | `NXR_S3_BUCKET` | model generated on task A 404s on task B | `NXR_REQUIRE_S3=1` |
  | `NXR_REDIS_URL` | each task sees only its own events; live updates stop for some users | `NXR_REQUIRE_REDIS=1` |

  Set all three flags for any multi-task service. A task that crash-loops with a
  one-line reason in CloudWatch is a far better outcome than a fleet that serves
  half the twins and half the models.
- **Sticky sessions are not required.** Agent checkpoints are in MySQL, so a
  run interrupted for human approval on one task resumes on another.
- **Live physics has one owner per twin** (`twins/coordinator.py`). The machine-twin
  runtime is a stateful integrator — wear accumulates, an injected fault persists —
  so running it in every task would mean every threshold breach written to the graph
  once per task, and a fault injected on one task invisible from the others. Instead
  each tenant's simulation is held by whichever task owns a short **Redis lease**:

  - the owner ticks the physics, evaluates the behaviour registry and persists
    findings **once**, and publishes the authoritative state every second;
  - other tasks never tick and never persist — they serve the published state, so
    every task reports identical values, and forward control actions (throttle,
    fault injection, start/stop) to the owner through a per-tenant command queue;
  - if an owner dies the lease expires after ~8s and another task **adopts the
    published state**, continuing the simulation rather than restarting it.

  Ownership is per tenant, so the simulation load still spreads across the fleet
  and there is no singleton service to keep alive. `/api/v1/health` reports this
  task's `twin_runtime.owned` list — across the fleet each tenant should appear
  exactly once. **This is another reason `NXR_REQUIRE_REDIS=1` matters:** without
  Redis every task believes it owns every twin, which is precisely the duplicate-write
  behaviour above.
- **The task is stateless** once RDS + S3 + ElastiCache are configured. Nothing
  durable is left on local disk or EFS (§7.4), so tasks are disposable and
  rolling deploys are safe.
- **Change-log writes serialise per tenant.** `append()` takes a MySQL
  `GET_LOCK()` advisory lock for the length of its transaction so a tenant's hash
  chain cannot fork under concurrent writers (the lock is released deterministically
  at transaction end, since MySQL's `GET_LOCK` is session- not transaction-scoped).
  Tenants never block each other, but a single tenant's write throughput is bounded
  by that lock — which is the correct trade for a tamper-evident ledger.

---

## 10. Security posture

- **Authentication is required by default.** This is the inversion of the previous
  posture. With nothing configured, the API now REFUSES requests rather than
  serving them; `NXR_DEV_MODE=1` is the explicit local opt-out. The old default
  meant every deployment that forgot a variable was open to the internet,
  `/copilot` LLM billing included, with only a boot-log warning to say so.
  The app still prints an `[auth]` line at startup — check it after every deploy.
- **The built-in demo keys are gone.** `nxr-demo-key` and `nxr-read-only` used to
  be minted whenever `NXR_API_KEYS` was unset, so anyone who read the source had
  an admin credential on any deployment that forgot to configure keys. There is
  no default credential of any kind now.
- **Accounts are the primary credential.** Users, organisations, memberships and
  database-backed API keys live in RDS (`identity/`), so revoking access is
  immediate rather than a redeploy. `NXR_API_KEYS` still works as the break-glass
  path when the identity tables are unreachable.
- **`NXR_JWT_SECRET` is mandatory.** The app refuses to boot without it while auth
  is enforced — see §13.
- **Passwords** are Argon2id (scrypt fallback), never reversible. API keys and
  refresh tokens are stored as peppered SHA-256 and returned exactly once.
- **Rate limiting is on by default** (`server/ratelimit.py`), bounding password
  guessing on `/auth/login` and spend on `/copilot`. Set `NXR_REDIS_URL` so the
  buckets are shared across tasks; without it each task counts separately and the
  fleet-wide limit is N× the configured value.
- **Security headers** are set on every response — `X-Content-Type-Options`,
  `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, a CSP on the SPA,
  and HSTS when `NXR_HSTS=1`. Set `NXR_TRUST_PROXY=1` behind the ALB so
  `X-Forwarded-For` is honoured for audit logs and rate-limit keys.
- **CORS:** same-origin only by default once auth is enforced (the container
  serves the SPA and the API together). Set `NXR_CORS_ORIGINS` only for a
  separately-hosted frontend.
- **`NEO4J_PASSWORD` is mandatory.** The development password is published in this
  repository and is no longer a fallback — the driver refuses to connect without
  an explicit password once auth is enforced.
- **Database:** RDS MySQL in private/isolated subnets, security group open to the
  task SG only, storage encrypted at rest, and TLS enforced in transit — set
  `NXR_DB_SSL_CA` to the RDS CA bundle (or `NXR_DB_SSL=1` for TLS without CA
  pinning); without it the driver connects unencrypted. Rotate the password
  through Secrets Manager, or skip it entirely with RDS IAM auth.
- **S3:** Block Public Access ON; the task role gets object read/write on this
  bucket only (§7.3). Presigned URLs still work with public access blocked.
- **Health check** never 503s by design; add a CloudWatch alarm on the `degraded`
  status so a broken dependency is visible even though the task stays "healthy".
  The payload reports `database`, `blobs`, `bus` and `neo4j` separately, so the
  alarm can say *which*.
- **Secrets** live in Secrets Manager / SSM, injected via the task-def `secrets:`
  block — never baked into the image (`.dockerignore` already excludes `.env` files).

---

## 11. Rehearse the deploy locally (do this first)

`docker compose` runs the same shared stores the deploy uses — MySQL 8, Redis 7
and **MinIO** (S3-compatible). With the required-flags on, a local run exercises
the exact code paths ECS will, so a configuration mistake surfaces on your machine
instead of in a rollout.

```powershell
# 1. Start every dependency (Neo4j, MySQL, Redis, MinIO + bucket creation)
docker compose up -d

# 2. Point the app at them, in production posture
$env:NXR_DATABASE_URL   = "mysql://nextxr:nextxr2026@localhost:3306/nextxr"
$env:NXR_REDIS_URL      = "redis://localhost:6379/0"
$env:NXR_S3_BUCKET      = "nextxr-blobs"
$env:NXR_S3_ENDPOINT_URL      = "http://localhost:9000"   # MinIO; unset for real S3
$env:NXR_S3_ADDRESSING_STYLE  = "path"                    # MinIO needs path style
$env:AWS_ACCESS_KEY_ID        = "nextxr"
$env:AWS_SECRET_ACCESS_KEY    = "nextxr2026"
$env:AWS_REGION               = "us-east-1"
$env:NXR_REQUIRE_DB = "1"; $env:NXR_REQUIRE_S3 = "1"; $env:NXR_REQUIRE_REDIS = "1"

# 3. Provision the schema, then run
cd nextxr-ontology
python -m db.schema
python -m db.migrations
python -m tools.historian_provision
python -m server.main
```

> Already running MySQL natively? It owns port 3306 and the container cannot bind
> it. Start with `MYSQL_PORT=3307 docker compose up -d mysql` and use 3307 in the
> URL.

**What a correct start looks like.** Four posture lines, and they are the same
four you will read in CloudWatch:

```
[auth]  API key enforcement ON - NXR_API_KEYS configured.
[db]    MySQL - mysql://nextxr:***@localhost:3306/nextxr (pool 1-10 per task)
[blobs] S3 - bucket=nextxr-blobs prefix=/ endpoint=http://localhost:9000
[bus]   Redis Streams - redis://localhost:6379/0
```

Any line reading `SQLite`, `local filesystem` or `IN-MEMORY` is a task-local
fallback and must not appear in a multi-task deploy.

**Verify:**

```powershell
curl http://localhost:8080/api/v1/health     # every component "connected", status "healthy"
python tools/track3_gate.py                  # write path + hash chain (23 checks)
python bus/bus_test.py                       # event bus contract (22 checks)
cd server/threed_platform; python selftest.py   # full photo->GLB, artifacts to S3
```

`/api/v1/health` must show `status: "healthy"` with `database.backend: mysql`,
`blobs.backend: s3` and `bus.backend: redis` + `scale_safe: true`. Anything else
is the thing that would have broken in production.

**Prove the guards work** — each of these must refuse to start:

```powershell
$env:NXR_DATABASE_URL=""; python -m server.main   # -> NXR_REQUIRE_DB is set but ...
$env:NXR_S3_BUCKET="";    python -m server.main   # -> NXR_REQUIRE_S3 is set but ...
$env:NXR_REDIS_URL="redis://localhost:6399/0"; python -m server.main   # -> BusUnavailable
```

To go back to zero-dependency offline dev, clear those variables: the app falls
back to SQLite + local files + the in-memory bus, and `start.ps1` works as before.

---

## 12. Deployment steps

Ordered. Each step ends in something you can check. Steps 1–5 are one-time
infrastructure; 6–10 are the deploy proper and are what you repeat.

### 1. Build and push the image

```bash
aws ecr create-repository --repository-name nextxr-twin
aws ecr get-login-password --region $REGION | docker login --username AWS \
  --password-stdin $ACCT.dkr.ecr.$REGION.amazonaws.com
docker build -t nextxr-twin .
docker tag nextxr-twin:latest $ACCT.dkr.ecr.$REGION.amazonaws.com/nextxr-twin:$SHA
docker push $ACCT.dkr.ecr.$REGION.amazonaws.com/nextxr-twin:$SHA
```

Tag with the commit SHA, not `latest` — an immutable tag is what makes a rollback
a task-definition change rather than a rebuild.

### 2. Network

VPC with public subnets (ALB, NAT) and private subnets (ECS tasks, RDS,
ElastiCache) across **two AZs**. Security groups:

| From | To | Port |
|---|---|---|
| ALB SG | task SG | 8080 |
| task SG | RDS SG | 3306 |
| task SG | ElastiCache SG | 6379 |

RDS and ElastiCache are **not** publicly reachable. S3 goes over a gateway VPC
endpoint (free, and keeps blob traffic off the NAT gateway's per-GB charge).

### 3. Create the three shared stores

```bash
# MySQL 8 — records
aws rds create-db-instance --db-instance-identifier nextxr-twin \
  --engine mysql --engine-version 8.0 --db-instance-class db.t4g.medium \
  --allocated-storage 50 --storage-type gp3 --storage-encrypted --multi-az \
  --master-username nextxr --manage-master-user-password \
  --db-name nextxr \
  --db-subnet-group-name nextxr-private --vpc-security-group-ids $RDS_SG \
  --backup-retention-period 7 --no-publicly-accessible

# S3 — blobs
aws s3api create-bucket --bucket nextxr-twin-blobs --region $REGION
aws s3api put-public-access-block --bucket nextxr-twin-blobs \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

# ElastiCache Redis 7 — event bus
aws elasticache create-replication-group \
  --replication-group-id nextxr-twin --replication-group-description "twin bus" \
  --engine redis --engine-version 7.1 --cache-node-type cache.t4g.micro \
  --num-cache-clusters 2 --automatic-failover-enabled \
  --cache-subnet-group-name nextxr-private --security-group-ids $REDIS_SG
```

Also stand up **Neo4j** (Aura, §6) and add **RDS Proxy** in front of MySQL
(§7.5) if you expect more than a couple of tasks.

### 4. Secrets

```bash
aws secretsmanager create-secret --name nextxr/database-url \
  --secret-string 'mysql://nextxr:PASS@nextxr-twin.xxxx.rds.amazonaws.com:3306/nextxr'
aws secretsmanager create-secret --name nextxr/api-keys \
  --secret-string '[{"key":"...","tenant":"*","role":"admin","name":"prod"}]'
aws secretsmanager create-secret --name nextxr/neo4j-password --secret-string '...'
aws secretsmanager create-secret --name nextxr/anthropic-key   --secret-string 'sk-ant-...'
```

### 5. IAM

- **Task execution role:** `AmazonECSTaskExecutionRolePolicy` +
  `secretsmanager:GetSecretValue` on the secrets above (this role injects them).
- **Task role** (what the app itself uses): `s3:GetObject`, `s3:PutObject`,
  `s3:DeleteObject` on `arn:aws:s3:::nextxr-twin-blobs/*` and `s3:ListBucket` on
  the bucket. Nothing else — no S3 keys go in the environment.

### 6. Provision the database schema

From inside the VPC (`ecs execute-command`, or a bastion), against the new RDS
instance:

```bash
export NXR_DATABASE_URL='mysql://nextxr:PASS@...rds.amazonaws.com:3306/nextxr'
python -m db.schema                 # create every table (idempotent)
python -m db.migrations             # apply ordered schema changes
python -m tools.historian_provision # create the measurements table
python -m db.schema --check         # confirm
```

The deploy starts from a fresh MySQL database — there is no SQLite→MySQL cutover
(§7.2).

### 7. Task definition

Container port 8080, image from step 1, `awslogs` driver, and:

```jsonc
"environment": [
  {"name":"PORT","value":"8080"},
  {"name":"NEO4J_URI","value":"neo4j+s://xxxx.databases.neo4j.io"},
  {"name":"NEO4J_USER","value":"neo4j"},
  {"name":"NXR_REDIS_URL","value":"redis://nextxr-twin.xxxx.cache.amazonaws.com:6379/0"},
  {"name":"NXR_S3_BUCKET","value":"nextxr-twin-blobs"},
  {"name":"AWS_REGION","value":"eu-west-1"},
  {"name":"NXR_REQUIRE_DB","value":"1"},      // the three guards — §9
  {"name":"NXR_REQUIRE_S3","value":"1"},
  {"name":"NXR_REQUIRE_REDIS","value":"1"},
  {"name":"NXR_REQUIRE_AUTH","value":"1"},
  {"name":"NXR_CORS_ORIGINS","value":"https://app.example.com"},
  {"name":"NXR_DB_POOL_MAX","value":"10"},
  {"name":"NXR_DB_SSL","value":"1"}          // TLS to RDS (or NXR_DB_SSL_CA for CA pinning)
],
"secrets": [
  {"name":"NXR_DATABASE_URL","valueFrom":"arn:aws:secretsmanager:...:nextxr/database-url"},
  {"name":"NXR_API_KEYS","valueFrom":"arn:aws:secretsmanager:...:nextxr/api-keys"},
  {"name":"NEO4J_PASSWORD","valueFrom":"arn:aws:secretsmanager:...:nextxr/neo4j-password"},
  {"name":"ANTHROPIC_API_KEY","valueFrom":"arn:aws:secretsmanager:...:nextxr/anthropic-key"}
]
```

No `NXR_DATA_DIR`/EFS needed once S3 is set (§7.4).

### 8. ALB + service

Target group on 8080, health check **`/api/v1/health/ready`** (matcher 200),
HTTPS listener with an ACM certificate. Do not point it at `/api/v1/health` —
that endpoint never fails, so an unready task would stay in rotation serving
errors (§0). Then:

```bash
aws ecs create-service --cluster nextxr --service-name twin \
  --task-definition nextxr-twin --desired-count 2 \
  --launch-type FARGATE --health-check-grace-period-seconds 60 \
  --deployment-configuration "minimumHealthyPercent=100,maximumPercent=200" \
  --network-configuration "awsvpcConfiguration={subnets=[$PRIV_A,$PRIV_B],securityGroups=[$TASK_SG]}" \
  --load-balancers "targetGroupArn=$TG,containerName=nextxr-twin,containerPort=8080"
```

`minimumHealthyPercent=100` + `maximumPercent=200` gives a true zero-downtime
rolling deploy — which is only safe because no store is single-writer any more.

### 9. Verify the rollout

```bash
# The four posture lines. This is the fastest way to catch a bad config.
aws logs tail /ecs/nextxr-twin --since 5m --filter-pattern '?[auth] ?[db] ?[blobs] ?[bus]'

curl -s https://twin.example.com/api/v1/health | jq
```

Every one of these must hold:

| Check | Required value |
|---|---|
| `status` | `healthy` |
| `database.backend` | `mysql` (**not** `sqlite`) |
| `blobs.backend` | `s3` (**not** `local`) |
| `bus.backend` / `bus.scale_safe` | `redis` / `true` |
| `twin_runtime.enabled` | `true` (leases active — each twin has one owner) |
| `neo4j` | `connected` |
| `[auth]` log line | enforcement **ON** |

Then three real checks, each aimed at a thing that used to break at 2+ tasks:

1. **Create a twin**, then poll it repeatedly — successive requests land on
   different tasks and must all show it (this needed RDS).
2. **Open a machine twin and poll `/api/v1/twin/state`** — the sensor values must
   not jump between calls, and injecting a fault must persist across refreshes
   (this needs the runtime leases; §9).
3. **Run one photo→3D job** and re-fetch the model several times (this needed S3).

Also confirm each tenant appears in exactly one task's `twin_runtime.owned`:

```bash
for i in 1 2 3 4; do curl -s https://twin.example.com/api/v1/health | jq -c .twin_runtime.owned; done
# no tenant should appear in two different tasks' lists
```

### 10. Alarms

- CloudWatch alarm on `/api/v1/health` returning `status != healthy` (the ALB
  check deliberately cannot see this — §10).
- RDS: CPU, free storage, connection count vs `max_connections`.
- ElastiCache: evictions, CPU.
- ECS: running-task count below desired; any task exiting non-zero — with the
  guards on (§9), a crash loop means a misconfiguration and the log says which.

### Rollback

`aws ecs update-service --task-definition <previous-revision> --force-new-deployment`.
Safe by default: schema changes are additive `CREATE TABLE IF NOT EXISTS`, so an
older image runs against the current database unchanged.

---

## 13. Identity operations

The subsystem that did not exist before. `identity/` owns accounts,
organisations, sessions and machine credentials; `server/tenancy.py` remains the
single enforcement point and now resolves against a real ownership table
(`org_tenants`) instead of a string-prefix convention over an environment
variable.

### The first administrator

A fresh database has no users, so nobody can create the first one. Set these on
the **first deploy only**:

```
NXR_BOOTSTRAP_ADMIN_EMAIL=admin@example.com
NXR_BOOTSTRAP_ADMIN_PASSWORD=<from Secrets Manager>
NXR_BOOTSTRAP_ORG=NextXR
```

The boot log prints `[identity] bootstrap platform admin created (usr_…)`. It is
idempotent twice over — skipped if the email exists, and refused entirely once
any user exists unless `NXR_BOOTSTRAP_FORCE=1`. **Remove both variables from the
task definition afterwards**; a credential left in a task definition is a
standing instruction to create an admin.

### Onboarding a customer

1. Sign in as the platform admin.
2. **Account → Members → Invite** (or `POST /api/v1/auth/orgs/{org}/members`).
   A new user gets a random password and a reset token — they set their own.
   With `NXR_MAIL_FROM` unset the token is returned in the API response so you
   can deliver it yourself.
3. Twins the org creates are claimed by it automatically
   (`tenancy.new_tenant_id`). To assign an existing twin:
   `identity.store.claim_tenant(tenant_id, org_id)`.

### Roles

| Role | Reach |
|---|---|
| `owner` | Everything `admin` can do, plus managing members and the org itself |
| `admin` | Full read/write across the org's tenants; issues and revokes API keys |
| `write` | Read + write within the org's tenants |
| `read` | Read-only within the org's tenants |

No role may grant one above itself, and the last owner cannot be demoted or
removed. `User.is_platform_admin` is a column rather than a role — it is our
staff, not a customer's, so no amount of escalation inside an org reaches it.

### Machine credentials

Issued from **Account → API keys**, or:

```bash
curl -X POST https://twin.example.com/api/v1/auth/orgs/$ORG/keys \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"ci","role":"write","expires_in_days":90}'
```

The secret is returned **once** and stored only as a peppered hash. A key with no
explicit `tenants` reaches everything its org owns, resolved live — so a twin
created tomorrow is reachable by a key issued today.

Revocation takes effect within ~5 seconds fleet-wide (the resolution cache TTL in
`identity/resolve.py`) and immediately on the task that served the revocation.

### Rotating secrets

| Secret | Effect of rotating |
|---|---|
| `NXR_JWT_SECRET` | Every access token is rejected. Users re-authenticate silently via their refresh cookie — no visible sign-out. |
| `NXR_SECRET_PEPPER` | **Every API key and every session is invalidated.** Keys must be re-issued. Treat as a break-glass action. |
| A user's password | That user's other sessions are revoked. |
| RDS password | Update the `database-url` secret and roll the service. |

### Incident response

```bash
# Sign one user out everywhere
python -c "import identity.store as s; s.revoke_user_sessions('usr_...')"

# Disable an account (keeps the audit trail; a delete does not)
python -c "import identity.store as s; s.update_user('usr_...', status='disabled')"

# Suspend a whole organisation — every member and every key stops working
python -c "import identity.store as s; s.update_org('acme', status='suspended')"

# Who did what
curl -H "Authorization: Bearer $TOKEN" \
  https://twin.example.com/api/v1/auth/orgs/$ORG/audit
```

Refresh-token **reuse detection** is automatic: a token presented after it has
been rotated means two parties hold it, so the whole session family is revoked
and a `session.reuse_detected` row is written to the audit log. Alarm on it — it
is the signal that a token leaked.

---

## 14. Database migrations

`db/schema.py` owns the *shape* of every table; `db/migrations.py` owns *change*.
`CREATE TABLE IF NOT EXISTS` cannot add a column, so a deploy that needed one
previously produced a fleet whose queries referenced a column that was not there.

```bash
python -m db.migrations --status     # applied vs pending
python -m db.migrations --verify     # do schema.py and the chain agree?
python -m db.migrations --dry-run    # print the SQL, change nothing
python -m db.migrations              # apply
```

**Run them as a one-off ECS task before the service rolls**, and confirm the task
exited 0 before you touch the service. This is a manual step with no pipeline
enforcing it (§0), so it is the one most worth building a habit around:

```bash
TASK_ARN=$(aws ecs run-task --cluster nextxr --task-definition nextxr-twin \
  --launch-type FARGATE --network-configuration "$NETWORK" \
  --overrides '{"containerOverrides":[{"name":"app","command":["python","-m","db.migrations"]}]}' \
  --query 'tasks[0].taskArn' --output text)
aws ecs wait tasks-stopped --cluster nextxr --tasks "$TASK_ARN"
aws ecs describe-tasks --cluster nextxr --tasks "$TASK_ARN" \
  --query 'tasks[0].containers[0].exitCode' --output text   # must be 0
```

`NXR_AUTO_MIGRATE` stays `0` for the same reason it always did: with several
tasks starting at once they would queue on the advisory lock, and the ones that
wait fail their health check — a failed deploy caused by the migration
*mechanism* rather than by the migration.

### Writing one

Forward-only — there is no `downgrade()`. A rollback of a schema change on a live
database discards data written in the meantime, and a down-migration nobody has
ever run is an untested script with a reassuring name. To undo a migration, write
a new one.

**Additive first.** Deploy the column, then the code that writes it, then the
code that reads it, and only then drop the old one — three releases, not one. A
single release that renames a column is broken for the entire rolling-deploy
window, because old and new tasks serve simultaneously.

On SQLite each store is a separate **file**, so a migration must name the store
its tables live in (`store="twins"`). On MySQL they share one database and the
name is only diagnostic — which means getting it wrong fails locally and works in
production, or the reverse.
