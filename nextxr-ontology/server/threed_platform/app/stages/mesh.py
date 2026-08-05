"""Stages 10–11, 16 — Mesh Repair, Topology/LOD, Mesh Validation (trimesh).

These run on the GLB produced by reconstruction. trimesh covers the bulk of
real mesh hygiene; LOD uses quadric decimation (via fast-simplification). Each
op is wrapped so a missing optional backend degrades to a recorded skip rather
than failing the job.
"""
from __future__ import annotations

import json
import shutil

from ..config import settings
from .base import Ctx, Stage, StageSkipped


def _load_mesh(path):
    import trimesh
    m = trimesh.load(path, force="mesh")
    return m


# Texture channels that only survive as long as nobody flattens the glTF.
_PBR_SLOTS = (("baseColorTexture", "baseColor"),
              ("metallicRoughnessTexture", "metallicRoughness"),
              ("normalTexture", "normal"),
              ("emissiveTexture", "emissive"),
              ("occlusionTexture", "occlusion"))


def textured_channels(path) -> list[str]:
    """Which PBR texture channels the GLB at `path` actually carries.

    Used to decide whether a stage is about to damage a finished asset.
    `trimesh.load(..., force="mesh")` CONCATENATES a glTF scene into one
    Trimesh; measured on a two-part textured scene, the two distinct materials
    collapse to one. A single-part mesh survives that round trip intact, so the
    flattening is only lossy for multi-part assets — but TRELLIS.2 emits exactly
    those, and the loss is silent.
    """
    import trimesh
    try:
        loaded = trimesh.load(path)
    except Exception:  # noqa: BLE001
        return []
    geoms = (list(loaded.geometry.values())
             if hasattr(loaded, "geometry") else [loaded])
    found: set[str] = set()
    for g in geoms:
        mat = getattr(getattr(g, "visual", None), "material", None)
        if mat is None:
            continue
        for attr, label in _PBR_SLOTS:
            if getattr(mat, attr, None) is not None:
                found.add(label)
    return sorted(found)


class MeshRepairStage(Stage):
    name = "mesh_repair"

    def run(self, ctx: Ctx) -> str:
        try:
            import trimesh  # noqa
        except Exception:
            raise StageSkipped("trimesh not installed")

        # A TEXTURED MESH IS ALREADY FINISHED — REPAIRING IT ONLY COSTS QUALITY.
        #
        # Both TRELLIS versions hand back a mesh their own post-processing has
        # already cleaned, decimated and UV-unwrapped; TRELLIS.2 additionally
        # remeshes it and paints a PBR set onto it. This stage's hygiene pass
        # was written for raw scan geometry, and on a generated asset each op is
        # at best a no-op and at worst destructive:
        #
        #   • force="mesh" concatenates a multi-part asset down to ONE material
        #     (measured: 2 materials → 1), which is how TRELLIS.2 output loses
        #     its per-part material separation.
        #   • fill_holes ADDS geometry to open surfaces (measured: 14 → 18 faces
        #     on an open shell, still not watertight). TRELLIS.2's O-Voxel
        #     representation supports open surfaces deliberately, so this is not
        #     a repair — it is a different model of the object.
        #   • merge_vertices / fix_normals alter topology underneath UVs that
        #     were baked against the original vertex set.
        #
        # On an already-clean single-part mesh these mostly do nothing, which is
        # why the damage went unnoticed: it only shows up on the multi-part,
        # open-surface assets that are precisely TRELLIS.2's selling point.
        channels = textured_channels(ctx.get("mesh_path"))
        if channels and settings.mesh_repair_preserve_textured:
            ctx.set("watertight", None)
            ctx.set("texture_channels", channels)
            raise StageSkipped(
                f"mesh carries PBR textures ({', '.join(channels)}) and is "
                f"already model-post-processed — repair would flatten it. "
                f"Set MESH_REPAIR_PRESERVE_TEXTURED=0 to force.")

        m = _load_mesh(ctx.get("mesh_path"))
        before = (len(m.vertices), len(m.faces))
        for op in ("merge_vertices", "remove_duplicate_faces", "remove_degenerate_faces",
                   "remove_infinite_values", "remove_unreferenced_vertices"):
            try:
                getattr(m, op)()
            except Exception:
                pass
        try:
            m.fill_holes()
        except Exception:
            pass
        try:
            m.fix_normals()
        except Exception:
            pass
        out = ctx.artifacts(self.name) / "repaired.glb"
        m.export(out)
        ctx.set("mesh_path", str(out))
        ctx.set("watertight", bool(getattr(m, "is_watertight", False)))
        return (f"verts {before[0]}→{len(m.vertices)}, faces {before[1]}→{len(m.faces)}, "
                f"watertight={ctx.get('watertight')}")


class TopologyLODStage(Stage):
    name = "topology_lod"

    def run(self, ctx: Ctx) -> str:
        try:
            import trimesh  # noqa
        except Exception:
            raise StageSkipped("trimesh not installed")
        src = ctx.get("mesh_path")
        m = _load_mesh(src)
        adir = ctx.artifacts(self.name)
        lods = {}
        # LOD0 = full. Copied byte-for-byte rather than re-exported from the
        # flattened load: LOD0 is meant to BE the source asset, and round-tripping
        # it through force="mesh" would strip exactly the PBR set that makes it
        # the highest-quality rung. LOD1-3 are decimated by definition, so the
        # flattened mesh is an acceptable input for those.
        lod0 = adir / "lod0.glb"
        shutil.copy(src, lod0)
        lods["lod0"] = ctx.rel(lod0)
        ratios = {"lod1": 0.5, "lod2": 0.2, "lod3": 0.05}
        faces = len(m.faces)
        for name, ratio in ratios.items():
            target = max(50, int(faces * ratio))
            simplified = None
            try:
                simplified = m.simplify_quadric_decimation(target)
            except Exception:
                simplified = None
            p = adir / f"{name}.glb"
            (simplified or m).export(p)
            lods[name] = ctx.rel(p)
        ctx.set("lods", lods)
        note = "LOD0–3 written"
        if faces and "lod1" in lods:
            note += f" (base {faces} faces)"
        return note


class MeshValidateStage(Stage):
    name = "mesh_validate"

    def run(self, ctx: Ctx) -> str:
        try:
            import trimesh  # noqa
        except Exception:
            raise StageSkipped("trimesh not installed")
        m = _load_mesh(ctx.get("mesh_path"))
        checks = {
            "watertight": bool(getattr(m, "is_watertight", False)),
            "winding_consistent": bool(getattr(m, "is_winding_consistent", False)),
            "vertices": int(len(m.vertices)),
            "faces": int(len(m.faces)),
            "degenerate_faces": int((m.area_faces <= 0).sum()) if m.area_faces is not None else None,
            "euler_number": int(getattr(m, "euler_number", 0)),
            "bounds": m.bounds.tolist() if m.bounds is not None else None,
        }
        # crude 0–100 quality score
        score = 100
        if not checks["watertight"]:
            score -= 30
        if not checks["winding_consistent"]:
            score -= 15
        if checks["faces"] < 200:
            score -= 20
        checks["quality_score"] = max(0, score)
        ctx.set("mesh_quality", checks)
        (ctx.artifacts(self.name) / "validation.json").write_text(
            json.dumps(checks, indent=2), encoding="utf-8")
        return f"quality={checks['quality_score']} · faces={checks['faces']} · watertight={checks['watertight']}"
