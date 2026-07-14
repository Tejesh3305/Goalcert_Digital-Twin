"""Stages 17–19 — Optimization/Compression, Digital-Twin Packaging, Export.

ExportStage writes the model in every requested format (trimesh handles
GLB/OBJ/STL/PLY natively). PackageStage attaches the digital-twin metadata that
lets telemetry bind live sensor data to the asset/components.
"""
from __future__ import annotations

import json
import shutil

from .base import Ctx, Stage, StageSkipped

EXPORT_FORMATS = ["glb", "obj", "stl", "ply"]


class ExportStage(Stage):
    name = "export"

    def run(self, ctx: Ctx) -> str:
        try:
            import trimesh
        except Exception:
            raise StageSkipped("trimesh not installed")
        m = trimesh.load(ctx.get("mesh_path"), force="mesh")
        adir = ctx.artifacts(self.name)
        exports = {}
        for fmt in EXPORT_FORMATS:
            try:
                p = adir / f"model.{fmt}"
                m.export(p)
                exports[fmt] = ctx.rel(p)
            except Exception as e:
                exports[fmt] = f"failed: {type(e).__name__}"
        # canonical viewer file
        primary = adir / "model.glb"
        if not primary.exists():
            shutil.copy(ctx.get("mesh_path"), primary)
        exports["glb"] = ctx.rel(primary)
        ctx.set("exports", exports)
        ctx.set("result_glb", ctx.rel(primary))
        ok = [k for k, v in exports.items() if not str(v).startswith("failed")]
        return f"exported: {', '.join(ok)}"


class PackageStage(Stage):
    name = "package"

    def run(self, ctx: Ctx) -> str:
        fields = ctx.get("fields", {}) or {}
        twin = {
            "asset_id": fields.get("asset_id") or f"ASSET-{ctx.job_id[:6].upper()}",
            "asset_type": (ctx.get("understanding", {}) or {}).get("object_label", "unknown"),
            "source": {
                "job_id": ctx.job_id,
                "route": ctx.get("route"),
                "reconstruction": ctx.get("reconstruction"),
                "input": ctx.get("filename"),
            },
            "mesh": (ctx.get("exports", {}) or {}).get("glb"),
            "lods": ctx.get("lods", {}),
            "exports": ctx.get("exports", {}),
            "materials": [ (ctx.get("material", {}) or {}).get("predicted", "unclassified") ],
            "parts": (ctx.get("semantic", {}) or {}).get("parts", []),
            "quality": ctx.get("mesh_quality", {}),
            "bbox_m": (ctx.get("mesh_quality", {}) or {}).get("bounds"),
        }
        out = ctx.artifacts(self.name) / "twin.json"
        out.write_text(json.dumps(twin, indent=2), encoding="utf-8")
        ctx.set("twin", twin)
        ctx.set("twin_path", ctx.rel(out))
        return f"twin {twin['asset_id']} packaged"
