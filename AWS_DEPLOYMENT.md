# AWS Deployment — NextXR Digital Twin

The authoritative runbook for deploying the twin to AWS. Section numbers are
stable — other files reference them (the `Dockerfile`, `.env.example §5.2`,
`paths.py §7`, and the auth notes at §10).

The system is a **single-container monolith**: one FastAPI process serves the
REST API **and** the built React UI, with the GPU reconstruction step offloaded
to an external RunPod endpoint. It is deliberately close to Fargate-ready — the
image is non-root, `$PORT`-driven, healthchecked, and serves its own frontend.

---

## 1. Runtime topology

```
Route 53 ─▶ CloudFront (optional) ─▶ ALB ─▶ ECS Fargate service
                                              └─ N× task: nextxr-twin container (:8080)
                                                   env      ← task definition
                                                   secrets  ← Secrets Manager
                                                   /data    ← EFS access point (uid/gid 10001)
                                              DESIRED COUNT = 2+  (see §9)
                                              │
                  ┌───────────────┬───────────┴───┬──────────────┬────────────────────┐
                  ▼               ▼               ▼              ▼                     ▼
            RDS PostgreSQL   Neo4j Aura        EFS          Secrets Manager     RunPod serverless
            16 (Multi-AZ)    (or EC2)      (scenes+GLBs)    (keys, §5.2/§10)    GPU (TRELLIS) — external
            via RDS Proxy
```

Stateful components that must survive a redeploy: **RDS Postgres** (twin
registry, change log, agent bundles, checkpoints, scene cache), **Neo4j** (graph
structure) and the **EFS volume** (reconstructed `scenes/` and generated GLBs).
Everything else — the API process, the LLM, the GPU worker — is stateless or
external.

> **Changed from the SQLite design.** Relational state used to be five SQLite
> files on the EFS mount. SQLite over NFS/EFS is single-writer, so the service
> was pinned at one task — no scale-out, no rolling deploy. That state now lives
> in RDS (§7), which is what makes desired count > 1 legal.

---

## 2. Build & image

`docker build -t nextxr-twin .` — the `Dockerfile` is multi-stage:

1. `node:20-slim` builds `frontend/dist`.
2. `python:3.12-slim` installs `requirements.txt`, copies `nextxr-ontology/` and the
   built `dist`, runs as non-root **uid/gid 10001**, exposes `:8080`, and
   healthchecks `GET /api/v1/health`.

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
| Application Load Balancer + target group | fronts `:8080`; health check path `/api/v1/health` |
| **RDS PostgreSQL 16** (Multi-AZ), private subnets | the relational store (§7) |
| **RDS Proxy** (optional but recommended) | connection pooling across tasks (§7) |
| EFS filesystem + access point (uid/gid **10001**) | the `/data` volume for blobs (§7.3) |
| Secrets Manager secrets | API keys, DB URL/password (§5.2, §10) |
| Neo4j Aura instance (or Neo4j on EC2) | the graph DB (§6) |
| RunPod serverless endpoint | GPU reconstruction (§8) — external to AWS |

Security groups: the RDS instance should accept 5432 **only** from the ECS task
security group (and RDS Proxy), and live in the isolated/private subnets — it is
never publicly addressable.

---

## 4. First-deploy checklist (minimum to be safe & durable)

1. **Set `NXR_DATABASE_URL`** to the RDS endpoint, as a *secret* (§7). Unset, the
   app falls back to per-task SQLite and **nothing fails** — each task just serves
   a different set of twins. Confirm the `[db] PostgreSQL — …` line in CloudWatch
   and `database.backend: "postgres"` in `/api/v1/health` after the first deploy.
2. **Provision the schema**: `python -m db.schema` (§7.1). The app self-provisions
   on first use, but running it explicitly turns a permissions problem into a
   clear error before traffic arrives.
3. **Set `NXR_API_KEYS`** (+ optionally `NXR_REQUIRE_AUTH=1`) — closes the fail-open
   hole (§10). Without it the API is public and unauthenticated.
4. **Mount EFS at `/data`** and confirm the Dockerfile's `NXR_DATA_DIR=/data` **and**
   `DATA_DIR=/data/threed` (§7.3) — otherwise generated GLBs are lost on task
   replacement.
5. **Move every key to Secrets Manager** (§5.2) — nothing in `.env` on the task.
6. **Stand up managed Neo4j** and rotate off the `nextxr2026` default (§6).
7. **Restrict CORS** with `NXR_CORS_ORIGINS` (§10).
8. **Migrating an existing deployment?** Run `python -m db.migrate --dry-run`,
   then `python -m db.migrate`, before pointing traffic at the new stack (§7.2).

