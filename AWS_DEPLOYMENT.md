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
                                              └─ 1× task: nextxr-twin container (:8080)
                                                   env      ← task definition
                                                   secrets  ← Secrets Manager
                                                   /data    ← EFS access point (uid/gid 10001)
                                              DESIRED COUNT = 1   (SQLite single-writer, §7/§9)
                                              │
                              ┌───────────────┼──────────────────┬────────────────────┐
                              ▼               ▼                  ▼                     ▼
                        Neo4j Aura       EFS (SQLite +      Secrets Manager       RunPod serverless
                        (or EC2)         scenes + GLBs)     (keys, §5.2/§10)      GPU (TRELLIS) — external
```

Stateful components that must survive a redeploy: **Neo4j** (graph structure) and
the **EFS volume** (SQLite stores + reconstructed `scenes/` and generated GLBs).
Everything else — the API process, the LLM, the GPU worker — is stateless or
external.

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
| ECS cluster + Fargate service + task def | runs the container (desired count **1**) |
| Application Load Balancer + target group | fronts `:8080`; health check path `/api/v1/health` |
| EFS filesystem + access point (uid/gid **10001**) | the `/data` volume (§7) |
| Secrets Manager secrets | API keys, DB password (§5.2, §10) |
| Neo4j Aura instance (or Neo4j on EC2) | the graph DB (§6) |
| RunPod serverless endpoint | GPU reconstruction (§8) — external to AWS |

---

## 4. First-deploy checklist (minimum to be safe & durable)

1. **Set `NXR_API_KEYS`** (+ optionally `NXR_REQUIRE_AUTH=1`) — closes the fail-open
   hole (§10). Without it the API is public and unauthenticated.
2. **Mount EFS at `/data`** and confirm the Dockerfile's `NXR_DATA_DIR=/data` **and**
   `DATA_DIR=/data/threed` (§7) — otherwise twins and generated GLBs are lost on
   task replacement.
3. **Move every key to Secrets Manager** (§5.2) — nothing in `.env` on the task.
4. **Stand up managed Neo4j** and rotate off the `nextxr2026` default (§6).
5. **Restrict CORS** with `NXR_CORS_ORIGINS` (§10).
6. **Keep desired count = 1** until state moves off SQLite (§9).

---

## 5. Deployment shape (ECS Fargate)

One Fargate task built from the `Dockerfile`, behind an ALB. **Desired count = 1**
(see §9). The ALB target-group health check uses `GET /api/v1/health`, which
returns 200 while the *process* is up even if Neo4j is down (a deliberate product
choice so a DB blip doesn't roll the fleet) — so add a separate CloudWatch alarm
on the health payload's `degraded` status for DB visibility.

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
| `NXR_DATA_DIR` | `/data` | EFS mount (§7) |
| `DATA_DIR` | `/data/threed` | 3-D platform artifacts under the same volume (§7) |
| `NEO4J_URI` | `neo4j+s://<aura-id>.databases.neo4j.io` | managed Neo4j (§6) |
| `NEO4J_USER` | `neo4j` | |
| `NXR_REQUIRE_AUTH` | `1` | enforce auth even before keys load (§10) |
| `NXR_CORS_ORIGINS` | `https://app…,https://hub…` | allow-list (§10) |
| `NXR_REDIS_URL` | (optional) ElastiCache URL | in-memory fallback if unset (§9) |

**Secrets** (task-def `secrets:` → Secrets Manager / SSM — never in `environment`):

`NXR_API_KEYS`, `NEO4J_PASSWORD`, `ANTHROPIC_API_KEY`, `RUNPOD_API_KEY`,
`RUNPOD_ENDPOINT_ID`, and (if used) `REPLICATE_API_TOKEN`.

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

## 7. State & the data volume (EFS)

Two directories hold all local state, **both** under the mounted volume:

- `NXR_DATA_DIR=/data` — the SQLite stores (`twins.db`, `changelog.db`, `bundles.db`,
  `agent_checkpoints.db`, `track3_gate.db`) and reconstructed `scenes/`.
- `DATA_DIR=/data/threed` — the 3-D platform's job artifacts and generated GLBs. This
  is a **different env var** the platform reads independently; if it is left at its
  in-image default, photo→3D output is written to ephemeral storage and lost on task
  replacement. The `Dockerfile` sets both.

Mount an **EFS access point** at `/data` with uid/gid **10001**. SQLite over NFS/EFS
is safe for a **single writer only** — this is why desired count must stay 1 (§9).

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

- **Single task only, for now.** The five SQLite DBs on EFS are single-writer, so
  the API **cannot** be scaled past one task or rolling-deployed without data
  corruption risk. This is the single biggest architectural constraint.
- **Path to scale-out:** move the SQLite stores to **RDS Postgres** and the
  `scenes/` + GLB blobs to **S3**. That is the one change that unlocks multi-task
  ECS and zero-downtime deploys (the code already marks these seams — `paths.py`,
  `threed_platform/app/store.py`).
- **Event bus:** the Redis Streams bus falls back to in-memory, which is correct at
  one task. Provision **ElastiCache** and set `NXR_REDIS_URL` only when you split
  into multiple tasks.

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
- **Health check** never 503s by design; add a CloudWatch alarm on the `degraded`
  status so a broken Neo4j is visible even though the task stays "healthy".
- **Secrets** live in Secrets Manager / SSM, injected via the task-def `secrets:`
  block — never baked into the image (`.dockerignore` already excludes `.env` files).
