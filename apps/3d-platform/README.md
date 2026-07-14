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

## Run

```powershell
cd 3d-platform
python -m pip install -r requirements.txt      # first time
copy .env.example .env                          # then fill in keys (optional)
./run.ps1
```

Open **http://localhost:8100** → drop an image → watch the stages stream →
preview the GLB. Works **without any keys** (reconstruction returns a clearly
marked *stub* mesh so you can exercise the whole pipeline); set the two TRELLIS
vars in `.env` for real models.

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
