"""Stage 9 — TRELLIS Reconstruction (core), provider-abstracted.

Providers (auto-selected by config.provider()):
  • runpod    — RunPod serverless endpoint (preferred). POST /runsync, poll if async.
  • http      — a generic TRELLIS HTTP server (e.g. a RunPod pod) honouring our contract.
  • replicate — Replicate model:version (alternative).
  • stub      — placeholder mesh so the pipeline runs with no GPU configured.

Geometry-prior integration:
  • prior.mode == "retrieve" (high-confidence catalogue match) → reuse the
    canonical mesh directly (deterministic consistency); skip generation.
  • prior.mode == "hint" → pass asset label/type into the model input.

── Worker contract (the deployed RunPod TRELLIS handler) ─────────────────────
  request  input: {
    "image" / "image_base64" / "images": "<png/jpg base64, no data: prefix>",
    "texture_size": 1024,
    "output_format": "glb",
    "prior": {"asset_type": str, "similarity": float} | null
  }
  response output: { "glb": "<base64 of .glb>" }   (verified live: glTF v2 binary)
  Also accepted (robust): {glb_base64|model|mesh}=base64, or {glb_url|model_url|url}
  pointing at a hosted .glb, or a bare .glb URL string — _save_glb_from_output
  classifies base64 vs URL automatically.
"""
from __future__ import annotations

import base64
import json
import time

import requests

from ..config import settings
from .base import Ctx, Stage, StageSkipped

POLL_SECONDS = 5
POLL_TIMEOUT = 900


def _b64_image(path) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _download(url, out) -> None:
    r = requests.get(url, stream=True, timeout=600)
    r.raise_for_status()
    with open(out, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)


# Keys that may carry the model, in priority order. The value can be base64 OR a
# URL — we classify per-value (URLs start with http / end .glb|.gltf; otherwise a
# long string is treated as base64, with a leading data: prefix stripped).
_MODEL_KEYS = ("glb", "glb_base64", "model", "mesh", "model_base64", "mesh_base64",
               "glb_url", "model_url", "mesh_url", "model_file", "url")


def _classify_model_str(v: str):
    if not isinstance(v, str) or len(v) < 16:
        return None
    low = v.strip().lower()
    if low.startswith(("http://", "https://")):
        return ("url", v.strip())
    if low.endswith((".glb", ".gltf")):
        return ("url", v.strip())
    s = v.strip()
    if s.startswith("data:"):                       # data:model/gltf-binary;base64,XXXX
        s = s.split(",", 1)[-1]
    return ("b64", s) if len(s) > 256 else None     # long blob → base64 model


def _save_glb_from_output(output, glb_out) -> bool:
    """Find the model anywhere in the response (base64 or URL) and write it."""
    def walk(o):
        if isinstance(o, dict):
            for key in _MODEL_KEYS:                  # priority keys first
                got = _classify_model_str(o.get(key))
                if got:
                    return got
            for v in o.values():
                got = walk(v)
                if got:
                    return got
        elif isinstance(o, list | tuple):
            for v in o:
                got = walk(v)
                if got:
                    return got
        elif isinstance(o, str):
            got = _classify_model_str(o)
            if got and got[0] == "url":              # bare URL string only
                return got
        return None

    found = walk(output)
    if not found:
        return False
    kind, val = found
    if kind == "b64":
        with open(glb_out, "wb") as f:
            f.write(base64.b64decode(val))
    else:
        _download(val, glb_out)
    return True


