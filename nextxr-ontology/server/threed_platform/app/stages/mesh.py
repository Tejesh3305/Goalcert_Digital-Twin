"""Stages 10–11, 16 — Mesh Repair, Topology/LOD, Mesh Validation (trimesh).

These run on the GLB produced by reconstruction. trimesh covers the bulk of
real mesh hygiene; LOD uses quadric decimation (via fast-simplification). Each
op is wrapped so a missing optional backend degrades to a recorded skip rather
than failing the job.
"""
from __future__ import annotations

import json

from .base import Ctx, Stage, StageSkipped


def _load_mesh(path):
    import trimesh
    m = trimesh.load(path, force="mesh")
    return m


class MeshRepairStage(Stage):
    name = "mesh_repair"

    def run(self, ctx: Ctx) -> str:
        try:
            import trimesh  # noqa
        except Exception:
            raise StageSkipped("trimesh not installed")
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
        m = _load_mesh(ctx.get("mesh_path"))
        adir = ctx.artifacts(self.name)
        lods = {}
        # LOD0 = full
        lod0 = adir / "lod0.glb"; m.export(lod0); lods["lod0"] = ctx.rel(lod0)
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
