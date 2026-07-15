"""library.py — the geometry-prior vector store.

A curated library of known assets (datacenter + hospital equipment). Each entry
holds a reference image, an optional canonical mesh, asset metadata, and a cached
embedding. Retrieval embeds the query image and returns nearest neighbours by
cosine similarity.

On-disk layout (transparent, inspectable):
    data/priors/
        index.json          # {method, items:[{id, asset_type, ref, mesh, meta, added}]}
        vecs.npy            # N×D float32, aligned to items order, for `method`
        refs/<id>.png       # stored reference image
        meshes/<id>.glb     # stored canonical mesh (optional)

If the active embedding method changes (e.g. you install CLIP), the cache is
rebuilt automatically from the stored reference images.
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

import numpy as np
from PIL import Image

from ..config import settings
from .embedder import active_method, embed_image


class PriorLibrary:
    def __init__(self, root: Path | None = None):
        self.dir = (root or settings.data_dir) / "priors"
        self.refs = self.dir / "refs"
        self.meshes = self.dir / "meshes"
        for d in (self.dir, self.refs, self.meshes):
            d.mkdir(parents=True, exist_ok=True)
        self.index_path = self.dir / "index.json"
        self.vecs_path = self.dir / "vecs.npy"

    # ── index io ─────────────────────────────────────────────────────────────
    def _load_index(self) -> dict:
        if self.index_path.exists():
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        return {"method": None, "items": []}

    def _save_index(self, idx: dict) -> None:
        self.index_path.write_text(json.dumps(idx, indent=2), encoding="utf-8")

    # ── add ──────────────────────────────────────────────────────────────────
    def add(self, image_path, asset_type: str, mesh_path=None, meta: dict | None = None) -> dict:
        idx = self._load_index()
        pid = uuid.uuid4().hex[:10]
        ref = self.refs / f"{pid}.png"
        Image.open(image_path).convert("RGB").save(ref)
        mesh_rel = None
        if mesh_path and Path(mesh_path).exists():
            dst = self.meshes / f"{pid}{Path(mesh_path).suffix.lower()}"
            shutil.copy(mesh_path, dst)
            mesh_rel = dst.name
        item = {"id": pid, "asset_type": asset_type, "ref": ref.name,
                "mesh": mesh_rel, "meta": meta or {}, "added": round(time.time(), 1)}
        idx["items"].append(item)
        self._save_index(idx)
        self._rebuild_cache(idx)   # keep vecs.npy in sync
        return item

    # ── cache ────────────────────────────────────────────────────────────────
    def _rebuild_cache(self, idx: dict | None = None) -> tuple[np.ndarray, dict]:
        idx = idx or self._load_index()
        method = active_method()
        vecs = []
        for it in idx["items"]:
            v, _ = embed_image(self.refs / it["ref"])
            vecs.append(v)
        arr = np.stack(vecs).astype("float32") if vecs else np.zeros((0, 1), "float32")
        np.save(self.vecs_path, arr)
        idx["method"] = method
        self._save_index(idx)
        return arr, idx

    def _ensure_cache(self) -> tuple[np.ndarray, dict]:
        idx = self._load_index()
        if not idx["items"]:
            return np.zeros((0, 1), "float32"), idx
        if idx.get("method") != active_method() or not self.vecs_path.exists():
            return self._rebuild_cache(idx)
        arr = np.load(self.vecs_path)
        if arr.shape[0] != len(idx["items"]):
            return self._rebuild_cache(idx)
        return arr, idx

    # ── search ───────────────────────────────────────────────────────────────
    def search(self, image_path, k: int = 5) -> list[dict]:
        arr, idx = self._ensure_cache()
        if arr.shape[0] == 0:
            return []
        q, _ = embed_image(image_path)
        if q.shape[0] != arr.shape[1]:
            arr, idx = self._rebuild_cache(idx)
            if arr.shape[0] == 0 or q.shape[0] != arr.shape[1]:
                return []
        sims = arr @ q
        order = np.argsort(-sims)[:k]
        out = []
        for i in order:
            it = idx["items"][int(i)]
            mesh_abs = str((self.meshes / it["mesh"]).resolve()) if it.get("mesh") else None
            out.append({**it, "similarity": round(float(sims[int(i)]), 4),
                        "mesh_path": mesh_abs})
        return out

    def list(self) -> dict:
        idx = self._load_index()
        return {"method": idx.get("method") or active_method(),
                "count": len(idx["items"]),
                "items": [{k: it[k] for k in ("id", "asset_type", "mesh", "added")} for it in idx["items"]]}


library = PriorLibrary()
