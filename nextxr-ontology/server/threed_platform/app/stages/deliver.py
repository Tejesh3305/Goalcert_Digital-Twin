"""Stages 17–19 — Optimization/Compression, Digital-Twin Packaging, Export.

ExportStage writes the model in every requested format (trimesh handles
GLB/OBJ/STL/PLY natively). PackageStage attaches the digital-twin metadata that
lets telemetry bind live sensor data to the asset/components.
"""
from __future__ import annotations

import json
import shutil

from ..config import settings
from .base import Ctx, Stage, StageSkipped

EXPORT_FORMATS = ["glb", "obj", "stl", "ply"]


class ExportStage(Stage):
    name = "export"

    def run(self, ctx: Ctx) -> str:
        try:
            import trimesh
        except Exception:
            raise StageSkipped("trimesh not installed")
        src = ctx.get("mesh_path")
        m = trimesh.load(src, force="mesh")
        adir = ctx.artifacts(self.name)
        exports = {}
        # obj/stl/ply are single-mesh formats with no PBR material model, so the
        # flattened load is the right input for them. GLB is NOT — see below.
        for fmt in EXPORT_FORMATS:
            if fmt == "glb":
                continue
            try:
                p = adir / f"model.{fmt}"
                m.export(p)
                exports[fmt] = ctx.rel(p)
            except Exception as e:
                exports[fmt] = f"failed: {type(e).__name__}"

        # THE CANONICAL VIEWER FILE IS A BYTE COPY, NOT A RE-EXPORT.
        #
        # "glb" is in EXPORT_FORMATS, so the loop above used to write model.glb
        # from the force="mesh" flattening — and then the `if not primary.exists()`
        # guard found the file it had just written and skipped the copy. The
        # guard read like a fallback; in practice it never fired, so every job
        # shipped the flattened re-export as `result_glb` and the byte copy was
        # dead code.
        #
        # That is the file the viewer loads and the twin embeds. Flattening
        # concatenates a multi-part asset to a single material, and the re-encode
        # drops WebP texture compression unless asked for it again — neither is
        # something to do to the model's final output on the way out the door.
        primary = adir / "model.glb"
        shutil.copy(src, primary)
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
                # Which chain produced this. Recorded because the two profiles
                # preprocess the input differently, so comparing two twins is
                # only meaningful once you know whether they were built the
                # same way.
                "pipeline_profile": settings.pipeline_profile,
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
