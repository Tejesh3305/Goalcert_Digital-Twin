"""pipeline.py — the ordered stage lists per route.

This is the whole 19-stage spine, made explicit and reorderable. RouterStage +
ValidateStage are shared; then we branch on route. Each entry is a Stage
instance; the orchestrator just iterates. Adding/removing a stage = editing one
list here.
"""
from __future__ import annotations

from .stages.base import Stage
from .stages.deliver import ExportStage, PackageStage
from .stages.drawing import DrawingStage
from .stages.enhance import EnhanceStage
from .stages.mesh import MeshRepairStage, MeshValidateStage, TopologyLODStage
from .stages.prior import GeometryPriorStage
from .stages.reconstruct import ReconstructStage
from .stages.refine import MaterialStage, SemanticStage, UVTextureStage
from .stages.segment import SegmentStage
from .stages.understand import UnderstandStage
from .stages.validate import RouterStage, ValidateStage

# Common front door.
COMMON: list[Stage] = [RouterStage(), ValidateStage()]

# Object photo → TRELLIS object reconstruction.
OBJECT: list[Stage] = [
    UnderstandStage(),      # 3  image understanding
    SegmentStage(),         # 4  background removal
    EnhanceStage(),         # 5  enhancement
    GeometryPriorStage(),   # 8  geometry prior retrieval (consistency)
    ReconstructStage(),     # 9  TRELLIS (core)
    MeshRepairStage(),      # 10 mesh repair
    TopologyLODStage(),     # 11/17 topology + LOD
    UVTextureStage(),       # 12/13 UV + texture
    MaterialStage(),        # 14 material (deferred)
    SemanticStage(),        # 15 semantic parts (deferred)
    MeshValidateStage(),    # 16 mesh validation
    ExportStage(),          # 19 export
    PackageStage(),         # 18 digital-twin packaging
]

# Technical drawing → 2d-to-3d scene parser.
DRAWING: list[Stage] = [
    DrawingStage(),
    PackageStage(),
]


def for_route(route: str) -> list[Stage]:
    return DRAWING if route == "drawing" else OBJECT
