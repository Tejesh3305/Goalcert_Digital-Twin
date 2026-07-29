"""Stage 5 — Image Enhancement.

Normalise the cutout before reconstruction: trim to the object bbox, gently
resize toward a standard working resolution, mild denoise + contrast/sharpen.
Pillow-only (no heavy super-res model in v1; that's a drop-in upgrade point).
"""
from __future__ import annotations

from PIL import Image, ImageFilter, ImageOps

from ..util.images import _LANCZOS
from .base import Ctx, Stage


class EnhanceStage(Stage):
    name = "enhance"

    def run(self, ctx: Ctx) -> str:
        src = ctx.get("cutout_image") or ctx.get("working_image") or ctx.get("input_path")
        img = Image.open(src)
        has_alpha = img.mode == "RGBA"
        bbox = ctx.get("object_bbox")
        if bbox:
            pad = 12
            x0, y0, x1, y1 = bbox
            x0 = max(0, x0 - pad); y0 = max(0, y0 - pad)
            x1 = min(img.size[0], x1 + pad); y1 = min(img.size[1], y1 + pad)
            img = img.crop((x0, y0, x1, y1))

        # standardise the working resolution
        target = 1024
        w, h = img.size
        scale = target / max(w, h)
        if abs(scale - 1) > 0.05:
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), _LANCZOS)

        rgb = img.convert("RGB")
        rgb = rgb.filter(ImageFilter.SMOOTH)                 # mild denoise
        rgb = ImageOps.autocontrast(rgb, cutoff=1)           # contrast normalise
        rgb = rgb.filter(ImageFilter.UnsharpMask(radius=2, percent=80))  # sharpen

        if has_alpha:
            out = Image.merge("RGBA", (*rgb.split(), img.split()[3].resize(rgb.size)))
        else:
            out = rgb

        out_path = ctx.artifacts(self.name) / "enhanced.png"
        out.save(out_path)
        ctx.set("working_image", str(out_path))
        ctx.set("recon_input_image", str(out_path))
        return f"normalised → {out.size[0]}x{out.size[1]}"
