"""Stage 1/2 — Router + Input Validation.

RouterStage classifies the input and sets ctx.state['route']. ValidateStage runs
the image-quality assessment (Stage 2) and rejects/flags unsuitable inputs before
any GPU time is spent.
"""
from __future__ import annotations

import json

from ..router import classify
from ..util.images import quality_report
from .base import Ctx, Stage


class RouterStage(Stage):
    name = "route"

    def run(self, ctx: Ctx) -> str:
        res = classify(ctx.get("input_path"), ctx.get("fields", {}))
        ctx.set("route", res["route"])
        ctx.set("route_info", res)
        out = ctx.artifacts(self.name) / "route.json"
        out.write_text(json.dumps(res, indent=2), encoding="utf-8")
        return f"route={res['route']} ({res['reason']})"


class ValidateStage(Stage):
    name = "validate"

    def run(self, ctx: Ctx) -> str:
        if ctx.get("route") == "drawing":
            # Drawings are judged on legibility, not photo sharpness — skip photo QA.
            ctx.set("quality", {"suitable": True, "note": "drawing route — photo QA skipped"})
            return "drawing route: photo QA skipped"
        rep = quality_report(ctx.get("input_path"))
        ctx.set("quality", rep)
        out = ctx.artifacts(self.name) / "quality.json"
        out.write_text(json.dumps(rep, indent=2), encoding="utf-8")
        if not rep["suitable"]:
            # Warn but don't hard-fail in v1 — surface it and continue.
            return f"⚠ low quality: {', '.join(rep['warnings']) or 'unsuitable'} (continuing)"
        return f"{rep['resolution']} · blur={rep['blur_score']} · exposure={rep['exposure']} · OK"
