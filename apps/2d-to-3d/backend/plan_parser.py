"""
plan_parser.py — the standalone 2-D → 3-D parser.

Self-contained: an uploaded plan image → a renderable `nxr-scene/1` scene graph.
No Neo4j, no ontology, no agent graph — just vision parse → geometry → scene.

  parse_plan(dataURL) -> scene
    gateway.complete_json_vision (Claude if ANTHROPIC_API_KEY, else gpt-4o)
      -> bim_support.normalize_bim_model  (real room polygons + printed sizes)
      -> bim_support.enrich_domain        (furnish by room type + fit-out)
      -> bim_support.bim_model_to_scene   (floors/walls/windows/roof/ground/props)
"""

from __future__ import annotations

import gateway
import bim_support as bs

PLAN_PARSER_SYSTEM = (
    "You are an architectural plan parser. From a 2-D floor-plan image, "
    "reconstruct the building geometry FAITHFULLY as JSON — match the plan's "
    "actual room arrangement, proportions, and outline.\n\n"
    "Coordinates are in METRES, origin at the building's top-left corner, x to the "
    "right, y downwards in the plan. IMPORTANT: many plans PRINT each room's size "
    "next to its label (e.g. 'WARD 16.88X7.26' means 16.88 m wide x 7.26 m deep, "
    "'TOIL. 2.40X1.20' means 2.4 x 1.2 m) — READ those numbers and use them as the "
    "room's bbox w/l. Lay rooms out so they tile the building without overlapping. "
    "Read EVERY labelled room (there may be 40+). Infer building.facility from the "
    "drawing (hospital, datacenter, residential, office, factory). Return EXACTLY "
    "this shape:\n"
    '{"building":{"name":str,"facility":str,"widthM":num,"lengthM":num,"floors":int},'
    '"levels":[{"index":int,"elevationM":num,"heightM":num}],'
    '"rooms":[{"id":str,"level":int,"name":str,"type":str,'
    '"bbox":{"x":num,"y":num,"w":num,"l":num},'
    '"footprint":[[x,y],...],"areaM2":num}],'
    '"walls":[{"level":int,"start":[x,y],"end":[x,y],"heightM":num,"thicknessM":num}],'
    '"openings":[{"type":"door"|"window","level":int,"at":[x,y],"widthM":num}],'
    '"equipment":[{"id":str,"label":str,"assetType":str,"room":str,"x":num,"y":num,"level":int}]}\n'
    "room.type in bedroom, master_bedroom, living, dining, kitchen, bathroom, "
    "garage, porch, office, balcony, utility, closet, corridor, ward, theatre, "
    "icu, consultation, emergency. Give bbox for every room; give footprint ONLY "
    "for non-rectangular rooms. Put a door/window opening wherever the plan shows "
    "one (an arc = a door). Use ONE level (index 0) unless the plan clearly shows "
    "multiple. Output only the JSON."
)


def parse_plan(image_data_url: str, filename: str = "plan.png",
               facility: str | None = None, floors: int = 1) -> dict:
    """Parse one plan image (data URL) into an nxr-scene/1 scene graph."""
    gw = gateway.get_gateway()
    facility = facility or bs.infer_facility(filename)
    result = gw.complete_json_vision(
        tenant_id="local", session_id="local",
        system=PLAN_PARSER_SYSTEM,
        user_text=(f"Facility hint: {facility}. Floors hint: {floors}. Parse this "
                   f"floor plan into the bim_model JSON. Capture every labelled "
                   f"room with its printed dimensions; do not omit rooms."),
        image_urls=[image_data_url],
        stub={"_synth": True},
        max_tokens=12000,
    )
    bm = bs.normalize_bim_model(result, facility, floors)
    bs.enrich_domain(bm, bm.get("facility") or facility)   # furnish + fit-out (visual)
    scene = bs.bim_model_to_scene(bm, id_map={})
    scene["facility"] = bm.get("facility")
    scene["synthesized"] = bool(bm.get("synthesized"))
    scene["parse_note"] = (gw.last_vision_error if bm.get("synthesized") else None)
    scene["vision_backend"] = gw.last_vision_backend
    return scene


def sample_scene(facility: str = "residential", floors: int = 1) -> dict:
    """A no-LLM sample twin for a facility — lets the app render with no key."""
    bm = bs.synthesize_bim_model(facility, floors)
    bs.enrich_domain(bm, facility)
    scene = bs.bim_model_to_scene(bm, id_map={})
    scene["facility"] = facility
    scene["synthesized"] = True
    return scene
