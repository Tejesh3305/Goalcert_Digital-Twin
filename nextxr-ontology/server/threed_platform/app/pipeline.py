"""pipeline.py — the ordered stage lists per route.

RouterStage + ValidateStage are shared; then we branch on route. Each entry is a
Stage instance; the orchestrator just iterates. Adding/removing a stage = editing
one list here.

TWO OBJECT PROFILES: "faithful" AND "legacy", selected by PIPELINE_PROFILE
--------------------------------------------------------------------------
The default is "auto", which follows the deployed worker — legacy while the live
endpoint runs TRELLIS 1, faithful once TRELLIS_VARIANT=2 says a v2 worker is
answering. The faithful chain is written against upstream TRELLIS.2's
preprocessing; until that worker exists, the working v1 path stays exactly as it
is. See Settings.pipeline_profile.

OBJECT_FAITHFUL is the upstream TRELLIS.2 pipeline — `example.py` — expressed in
our stage system. Upstream's whole image→3D path is four steps:

    image → pipeline.run(image)          # their preprocessing, inside the model
          → mesh.simplify(16777216)      # nvdiffrast clamp
          → o_voxel.postprocess.to_glb() # their decimation, remesh, UV, PBR bake
          → glb.export(extension_webp=True)

Everything there happens on the GPU worker. There is no separate segmentation
stage, no image enhancement, no mesh repair, no LOD pass, no UV re-texturing —
not because upstream forgot them, but because the model was trained against its
own preprocessing and its own post-processing, and each of those is a step the
authors tuned to hold accuracy.

OBJECT_LEGACY is the 13-stage spine we designed ourselves. It wrapped the model
in five stages that mutate pixels or geometry, and measurably degraded the
result:

  • SegmentStage ran OUR rembg (u2net, or a centre-oval fallback when rembg is
    missing) and handed the worker a pre-matted RGBA. Upstream's
    `preprocess_image` begins with "if the input already has a real alpha
    channel, use it and skip matting" — so supplying our own cutout SILENTLY
    DISABLES TRELLIS.2's matting model. We were replacing the authors' matte
    with a weaker one and the model never got a say.
  • EnhanceStage cropped to a rectangular bbox with 12px padding, then applied
    SMOOTH + autocontrast + UnsharpMask. Upstream crops a SQUARE centred on the
    object and applies no pixel filtering at all. Sharpening halos and a
    stretched histogram are not what the image conditioner was trained on, and
    the framing difference alone moves the object off the distribution.
  • MeshRepair / TopologyLOD / UVTexture re-processed a mesh that to_glb had
    already remeshed, decimated and UV-unwrapped.

So the faithful profile does not "skip" our stages as a shortcut — removing them
IS the replication. Set PIPELINE_PROFILE=legacy to get the old chain back.
"""
from __future__ import annotations

from .config import settings
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

# Object photo → TRELLIS.2, upstream-equivalent. Active once a v2 worker is
# deployed (or with PIPELINE_PROFILE=faithful to A/B it against v1).
#
# Only read-only stages surround the model. Understand records what the photo
# shows, MeshValidate scores the result, Export/Package ship it — none of them
# touch the pixels going in or the geometry coming out. The image the worker
# receives is the ORIGINAL upload (see ReconstructStage), so upstream's
# `preprocess_image` performs 100% of the matting, square-cropping and alpha
# premultiplication, exactly as it does in their example.py.
OBJECT_FAITHFUL: list[Stage] = [
    UnderstandStage(),      # read-only: records image understanding
    ReconstructStage(),     # TRELLIS.2 — preprocess + generate + to_glb + export
    MeshValidateStage(),    # read-only: quality score for the job record
    ExportStage(),          # obj/stl/ply siblings; GLB is copied byte-for-byte
    PackageStage(),         # digital-twin packaging
]

# The 13-stage spine we built before adopting upstream's. THIS IS WHAT RUNS
# TODAY against the live TRELLIS 1 endpoint — it works, and it stays the default
# until a v2 worker is deployed. Also the right chain for genuine scan geometry,
# which does need repair. See the module docstring for why each extra stage costs
# accuracy on generated assets.
OBJECT_LEGACY: list[Stage] = [
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


def object_stages() -> list[Stage]:
    return OBJECT_LEGACY if settings.pipeline_profile == "legacy" else OBJECT_FAITHFUL


def for_route(route: str) -> list[Stage]:
    return DRAWING if route == "drawing" else object_stages()


# Back-compat alias for anything importing the old name. Resolved at import time,
# so PIPELINE_PROFILE is read once at startup like every other setting.
OBJECT: list[Stage] = object_stages()
