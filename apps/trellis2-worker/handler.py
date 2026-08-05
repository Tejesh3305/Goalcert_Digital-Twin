"""
handler.py — RunPod serverless worker for Microsoft TRELLIS.2
(image → PBR-textured 3D GLB).

THIS IS A FAITHFUL REPLICATION OF THE UPSTREAM REFERENCE CODE.
Every generation parameter, default value and export argument below is copied
from microsoft/TRELLIS.2 `app.py` / `example.py`. Nothing is "improved", because
the point is to reproduce their published quality exactly. The only additions
are transport concerns (base64 in/out, clamping, error reporting) which sit
outside the model.

WHY THIS EXISTS ALONGSIDE apps/trellis-worker
---------------------------------------------
TRELLIS.2 is not a new checkpoint for TRELLIS 1 — it is a different repository,
a different Python package (`trellis2`, not `trellis`), a different pipeline
class, a different latent representation (O-Voxel rather than SLat over a sparse
structure), and a different export path (`o_voxel.postprocess.to_glb`, not
`trellis.utils.postprocessing_utils.to_glb`). None of the v1 worker's parameter
names exist here. So it is a new worker, not an edit of the old one, and both
can be deployed side by side.

Contract (only an image is required):
  input: {
    "images": ["<b64 png/jpg>"] | "image" | "image_base64": "<b64>",
    "seed": 1,
    "resolution": "512" | "1024" | "1536",
    "ss_sampling_steps": 12,   "ss_guidance_strength": 7.5,
    "ss_guidance_rescale": 0.7, "ss_rescale_t": 5.0,
    "shape_slat_sampling_steps": 12,   "shape_slat_guidance_strength": 7.5,
    "shape_slat_guidance_rescale": 0.5, "shape_slat_rescale_t": 3.0,
    "tex_slat_sampling_steps": 12,   "tex_slat_guidance_strength": 1.0,
    "tex_slat_guidance_rescale": 0.0, "tex_slat_rescale_t": 3.0,
    "decimation_target": 500000, "texture_size": 2048,
    "preprocess": true, "extension_webp": true
  }
  output: { "glb": "<b64>", "metadata": {...} }   (errors: {"error": "..."})

The response shape is deliberately identical to the v1 worker so the
orchestrator's `_save_glb_from_output` reads it with no changes.
"""
from __future__ import annotations

import base64
import inspect
import io
import os
import time
import traceback

# Set before torch/trellis2 import — these are read at module-import time.
os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
os.environ.setdefault("ATTN_BACKEND", "flash-attn")
os.environ.setdefault("SPCONV_ALGO", "native")

import runpod  # type: ignore
from PIL import Image

MODEL = os.environ.get("TRELLIS2_MODEL", "microsoft/TRELLIS.2-4B")

# Upstream app.py exposes resolution as a 3-way choice and maps it to the
# pipeline's cascade variants. Copied verbatim — the "_cascade" suffix is not
# cosmetic, it selects a genuinely different multi-stage sampling path.
PIPELINE_TYPES = {"512": "512", "1024": "1024_cascade", "1536": "1536_cascade"}

# ── model singleton (survives across invocations on a warm worker) ───────────
_PIPELINE = None


def _pipeline():
    global _PIPELINE
    if _PIPELINE is None:
        from trellis2.pipelines import Trellis2ImageTo3DPipeline
        p = Trellis2ImageTo3DPipeline.from_pretrained(MODEL)
        p.cuda()
        _PIPELINE = p
    return _PIPELINE


def _decode_images(inp: dict) -> list:
    raws: list[str] = []
    if isinstance(inp.get("images"), list) and inp["images"]:
        raws = [x for x in inp["images"] if isinstance(x, str) and len(x) > 64]
    else:
        one = inp.get("image") or inp.get("image_base64")
        if isinstance(one, str) and len(one) > 64:
            raws = [one]
    images = []
    for r in raws:
        if r.startswith("data:"):
            r = r.split(",", 1)[-1]
        images.append(Image.open(io.BytesIO(base64.b64decode(r))).convert("RGBA"))
    return images


def _num(inp, key, default, lo, hi, cast=float):
    try:
        return max(lo, min(hi, cast(inp.get(key, default))))
    except Exception:  # noqa: BLE001
        return default


def _to_glb(pipeline, mesh, decimation_target: int, texture_size: int):
    """Call o_voxel.postprocess.to_glb with the arguments THIS checkout accepts.

    Upstream spells the geometry-description arguments two different ways in two
    different files — `example.py` passes `attr_layout=mesh.layout,
    voxel_size=mesh.voxel_size, verbose=...`, while `app.py` passes
    `attr_layout=pipeline.pbr_attr_layout, grid_size=res, use_tqdm=...`. Both are
    current; which one a given commit accepts depends on the pinned o_voxel.

    Hard-coding either spelling makes this worker silently version-locked to one
    upstream commit and fail with a TypeError on the other. Reading the actual
    signature is what keeps "replicate upstream exactly" true over time instead
    of true on the day it was written.
    """
    import o_voxel  # noqa: PLC0415

    fn = o_voxel.postprocess.to_glb
    accepted = set(inspect.signature(fn).parameters)

    kwargs = dict(
        vertices=mesh.vertices,
        faces=mesh.faces,
        attr_volume=mesh.attrs,
        coords=mesh.coords,
        aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
        decimation_target=decimation_target,
        texture_size=texture_size,
        remesh=True,
        remesh_band=1,
        remesh_project=0,
    )

    # attr_layout: prefer the mesh's own, else the pipeline's PBR layout.
    kwargs["attr_layout"] = getattr(mesh, "layout", None)
    if kwargs["attr_layout"] is None:
        kwargs["attr_layout"] = pipeline.pbr_attr_layout

    # Grid description: voxel_size (example.py) or grid_size (app.py).
    if "voxel_size" in accepted and getattr(mesh, "voxel_size", None) is not None:
        kwargs["voxel_size"] = mesh.voxel_size
    elif "grid_size" in accepted:
        grid = getattr(mesh, "resolution", None) or getattr(mesh, "grid_size", None)
        if grid is None:
            raise RuntimeError(
                "to_glb requires grid_size but the mesh exposes neither "
                "`resolution` nor `grid_size` — upstream API changed.")
        kwargs["grid_size"] = grid

    # Progress flag is named differently across the two files.
    if "use_tqdm" in accepted:
        kwargs["use_tqdm"] = False
    elif "verbose" in accepted:
        kwargs["verbose"] = False

    return fn(**{k: v for k, v in kwargs.items() if k in accepted})


