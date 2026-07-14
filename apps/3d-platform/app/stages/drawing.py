"""Drawing route — floor plans / CAD / blueprints / PDFs.

These are NOT object photos, so they bypass TRELLIS entirely and go to the
existing 2d-to-3d parser, which turns a plan into an `nxr-scene/1` scene graph
(rooms, walls, openings, furnished props). We reuse that backend either over HTTP
(if TWO_D_TO_3D_URL is set) or by importing it in-process from the sibling app.

Output artifact: scene.json — rendered by the 2d-to-3d React viewer. Converting
the scene graph to a single GLB is a separate (documented) follow-up.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import requests

from ..config import settings
from .base import Ctx, Stage, StageSkipped

_SIBLING_BACKEND = (Path(__file__).resolve().parents[3] / "2d-to-3d" / "backend")


def _data_url(path: Path) -> str:
    raw = path.read_bytes()
    ext = path.suffix.lower().lstrip(".") or "png"
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "pdf": "pdf"}.get(ext, "png")
    return f"data:image/{mime};base64," + base64.b64encode(raw).decode()


class DrawingStage(Stage):
    name = "drawing_parse"

    def run(self, ctx: Ctx) -> str:
        src = Path(ctx.get("input_path"))
        fields = ctx.get("fields", {}) or {}
        facility = fields.get("facility")
        floors = int(fields.get("floors", 1) or 1)
        adir = ctx.artifacts(self.name)

        scene = None
        if settings.two_d_to_3d_url:
            scene = self._http(src, facility, floors)
            note = "parsed via 2d-to-3d HTTP service"
        else:
            scene = self._in_process(src, facility, floors)
            note = "parsed via in-process 2d-to-3d parser"

        (adir / "scene.json").write_text(json.dumps(scene, indent=2), encoding="utf-8")
        ctx.set("scene", {"nodes": len(scene.get("nodes", [])), "facility": scene.get("facility")})
        ctx.set("scene_path", ctx.rel(adir / "scene.json"))
        ctx.set("result_glb", None)  # drawings render in the 2d-to-3d viewer, not a GLB (yet)
        n = len(scene.get("nodes", []))
        return f"{note} · {n} nodes · facility={scene.get('facility')}"

    def _http(self, src: Path, facility, floors) -> dict:
        base = settings.two_d_to_3d_url.rstrip("/")
        r = requests.post(f"{base}/api/parse", timeout=180, json={
            "data": _data_url(src), "filename": src.name,
            "facility": facility, "floors": floors})
        r.raise_for_status()
        return r.json()["scene"]

    def _in_process(self, src: Path, facility, floors) -> dict:
        if not _SIBLING_BACKEND.exists():
            raise StageSkipped(f"2d-to-3d backend not found at {_SIBLING_BACKEND}")
        if str(_SIBLING_BACKEND) not in sys.path:
            sys.path.insert(0, str(_SIBLING_BACKEND))
        try:
            import plan_parser  # type: ignore
        except Exception as e:
            raise StageSkipped(f"could not import 2d-to-3d parser: {e}")
        return plan_parser.parse_plan(_data_url(src), src.name, facility, floors)
