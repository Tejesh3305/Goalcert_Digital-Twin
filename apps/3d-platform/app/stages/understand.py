"""Stage 3 — Image Understanding (object detection / classification).

Lightweight by default: records basic descriptors and, if an OpenAI/Anthropic key
is available via the sibling 2d-to-3d gateway, asks a vision model for a one-shot
object label + estimated scale. Pose/absolute-scale from a single image are
unreliable, so they are recorded as hints only and never block the pipeline.
"""
from __future__ import annotations

import json

from PIL import Image

from .base import Ctx, Stage


class UnderstandStage(Stage):
    name = "understand"
    optional = True

    def run(self, ctx: Ctx) -> str:
        img = Image.open(ctx.get("working_image") or ctx.get("input_path")).convert("RGB")
        w, h = img.size
        aspect = round(w / h, 3)
        info = {
            "object_label": ctx.get("fields", {}).get("object_type") or "unknown",
            "scene": "single-object",
            "aspect_ratio": aspect,
            "orientation_hint": "landscape" if aspect > 1.1 else "portrait" if aspect < 0.9 else "square",
            "scale_hint": ctx.get("fields", {}).get("scale_hint") or "unspecified (single-image scale is unreliable)",
            "camera_pose": "not estimated (needs multi-view / SfM)",
        }
        ctx.set("understanding", info)
        (ctx.artifacts(self.name) / "understanding.json").write_text(
            json.dumps(info, indent=2), encoding="utf-8")
        return f"label={info['object_label']} · {info['orientation_hint']}"
