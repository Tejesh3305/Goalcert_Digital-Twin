# NextXR · 3D Generation Platform (Tripo-style)

Image → 3D model. A modular **monolith** that runs the full pipeline end-to-end
on one node, with every stage isolated behind the same interface so it splits
into horizontally-scaled microservices later **with no rewrite**.

Two routes share one front door:

| Input | Route | Backend |
|-------|-------|---------|
| Photo of an object (phone, drone, multi-view) | `object` | **TRELLIS** (hosted GPU) |
| Floor plan / CAD / blueprint / PDF | `drawing` | existing **2d-to-3d** scene parser |

A router auto-classifies each upload (overridable). We never push line drawings
through TRELLIS.

## Moved — now part of the digital-twin server

This used to be the standalone `apps/3d-platform` service on its own port. It's
now mounted **in-process** inside the main NextXR server at `/api/v1/threed`
(`server/main.py` imports `server.threed_platform.app.main:app` and
`app.mount("/api/v1/threed", ...)`) — the pipeline itself (this whole
directory) is otherwise unchanged. **Build a Twin**'s upload handler
(`server/agent_routes.py::twin_build_from_plan`) classifies each upload with
`app/router.py` and, for a photo of an object, runs it through this pipeline's
job store + orchestrator in-process (see `_build_twin_from_object_photo`)
instead of making an HTTP call to itself.

## Run

It starts automatically with the main server — no separate process:

```powershell
cd nextxr-ontology
python -m server.main            # http://localhost:8080
```

The bundled job-tester UI (this folder's `web/index.html`) is served at
**http://localhost:8080/api/v1/threed/** → drop an image → watch the stages
stream → preview the GLB. Works **without any keys** (reconstruction returns a
clearly marked *stub* mesh so you can exercise the whole pipeline); the RunPod
vars are already set in this folder's `.env` for real models.

## The pipeline (19-stage spine)

Defined in [`app/pipeline.py`](app/pipeline.py) — one editable list per route.

**Object route:** route → validate → understand → segment (bg removal) →
enhance → **TRELLIS** → mesh repair → topology/LOD → UV/texture → material* →
semantic* → mesh validate → export → twin packaging.
(*material & semantic-parts are honest, deferred post-MVP placeholders.)

**Drawing route:** route → validate → 2d-to-3d parse → twin packaging.

Every stage writes to `data/jobs/<id>/artifacts/<stage>/` and records status +
timing + notes in `job.json`, so the whole run is inspectable on disk.

### What changed vs. the original 19-stage spec (and why)
- **Split object vs. drawing inputs** — TRELLIS expects an object photo; floor
  plans/CAD go to the 2d-to-3d parser instead.
- **Dropped precomputed depth/normals/multi-view as TRELLIS inputs** — TRELLIS
  generates structure from the image itself; those maps only matter for a
  different (multi-view-diffusion) backbone. Re-add them if/when we swap models.
- **Camera pose / absolute scale = optional hints** — unreliable from a single
  image; recorded, never blocking.
- **Material prediction & semantic part detection = post-MVP** — research-grade
  on arbitrary generated meshes; stubbed honestly, ready to drop in real models.
- **Geometry-prior retrieval** kept as a valuable v2 add-on (not yet wired).

## API

Paths below are relative to this mount; prefix with `/api/v1/threed` on the
main server (e.g. `POST /api/v1/threed/api/jobs`).

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/jobs` | multipart `file` + optional `route,object_type,asset_id,material,facility,floors` |
| `GET`  | `/api/jobs/{id}` | full job record (status, per-stage, state) |
| `GET`  | `/api/jobs/{id}/file/{path}` | fetch any artifact (e.g. `artifacts/export/model.glb`) |
| `GET`  | `/api/jobs/{id}/result` | redirect to the primary GLB |
| `GET`  | `/api/health` | TRELLIS configured? backends? |

## Configure hosted TRELLIS

In `.env`:
```
RUNPOD_API_KEY=...
RUNPOD_ENDPOINT_ID=...
TRELLIS_TEXTURE_SIZE=1024     # optional, default 1024
TRELLIS_WAIT_SECONDS=900      # optional, how long ONE attempt waits
```
Or Replicate as an alternative:
```
REPLICATE_API_TOKEN=r8_xxx
TRELLIS_REPLICATE_MODEL=owner/trellis:<version-hash>
```
Pin the exact Replicate model:version you intend to use. The reconstruct stage
saves the provider response to `artifacts/reconstruct/<provider>_output.json`
(with the base64 mesh elided — it is already written as `model.glb`) and downloads
the returned GLB, so if a deployment names its output field differently, that file
shows you what came back.

## The RunPod endpoint — the settings that decide whether this works

**Measured on the live endpoint (2026-07-30):** a warm worker turns one photo into
a 1.34 MB GLB in **126 s** at `texture_size: 1024`, after ~7 s of queueing. It
works. What fails is getting a warm worker.

The endpoint was configured `workersMin: 0`, `idleTimeout: 30 s`, so a worker is
torn down 30 seconds after finishing and the next upload waits for a full cold
start of a multi-gigabyte TRELLIS image. Jobs then sit `IN_QUEUE` past any
reasonable client timeout — which is how 8 of 54 jobs ended up failed and how the
pipeline hit "RunPod job timed out" on a job that was fine.

| Setting | Was | Use | Why |
|---|---|---|---|
| **Active workers** (`workersMin`) | `0` | `1` while demoing | The single change that removes cold start. Bills continuously — set it back to 0 afterwards. |
| **Idle timeout** | `30 s` | `300 s`+ | Keeps a warm worker between uploads, so the second photo of a demo is not another cold start. |
| **Container disk** | `30 GB` | check | TRELLIS-image-large plus CUDA and deps is tight in 30 GB. A worker that cannot unpack loops in `initializing`. |
| **Network volume** | none | consider | Weights on a volume make start-up a mount instead of a download. Baking them into the image works too. |
| **Max workers** | `3` | fine | Only matters once several people upload at once. |

Check the endpoint any time with:
```powershell
cd nextxr-ontology/server/threed_platform
python check_runpod.py        # balance, queue depth, worker states
```
`workers: {idle: 0, initializing: N, ready: 0}` with jobs `inQueue` is the
cold-start problem, not a broken pipeline.

### A slow job is no longer a lost job

`app/stages/reconstruct.py` submits asynchronously (`POST /run`), writes the RunPod
job id to the job store **before** it starts waiting, and resumes from that id on
the next run. Running out of patience now costs a retry, not the GPU minutes:
RunPod finishes the job, and the next attempt collects the result instead of
paying to generate it again. A dropped connection mid-poll is retried rather than
treated as a failed job.

## Extend
- **New pipeline stage:** add a `Stage` subclass in `app/stages/`, drop it into
  the right list in `pipeline.py`. Timing/artifacts/recording are automatic.
- **Swap the worker for a real queue:** replace the thread pool in
  `app/orchestrator.py` with a Celery/RQ consumer — the stage contract is unchanged.
- **Drawing→GLB:** currently the drawing route emits `scene.json` (rendered by
  the 2d-to-3d React viewer). Converting that scene graph to a single GLB is the
  documented next step.