def generate(job):
    t0 = time.time()
    inp = job.get("input") or {}
    try:
        images = _decode_images(inp)
        if not images:
            return {"error": "no image provided — send input.images[] or "
                             "input.image (base64)"}
        # TRELLIS.2 is single-image conditioned; upstream documents no
        # multi-view entry point. Extra views are ignored rather than silently
        # averaged into something upstream never validated.
        image = images[0]

        seed = _num(inp, "seed", 1, 0, 2**31 - 1, int)
        resolution = str(inp.get("resolution", os.environ.get("TRELLIS2_RESOLUTION", "1024")))
        if resolution not in PIPELINE_TYPES:
            resolution = "1024"

        # Ranges below are the upstream slider bounds; defaults are the upstream
        # slider defaults. Do not "tune" these without a measured reason.
        ss = {
            "steps": _num(inp, "ss_sampling_steps", 12, 1, 50, int),
            "guidance_strength": _num(inp, "ss_guidance_strength", 7.5, 1.0, 10.0),
            "guidance_rescale": _num(inp, "ss_guidance_rescale", 0.7, 0.0, 1.0),
            "rescale_t": _num(inp, "ss_rescale_t", 5.0, 1.0, 6.0),
        }
        shape_slat = {
            "steps": _num(inp, "shape_slat_sampling_steps", 12, 1, 50, int),
            "guidance_strength": _num(inp, "shape_slat_guidance_strength", 7.5, 1.0, 10.0),
            "guidance_rescale": _num(inp, "shape_slat_guidance_rescale", 0.5, 0.0, 1.0),
            "rescale_t": _num(inp, "shape_slat_rescale_t", 3.0, 1.0, 6.0),
        }
        tex_slat = {
            "steps": _num(inp, "tex_slat_sampling_steps", 12, 1, 50, int),
            "guidance_strength": _num(inp, "tex_slat_guidance_strength", 1.0, 1.0, 10.0),
            "guidance_rescale": _num(inp, "tex_slat_guidance_rescale", 0.0, 0.0, 1.0),
            "rescale_t": _num(inp, "tex_slat_rescale_t", 3.0, 1.0, 6.0),
        }
        decimation_target = _num(inp, "decimation_target", 500_000, 100_000, 1_000_000, int)
        texture_size = _num(inp, "texture_size", 2048, 1024, 4096, int)
        preprocess = bool(inp.get("preprocess", True))
        webp = bool(inp.get("extension_webp", True))

        pipe = _pipeline()

        t_gen = time.time()
        mesh = pipe.run(
            image,
            seed=seed,
            preprocess_image=preprocess,
            sparse_structure_sampler_params=ss,
            shape_slat_sampler_params=shape_slat,
            tex_slat_sampler_params=tex_slat,
            pipeline_type=PIPELINE_TYPES[resolution],
        )[0]
        gen_s = time.time() - t_gen

        # Upstream example.py calls this between run() and to_glb(), commented
        # "nvdiffrast limit". 16777216 is 2^24 — a hard clamp on what the
        # rasterizer can index, not a quality setting. Omitting it lets a dense
        # 1536³ mesh overflow that limit inside to_glb's texture bake.
        try:
            mesh.simplify(16777216)
        except Exception:  # noqa: BLE001
            pass  # older builds without .simplify — the clamp is a safety net

        t_glb = time.time()
        glb = _to_glb(pipe, mesh, decimation_target, texture_size)
        # glb.export() writes a path, not a buffer, in the upstream API.
        out_path = "/tmp/out.glb"
        glb.export(out_path, extension_webp=webp)
        with open(out_path, "rb") as f:
            data = f.read()
        os.remove(out_path)
        glb_s = time.time() - t_glb

        meta = {
            "model": MODEL,
            "params": {
                "seed": seed, "resolution": resolution,
                "pipeline_type": PIPELINE_TYPES[resolution],
                "sparse_structure_sampler_params": ss,
                "shape_slat_sampler_params": shape_slat,
                "tex_slat_sampler_params": tex_slat,
                "decimation_target": decimation_target,
                "texture_size": texture_size,
                "preprocess": preprocess, "extension_webp": webp,
                "views_received": len(images), "views_used": 1,
            },
            "timings_s": {"generate": round(gen_s, 2),
                          "extract_glb": round(glb_s, 2),
                          "total": round(time.time() - t0, 2)},
            "glb_bytes": len(data),
        }
        return {"glb": base64.b64encode(data).decode(), "metadata": meta}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}",
                "trace": traceback.format_exc()[-2000:]}


runpod.serverless.start({"handler": generate})
