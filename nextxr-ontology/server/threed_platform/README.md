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
REPLICATE_API_TOKEN=r8_xxx
TRELLIS_REPLICATE_MODEL=owner/trellis:<version-hash>
```
Pin the exact Replicate model:version you intend to use. The reconstruct stage
saves the raw provider response to `artifacts/reconstruct/replicate_output.json`
and downloads the returned GLB — if a deployment names its output field
differently, that file shows you what came back.

## Extend
- **New pipeline stage:** add a `Stage` subclass in `app/stages/`, drop it into
  the right list in `pipeline.py`. Timing/artifacts/recording are automatic.
- **Swap the worker for a real queue:** replace the thread pool in
  `app/orchestrator.py` with a Celery/RQ consumer — the stage contract is unchanged.
- **Drawing→GLB:** currently the drawing route emits `scene.json` (rendered by
  the 2d-to-3d React viewer). Converting that scene graph to a single GLB is the
  documented next step.
