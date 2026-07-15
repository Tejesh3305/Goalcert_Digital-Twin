"""images.py — lightweight image loading + quality metrics (numpy/Pillow only).

No OpenCV dependency: blur is estimated via the variance of a numpy Laplacian,
exposure via histogram statistics. Good enough to gate GPU spend in Stage 2.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

# Pillow >= 10 renamed the resample enums.
_LANCZOS = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.BICUBIC)


def load_rgb(path: Path | str) -> Image.Image:
    return Image.open(path).convert("RGB")


def _gray(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("L"), dtype=np.float32)


def laplacian_var(gray: np.ndarray) -> float:
    """Variance of the Laplacian — a standard sharpness proxy (low = blurry)."""
    k = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float32)
    g = gray
    # valid 2D convolution without scipy
    sub = (
        g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:] - 4 * g[1:-1, 1:-1]
    )
    return float(sub.var())


def quality_report(path: Path | str) -> dict:
    img = load_rgb(path)
    w, h = img.size
    gray = _gray(img)
    lap = laplacian_var(gray)
    mean = float(gray.mean())
    # normalise sharpness to a 0..1-ish "blur score" (lower is sharper)
    sharpness = min(1.0, lap / 500.0)
    blur_score = round(1.0 - sharpness, 3)
    if mean < 45:
        exposure = "underexposed"
    elif mean > 210:
        exposure = "overexposed"
    else:
        exposure = "good"
    megapixels = round(w * h / 1_000_000, 2)
    suitable = (min(w, h) >= 256) and (blur_score < 0.85) and (exposure == "good" or mean > 25)
    warnings = []
    if min(w, h) < 512:
        warnings.append("low resolution (<512px on short side)")
    if blur_score >= 0.7:
        warnings.append("image looks soft/blurry")
    if exposure != "good":
        warnings.append(f"exposure {exposure}")
    return {
        "resolution": f"{w}x{h}",
        "width": w, "height": h, "megapixels": megapixels,
        "blur_score": blur_score, "sharpness": round(sharpness, 3),
        "mean_luma": round(mean, 1), "exposure": exposure,
        "suitable": bool(suitable), "warnings": warnings,
    }


def save_upscaled(img: Image.Image, out: Path, max_side: int = 1024) -> Path:
    w, h = img.size
    scale = max_side / max(w, h)
    if scale < 1:  # downscale large inputs
        img = img.resize((int(w * scale), int(h * scale)), _LANCZOS)
    elif scale > 1.4:  # gently upscale tiny inputs
        img = img.resize((int(w * scale), int(h * scale)), _LANCZOS)
    img.save(out)
    return out
