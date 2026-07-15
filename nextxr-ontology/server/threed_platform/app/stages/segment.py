"""Stage 4 — Segmentation / Background Removal.

Removes walls, people, floor, sky, tools etc. so TRELLIS sees only the target.
Uses `rembg` (U^2-Net) when installed; otherwise falls back to a centre-weighted
alpha so the pipeline still runs (clearly noted in the job record).

Emits: rgba.png (cutout), mask.png (binary), and records bbox.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from .base import Ctx, Stage


def _bbox_of_alpha(alpha: np.ndarray) -> list[int] | None:
    ys, xs = np.where(alpha > 16)
    if len(xs) == 0:
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]


class SegmentStage(Stage):
    name = "segment"

    def run(self, ctx: Ctx) -> str:
        src = ctx.get("working_image") or ctx.get("input_path")
        img = Image.open(src).convert("RGB")
        adir = ctx.artifacts(self.name)
        note = ""

        rgba = None
        try:
            from rembg import remove  # type: ignore
            rgba = remove(img)  # returns RGBA PIL image
            note = "rembg U^2-Net"
        except Exception as e:
            # Fallback: keep full image, soft oval alpha so downstream still works.
            note = f"rembg unavailable ({type(e).__name__}); centre-oval fallback"
            w, h = img.size
            yy, xx = np.mgrid[0:h, 0:w]
            cx, cy = w / 2, h / 2
            r = (((xx - cx) / (w * 0.48)) ** 2 + ((yy - cy) / (h * 0.48)) ** 2)
            alpha = np.clip((1.0 - r) * 255 * 2, 0, 255).astype(np.uint8)
            rgba = Image.fromarray(np.dstack([np.asarray(img), alpha]))

        rgba_path = adir / "rgba.png"
        rgba.save(rgba_path)
        alpha = np.asarray(rgba)[:, :, 3]
        mask = Image.fromarray((alpha > 16).astype(np.uint8) * 255)
        mask_path = adir / "mask.png"
        mask.save(mask_path)

        bbox = _bbox_of_alpha(alpha)
        coverage = round(float((alpha > 16).mean()) * 100, 1)
        ctx.set("cutout_image", str(rgba_path))
        ctx.set("mask_image", str(mask_path))
        ctx.set("object_bbox", bbox)
        ctx.set("object_coverage_pct", coverage)
        ctx.set("working_image", str(rgba_path))
        return f"{note} · coverage={coverage}%"
