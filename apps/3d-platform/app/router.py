"""router.py — decide which backend an upload belongs to.

Two very different problems share the front door:
  • object  — a photo of a single object  → TRELLIS object reconstruction
  • drawing — a floor plan / CAD / blueprint / PDF → the 2d-to-3d scene parser

We never send a line drawing through TRELLIS. Routing is heuristic (saturation +
white-ratio + file type) and always overridable via the upload field `route`.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


def classify(path: Path | str, fields: dict | None = None) -> dict:
    fields = fields or {}
    forced = (fields.get("route") or "").strip().lower()
    if forced in ("object", "drawing"):
        return {"route": forced, "reason": "forced by upload field", "confidence": 1.0}

    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return {"route": "drawing", "reason": "PDF engineering drawing", "confidence": 0.95}

    try:
        img = Image.open(path).convert("RGB")
    except Exception:
        return {"route": "object", "reason": "unreadable as image; defaulting", "confidence": 0.3}

    arr = np.asarray(img.resize((256, 256))).astype(np.float32) / 255.0
    mx = arr.max(axis=2)
    mn = arr.min(axis=2)
    sat = np.where(mx > 0, (mx - mn) / np.clip(mx, 1e-6, None), 0.0)
    sat_mean = float(sat.mean())
    white_ratio = float((arr.min(axis=2) > 0.82).mean())   # near-white pixels
    dark_ink = float((arr.max(axis=2) < 0.25).mean())       # black line ink

    # Drawings: desaturated, lots of white paper, thin dark ink lines.
    is_drawing = (sat_mean < 0.12 and white_ratio > 0.35) or (white_ratio > 0.55 and dark_ink > 0.02)
    route = "drawing" if is_drawing else "object"
    conf = 0.8 if is_drawing else 0.7
    reason = (f"sat={sat_mean:.2f} white={white_ratio:.2f} ink={dark_ink:.3f} "
              f"→ {'line drawing' if is_drawing else 'photo of object'}")
    return {"route": route, "reason": reason, "confidence": conf,
            "metrics": {"saturation": round(sat_mean, 3), "white_ratio": round(white_ratio, 3),
                        "dark_ink": round(dark_ink, 4)}}
