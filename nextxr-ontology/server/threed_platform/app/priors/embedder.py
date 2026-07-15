"""embedder.py — pluggable image embedding for geometry-prior retrieval.

Quality ladder (auto-selected, best available first):
  1. CLIP (open_clip ViT-B/32) — semantic, robust to lighting/background. Needs
     torch + open-clip-torch (install when you have a GPU box / RunPod worker).
  2. Handcrafted descriptor (numpy only) — HSV colour histogram + edge-orientation
     histogram + shape stats. Deterministic, runs everywhere, good enough to wire
     and test the pipeline now; swap up to CLIP for production precision.

Every vector is L2-normalised, so cosine similarity == dot product. The active
method name is returned alongside the vector and stored in the index, so the
library re-embeds automatically if you upgrade the embedder.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

_CLIP = None


def _l2(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return (v / n).astype("float32") if n > 0 else v.astype("float32")


def _load_clip():
    global _CLIP
    if _CLIP is not None:
        return _CLIP
    try:
        import open_clip  # type: ignore
        import torch  # type: ignore
        model, _, preprocess = open_clip.create_model_and_transforms(
            "ViT-B-32", pretrained="laion2b_s34b_b79k")
        model.eval()
        _CLIP = ("clip-vit-b32", model, preprocess, torch)
    except Exception:
        _CLIP = ("none",)
    return _CLIP


def _handcrafted(path) -> np.ndarray:
    img = Image.open(path).convert("RGB").resize((224, 224))
    arr = np.asarray(img).astype("float32") / 255.0
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    mx = arr.max(2); mn = arr.min(2); df = mx - mn
    # HSV
    v = mx
    s = np.where(mx > 0, df / np.clip(mx, 1e-6, None), 0)
    h = np.zeros_like(mx)
    mask = df > 1e-6
    # hue (approx, in [0,1))
    rc = (mx - r) / np.clip(df, 1e-6, None)
    gc = (mx - g) / np.clip(df, 1e-6, None)
    bc = (mx - b) / np.clip(df, 1e-6, None)
    hue = np.where(mx == r, bc - gc, np.where(mx == g, 2.0 + rc - bc, 4.0 + gc - rc))
    h = np.where(mask, (hue / 6.0) % 1.0, 0)
    # 12x4x4 HSV histogram
    hi = np.clip((h * 12).astype(int), 0, 11)
    si = np.clip((s * 4).astype(int), 0, 3)
    vi = np.clip((v * 4).astype(int), 0, 3)
    hist = np.zeros((12, 4, 4), "float32")
    np.add.at(hist, (hi, si, vi), 1.0)
    hist = hist.ravel() / hist.sum()

    # edge-orientation histogram (Sobel via numpy gradient on luma)
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    gy, gx = np.gradient(luma)
    mag = np.sqrt(gx * gx + gy * gy)
    ang = (np.arctan2(gy, gx) + np.pi) / (2 * np.pi)  # [0,1)
    abins = np.clip((ang * 16).astype(int), 0, 15)
    eoh = np.zeros(16, "float32")
    np.add.at(eoh, abins, mag)
    eoh = eoh / max(eoh.sum(), 1e-6)

    # coarse shape: occupancy of a 8x8 grid where luma deviates from border bg
    bg = float(np.median(np.concatenate([luma[0], luma[-1], luma[:, 0], luma[:, -1]])))
    fg = (np.abs(luma - bg) > 0.12).astype("float32")
    grid = fg.reshape(8, 28, 8, 28).mean(axis=(1, 3)).ravel()  # 64

    vec = np.concatenate([hist * 2.0, eoh * 1.5, grid])
    return _l2(vec)


def embed_image(path) -> tuple[np.ndarray, str]:
    clip = _load_clip()
    if clip[0] == "clip-vit-b32":
        _, model, preprocess, torch = clip
        img = preprocess(Image.open(path).convert("RGB")).unsqueeze(0)
        with torch.no_grad():
            v = model.encode_image(img)[0].cpu().numpy()
        return _l2(v.astype("float32")), "clip-vit-b32"
    return _handcrafted(path), "handcrafted-v1"


def active_method() -> str:
    return _load_clip()[0] if _load_clip()[0] != "none" else "handcrafted-v1"
