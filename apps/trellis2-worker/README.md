# TRELLIS.2 RunPod worker

Faithful replication of [microsoft/TRELLIS.2](https://github.com/microsoft/TRELLIS.2)
(`microsoft/TRELLIS.2-4B`, MIT) as a RunPod serverless worker, wired into the
3D platform's reconstruct stage.

## Why this is a new worker, not an upgrade of `apps/trellis-worker`

TRELLIS.2 is **not a newer checkpoint for TRELLIS 1**. It shares a name and
almost nothing else:

|                    | TRELLIS 1 (`apps/trellis-worker`)   | TRELLIS.2 (this worker)                              |
| ------------------ | ----------------------------------- | ---------------------------------------------------- |
| Repo               | `microsoft/TRELLIS`                 | `microsoft/TRELLIS.2`                                |
| Python package     | `trellis`                           | `trellis2`                                           |
| Pipeline class     | `TrellisImageTo3DPipeline`          | `Trellis2ImageTo3DPipeline`                          |
| Model              | `microsoft/TRELLIS-image-large`     | `microsoft/TRELLIS.2-4B`                             |
| Params             | ~1.2 B                              | 4 B                                                  |
| Released           | Dec 2024 (CVPR'25)                  | Dec 2025 (arXiv 2512.14692)                          |
| Representation     | SLat over a sparse structure        | O-Voxel (field-free sparse voxel)                    |
| Samplers           | 2 (sparse structure, SLat)          | 3 (sparse structure, shape SLat, tex SLat)           |
| Guidance knobs     | `cfg_strength`                      | `guidance_strength` + `guidance_rescale` + `rescale_t` |
| Resolution         | fixed                               | 512³ / 1024³ / 1536³ (cascade)                       |
| Materials          | baked colour                        | **PBR**: base colour, roughness, metallic, opacity   |
| GLB export         | `trellis.utils.postprocessing_utils`| `o_voxel.postprocess.to_glb`                         |
| Torch / CUDA       | 2.4.0 / cu121                       | 2.6.0 / cu124                                        |

None of v1's parameter names exist in v2. That is why `TRELLIS_VARIANT` exists
in the orchestrator rather than a single shared payload.

## Replication fidelity

Everything in `handler.py` that touches the model is copied from upstream
`app.py` / `example.py`:

- the three sampler parameter dicts, with upstream's slider defaults and bounds
  (ss: 12/7.5/0.7/5.0 · shape: 12/7.5/0.5/3.0 · tex: 12/1.0/0.0/3.0)
- `pipeline_type` mapping `{"512": "512", "1024": "1024_cascade", "1536": "1536_cascade"}`
- `o_voxel.postprocess.to_glb(..., aabb=[[-0.5]*3, [0.5]*3], remesh=True,
  remesh_band=1, remesh_project=0)` and `glb.export(..., extension_webp=True)`
- the environment flags upstream sets before import (`OPENCV_IO_ENABLE_OPENEXR`,
  `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`)

The Dockerfile runs **upstream's own `setup.sh`** with their documented flag set
rather than a hand-written pip list, because `o_voxel`, `cumesh`, `flexgemm` and
`nvdiffrec` are custom CUDA/Triton builds pinned to specific commits.

The one deliberate deviation: `to_glb`'s geometry arguments are resolved by
reading the function signature at runtime. Upstream spells them two ways in two
files (`voxel_size` + `mesh.layout` + `verbose` in `example.py`; `grid_size` +
`pipeline.pbr_attr_layout` + `use_tqdm` in `app.py`) and which one applies
depends on the pinned `o_voxel`. Hard-coding either would version-lock the
worker and fail with a `TypeError` on the other.

**Not replicated:** multi-image conditioning. TRELLIS 1 had `run_multi_image`;
upstream TRELLIS.2 documents single-image input only. Extra views sent to this
worker are ignored rather than fed through a path upstream never validated.

## Requirements

- Linux, NVIDIA GPU, **≥ 24 GB VRAM** (upstream floor; verified on A100/H100)
- CUDA 12.4
- 1536³ wants headroom above 24 GB — use 1024 on L4/A10/4090

Upstream timings on an H100: **512³ ≈ 3 s · 1024³ ≈ 17 s · 1536³ ≈ 60 s.**

## Build and deploy

```bash
cd apps/trellis2-worker
docker build -t <registry>/trellis2-worker:v1 .
docker push <registry>/trellis2-worker:v1
```

The build compiles flash-attn and four CUDA extensions — budget **30–60 min**
and a builder with ≥ 16 GB RAM. `MAX_JOBS=4` is already set to keep the
flash-attn compile from exhausting memory. The image ends with an import check,
so a broken extension build fails at build time rather than on your first paid
GPU invocation.

Then in RunPod → Serverless:

1. **New endpoint** (or edit the existing one) pointing at the pushed image.
2. GPU filter: 24 GB minimum; prefer 48 GB (L40S / A6000) for 1536³.
3. Attach a **network volume mounted at `/runpod-volume`** so the 4B checkpoint
   downloads once instead of on every cold worker. Alternatively uncomment the
   bake line in the Dockerfile (adds ~8–16 GB to the image, zero cold-start).
4. Set `workersMin: 1` if you want to avoid cold starts; the reconstruct stage
   survives them either way (it records the job id and resumes), but a cold
   start on a 4B model is not quick.

## The orchestrator side: `PIPELINE_PROFILE`

Replicating upstream is not only about the worker. Our object route used to wrap
the model in five stages that upstream does not have, and two of them ran
**before** the model:

- `SegmentStage` matted the photo with our own `rembg` (u2net — or a centre-oval
  ellipse when rembg is missing) and passed the worker an RGBA cutout.
- `EnhanceStage` cropped to a **rectangular** bbox with 12 px padding, then
  applied `SMOOTH` + `autocontrast` + `UnsharpMask`.

Upstream's `preprocess_image` opens with *"if the input already has a non-opaque
alpha channel, use it and skip matting."* So handing it our cutout **silently
disabled TRELLIS.2's own matting model** — we replaced the authors' matte with a
weaker one, and then fed it an image that had been sharpened, contrast-stretched
and framed to the wrong aspect ratio. Upstream crops a **square** centred on the
object and premultiplies alpha onto black, with no pixel filtering at all.
Measured on a 500×700 photo: our path produced a 707×1024 filtered RGB, theirs
produces a 698×698 premultiplied RGB.

`PIPELINE_PROFILE=faithful` therefore sends the **original upload** to the worker
and lets upstream do 100% of the preprocessing, with only read-only stages around
the model:

```
understand → reconstruct → mesh_validate → export → package
```

### The default is `auto`, and it will not change your live behaviour

The faithful chain is written against a worker **that is not deployed yet**.
Making it the unconditional default would silently change the one image→3D path
that currently works. So `PIPELINE_PROFILE=auto` (the default) resolves by what
is actually deployed:

| `TRELLIS_VARIANT` | resolved profile |
| ----------------- | ---------------- |
| `1` or `both` (today) | `legacy` — the 13-stage chain, unchanged |
| `2` (after you deploy) | `faithful` |

Deploying the v2 worker and setting `TRELLIS_VARIANT=2` is the only thing that
flips it. Nothing changes under you before then.

Set `PIPELINE_PROFILE=faithful` explicitly to A/B the upstream chain against your
**existing v1 endpoint** — worth trying, because it hands v1 the original photo
and lets TRELLIS 1's own rembg + recentring run instead of ours. Same argument as
for v2, just less dramatic. `PIPELINE_PROFILE=legacy` pins the old chain.

Every job records which chain built it, at `source.pipeline_profile` in
`twin.json`, so two twins are only comparable once you have checked that.

## Point the app at it

In `nextxr-ontology/server/threed_platform/.env`:

```ini
RUNPOD_ENDPOINT_ID=<the new endpoint id>
TRELLIS_VARIANT=2               # this alone flips PIPELINE_PROFILE=auto to faithful
TRELLIS2_RESOLUTION=1024        # 1536 for maximum fidelity
TRELLIS2_DECIMATION_TARGET=500000   # example.py uses 1000000
TRELLIS_TEXTURE_SIZE=2048           # example.py uses 4096 (max)
TRELLIS2_WEBP=1
```

The worker returns the GLB as base64 inside the job result, so pushing
`texture_size` to 4096 *and* `decimation_target` to 1 M can exceed RunPod's
response payload limit. Raise them together and confirm a job round-trips before
making it your default.

## What is deliberately NOT replicated

`example.py` also renders a turntable MP4 via `render_utils` + an HDRI
`EnvMap` (`assets/hdri/forest.exr`). That is a visualisation of the result, not
part of producing it, so the worker skips it — it would cost GPU seconds and
return a video nobody asked for. Everything on the geometry path
(`run` → `mesh.simplify(16777216)` → `to_glb` → `export`) is replicated.

Also dropped in faithful mode, and worth adding back deliberately later: our
LOD chain (`lods` is `{}` in `twin.json` under this profile) and the
geometry-prior catalogue retrieval. Both are real platform features, not accuracy
bugs — they were simply not part of upstream's pipeline, and upstream's is what
we are landing first. The legacy profile still produces both, so nothing is lost
while v1 is live.

Re-adding LODs is a one-line change (`TopologyLODStage()` into `OBJECT_FAITHFUL`)
and is safe: LOD0 is now a byte copy of the model's own GLB, and LOD1–3 are
derived files that never replace it. That is the recommended first "change from
our side" once the upstream baseline is confirmed working.

`TRELLIS_VARIANT=both` (the default) sends v1 *and* v2 parameters in one
payload so either worker reads what it understands — useful while migrating.
Set it to `2` once the v2 endpoint is the only one.

## Verify

```bash
cd nextxr-ontology/server/threed_platform
python check_runpod.py          # balance + worker health
```

Then run a real job through the platform. The reconstruct stage now records the
model id the worker reported, so the job note reads:

```
TRELLIS (RunPod) → model.glb · microsoft/TRELLIS.2-4B · queued 8s + ran 21s
```

If it still says `microsoft/TRELLIS-image-large`, the endpoint is serving the
old image. That echo exists precisely because v1 and v2 return
identically-shaped responses — a stale endpoint is otherwise indistinguishable
from a fresh one on the client side.

`data/jobs/<id>/artifacts/reconstruct/runpod_output.json` holds the full
metadata echo (params actually used, per-stage timings, mesh size).
