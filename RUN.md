# Running NextXR — and the 503 explained

## TL;DR — how to run

**Easiest (Windows):** double-click `start.bat`, or in a terminal:

```powershell
./start.ps1
```

This starts Docker (Neo4j + Redis) if it isn't already, frees port 8080, and
launches the backend serving the built UI at **http://localhost:8080**.

For live frontend development with hot-reload:

```powershell
./start.ps1 -Dev        # backend + Vite dev server (http://localhost:5173)
```

Manual equivalent:

```powershell
docker compose up -d            # Neo4j + MySQL + Redis
cd nextxr-ontology
$env:NXR_DEV_MODE = "1"         # see below — the API is closed without this
python -m server.main           # http://localhost:8080
```

---

## Authentication, and why `NXR_DEV_MODE` exists

**The API defaults to closed.** With nothing configured it refuses every `/api`
request. That is deliberate and it is the opposite of how it used to behave: the
service was open whenever `NXR_API_KEYS` was unset, so any deployment that forgot
one environment variable served customer data — and the LLM-billing `/copilot`
endpoints — to anyone who found the URL.

`start.ps1` sets `NXR_DEV_MODE=1`, which is the explicit local opt-out. So the
open posture is something you run on purpose on a laptop, rather than a hole a
deployment inherits by forgetting a variable.

### Rehearsing the real thing

```powershell
./start.ps1 -Secure
```

Authentication enforced, exactly as it ships. Create an account at
**http://localhost:8080/signup**, then sign in at `/login`. Worth doing before a
release — it is the configuration that will actually run.

| Posture | Command | `/api/v1/twins` without a credential |
|---|---|---|
| Local dev | `./start.ps1` | 200 |
| Production | `./start.ps1 -Secure` | 401 |

### What a signed-in session gets you

A user belongs to an **organisation**, and the organisation owns twins. You reach
a twin because your org owns it — not because of how its id is spelled. So a
fresh account sees an empty twin list until it creates one or is given one, which
is correct rather than broken.

**Twins that already existed belong to nobody.** Every twin created before
`identity/` was reachable by a tenant-prefix convention, so there is no
`org_tenants` row saying who owns it — and the rule above then makes it invisible
to every signed-in user. The boot log says so:

```
[tenancy] !! 14 of 14 twin(s) are owned by no organisation, so no signed-in
          user can see them (a platform admin can).
```

Sign up first, so the organisation exists, then hand them over:

```powershell
cd nextxr-ontology
python -m identity.tenants                                 # who owns what
python -m identity.tenants --org <ORG_ID> --adopt-unowned  # claim the orphans
```

`--org` is the id from the report (a slug of the organisation name, e.g.
`acme-energy`). Add `--dry-run` to see the assignment without making it. It never
reassigns a twin another organisation already owns.

**Account → API keys** issues machine credentials for scripts and CI. The secret
is shown once and stored only as a hash; there is no way to read it back.

---

## Where local state goes

Three stores, and each has a **task-local fallback** so you can work offline with
nothing running:

| Store | Production | Fallback when unset |
|---|---|---|
| Records — twins, change log, identity/auth, bundles, checkpoints, scenes, 3-D jobs, historian | RDS MySQL 8 (`NXR_DATABASE_URL`) | SQLite files in `nextxr-ontology/data/` |
| Blobs — generated GLBs, 3-D artifacts | S3 (`NXR_S3_BUCKET`) | files in `data/blobs/` |
| Event bus | ElastiCache Redis (`NXR_REDIS_URL`) | in-memory, this process only |

The fallbacks are correct for one process and **wrong for a multi-task deploy** —
each is per-task, and none of them errors. That is why the deploy sets
`NXR_REQUIRE_DB` / `NXR_REQUIRE_S3` / `NXR_REQUIRE_REDIS`, which turn a missing
variable into a refusal to start (AWS_DEPLOYMENT.md §9).

### Run the production shape locally

`docker compose` brings up the real thing — MySQL 8, Redis 7 and **MinIO**
(S3-compatible), so the same code paths run as on AWS. Worth doing before any
deploy; the full procedure is AWS_DEPLOYMENT.md §11.

```powershell
docker compose up -d            # Neo4j + MySQL + Redis + MinIO (+ bucket)

$env:NXR_DATABASE_URL  = "mysql://nextxr:nextxr2026@localhost:3306/nextxr"
$env:NXR_REDIS_URL     = "redis://localhost:6379/0"
$env:NXR_S3_BUCKET     = "nextxr-blobs"
$env:NXR_S3_ENDPOINT_URL     = "http://localhost:9000"
$env:NXR_S3_ADDRESSING_STYLE = "path"
$env:AWS_ACCESS_KEY_ID = "nextxr"; $env:AWS_SECRET_ACCESS_KEY = "nextxr2026"
$env:AWS_REGION        = "us-east-1"
$env:NXR_REQUIRE_DB="1"; $env:NXR_REQUIRE_S3="1"; $env:NXR_REQUIRE_REDIS="1"

cd nextxr-ontology
python -m db.schema             # provision (idempotent); --check to inspect
python -m db.migrations         # apply ordered schema changes
python -m tools.historian_provision   # create the measurements table
python -m server.main
```