---

## 5. Deployment shape (ECS Fargate)

Fargate tasks built from the `Dockerfile`, behind an ALB, **desired count 2+**
across two AZs (see §9). The ALB target-group health check uses
`GET /api/v1/health`, which returns 200 while the *process* is up even if Neo4j
or Postgres is down (a deliberate product choice so a DB blip doesn't roll the
fleet) — so add a separate CloudWatch alarm on the health payload's `degraded`
status for DB visibility.

### 5.1 Task definition (essentials)

- **Container port** 8080; ALB → target group → 8080.
- **User** uid/gid 10001 (baked into the image); the **EFS access point must use the
  same uid/gid** or the first write to `/data` fails.
- **Mount** the EFS access point at `/data`.
- **Logging** to CloudWatch (`awslogs` driver). `PYTHONUNBUFFERED=1` is already set,
  so stdout (including the `[auth]` posture line, §10) reaches CloudWatch.

### 5.2 Environment & secrets

Plain **environment** (task-def `environment:`):

| Var | Value | Notes |
|---|---|---|
| `PORT` | `8080` | ALB target port |
| `NXR_DATA_DIR` | `/data` | EFS mount — blobs only now (§7.3) |
| `DATA_DIR` | `/data/threed` | 3-D platform artifacts under the same volume (§7.3) |
| `NEO4J_URI` | `neo4j+s://<aura-id>.databases.neo4j.io` | managed Neo4j (§6) |
| `NEO4J_USER` | `neo4j` | |
| `NXR_DB_POOL_MAX` | `10` | per-task pool ceiling; server-side total is tasks × this (§7.4) |
| `NXR_DB_SSLMODE` | `require` | unless already in the URL (§7) |
| `NXR_REQUIRE_AUTH` | `1` | enforce auth even before keys load (§10) |
| `NXR_CORS_ORIGINS` | `https://app…,https://hub…` | allow-list (§10) |
| `NXR_REDIS_URL` | ElastiCache URL | **required at 2+ tasks** (§9) |

**Secrets** (task-def `secrets:` → Secrets Manager / SSM — never in `environment`):

`NXR_DATABASE_URL`, `NXR_API_KEYS`, `NEO4J_PASSWORD`, `ANTHROPIC_API_KEY`,
`RUNPOD_API_KEY`, `RUNPOD_ENDPOINT_ID`, and (if used) `REPLICATE_API_TOKEN`.

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

## 7. State: RDS PostgreSQL 16

All relational state lives in one Postgres database, reached via
`NXR_DATABASE_URL`:

| Table | Holds | Was |
|---|---|---|
| `twins` | the twin registry — every twin a user has created | `twins.db` |
| `events` | the governance change log (per-tenant hash chain) | `changelog.db` |
| `published_bundles` | agent-authored capability bundles | `bundles.db` |
| `checkpoints` | agent graph checkpoints (human-in-the-loop resume) | `agent_checkpoints.db` |
| `scene_cache` | BIM scene graphs for the 3-D viewer | `data/scenes/*.json` |

`field_changes`, `payload`, `domains`, `state` and `scene` are **JSONB**, so the
change log and scene graphs are queryable in place rather than opaque text.

**Instance shape.** `db.t4g.medium` + gp3 is ample for this workload — it is
metadata and an audit log, not telemetry (live physics state is in-process and on
Redis; the graph is in Neo4j). Multi-AZ for failover. Plain **RDS, not Aurora**:
cheaper at this size, flat I/O pricing, and the same engine runs on-prem for a
customer who needs it. Enable automated backups; the change log is an audit
record and PITR is the point.

**Extensions.** `python -m db.schema --extensions` enables `pgvector` and
`postgis` if the role permits. Nothing queries them yet — they are why the
architecture specifies Postgres (geospatial asset positions, embedding search
over the ontology) rather than a KV store. Skipping them changes nothing today.

### 7.1 Provisioning

```bash
export NXR_DATABASE_URL='postgresql://nextxr:PASS@twin.xxxx.rds.amazonaws.com:5432/nextxr?sslmode=require'
python -m db.schema              # create every table (idempotent)
python -m db.schema --check      # report only, create nothing
```

