"""Stage 8 — Geometry Prior Retrieval.

Embeds the clean input and searches the curated prior library for visually
similar known assets. The match is handed to reconstruction:

  • similarity ≥ PRIOR_RETRIEVE_THRESHOLD and the match has a canonical mesh
    → reconstruction *reuses that mesh* (deterministic, maximally consistent for
      catalogued datacenter / hospital equipment).
  • similarity ≥ PRIOR_HINT_THRESHOLD
    → the asset label is passed to TRELLIS as a conditioning hint to stabilise
      generation of near-but-not-identical items.

Empty library ⇒ clean skip (the platform still reconstructs from the image).
"""
from __future__ import annotations

import json

from ..config import settings
from ..priors.library import library
from .base import Ctx, Stage


class GeometryPriorStage(Stage):
    name = "geometry_prior"
    optional = True

    def run(self, ctx: Ctx) -> str:
        if not settings.prior_enabled:
            return "disabled"
        img = ctx.get("recon_input_image") or ctx.get("working_image") or ctx.get("input_path")
        results = library.search(img, k=5)
        (ctx.artifacts(self.name) / "matches.json").write_text(
            json.dumps(results, indent=2), encoding="utf-8")
        if not results:
            return "no prior (library empty)"

        best = results[0]
        sim = best["similarity"]
        mode = "none"
        if sim >= settings.prior_retrieve_threshold and best.get("mesh_path"):
            mode = "retrieve"
        elif sim >= settings.prior_hint_threshold:
            mode = "hint"
        prior = {
            "asset_id": best["id"], "asset_type": best["asset_type"],
            "similarity": sim, "mode": mode, "mesh_path": best.get("mesh_path"),
            "top": [{"asset_type": r["asset_type"], "similarity": r["similarity"]} for r in results[:3]],
        }
        ctx.set("geometry_prior", prior)
        return f"match={best['asset_type']} sim={sim} → {mode}"
