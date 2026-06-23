"""
ifc_parser.py — IFC fast-follow (optional, stub-safe).

True-BIM path: parse a `.ifc` model into the SAME `bim_model` schema the image
Plan Parser emits, so everything downstream (Schema Mapper → ontology → Scene
Generator → viewer) is unchanged. IfcOpenShell is an OPTIONAL dependency — when
it (or the file) is unavailable, parse_ifc returns None and the caller falls
back to synthesis.

This is intentionally a thin first cut: it extracts storeys, spaces (rooms),
and a few common equipment types with their plan placement. Highest fidelity is
deferred; the contract (the bim_model shape) is what matters here.
"""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path


def _materialise(file_rec: dict) -> str | None:
    """Write an uploaded {url|data, filename} record to a temp .ifc path."""
    url = file_rec.get("url") or file_rec.get("data") or ""
    if url.startswith("data:"):
        try:
            b64 = url.split(",", 1)[1]
            raw = base64.b64decode(b64)
        except Exception:
            return None
        tmp = Path(tempfile.mkstemp(suffix=".ifc")[1])
        tmp.write_bytes(raw)
        return str(tmp)
    if url and Path(url).exists():
        return url
    return None


def parse_ifc(file_rec: dict) -> dict | None:
    """Return a bim_model from an IFC file, or None if unavailable."""
    try:
        import ifcopenshell  # optional dependency
        import ifcopenshell.util.placement as placement  # noqa: F401
    except Exception:
        return None

    path = _materialise(file_rec)
    if not path:
        return None

    try:
        model = ifcopenshell.open(path)
    except Exception:
        return None

    # Storeys → levels
    storeys = sorted(model.by_type("IfcBuildingStorey"),
                     key=lambda s: getattr(s, "Elevation", 0) or 0)
    level_index = {s.id(): i for i, s in enumerate(storeys)}
    levels = []
    for i, s in enumerate(storeys):
        elev = (getattr(s, "Elevation", 0) or 0) / 1000.0  # mm → m (common)
        levels.append({"index": i, "elevationM": round(elev, 3), "heightM": 3.5})
    if not levels:
        levels = [{"index": 0, "elevationM": 0, "heightM": 3.5}]

    rooms, maxx, maxy = [], 0.0, 0.0
    for sp in model.by_type("IfcSpace"):
        try:
            x, y, lvl = _placement_xy(sp, level_index)
        except Exception:
            x, y, lvl = 0.0, 0.0, 0
        w, l = 6.0, 6.0  # bbox extraction omitted in this first cut
        maxx, maxy = max(maxx, x + w), max(maxy, y + l)
        rooms.append({
            "id": f"sp{sp.id()}", "level": lvl,
            "name": getattr(sp, "LongName", None) or getattr(sp, "Name", None) or f"Space {sp.id()}",
            "function": "room",
            "bbox": {"x": x, "y": y, "w": w, "l": l}, "areaM2": round(w * l, 1),
        })

    if not rooms:
        return None  # nothing usable — let the caller synthesize

    equipment = []
    equip_types = ["IfcUnitaryEquipment", "IfcAirTerminal", "IfcChiller",
                   "IfcPump", "IfcElectricGenerator", "IfcDoor"]
    for t in equip_types:
        for e in model.by_type(t):
            try:
                x, y, lvl = _placement_xy(e, level_index)
            except Exception:
                continue
            equipment.append({
                "id": f"e{e.id()}", "label": getattr(e, "Name", None) or t,
                "assetType": t.replace("Ifc", "").lower(),
                "level": lvl, "x": round(x, 2), "y": round(y, 2),
            })

    W = round(maxx + 6, 1) or 40.0
    L = round(maxy + 6, 1) or 30.0
    return {
        "building": {"name": "IFC Building", "widthM": W, "lengthM": L,
                     "floors": len(levels)},
        "levels": levels, "rooms": rooms, "walls": [], "openings": [],
        "equipment": equipment, "synthesized": False,
    }


def _placement_xy(product, level_index: dict):
    """Best-effort plan (x, y) in metres + level index for an IFC product."""
    import ifcopenshell.util.placement as placement
    m = placement.get_local_placement(product.ObjectPlacement)
    x, y = float(m[0][3]) / 1000.0, float(m[1][3]) / 1000.0
    lvl = 0
    container = getattr(product, "ContainedInStructure", None)
    if container:
        for rel in container:
            st = getattr(rel, "RelatingStructure", None)
            if st is not None and st.id() in level_index:
                lvl = level_index[st.id()]
                break
    return x, y, lvl