The server prints its posture at startup — this is the same thing you read in
CloudWatch after a deploy:

```
[db]    MySQL - mysql://nextxr:***@localhost:3306/nextxr (pool 1-10 per task)
[blobs] S3 - bucket=nextxr-blobs prefix=/ endpoint=http://localhost:9000
[bus]   Redis Streams - redis://localhost:6379/0
```

`/api/v1/health` reports all of it under `database`, `blobs` and `bus`. MinIO's
console is at http://localhost:9001 (nextxr / nextxr2026) if you want to see the
GLBs land.

Clear those variables to go back to zero-dependency offline dev — the SQLite
files and local blobs are untouched, so you can switch back and forth freely.

The deploy starts from a fresh MySQL database — there is no SQLite→MySQL data
cutover (the old `db.migrate` importer was Postgres-specific and is retired).

> **Port 3306 already taken?** A locally-installed MySQL owns it and the
> container cannot bind. Start with `MYSQL_PORT=3307 docker compose up -d mysql`
> and use 3307 in the URL.

---

## What the 503 was — and why it won't break the app again

### The cause
`GET /api/v1/health` returned **503** because **Neo4j was unreachable** — and
Neo4j was unreachable because **Docker Desktop's engine wasn't running**. It was
never a bug in the app code. The health check was doing its job (reporting the
database is down); the problem was that a 503 there made the *whole UI look
dead*, even though most of the platform doesn't need the database to load.

### The fix (three layers, so it's robust)

1. **`/health` never returns 503 anymore.** It always returns `200` with a
   status field:
   - `"healthy"` — server + Neo4j both up
   - `"degraded"` — server up, Neo4j down (Docker off)

   The frontend reads this and shows an amber **"DB offline"** chip in the top
   bar instead of looking broken.

2. **Read endpoints degrade gracefully.** `/stats`, `/entities`, `/findings`,
   `/topology` return `200` with empty data + `"degraded": true` when the DB is
   down (the change-log count still works — it's the relational store, not
   Neo4j). The app
   shell, Twins list, Copilot, schema, and the event bus all keep working.

3. **Write endpoints fail cleanly.** Creating a twin or asset needs the DB, so
   those return a clear **503 with instructions** ("start the database with
   `docker compose up -d`") instead of a raw 500 — and no longer leave behind
   empty "orphan" twins.

So: **with Docker off, the app fully loads and is usable; only live graph data
and writes are paused.** With Docker on, everything is live.

---

## If Docker gets stuck (the real culprit here)

Symptom: `docker ps` returns
`request returned 500 Internal Server Error ... check if the server supports the
requested API version`. This means Docker Desktop's Linux engine is half-started
or wedged — a Docker problem, not a NextXR one.

Fix, in order of escalation:
1. **Wait** ~1–2 min after launching Docker Desktop (the whale icon in the tray
   should stop animating).
2. **Restart the engine:** Docker Desktop → ⚙ menu → *Restart*, or run
   `& "C:\Program Files\Docker\Docker\DockerCli.exe" -SwitchLinuxEngine`.
3. **Quit Docker Desktop fully** (tray → Quit), then reopen it.
4. **Reset WSL:** `wsl --shutdown`, then reopen Docker Desktop.
5. Last resort: reboot.

`./start.ps1` automates step 1 (it launches Docker and waits up to 2 min). If
Docker still isn't ready, it drops to degraded mode and tells you — the app
still starts.

---

## Two ways to run the server (don't mix them)

The backend can run **locally** or **in a container** — but only one can own
port 8080 at a time.

- **Local (recommended for development):** `python -m server.main` runs your
  live code. `docker compose up -d` now starts **only Neo4j + Redis** (the
  `server` service is behind a `full` profile), so it won't collide.
- **Containerized (self-contained demo):** `docker compose --profile full up -d
  --build`. The `--build` is important — without it you run a **stale image**
  with old code (this caused the "old UI" and "Driver closed" symptoms). Stop
  it with `docker stop nxr-server` before switching back to local.

If you ever see behavior that doesn't match your edits, check whether a
container is serving port 8080: `docker ps` → if `nxr-server` is listed,
`docker stop nxr-server` and run locally.

## The other recurring gotcha: stale servers on port 8080

If you start the server multiple times, old `python` processes can keep holding
port 8080 and serve **old code** (this is why the UI once showed a stale page).
`./start.ps1` kills anything on port 8080 before starting. To do it by hand:

```powershell
Get-NetTCPConnection -LocalPort 8080 -State Listen |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force }
```

---

## Quick status check

```powershell
curl http://localhost:8080/api/v1/health
```

- `{"status":"healthy", ...}`   → all good, live data flowing
- `{"status":"degraded", ...}`  → app works; start Docker for live data
- connection refused            → the backend isn't running; run `./start.ps1`