Run it from a bastion/ECS-exec session inside the VPC — RDS is not publicly
reachable. The app also self-provisions on first use, so this is a
fail-early check rather than a hard prerequisite.

### 7.2 Migrating an existing SQLite deployment

```bash
python -m db.migrate --dry-run    # read the .db files, write nothing
python -m db.migrate              # copy them in
```

`--source` defaults to `NXR_DATA_DIR`, so run it on a task that has the EFS
volume mounted. It is idempotent (`ON CONFLICT DO NOTHING` — a live row always
beats the snapshot), copies the change log **verbatim including `seq`**, resets
the `events.seq` sequence past the copied rows, and then re-verifies every
tenant's hash chain in the target, exiting non-zero if any fails. Keep the SQLite
files until the deployment is confirmed.

### 7.3 What is still on EFS

Blobs only:

- `NXR_DATA_DIR=/data` — reconstructed `scenes/` GLB output (the scene *graphs*
  are in `scene_cache`; the meshes are files).
- `DATA_DIR=/data/threed` — the 3-D platform's job artifacts and generated GLBs.
  This is a **different env var** the platform reads independently; if it is left
  at its in-image default, photo→3D output goes to ephemeral storage and is lost
  on task replacement. The `Dockerfile` sets both.

Mount an **EFS access point** at `/data` with uid/gid **10001**. Concurrent
readers are fine — the single-writer constraint went away with the SQLite files.
Moving these blobs to S3 is the remaining step to a fully volume-free task; it is
independent of everything above.

### 7.4 Connection pooling

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
- **Provision ElastiCache before you scale.** This one is easy to miss: the event
  bus falls back to an in-memory implementation when `NXR_REDIS_URL` is unset,
  which is *correct at one task and silently wrong at two* — each task publishes
  to its own memory and no task sees the others' events. Set `NXR_REDIS_URL` in
  the same change that raises desired count.
- **Sticky sessions are not required.** Agent checkpoints are in Postgres, so a
  run interrupted for human approval on one task resumes on another.
- **Live physics runtime state is per-task and in-memory.** Two tasks each run
  their own machine-twin simulation loop, so a twin's instantaneous sensor values
  can differ slightly between tasks depending on which one answers. Findings and
  everything committed through the Graph Writer are shared and consistent; the
  transient signal values are not. Pin a twin to a task, or move the runtime
  behind the bus, if you need them identical.
- **Remaining file state:** `scenes/` meshes and generated GLBs on EFS (§7.3).
  Concurrent readers are fine; moving them to **S3** is what would make the task
  fully volume-free.
- **Change-log writes serialise per tenant.** `append()` takes a Postgres
  advisory lock for the length of its transaction so a tenant's hash chain cannot
  fork under concurrent writers. Tenants never block each other, but a single
  tenant's write throughput is bounded by that lock — which is the correct
  trade for a tamper-evident ledger.

---

## 10. Security posture

- **API keys are mandatory in production.** With `NXR_API_KEYS` unset the service is
  **open** — every `/api` call, including the LLM-billing `/copilot` endpoints, is
  served without a key. Set `NXR_API_KEYS` (JSON array of `{key,tenant,role,name}`)
  and put it in Secrets Manager. Set `NXR_REQUIRE_AUTH=1` to reject keyless calls
  even before keys load. At startup the app prints an `[auth]` line stating whether
  enforcement is ON or the API is OPEN — check it in CloudWatch after every deploy.
- **The built-in demo keys** (`nxr-demo-key`, `nxr-read-only` in `auth.py`) load
  **only** when `NXR_API_KEYS` is unset, i.e. never in a correctly-configured prod
  deploy. Do not rely on them.
- **CORS:** default `*`. Set `NXR_CORS_ORIGINS` to the real frontend/hub origins.
- **Database:** RDS in private/isolated subnets, security group open to the task
  SG only, storage encrypted at rest, and TLS enforced in transit
  (`sslmode=require` in the URL or `NXR_DB_SSLMODE`) — without it psycopg2 will
  quietly accept an unencrypted connection. Rotate the password through Secrets
  Manager, or skip it entirely with RDS IAM auth.
- **Health check** never 503s by design; add a CloudWatch alarm on the `degraded`
  status so a broken Neo4j *or Postgres* is visible even though the task stays
  "healthy". The payload's `database` block reports backend, endpoint (password
  redacted) and reachability.
- **Secrets** live in Secrets Manager / SSM, injected via the task-def `secrets:`
  block — never baked into the image (`.dockerignore` already excludes `.env` files).
