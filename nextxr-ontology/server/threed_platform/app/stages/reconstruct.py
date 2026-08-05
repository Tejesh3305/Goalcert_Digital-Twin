"""Stage 9 — TRELLIS Reconstruction (core), provider-abstracted.

Providers (auto-selected by config.provider()):
  • runpod    — RunPod serverless endpoint (preferred). POST /run, then poll.
  • http      — a generic TRELLIS HTTP server (e.g. a RunPod pod) honouring our contract.
  • replicate — Replicate model:version (alternative).
  • stub      — placeholder mesh so the pipeline runs with no GPU configured.

THE GPU WORK OUTLIVES THE REQUEST, SO THE JOB ID IS STATE
---------------------------------------------------------
This is the only stage whose work happens somewhere else, on a clock we do not
control. A serverless endpoint with no always-on worker cold-starts the TRELLIS
image before it can run anything, and a job can sit IN_QUEUE for longer than any
sensible client will wait. So the RunPod job id is written to the job store the
moment it exists, before waiting — and giving up on the wait no longer destroys
the work, because the next run of this stage asks about that id first.

Geometry-prior integration:
  • prior.mode == "retrieve" (high-confidence catalogue match) → reuse the
    canonical mesh directly (deterministic consistency); skip generation.
  • prior.mode == "hint" → pass asset label/type into the model input.

── Worker contract (the deployed RunPod TRELLIS handler) ─────────────────────
  request  input: {
    "image" / "image_base64" / "images": "<png/jpg base64, no data: prefix>",
    "texture_size": 1024,          # settings.trellis_texture_size
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
# How many consecutive FAILED STATUS READS end the wait. A job that is running
# fine must survive a network blip — one dropped TLS connection used to abandon
# it — but a permanently unreachable API should not be polled forever.
MAX_POLL_ERRORS = 12


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
        hint = prior if prior.get("mode") == "hint" else None

        # WHICH IMAGE THE MODEL SEES — the single most consequential line here.
        #
        # Faithful profile sends the ORIGINAL upload, because upstream's
        # `preprocess_image` is part of the model, not a convenience wrapper: it
        # mattes, crops a SQUARE around the object, and premultiplies alpha onto
        # black, and the image conditioner was trained on exactly that output.
        #
        # Sending our own cutout instead is worse than redundant. Upstream opens
        # with "if the input already carries a non-opaque alpha channel, use it
        # and skip matting" — so a pre-matted RGBA DISABLES their matting model
        # and substitutes ours. We were overriding the authors' preprocessing
        # with a weaker imitation and then wondering why the output was weaker.
        if settings.pipeline_profile == "legacy":
            img_path = (ctx.get("recon_input_image") or ctx.get("working_image")
                        or ctx.get("input_path"))
        else:
            img_path = ctx.get("input_path") or ctx.get("working_image")

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
        inp = {
            "image": b64,
            "image_base64": b64,
            "images": [b64],
            "seed": 1,
            "texture_size": settings.trellis_texture_size,
            "preprocess": True,
            "output_format": "glb",
            "prior": ({"asset_type": hint["asset_type"], "similarity": hint["similarity"]}
                      if hint else None),
        }
        variant = settings.trellis_variant
        faithful = settings.pipeline_profile == "faithful"

        # ── TRELLIS 1 (apps/trellis-worker) ──────────────────────────────────
        # `ss_guidance_strength` here means cfg_strength over the sparse
        # structure; `slat_*` drives the single SLat sampler. Neither name
        # exists in TRELLIS.2.
        #
        # THE FAITHFUL VALUES ARE TRELLIS 1's OWN example.py, WHICH IS:
        #
        #     outputs = pipeline.run(image, seed=1)
        #     glb = postprocessing_utils.to_glb(
        #         outputs['gaussian'][0], outputs['mesh'][0],
        #         simplify=0.95, texture_size=1024)
        #
        # `run(image, seed=1)` passes NO sampler params, so it takes the library
        # defaults — steps 12 / cfg 7.5 for the sparse structure, steps 12 /
        # cfg 3.0 for SLat. Their repo lists both, commented out, at exactly
        # those values. We send them explicitly rather than omitting them,
        # because a worker we did not build might carry different defaults and
        # "faithful" should not depend on that.
        #
        # The legacy numbers below (25 steps, simplify 0.90) were ours, not
        # theirs. More steps is a plausible guess and simplify 0.90 keeps more
        # faces than their 0.95 — but neither is what the authors published or
        # measured, and matching their example is the point of this profile.
        if variant in ("1", "both"):
            if faithful:
                inp.update({
                    "ss_sampling_steps": 12, "ss_guidance_strength": 7.5,
                    "slat_sampling_steps": 12, "slat_guidance_strength": 3.0,
                    "mesh_simplify": 0.95,
                })
            else:
                inp.update({
                    "ss_sampling_steps": 25, "ss_guidance_strength": 7.5,
                    "slat_sampling_steps": 25, "slat_guidance_strength": 3.0,
                    "mesh_simplify": 0.90,
                })

        # ── TRELLIS.2 (apps/trellis2-worker) ─────────────────────────────────
        # Three separate samplers, each with guidance_rescale and rescale_t.
        # Values are upstream's published defaults — the v2 worker clamps and
        # re-applies its own defaults for anything omitted, so sending them is
        # about making the request self-documenting, not about overriding.
        #
        # `ss_sampling_steps` is intentionally NOT set to the v1 value of 25
        # when both sets are sent: v2's worker clamps that key to its own 1-50
        # range and reads it as ITS sparse-structure step count. 25 steps is a
        # legitimate (slower, marginally cleaner) v2 setting, so the overlap is
        # harmless — but it is the one key the two schemas genuinely share, and
        # it is worth knowing that it lands in both.
        if variant in ("2", "both"):
            inp.update({
                "resolution": settings.trellis2_resolution,
                "ss_guidance_rescale": 0.7, "ss_rescale_t": 5.0,
                "shape_slat_sampling_steps": 12,
                "shape_slat_guidance_strength": 7.5,
                "shape_slat_guidance_rescale": 0.5, "shape_slat_rescale_t": 3.0,
                "tex_slat_sampling_steps": 12,
                "tex_slat_guidance_strength": 1.0,
                "tex_slat_guidance_rescale": 0.0, "tex_slat_rescale_t": 3.0,
                "decimation_target": settings.trellis2_decimation_target,
                "extension_webp": settings.trellis2_webp,
            })
        return {"input": inp}

    # ── RunPod serverless ────────────────────────────────────────────────────
    def _runpod(self, ctx, img_path, glb_out, hint) -> str:
        """Generate on the RunPod TRELLIS endpoint.

        SUBMIT ASYNC, RECORD THE ID, THEN WAIT — in that order, because the order
        is what makes a slow job recoverable.

        What this replaced held a `/runsync` connection open for up to ten
        minutes, then polled, and on running out of patience raised "RunPod job
        timed out" and threw the job id away. The job itself kept running: RunPod
        finished it, billed for it, and the GLB sat in a result nothing could ever
        fetch again. Retrying paid for the same work twice.

        Measured against the live endpoint, that is not a rare edge: with
        `workersMin: 0` and a 30-second idle timeout, every upload after a quiet
        spell waits for a cold start, and a queued job routinely outlives the
        client's patience. Writing the id to the job store first (`ctx.set` goes
        straight through to Postgres/SQLite) means the NEXT run of this stage
        collects the finished result instead of starting over.
        """
        base = f"https://api.runpod.ai/v2/{settings.runpod_endpoint_id}"
        headers = {"Authorization": f"Bearer {settings.runpod_api_key}",
                   "Content-Type": "application/json"}

        job_id = ctx.get("runpod_job_id") or ""
        state = None
        resumed = False
        if job_id:
            # A previous attempt submitted this and did not see it finish. Ask
            # about THAT job before spending another GPU-minute on a new one.
            state = self._runpod_status(base, headers, job_id)
            if state and state.get("status") not in ("FAILED", "CANCELLED",
                                                     "TIMED_OUT", None):
                resumed = True
            else:
                job_id, state = "", None         # dead or unknown — submit again

        if not job_id:
            r = requests.post(f"{base}/run", json=self._payload(img_path, hint),
                              headers=headers, timeout=120)
            r.raise_for_status()
            job_id = (r.json() or {}).get("id") or ""
            if not job_id:
                raise RuntimeError("RunPod accepted the request but returned no "
                                   "job id — nothing can be polled or recovered.")
            # BEFORE waiting, so a crash or a timeout still leaves the id behind.
            ctx.set("runpod_job_id", job_id)
            ctx.set("runpod_submitted_at", time.time())

        # The resume read above may ALREADY be the finished job — which is the
        # whole point of resuming. Polling again for something we are holding
        # would sit through another wait cycle for no reason.
        data = (state if (state or {}).get("status") == "COMPLETED"
                else self._await_runpod(base, headers, job_id, ctx))

        (ctx.artifacts(self.name) / "runpod_output.json").write_text(
            json.dumps(_elide_blobs(_safe(data)), indent=2), encoding="utf-8")
        if not _save_glb_from_output(data.get("output", data), glb_out):
            raise RuntimeError("RunPod returned no GLB — see runpod_output.json")

        # Consumed. Clearing it stops a LATER re-run from resurrecting this
        # result instead of generating from a new image.
        ctx.set("runpod_job_id", "")
        model = _worker_model(data)
        ctx.set("reconstruction", f"trellis@runpod ({model})" if model
                else "trellis@runpod")
        exec_s = (data.get("executionTime") or 0) / 1000
        delay_s = (data.get("delayTime") or 0) / 1000
        return (f"TRELLIS (RunPod{', resumed' if resumed else ''}) → {glb_out.name}"
                f"{' · ' + model if model else ''}"
                f" · queued {delay_s:.0f}s + ran {exec_s:.0f}s")

    def _runpod_status(self, base, headers, job_id) -> dict | None:
        """One status read. Returns None when the call itself failed, which is
        NOT the same as the job having failed and must not be treated as such."""
        try:
            r = requests.get(f"{base}/status/{job_id}", headers=headers, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception:
            return None

    def _await_runpod(self, base, headers, job_id, ctx) -> dict:
        """Poll until the job finishes, or until patience runs out.

        A FAILED STATUS READ IS NOT A FAILED JOB. The poll loop this replaced
        raised on any exception, so a single TLS blip mid-poll abandoned a job
        that was running perfectly well — observed against the live endpoint.
        Transient errors are retried; only a terminal status from RunPod, or the
        budget expiring, ends the wait.
        """
        deadline = time.time() + settings.trellis_wait_seconds
        last_status = ""
        consecutive_errors = 0

        while time.time() < deadline:
            time.sleep(POLL_SECONDS)
            state = self._runpod_status(base, headers, job_id)
            if state is None:
                consecutive_errors += 1
                if consecutive_errors >= MAX_POLL_ERRORS:
                    raise RuntimeError(
                        f"Lost contact with RunPod while job {job_id} was "
                        f"{last_status or 'in flight'} ({consecutive_errors} "
                        f"failed status reads). The job id is recorded, so "
                        f"re-running this job will pick it up.")
                continue
            consecutive_errors = 0

            status = state.get("status") or ""
            if status != last_status:
                last_status = status
                # Visible in the job record while it is still running, which is
                # the difference between "queued behind a cold start" and "stuck".
                ctx.set("runpod_status", status)

            if status == "COMPLETED":
                return state
            if status in ("FAILED", "CANCELLED", "TIMED_OUT"):
                ctx.set("runpod_job_id", "")      # terminal: nothing to resume
                raise RuntimeError(f"RunPod job {status}: {state.get('error')}")

        waited = settings.trellis_wait_seconds
        raise RuntimeError(
            f"RunPod job {job_id} was still {last_status or 'unstarted'} after "
            f"{waited}s. It has NOT been cancelled and its id is recorded — "
            f"re-run this job to collect the result. If it is stuck IN_QUEUE, "
            f"the endpoint has no ready worker: set an active worker "
            f"(workersMin >= 1) or raise the idle timeout, see the README.")

    # ── generic HTTP server ──────────────────────────────────────────────────
    def _http(self, ctx, img_path, glb_out, hint) -> str:
        payload = self._payload(img_path, hint)["input"]
        r = requests.post(settings.trellis_http_url, json=payload, timeout=900)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "application/json" in ctype:
            data = r.json()
            (ctx.artifacts(self.name) / "http_output.json").write_text(
                json.dumps(_elide_blobs(_safe(data)), indent=2), encoding="utf-8")
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


def _worker_model(data) -> str:
    """The model id the worker reported, if it reported one.

    Both workers echo `metadata.model`. Recording it turns "which TRELLIS
    actually produced this mesh?" from an inference about deployment state into
    a fact stored on the job — which matters precisely because v1 and v2 return
    identically-shaped responses and a stale endpoint looks exactly like a
    fresh one from the client side.
    """
    try:
        return str((((data.get("output") or {}).get("metadata")) or {}).get("model") or "")
    except Exception:  # noqa: BLE001
        return ""


def _safe(o):
    try:
        json.dumps(o)
        return o
    except Exception:
        return str(o)


# Long enough to be a payload rather than an id, short enough to keep every field
# that helps diagnose a response — urls, statuses, error strings, metadata.
_BLOB_CHARS = 512


def _elide_blobs(o):
    """A copy of a RunPod response with the model blobs replaced by a summary.

    `runpod_output.json` exists so an operator can see what the worker actually
    returned when something looks wrong. It was written verbatim, which meant the
    base64 mesh was stored a SECOND time — a 1.34 MB GLB becomes a 1.8 MB JSON
    file sitting next to the model.glb that already holds it, on every job.

    What matters for diagnosis is the shape of the response: which key carried
    the model and how big it was. That is kept; the payload is not.
    """
    if isinstance(o, dict):
        return {k: _elide_blobs(v) for k, v in o.items()}
    if isinstance(o, list | tuple):
        return [_elide_blobs(v) for v in o]
    if isinstance(o, str) and len(o) > _BLOB_CHARS:
        return f"<{len(o)} chars elided — see model.glb>"
    return o
