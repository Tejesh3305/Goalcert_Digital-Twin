# Running NextXR — and the 503 explained

## TL;DR — how to run

**Easiest (Windows):** double-click `start.bat`, or in a terminal:

```powershell
./start.ps1
```

This starts Docker (Neo4j + Redis) if it isn't already, frees port 8000, and
launches the backend serving the built UI at **http://localhost:8000**.

For live frontend development with hot-reload:

```powershell
./start.ps1 -Dev        # backend + Vite dev server (http://localhost:5173)
```

Manual equivalent:

```powershell
docker compose up -d            # Neo4j + Redis
cd nextxr-ontology
python -m server.main           # http://localhost:8000
```

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
   down (the change-log count still works — it's SQLite, not Neo4j). The app
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
port 8000 at a time.

- **Local (recommended for development):** `python -m server.main` runs your
  live code. `docker compose up -d` now starts **only Neo4j + Redis** (the
  `server` service is behind a `full` profile), so it won't collide.
- **Containerized (self-contained demo):** `docker compose --profile full up -d
  --build`. The `--build` is important — without it you run a **stale image**
  with old code (this caused the "old UI" and "Driver closed" symptoms). Stop
  it with `docker stop nxr-server` before switching back to local.

If you ever see behavior that doesn't match your edits, check whether a
container is serving port 8000: `docker ps` → if `nxr-server` is listed,
`docker stop nxr-server` and run locally.

## The other recurring gotcha: stale servers on port 8000

If you start the server multiple times, old `python` processes can keep holding
port 8000 and serve **old code** (this is why the UI once showed a stale page).
`./start.ps1` kills anything on port 8000 before starting. To do it by hand:

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force }
```

---

## Quick status check

```powershell
curl http://localhost:8000/api/v1/health
```

- `{"status":"healthy", ...}`   → all good, live data flowing
- `{"status":"degraded", ...}`  → app works; start Docker for live data
- connection refused            → the backend isn't running; run `./start.ps1`
