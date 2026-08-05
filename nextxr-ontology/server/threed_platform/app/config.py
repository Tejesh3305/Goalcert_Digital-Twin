"""config.py — environment-driven settings for the 3D platform.

Plain os.environ + dotenv (no extra deps). Import `settings` anywhere.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except Exception:
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except Exception:
        return default


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path(os.getenv("DATA_DIR", ROOT / "data")).resolve()

    # ── reconstruction providers (first configured wins) ──
    # RunPod serverless endpoint (preferred): set both.
    runpod_api_key: str = os.getenv("RUNPOD_API_KEY", "") or ""
    runpod_endpoint_id: str = os.getenv("RUNPOD_ENDPOINT_ID", "") or ""
    # Generic HTTP TRELLIS server (e.g. a RunPod *pod* exposing our contract).
    trellis_http_url: str = os.getenv("TRELLIS_HTTP_URL", "") or ""
    # Replicate (kept as an alternative).
    replicate_token: str = os.getenv("REPLICATE_API_TOKEN", "") or ""
    replicate_model: str = os.getenv("TRELLIS_REPLICATE_MODEL", "") or ""

    # ── TRELLIS generation ──
    # Which TRELLIS generation the deployed worker runs. These are NOT two
    # checkpoints of one model — they are different repos with disjoint
    # parameter names (v1: cfg_strength + mesh_simplify over SLat; v2: three
    # separate samplers with guidance_rescale/rescale_t over O-Voxel, plus PBR).
    # A payload built for one is meaningless to the other, so the stage has to
    # know which it is talking to.
    #   "2"    → TRELLIS.2 params only (apps/trellis2-worker)
    #   "1"    → TRELLIS 1 params only (apps/trellis-worker)
    #   "both" → send both sets; each worker reads its own and ignores the rest.
    #            The safe default while an endpoint is mid-migration.
    trellis_variant: str = os.getenv("TRELLIS_VARIANT", "both") or "both"

    # Texture resolution asked of the worker. 1024 rather than 2048, measured:
    # 2048 quadruples the texture data, and the worker hands the GLB back INSIDE
    # the job result as base64 — so it lengthens both the inference and the
    # payload that has to survive the round trip. A 1024 run on the live endpoint
    # returned a 1.34 MB GLB in 126s. Raise it when a job is worth the wait.
    # NOTE: upstream TRELLIS.2's own default is 2048; raise this once you are on
    # the v2 worker and have confirmed the payload round-trips.
    trellis_texture_size: int = _i("TRELLIS_TEXTURE_SIZE", 1024)

    # ── TRELLIS.2 only ──
    # Voxel grid resolution: "512" | "1024" | "1536". Upstream timings on an
    # H100 are ~3s / ~17s / ~60s, so this is the main quality-vs-cost dial.
    # 1024 is the balanced default; 1536 is the maximum-fidelity setting and
    # needs headroom above the 24 GB floor.
    trellis2_resolution: str = os.getenv("TRELLIS2_RESOLUTION", "1024") or "1024"
    # Target face count for upstream's decimation (their slider: 100k–1M).
    trellis2_decimation_target: int = _i("TRELLIS2_DECIMATION_TARGET", 500_000)
    # Upstream exports textures as WebP by default. three.js reads this via the
    # EXT_texture_webp extension, which our GlbViewer's GLTFLoader supports.
    trellis2_webp: bool = (os.getenv("TRELLIS2_WEBP", "1") not in ("0", "false", "False"))
    # How long to wait for one job before giving up on THIS attempt. Giving up no
    # longer loses the job — the RunPod job id is recorded, so a re-run collects
    # the result (see stages/reconstruct.py) — so this is a patience setting, not
    # a deadline for the work.
    trellis_wait_seconds: int = _i("TRELLIS_WAIT_SECONDS", 900)

    # ── object pipeline profile ──
    # "faithful" (default) — upstream's own example.py expressed in our stages,
    #   for WHICHEVER TRELLIS is deployed. The original photo goes straight to
    #   the model so the model's own preprocessing runs, and the model's own
    #   post-processing produces the GLB. Works against TRELLIS 1 today (their
    #   example.py: run(image, seed=1) → to_glb(simplify=0.95, texture_size=1024))
    #   and against TRELLIS.2 when that worker is deployed.
    # "legacy" — our original 13-stage spine (our rembg + enhancement before the
    #   model, our repair/LOD/UV after it). Correct for raw scan geometry.
    # "auto" — follow the deployed worker: legacy on v1, faithful on v2. What the
    #   default was while the faithful chain still assumed a v2-only worker.
    pipeline_profile_setting: str = (
        os.getenv("PIPELINE_PROFILE", "faithful") or "faithful").strip().lower()

    # ── mesh post-processing ──
    # Skip the trimesh hygiene pass on meshes that already carry PBR textures.
    # Both TRELLIS versions post-process their own output; re-repairing it
    # changes topology under the existing UVs and flattens the material set.
    # Set to 0 only for raw scan geometry that genuinely needs repair.
    mesh_repair_preserve_textured: bool = (
        os.getenv("MESH_REPAIR_PRESERVE_TEXTURED", "1") not in ("0", "false", "False"))

    # ── geometry prior retrieval ──
    prior_enabled: bool = (os.getenv("PRIOR_ENABLED", "1") not in ("0", "false", "False"))
    prior_retrieve_threshold: float = _f("PRIOR_RETRIEVE_THRESHOLD", 0.92)  # ≥ → reuse canonical mesh
    prior_hint_threshold: float = _f("PRIOR_HINT_THRESHOLD", 0.55)          # ≥ → pass as hint to TRELLIS

    # ── drawing route ──
    two_d_to_3d_url: str = os.getenv("TWO_D_TO_3D_URL", "") or ""

    @property
    def pipeline_profile(self) -> str:
        """Which object pipeline to run, resolving "auto".

        FAITHFUL IS NOT TRELLIS.2-SPECIFIC — it means "run the authors' own
        example.py for whichever model is deployed". Both TRELLIS versions ship
        one, both do their own preprocessing inside `run()`, and both do their
        own decimation and texture bake inside `to_glb()`. So the profile is a
        statement about WHO owns the pipeline, not about which checkpoint
        answers: upstream does, and our stages stay out of the way.

        Concretely, on the TRELLIS 1 endpoint that is live today, faithful sends
        the original photo (so v1's own rembg and recentring run instead of our
        u2net + sharpen) and asks for their published `simplify=0.95,
        texture_size=1024` instead of our hand-picked 0.90.

        "auto" remains for the case where you want the old 13-stage chain on v1
        and the upstream chain on v2 — it was the default while faithful still
        assumed a v2-only worker.
        """
        setting = self.pipeline_profile_setting
        if setting in ("faithful", "legacy"):
            return setting
        return "faithful" if self.trellis_variant == "2" else "legacy"

    def provider(self) -> str:
        if self.runpod_api_key and self.runpod_endpoint_id:
            return "runpod"
        if self.trellis_http_url:
            return "http"
        if self.replicate_token and self.replicate_model:
            return "replicate"
        return "stub"

    @property
    def has_trellis(self) -> bool:
        return self.provider() != "stub"


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
