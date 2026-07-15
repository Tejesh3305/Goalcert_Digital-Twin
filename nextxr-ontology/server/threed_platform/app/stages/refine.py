"""Stages 12–15 — UV / Texture / Material / Semantic (v1: record + defer).

TRELLIS already emits a textured mesh, so v1 keeps its UVs/texture and records
what's present. Material prediction and semantic part detection on arbitrary
generated meshes are research-grade; we record them as explicit, honest
post-MVP placeholders rather than fabricating data. Each is a clean drop-in
point when we add the dedicated models.
"""
from __future__ import annotations

import json

from .base import Ctx, Stage


class UVTextureStage(Stage):
    name = "uv_texture"
    optional = True

    def run(self, ctx: Ctx) -> str:
        info = {"uv": "from TRELLIS", "pbr_maps": []}
        try:
            import trimesh
            m = trimesh.load(ctx.get("mesh_path"), force="mesh")
            has_uv = getattr(getattr(m.visual, "uv", None), "shape", None) is not None
            info["uv"] = "present" if has_uv else "vertex-colors only"
            vis = type(m.visual).__name__
            info["visual"] = vis
        except Exception as e:
            info["note"] = f"inspect skipped: {type(e).__name__}"
        ctx.set("texture_info", info)
        (ctx.artifacts(self.name) / "texture.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
        return info.get("uv", "ok")


class MaterialStage(Stage):
    name = "material"
    optional = True

    def run(self, ctx: Ctx) -> str:
        hint = (ctx.get("fields", {}) or {}).get("material") or "unclassified"
        data = {"predicted": hint, "method": "user-hint / deferred",
                "note": "post-MVP: train a material classifier or use a curated taxonomy"}
        ctx.set("material", data)
        (ctx.artifacts(self.name) / "material.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        return f"material={hint} (deferred)"


class SemanticStage(Stage):
    name = "semantic"
    optional = True

    def run(self, ctx: Ctx) -> str:
        data = {"parts": [], "method": "deferred",
                "note": "post-MVP: part segmentation / retrieval against a parts taxonomy"}
        ctx.set("semantic", data)
        (ctx.artifacts(self.name) / "semantic.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        return "part detection deferred (post-MVP)"