class ReconstructStage(Stage):
    name = "reconstruct"

    def run(self, ctx: Ctx) -> str:
        adir = ctx.artifacts(self.name)
        glb_out = adir / "model.glb"
        prior = ctx.get("geometry_prior") or {}

        # 1) High-confidence catalogue match → reuse canonical mesh (max consistency).
        if prior.get("mode") == "retrieve" and prior.get("mesh_path"):
            import shutil
            shutil.copy(prior["mesh_path"], glb_out)
            ctx.set("mesh_path", str(glb_out))
            ctx.set("reconstruction", "retrieved")
            return (f"retrieved canonical mesh: {prior.get('asset_type')} "
                    f"(sim={prior.get('similarity')}) — generation skipped")

        # 2) Otherwise generate, optionally with the prior as a hint.
        provider = settings.provider()
        img_path = ctx.get("recon_input_image") or ctx.get("working_image")
        hint = prior if prior.get("mode") == "hint" else None

        if provider == "runpod":
            note = self._runpod(ctx, img_path, glb_out, hint)
        elif provider == "http":
            note = self._http(ctx, img_path, glb_out, hint)
        elif provider == "replicate":
            note = self._replicate(ctx, img_path, glb_out)
        else:
            note = self._stub(ctx, glb_out)

        ctx.set("mesh_path", str(glb_out))
        if hint:
            note += f" · prior-hint={hint.get('asset_type')}({hint.get('similarity')})"
        return note

    def _payload(self, img_path, hint):
        b64 = _b64_image(img_path)
        # Send the image under the common base64 aliases so the worker finds it
        # regardless of which key it reads (all raw base64, no data: prefix).
        # Full TRELLIS sampler params included: the legacy worker ignores them,
        # the production worker (apps/trellis-worker) honours them.
        return {"input": {
            "image": b64,
            "image_base64": b64,
            "images": [b64],
            "seed": 1,
            "ss_sampling_steps": 25, "ss_guidance_strength": 7.5,
            "slat_sampling_steps": 25, "slat_guidance_strength": 3.0,
            "mesh_simplify": 0.90,
            "texture_size": 2048,
            "preprocess": True,
            "output_format": "glb",
            "prior": ({"asset_type": hint["asset_type"], "similarity": hint["similarity"]}
                      if hint else None),
        }}

    # ── RunPod serverless ────────────────────────────────────────────────────
    def _runpod(self, ctx, img_path, glb_out, hint) -> str:
        base = f"https://api.runpod.ai/v2/{settings.runpod_endpoint_id}"
        headers = {"Authorization": f"Bearer {settings.runpod_api_key}",
                   "Content-Type": "application/json"}
        payload = self._payload(img_path, hint)
        r = requests.post(f"{base}/runsync", json=payload, headers=headers, timeout=600)
        r.raise_for_status()
        data = r.json()
        status = data.get("status")
        # If runsync didn't finish, poll the async status endpoint.
        if status in ("IN_QUEUE", "IN_PROGRESS"):
            job_id = data.get("id")
            t0 = time.time()
            while time.time() - t0 < POLL_TIMEOUT:
                time.sleep(POLL_SECONDS)
                s = requests.get(f"{base}/status/{job_id}", headers=headers, timeout=60).json()
                status = s.get("status")
                if status == "COMPLETED":
                    data = s
                    break
                if status in ("FAILED", "CANCELLED", "TIMED_OUT"):
                    raise RuntimeError(f"RunPod job {status}: {s.get('error')}")
            else:
                raise RuntimeError("RunPod job timed out")
        (ctx.artifacts(self.name) / "runpod_output.json").write_text(
            json.dumps(_safe(data), indent=2), encoding="utf-8")
        if not _save_glb_from_output(data.get("output", data), glb_out):
            raise RuntimeError("RunPod returned no GLB — see runpod_output.json")
        ctx.set("reconstruction", "trellis@runpod")
        return f"TRELLIS (RunPod) → {glb_out.name}"

    # ── generic HTTP server ──────────────────────────────────────────────────
    def _http(self, ctx, img_path, glb_out, hint) -> str:
        payload = self._payload(img_path, hint)["input"]
        r = requests.post(settings.trellis_http_url, json=payload, timeout=900)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "application/json" in ctype:
            data = r.json()
            (ctx.artifacts(self.name) / "http_output.json").write_text(
                json.dumps(_safe(data), indent=2), encoding="utf-8")
            if not _save_glb_from_output(data, glb_out):
                raise RuntimeError("HTTP server returned no GLB")
        else:  # raw .glb bytes
            with open(glb_out, "wb") as f:
                f.write(r.content)
        ctx.set("reconstruction", "trellis@http")
        return f"TRELLIS (HTTP) → {glb_out.name}"

    # ── Replicate (alternative) ──────────────────────────────────────────────
    def _replicate(self, ctx, img_path, glb_out) -> str:
        try:
            import replicate  # type: ignore
        except Exception:
            raise StageSkipped("replicate not installed")
        client = replicate.Client(api_token=settings.replicate_token)
        with open(img_path, "rb") as f:
            output = client.run(settings.replicate_model,
                                input={"images": [f], "image": f, "texture_size": 1024})
        (ctx.artifacts(self.name) / "replicate_output.json").write_text(
            json.dumps(_safe(output), indent=2), encoding="utf-8")
        if not _save_glb_from_output(output, glb_out):
            raise RuntimeError("Replicate returned no GLB")
        ctx.set("reconstruction", "trellis@replicate")
        return f"TRELLIS (Replicate) → {glb_out.name}"

    # ── dev stub ─────────────────────────────────────────────────────────────
    def _stub(self, ctx, glb_out) -> str:
        try:
            import numpy as np
            import trimesh
        except Exception:
            raise StageSkipped("install trimesh for the dev mesh")
        mesh = trimesh.creation.icosphere(subdivisions=3, radius=1.0)
        mesh.vertices[:, 1] *= 1.4
        h = mesh.vertices[:, 1] - mesh.vertices[:, 1].min()
        h = h / max(h.max(), 1e-6)
        colors = (np.stack([0.4 + 0.5 * h, 0.45 + 0.3 * (1 - h), 0.8 - 0.4 * h,
                            np.ones_like(h)], axis=1) * 255).astype(np.uint8)
        mesh.visual.vertex_colors = colors
        mesh.export(glb_out)
        ctx.set("reconstruction", "stub")
        return "⚠ STUB mesh (configure RunPod: RUNPOD_API_KEY + RUNPOD_ENDPOINT_ID)"


def _safe(o):
    try:
        json.dumps(o)
        return o
    except Exception:
        return str(o)
