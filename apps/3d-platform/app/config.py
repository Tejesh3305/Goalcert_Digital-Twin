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

    # ── geometry prior retrieval ──
    prior_enabled: bool = (os.getenv("PRIOR_ENABLED", "1") not in ("0", "false", "False"))
    prior_retrieve_threshold: float = _f("PRIOR_RETRIEVE_THRESHOLD", 0.92)  # ≥ → reuse canonical mesh
    prior_hint_threshold: float = _f("PRIOR_HINT_THRESHOLD", 0.55)          # ≥ → pass as hint to TRELLIS

    # ── drawing route ──
    two_d_to_3d_url: str = os.getenv("TWO_D_TO_3D_URL", "") or ""

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
