"""
handler.py — production RunPod serverless worker for Microsoft TRELLIS
(image → 3D GLB), exposing the model's FULL capability:

  • both diffusion stages' sampler parameters (steps + guidance) — the real
    quality levers the legacy worker ran at silent defaults
  • multi-image conditioning (2-4 views of the same object) via
    run_multi_image — a large accuracy jump on complex objects
  • authoritative GPU-side preprocessing (rembg matting + recentring),
    skippable when the client already isolated the subject
  • controlled GLB extraction (mesh_simplify, texture_size)
  • a metadata echo (params used, stage timings, mesh counts) so the
    orchestrator's pipeline report can explain every generation

Contract (all input fields optional except one image):
  input: {
    "images": ["<b64 png/jpg>", ...]   # preferred; 1-4 views of ONE object
    "image" | "image_base64": "<b64>"  # legacy single-image aliases
    "seed": 1,
    "ss_sampling_steps": 12,           "ss_guidance_strength": 7.5,
    "slat_sampling_steps": 12,         "slat_guidance_strength": 3.0,
    "mesh_simplify": 0.95,             "texture_size": 1024,
    "multiimage_algo": "stochastic" | "multidiffusion",
    "preprocess": true                 # GPU-side rembg + recenter
  }
  output: { "glb": "<b64>", "metadata": {...} }   (errors: {"error": "..."} )

Backwards compatible: a bare {"image": "<b64>"} behaves like the old worker.
"""
from __future__ import annotations

import base64
import io
import os
import time
import traceback

os.environ.setdefault("ATTN_BACKEND", "flash-attn")     # or "xformers"
os.environ.setdefault("SPCONV_ALGO", "native")          # deterministic startup

import runpod  # type: ignore
from PIL import Image

# ── model singleton (survives across invocations on a warm worker) ────
_PIPELINE = None


def _pipeline():
    global _PIPELINE
    if _PIPELINE is None:
        from trellis.pipelines import TrellisImageTo3DPipeline
        model = os.environ.get("TRELLIS_MODEL", "microsoft/TRELLIS-image-large")
        p = TrellisImageTo3DPipeline.from_pretrained(model)
        p.cuda()
        _PIPELINE = p
    return _PIPELINE


def _decode_images(inp: dict) -> list[Image.Image]:
    raws: list[str] = []
    if isinstance(inp.get("images"), list) and inp["images"]:
        raws = [x for x in inp["images"] if isinstance(x, str) and len(x) > 64]
    else:
        one = inp.get("image") or inp.get("image_base64")
        if isinstance(one, str) and len(one) > 64:
            raws = [one]
    images = []
    for r in raws[:4]:
        if r.startswith("data:"):
            r = r.split(",", 1)[-1]
        images.append(Image.open(io.BytesIO(base64.b64decode(r))).convert("RGBA"))
    return images


def _num(inp, key, default, lo, hi, cast=float):
    try:
        return max(lo, min(hi, cast(inp.get(key, default))))
    except Exception:  # noqa: BLE001
        return default


def generate(job):
    t0 = time.time()
    inp = job.get("input") or {}
    try:
        images = _decode_images(inp)
        if not images:
            return {"error": "no image provided — send input.images[] or input.image (base64)"}

        seed = _num(inp, "seed", 1, 0, 2**31 - 1, int)
        ss_steps = _num(inp, "ss_sampling_steps", 12, 1, 100, int)
        ss_cfg = _num(inp, "ss_guidance_strength", 7.5, 0.0, 15.0)
        slat_steps = _num(inp, "slat_sampling_steps", 12, 1, 100, int)
        slat_cfg = _num(inp, "slat_guidance_strength", 3.0, 0.0, 15.0)
        simplify = _num(inp, "mesh_simplify", 0.95, 0.0, 0.98)
        tex_size = int(_num(inp, "texture_size", 1024, 256, 4096, int))
        algo = str(inp.get("multiimage_algo", "stochastic"))
        if algo not in ("stochastic", "multidiffusion"):
            algo = "stochastic"
        preprocess = bool(inp.get("preprocess", True))

        pipe = _pipeline()
        sampler_args = dict(
            seed=seed,
            formats=["mesh", "gaussian"],           # exactly what to_glb needs
            preprocess_image=preprocess,
            sparse_structure_sampler_params={"steps": ss_steps, "cfg_strength": ss_cfg},
            slat_sampler_params={"steps": slat_steps, "cfg_strength": slat_cfg},
        )
        t_gen = time.time()
        if len(images) > 1:
            outputs = pipe.run_multi_image(images, mode=algo, **sampler_args)
        else:
            outputs = pipe.run(images[0], **sampler_args)
        gen_s = time.time() - t_gen

        from trellis.utils import postprocessing_utils
        t_glb = time.time()
        glb = postprocessing_utils.to_glb(
            outputs["gaussian"][0], outputs["mesh"][0],
            simplify=simplify, texture_size=tex_size, verbose=False)
        buf = io.BytesIO()
        glb.export(buf, file_type="glb")
        data = buf.getvalue()
        glb_s = time.time() - t_glb

        meta = {
            "params": {
                "seed": seed, "ss_sampling_steps": ss_steps,
                "ss_guidance_strength": ss_cfg, "slat_sampling_steps": slat_steps,
                "slat_guidance_strength": slat_cfg, "mesh_simplify": simplify,
                "texture_size": tex_size, "preprocess": preprocess,
                "views": len(images),
                "multiimage_algo": algo if len(images) > 1 else None,
            },
            "timings_s": {"generate": round(gen_s, 2), "extract_glb": round(glb_s, 2),
                          "total": round(time.time() - t0, 2)},
            "glb_bytes": len(data),
            "model": os.environ.get("TRELLIS_MODEL", "microsoft/TRELLIS-image-large"),
        }
        return {"glb": base64.b64encode(data).decode(), "metadata": meta}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}",
                "trace": traceback.format_exc()[-2000:]}


runpod.serverless.start({"handler": generate})
