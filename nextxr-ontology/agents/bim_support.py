"""
bim_support.py — the geometry brain behind the BIM vertical.

Pure functions, no I/O, easy to unit-test. Three jobs:

  1. classify_asset()        — map a parsed equipment label/hint to a real CFP
                               canonical type + a viewer prop key.
  2. synthesize_bim_model()  — fabricate a plausible building (rooms, walls,
                               equipment) when a plan can't be parsed (keyless
                               / blurry upload) so the demo always renders.
  3. bim_model_to_drafts()   — turn a `bim_model` into ontology draft entities +
                               relationships for the Schema Mapper.
  4. bim_model_to_scene()    — turn a `bim_model` into an `nxr-scene/1` scene
                               graph for the 3-D viewer.

Coordinate convention (the `bim_model`): metres, origin at the building's
min corner, x/y in the floor plane, z up. The scene graph re-centres the
building on the world origin (x → x - W/2, z → y - L/2) so the viewer frames
it like the demo's archviz scenes.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

CFP = "https://ontology.nextxr.io/v3/cfp#"
BIM = "https://ontology.nextxr.io/v3/bim#"
HSP = "https://ontology.nextxr.io/v3/hospital#"
DC = "https://ontology.nextxr.io/v3/datacenter#"


# ── scene cache (so any twin re-renders fully — incl. furniture — per tenant) ──
def _scene_dir() -> Path:
    d = Path(__file__).resolve().parent.parent / "data" / "scenes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe(tenant: str) -> str:
    return "".join(c for c in str(tenant) if c.isalnum() or c in "-_") or "twin"


def save_scene_cache(tenant: str, scene: dict) -> None:
    try:
        (_scene_dir() / f"{_safe(tenant)}.json").write_text(
            json.dumps(scene), encoding="utf-8")
    except Exception:
        pass


def load_scene_cache(tenant: str) -> dict | None:
    try:
        p = _scene_dir() / f"{_safe(tenant)}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None

# ── Equipment classification ────────────────────────────────────────────
# (keyword substring) → (canonical CFP class IRI, viewer prop key)
# Order matters: more specific keywords first. Every class below is a
# PhysicalAsset subtype covered by the base shape only (NOT nxr:Sensor, which
# would require monitors/observes), so they commit without extra relationships.
# Each rule: (keyword, CFP class | None, viewer prop key, is_decor).
#   - asset → a non-Sensor PhysicalAsset CFP class (commits to the graph)
#   - decor → class None, is_decor True (furniture / fit-out: RENDERED only,
#             never committed as a monitored asset)
# Order matters: most specific keywords first; the collision group at the top
# disambiguates words that are substrings of others (e.g. "operating table"
# before "table", "forklift" before "lift", "fire panel" before "panel").
_ITEM_RULES: list[tuple[str, str | None, str, bool]] = [
    # — collision disambiguation (must precede the generic keywords) —
    ("coffee table", None, "coffeetable", True),
    ("operating table", "FacilityEquipment", "operatingtable", False),
    ("patient monitor", "FacilityEquipment", "patientmonitor", False),
    ("water cooler", None, "watercooler", True),
    ("water dispenser", None, "watercooler", True),
    ("hospital bed", "FacilityEquipment", "hospitalbed", False),
    ("patient bed", "FacilityEquipment", "hospitalbed", False),
    ("fire panel", "FireAlarmPanel", "firepanel", False),
    ("fire alarm", "FireAlarmPanel", "firepanel", False),
    ("water tank", "WaterTank", "watertank", False),
    ("cooling tower", "CoolingTower", "coolingtower", False),
    ("forklift", "FacilityEquipment", "forklift", False),
    ("pallet", None, "palletrack", True),
    ("exit sign", None, "exitsign", True),
    # — IT / data center —
    ("rack", "Server", "rack", False),
    ("server", "Server", "rack", False),
    ("compute", "Server", "rack", False),
    ("blade", "Server", "rack", False),
    ("pdu", "PowerPanel", "pdu", False),
    ("switchgear", "Switchgear", "switchgear", False),  # before "switch"
    ("network", "NetworkSwitch", "network", False),
    ("switch", "NetworkSwitch", "network", False),
    ("router", "NetworkSwitch", "network", False),
    # — cooling / HVAC —
    ("crac", "PrecisionCooler", "crac", False),
    ("crah", "PrecisionCooler", "crac", False),
    ("precision", "PrecisionCooler", "crac", False),
    ("computer room", "PrecisionCooler", "crac", False),
    ("chiller", "Chiller", "chiller", False),
    ("cooling", "PrecisionCooler", "crac", False),
    ("air hand", "AirHandlingUnit", "ahu", False),
    ("air-hand", "AirHandlingUnit", "ahu", False),
    ("handler", "AirHandlingUnit", "ahu", False),
    ("ahu", "AirHandlingUnit", "ahu", False),
    ("boiler", "Boiler", "boiler", False),
    ("vav", "VAVBox", "vav", False),
    ("damper", "VAVBox", "vav", False),
    # — power —
    ("ups", "UPS", "ups", False),
    ("battery", "BatteryStorage", "battery", False),
    ("transformer", "Transformer", "transformer", False),
    ("generator", "Generator", "generator", False),
    ("genset", "Generator", "generator", False),
    ("solar", "SolarArray", "solar", False),
    ("ev charg", "FacilityEquipment", "evcharger", False),
    ("charger", "FacilityEquipment", "evcharger", False),
    ("panel", "PowerPanel", "switchgear", False),
    # NB: cfp:EnergyMeter is a nxr:Sensor (needs monitors/observes) → PowerPanel.
    ("meter", "PowerPanel", "meter", False),
    ("energy", "PowerPanel", "meter", False),
    # — water / mechanical —
    ("pump", "Pump", "pump", False),
    ("valve", "Valve", "valve", False),
    ("compressor", "FacilityEquipment", "compressor", False),
    ("tank", "FacilityEquipment", "tank", False),
    # — vertical transport / access —
    ("elevator", "Elevator", "elevator", False),
    ("lift", "Elevator", "elevator", False),
    ("escalator", "Escalator", "escalator", False),
    ("door", "AccessDoor", "door", False),
    ("access", "AccessDoor", "door", False),
    # — fire / safety / security —
    ("extinguisher", "Extinguisher", "extinguisher", False),
    ("sprinkler", "SuppressionSystem", "sprinkler", False),
    ("suppression", "SuppressionSystem", "sprinkler", False),
    ("smoke", None, "smoke", True),   # detectors are Sensors → render only
    ("camera", "VideoRecorder", "camera", False),
    ("cctv", "VideoRecorder", "camera", False),
    # — lighting —
    ("light", "FacilityEquipment", "light", False),
    ("lumin", "FacilityEquipment", "light", False),
    # — hospital —
    ("mri", "FacilityEquipment", "mri", False),
    ("ct scan", "FacilityEquipment", "ctscanner", False),
    ("ct-scan", "FacilityEquipment", "ctscanner", False),
    ("imaging", "FacilityEquipment", "mri", False),
    ("scanner", "FacilityEquipment", "ctscanner", False),
    ("x-ray", "FacilityEquipment", "xray", False),
    ("xray", "FacilityEquipment", "xray", False),
    ("gas", "FacilityEquipment", "gas", False),
    ("laminar", "FacilityEquipment", "laf", False),
    ("clean air", "FacilityEquipment", "laf", False),
    ("nurse", "FacilityEquipment", "nurse", False),
    ("fridge", "FacilityEquipment", "fridge", False),
    ("cold", "FacilityEquipment", "fridge", False),
    ("freezer", "FacilityEquipment", "fridge", False),
    ("stretcher", None, "stretcher", True),
    ("gurney", None, "stretcher", True),
    ("wheelchair", None, "wheelchair", True),
    ("iv stand", None, "ivstand", True),
    ("iv pole", None, "ivstand", True),
    # — factory / logistics —
    ("robot", "FacilityEquipment", "robot", False),
    ("conveyor", "FacilityEquipment", "conveyor", False),
    ("cnc", "FacilityEquipment", "cnc", False),
    ("mill", "FacilityEquipment", "cnc", False),
    ("lathe", "FacilityEquipment", "cnc", False),
    ("machin", "FacilityEquipment", "cnc", False),
    ("weld", "FacilityEquipment", "welder", False),
    ("press", "FacilityEquipment", "press", False),
    ("stamp", "FacilityEquipment", "press", False),
    ("workbench", None, "workbench", True),
    # — office / furniture / fit-out (decor) —
    ("reception", None, "reception", True),
    ("desk", None, "desk", True),
    ("office chair", None, "chair", True),
    ("chair", None, "chair", True),
    ("stool", None, "stool", True),
    ("sofa", None, "sofa", True),
    ("couch", None, "sofa", True),
    ("bookshelf", None, "bookshelf", True),
    ("bookcase", None, "bookshelf", True),
    ("shelf", None, "bookshelf", True),
    ("cabinet", None, "cabinet", True),
    ("wardrobe", None, "cabinet", True),
    ("locker", None, "locker", True),
    ("whiteboard", None, "whiteboard", True),
    ("plant", None, "plant", True),
    ("printer", None, "printer", True),
    ("copier", None, "printer", True),
    ("television", None, "tv", True),
    ("display", None, "tv", True),
    ("tv", None, "tv", True),
    ("monitor", None, "monitor", True),
    ("table", None, "table", True),
    ("bed", None, "bed", True),
    # — generic sensor (avoid the cfp:Sensor classes) —
    ("sensor", "FacilityEquipment", "sensor", False),
]


def classify_item(label: str = "", hint: str = "") -> tuple[str | None, str, bool]:
    """Return (canonical_type | None, prop key, is_decor) for a parsed item.
    canonical_type is None for decor (render-only fit-out)."""
    text = f"{label} {hint}".lower()
    for kw, cls, prop, decor in _ITEM_RULES:
        if kw in text:
            return (None if cls is None else CFP + cls), prop, decor
    return CFP + "FacilityEquipment", "box", False  # unknown → generic equipment


def classify_asset(label: str = "", hint: str = "") -> tuple[str, str]:
    """Back-compat: (canonical_type, prop) — decor falls back to generic equipment."""
    ct, prop, _ = classify_item(label, hint)
    return (ct or (CFP + "FacilityEquipment")), prop


# ── conversation inference (for fallback synthesis) ──────────────────────
def infer_facility(text: str = "") -> str:
    t = (text or "").lower()
    if "data cent" in t or "datacenter" in t or "data centre" in t or "server room" in t:
        return "datacenter"
    if "hospital" in t or "clinic" in t or "ward" in t or "patient" in t:
        return "hospital"
    if "factory" in t or "plant" in t or "manufactur" in t or "warehouse" in t or "production" in t:
        return "factory"
    if "office" in t or "workplace" in t or "corporate" in t:
        return "office"
    return "office"


def infer_floors(text: str = "", default: int = 1) -> int:
    import re
    t = (text or "").lower()
    m = re.search(r"(\d+)\s*(?:-|\s)?(?:floor|storey|story|level|stories|floors)", t)
    if m:
        try:
            return max(1, min(int(m.group(1)), 12))
        except ValueError:
            pass
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
             "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
    for w, n in words.items():
        if re.search(rf"\b{w}\b[ -]*(?:floor|storey|story|level)", t):
            return n
    return default


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def normalize_bim_model(raw: dict, facility: str = "office", floors: int = 1) -> dict:
    """Coerce a (possibly partial) LLM bim_model into a complete, valid one.
    Falls back to synthesis when the parse is unusable."""
    if not isinstance(raw, dict) or raw.get("_synth"):
        return synthesize_bim_model(facility, floors)
    rooms_in = raw.get("rooms") or []
    b = raw.get("building") or {}
    if not rooms_in:
        return synthesize_bim_model(facility, floors)

    rooms, maxx, maxy, maxlvl = [], 0.0, 0.0, 0
    for i, r in enumerate(rooms_in):
        bb = r.get("bbox")
        fp = r.get("footprint")
        if (not bb) and isinstance(fp, list) and len(fp) >= 3:
            xs = [_f(p[0]) for p in fp]
            ys = [_f(p[1]) for p in fp]
            bb = {"x": min(xs), "y": min(ys), "w": max(xs) - min(xs), "l": max(ys) - min(ys)}
        if not bb:
            continue
        x, y = _f(bb.get("x")), _f(bb.get("y"))
        w, l = _f(bb.get("w")), _f(bb.get("l"))
        if w <= 0 or l <= 0:
            continue
        lvl = int(r.get("level", 0) or 0)
        maxx, maxy, maxlvl = max(maxx, x + w), max(maxy, y + l), max(maxlvl, lvl)
        rooms.append({
            "id": str(r.get("id") or f"r{i}"), "level": lvl,
            "name": r.get("name") or f"Room {i+1}",
            "function": r.get("function") or "room",
            "footprint": [[x, y], [x + w, y], [x + w, y + l], [x, y + l]],
            "bbox": {"x": x, "y": y, "w": w, "l": l},
            "areaM2": round(w * l, 1),
        })
    if not rooms:
        return synthesize_bim_model(facility, floors)

    W = _f(b.get("widthM")) or round(maxx, 1) or 40.0
    L = _f(b.get("lengthM")) or round(maxy, 1) or 30.0
    n_floors = int(b.get("floors") or 0) or (maxlvl + 1)

    levels_in = raw.get("levels") or []
    levels = []
    fh = _FACILITY_DIMS.get(facility, _DEFAULT_DIMS)[2]
    for idx in range(n_floors):
        lv = next((x for x in levels_in if int(x.get("index", -99)) == idx), None)
        levels.append({
            "index": idx,
            "elevationM": _f(lv.get("elevationM")) if lv else round(idx * (fh + 0.4), 3),
            "heightM": (_f(lv.get("heightM")) or fh) if lv else fh,
        })

    walls = []
    for w in (raw.get("walls") or []):
        s, e = w.get("start"), w.get("end")
        if isinstance(s, list) and isinstance(e, list) and len(s) == 2 and len(e) == 2:
            walls.append({"level": int(w.get("level", 0) or 0),
                          "start": [_f(s[0]), _f(s[1])], "end": [_f(e[0]), _f(e[1])],
                          "heightM": _f(w.get("heightM")) or fh,
                          "thicknessM": _f(w.get("thicknessM")) or 0.2})
    if not walls:  # at least give the building an envelope per level
        for lv in levels:
            walls.extend(_perimeter_walls(lv["index"], W, L, lv["heightM"]))

    equipment = []
    room_by_id = {r["id"]: r for r in rooms}
    for i, eq in enumerate(raw.get("equipment") or []):
        rid = eq.get("room")
        room = room_by_id.get(rid) or (rooms[i % len(rooms)] if rooms else None)
        bb = (room or {}).get("bbox", {})
        x = eq.get("x")
        y = eq.get("y")
        x = _f(x) if x is not None else _f(bb.get("x")) + _f(bb.get("w")) / 2
        y = _f(y) if y is not None else _f(bb.get("y")) + _f(bb.get("l")) / 2
        equipment.append({
            "id": str(eq.get("id") or f"eq{i}"),
            "label": eq.get("label") or "Equipment",
            "assetType": eq.get("assetType") or eq.get("type") or "",
            "room": (room or {}).get("id"), "level": int((room or {}).get("level", 0)),
            "x": round(x, 2), "y": round(y, 2),
            "rotationDeg": _f(eq.get("rotationDeg")),
        })

    return {
        "building": {"name": b.get("name") or f"{facility.title()} Building",
                     "widthM": round(W, 2), "lengthM": round(L, 2), "floors": n_floors},
        "levels": levels, "rooms": rooms, "walls": walls,
        "openings": raw.get("openings") or [], "equipment": equipment,
        "facility": facility, "synthesized": False,
    }


def graph_entities_to_bim_model(locations: list[dict], assets: list[dict]) -> dict | None:
    """Reconstruct a `bim_model` from committed graph entities (their flat
    geometry props), so any BIM twin's scene can be rebuilt per-tenant without a
    live session. Returns None when the twin has no usable building geometry
    (caller should then synthesize from the asset list)."""
    def ct(e):
        return (e.get("canonicalType") or "").split("#")[-1]

    buildings = [l for l in locations if ct(l) == "Building"]
    floors_in = [l for l in locations if ct(l) == "Floor"]
    rooms_in = [l for l in locations if ct(l) in ("Room", "Zone")]
    if not buildings or not rooms_in:
        return None

    geom_rooms = [r for r in rooms_in if r.get("originX") is not None and r.get("widthM")]
    if len(geom_rooms) < max(1, len(rooms_in) // 2):
        return None  # too little geometry survived — let the caller synthesize

    b = buildings[0]
    fh_default = _f(b.get("heightM")) / max(len(floors_in), 1) if b.get("heightM") else 3.6

    # levels
    lvl_indices = sorted({int(_f(r.get("levelIndex"))) for r in geom_rooms}
                         | {int(_f(f.get("levelIndex"))) for f in floors_in})
    if not lvl_indices:
        lvl_indices = [0]
    floor_by_idx = {int(_f(f.get("levelIndex"))): f for f in floors_in}
    levels = []
    for idx in lvl_indices:
        f = floor_by_idx.get(idx)
        levels.append({
            "index": idx,
            "elevationM": _f(f.get("elevationM")) if f else round(idx * (fh_default + 0.4), 3),
            "heightM": (_f(f.get("heightM")) or fh_default) if f else fh_default,
        })

    rooms, walls, maxx, maxy = [], [], 0.0, 0.0
    for r in geom_rooms:
        x, y = _f(r.get("originX")), _f(r.get("originY"))
        w, l = _f(r.get("widthM")), _f(r.get("lengthM"))
        if w <= 0 or l <= 0:
            continue
        lvl = int(_f(r.get("levelIndex")))
        maxx, maxy = max(maxx, x + w), max(maxy, y + l)
        rooms.append({
            "id": r.get("id"), "level": lvl,
            "name": r.get("displayName") or r.get("id"),
            "function": r.get("roomFunction") or "room",
            "footprint": [[x, y], [x + w, y], [x + w, y + l], [x, y + l]],
            "bbox": {"x": x, "y": y, "w": w, "l": l},
            "areaM2": _f(r.get("areaM2")) or round(w * l, 1),
        })
    if not rooms:
        return None

    W = _f(b.get("widthM")) or round(maxx, 1) or 40.0
    L = _f(b.get("lengthM")) or round(maxy, 1) or 30.0
    for lv in levels:
        walls.extend(_perimeter_walls(lv["index"], W, L, lv["heightM"]))

    equipment = []
    for a in assets:
        x, y = a.get("originX"), a.get("originY")
        if x is None or y is None:
            continue
        equipment.append({
            "id": a.get("id"), "label": a.get("displayName") or "Equipment",
            "assetType": a.get("assetKind") or ct(a),
            "level": int(_f(a.get("levelIndex"))),
            "x": _f(x), "y": _f(y), "rotationDeg": _f(a.get("rotationDeg")),
        })

    return {
        "building": {"name": b.get("displayName") or "Building",
                     "widthM": round(W, 2), "lengthM": round(L, 2),
                     "floors": int(_f(b.get("floorCount"))) or len(levels)},
        "levels": levels, "rooms": rooms, "walls": walls,
        "openings": [], "equipment": equipment, "synthesized": False,
    }


def findings_from_bim(bm: dict) -> list[dict]:
    """Coarse vision_findings (label + count) so the Domain Classifier still
    gets equipment hints from a parsed plan."""
    counts: dict[str, int] = {}
    for eq in bm.get("equipment", []):
        label = eq.get("label") or "Equipment"
        counts[label] = counts.get(label, 0) + 1
    return [{"label": k, "count": v, "location": None, "confidence": 0.8, "type": "asset"}
            for k, v in counts.items()]


# ── Facility presets for synthesis ───────────────────────────────────────
_FACILITY_DIMS = {
    "datacenter": (44.0, 34.0, 4.5),
    "office": (40.0, 30.0, 3.6),
    "hospital": (44.0, 32.0, 4.0),
    "factory": (50.0, 38.0, 7.0),
}
_DEFAULT_DIMS = (40.0, 30.0, 3.8)

_ROOM_FUNCTIONS = {
    "datacenter": ["data hall", "ups room", "network room", "cooling plant", "noc"],
    "office": ["open office", "meeting room", "server closet", "reception", "break room"],
    "hospital": ["ward", "imaging", "pharmacy", "nurse station", "theatre"],
    "factory": ["production", "assembly", "warehouse", "qc lab", "utilities"],
}

# Per-floor equipment recipe: (assetType, label) entries.
_FACILITY_EQUIP = {
    "datacenter": [("rack", "Server Rack")] * 6 + [("crac", "CRAC Unit")] * 2
                  + [("ups", "UPS"), ("network", "Core Switch")],
    "office": [("ahu", "Air Handler")] * 2 + [("vav", "VAV Box")] * 3
              + [("light", "Lighting"), ("meter", "Energy Meter"), ("door", "Access Door")],
    "hospital": [("hospitalbed", "Hospital Bed")] * 3 + [("mri", "MRI Scanner"),
                 ("ctscanner", "CT Scanner"), ("gas", "Medical Gas"),
                 ("fridge", "Cold Chain"), ("nurse", "Nurse Call"),
                 ("patientmonitor", "Patient Monitor"), ("ahu", "Air Handler"),
                 ("ups", "UPS"), ("door", "Access Door")],
    "factory": [("robot", "Robot Arm")] * 2 + [("conveyor", "Conveyor"),
                ("cnc", "CNC Machine"), ("welder", "Welder"), ("forklift", "Forklift"),
                ("compressor", "Compressor"), ("meter", "Energy Meter")],
}

# Fit-out / furniture per facility (rendered as decor, never committed as assets).
_FACILITY_FURNITURE = {
    "datacenter": ["Operator Desk", "Office Chair", "Cabinet", "Plant", "Whiteboard"],
    "office": ["Desk", "Office Chair", "Sofa", "Meeting Table", "Plant", "Bookshelf",
               "Cabinet", "Water Cooler", "TV", "Printer", "Reception Desk"],
    "hospital": ["Visitor Chair", "Cabinet", "IV Stand", "Wheelchair", "Plant", "TV", "Stretcher"],
    "factory": ["Workbench", "Cabinet", "Pallet Rack", "Office Chair", "Whiteboard"],
}


def _grid(cols: int, rows: int, W: float, L: float):
    """Yield (r, c, x, y, w, l) cells over a W×L rectangle."""
    cw, cl = W / cols, L / rows
    for r in range(rows):
        for c in range(cols):
            yield r, c, c * cw, r * cl, cw, cl


def _perimeter_walls(level: int, W: float, L: float, h: float, t: float = 0.25):
    return [
        {"level": level, "start": [0, 0], "end": [W, 0], "heightM": h, "thicknessM": t},
        {"level": level, "start": [W, 0], "end": [W, L], "heightM": h, "thicknessM": t},
        {"level": level, "start": [W, L], "end": [0, L], "heightM": h, "thicknessM": t},
        {"level": level, "start": [0, L], "end": [0, 0], "heightM": h, "thicknessM": t},
    ]


def synthesize_bim_model(facility: str = "office", floors: int = 1,
                         equipment_hints: list[dict] | None = None,
                         name: str | None = None) -> dict:
    """Fabricate a plausible building when no plan geometry is available.

    equipment_hints (optional): [{label, assetType?}] from a coarse parse; if
    given, those drive the equipment instead of the facility recipe."""
    facility = (facility or "office").lower()
    floors = max(1, min(int(floors or 1), 12))
    W, L, fh = _FACILITY_DIMS.get(facility, _DEFAULT_DIMS)
    cols = max(2, min(round(W / 12), 4))
    rows = max(2, min(round(L / 12), 3))
    funcs = _ROOM_FUNCTIONS.get(facility, _ROOM_FUNCTIONS["office"])

    levels, rooms, walls = [], [], []
    for f in range(floors):
        elevation = round(f * (fh + 0.4), 3)
        levels.append({"index": f, "elevationM": elevation, "heightM": fh})
        walls.extend(_perimeter_walls(f, W, L, fh))
        # interior partition lines
        cw, cl = W / cols, L / rows
        for c in range(1, cols):
            walls.append({"level": f, "start": [c * cw, 0], "end": [c * cw, L],
                          "heightM": fh, "thicknessM": 0.15})
        for r in range(1, rows):
            walls.append({"level": f, "start": [0, r * cl], "end": [W, r * cl],
                          "heightM": fh, "thicknessM": 0.15})
        for i, (r, c, x, y, w, l) in enumerate(_grid(cols, rows, W, L)):
            rooms.append({
                "id": f"r{f}-{r}-{c}", "level": f,
                "name": f"{funcs[(r * cols + c) % len(funcs)].title()} {f}-{i+1}",
                "function": funcs[(r * cols + c) % len(funcs)],
                "footprint": [[x, y], [x + w, y], [x + w, y + l], [x, y + l]],
                "bbox": {"x": x, "y": y, "w": w, "l": l},
                "areaM2": round(w * l, 1),
            })

    # equipment: spread across rooms of all floors
    recipe = []
    if equipment_hints:
        for h in equipment_hints:
            label = h.get("label") or "Equipment"
            atype = h.get("assetType") or classify_asset(label)[1]
            for _ in range(max(1, int(h.get("count", 1) or 1))):
                recipe.append((atype, label))
    else:
        recipe = list(_FACILITY_EQUIP.get(facility, _FACILITY_EQUIP["office"])) * floors

    equipment = []
    if rooms:
        for i, (atype, label) in enumerate(recipe):
            room = rooms[i % len(rooms)]
            bb = room["bbox"]
            jx = (i % 3 - 1) * bb["w"] * 0.22
            jy = ((i // 3) % 3 - 1) * bb["l"] * 0.22
            equipment.append({
                "id": f"eq{i}", "label": label, "assetType": atype,
                "room": room["id"], "level": room["level"],
                "x": round(bb["x"] + bb["w"] / 2 + jx, 2),
                "y": round(bb["y"] + bb["l"] / 2 + jy, 2),
            })

        # fit-out / furniture (rendered, not committed) — two pieces per room,
        # tucked toward the corners so they don't overlap the equipment.
        if not equipment_hints:
            fpool = _FACILITY_FURNITURE.get(facility, _FACILITY_FURNITURE["office"])
            fi = 0
            for ri, room in enumerate(rooms):
                bb = room["bbox"]
                for k in range(2):
                    label = fpool[(ri * 2 + k) % len(fpool)]
                    ox = (-1 if k == 0 else 1) * bb["w"] * 0.28
                    oz = (-1 if k == 0 else 1) * bb["l"] * 0.28
                    equipment.append({
                        "id": f"fr{fi}", "label": label, "assetType": label.lower(),
                        "room": room["id"], "level": room["level"],
                        "x": round(bb["x"] + bb["w"] / 2 + ox, 2),
                        "y": round(bb["y"] + bb["l"] / 2 + oz, 2),
                    })
                    fi += 1

    return {
        "building": {"name": name or f"{facility.title()} Building",
                     "widthM": W, "lengthM": L, "floors": floors},
        "levels": levels, "rooms": rooms, "walls": walls,
        "openings": [], "equipment": equipment,
        "facility": facility, "synthesized": True,
    }


# ── bim_model → ontology drafts (for the Schema Mapper) ───────────────────
def bim_model_to_drafts(bm: dict, twin_name: str | None = None,
                        source_plan: str | None = None):
    """Return (entities, relationships) drafts for the Graph Writer.

    Keys are the bim element ids so the Scene Generator can map committed
    graph ids back onto geometry (entity['key'] == bim id)."""
    b = bm.get("building", {})
    name = twin_name or b.get("name") or "Building"
    entities: list[dict] = []
    rels: list[dict] = []

    # Building (root) — created first (no outgoing rels).
    entities.append({
        "key": "building", "canonical_type": CFP + "Building",
        "properties": {
            "displayName": name,
            "widthM": b.get("widthM"), "lengthM": b.get("lengthM"),
            "floorCount": b.get("floors", len(bm.get("levels", [])) or 1),
            "heightM": sum(l.get("heightM", 3.5) for l in bm.get("levels", [])) or 3.5,
            "areaM2": round((b.get("widthM", 0) or 0) * (b.get("lengthM", 0) or 0), 1),
            **({"sourcePlan": source_plan} if source_plan else {}),
        },
    })
    # BuildingPlan provenance document.
    if source_plan:
        entities.append({
            "key": "plan", "canonical_type": BIM + "BuildingPlan",
            "properties": {"displayName": f"{name} — source plan", "sourcePlan": source_plan},
        })

    # Floors
    for lvl in bm.get("levels", []):
        idx = lvl.get("index", 0)
        fkey = f"floor-{idx}"
        entities.append({
            "key": fkey, "canonical_type": CFP + "Floor",
            "properties": {
                "displayName": f"Level {idx}",
                "levelIndex": idx, "elevationM": lvl.get("elevationM", 0),
                "heightM": lvl.get("heightM", 3.5),
            },
        })
        rels.append({"source_key": fkey, "predicate": "nxr:containedIn", "target_key": "building"})

    # Rooms
    for room in bm.get("rooms", []):
        rkey = room["id"]
        bb = room.get("bbox") or {}
        props = {
            "displayName": room.get("name") or rkey,
            "roomFunction": room.get("function") or "room",
            "areaM2": room.get("areaM2"),
            "levelIndex": room.get("level", 0),
            "originX": bb.get("x"), "originY": bb.get("y"),
            "widthM": bb.get("w"), "lengthM": bb.get("l"),
            "setpoint": room.get("setpoint"),    # target temp for space over-temp monitors
        }
        if room.get("footprint"):
            props["footprint"] = _to_wkt(room["footprint"])
        room_ct = room.get("canonicalType") or (CFP + "Room")   # domain space override
        entities.append({"key": rkey, "canonical_type": room_ct,
                         "properties": {k: v for k, v in props.items() if v is not None}})
        rels.append({"source_key": rkey, "predicate": "nxr:containedIn",
                     "target_key": f"floor-{room.get('level', 0)}"})

    # Equipment (furniture/decor is rendered only — never committed as an asset)
    for eq in bm.get("equipment", []):
        ct, prop, decor = classify_item(eq.get("label", ""), eq.get("assetType", ""))
        if decor or ct is None:
            continue
        ct = eq.get("canonicalType") or ct          # domain class override (apply-to-plan)
        ekey = eq["id"]
        lvl = eq.get("level", 0)
        elev = next((l.get("elevationM", 0) for l in bm.get("levels", [])
                     if l.get("index") == lvl), 0)
        props = {
            "displayName": eq.get("label") or ekey,
            "originX": eq.get("x"), "originY": eq.get("y"), "originZ": elev,
            "levelIndex": lvl, "assetKind": prop,
            "rotationDeg": eq.get("rotationDeg", 0),
            **(eq.get("params") or {}),             # per-instance physics overrides
        }
        entities.append({"key": ekey, "canonical_type": ct,
                         "properties": {k: v for k, v in props.items() if v is not None}})
        if eq.get("room"):
            rels.append({"source_key": ekey, "predicate": "nxr:locatedAt",
                         "target_key": eq["room"]})

    # Functional coupling links injected by the domain topology builder
    # (feeds / suppliesAirTo / backsUp / serves). Keys are bim element ids.
    for link in bm.get("_links", []):
        if link.get("source") and link.get("target") and link.get("predicate"):
            rels.append({"source_key": link["source"], "predicate": link["predicate"],
                         "target_key": link["target"]})

    return entities, rels


def _to_wkt(footprint: list) -> str:
    pts = " ".join(f"{p[0]} {p[1]}" for p in footprint)
    first = footprint[0]
    return f"POLYGON(({pts} {first[0]} {first[1]}))"


# ── APPLY-TO-PLAN domain auto-wiring ─────────────────────────────────────
_DC_CLASS = {"rack": DC + "ComputeRack", "crac": DC + "CRAHUnit"}
_HSP_CLASS = {"mri": HSP + "MRIScanner", "ctscanner": HSP + "CTScanner",
              "gas": HSP + "MedicalGasManifold", "fridge": HSP + "ColdChainFridge",
              "nurse": HSP + "NurseCallSystem", "patientmonitor": HSP + "PatientMonitor"}
_ELECTRICAL_LOADS = {"rack", "crac", "ahu", "vav", "ups", "network", "pump",
                     "mri", "ctscanner", "fridge", "gas", "nurse", "patientmonitor",
                     "light", "meter", "boiler"}
_AIR_MOVERS = {"crac", "ahu", "vav"}
_IMAGING = {"mri", "ctscanner"}
_PROP_LABEL = {"switchgear": "Switchgear", "transformer": "Transformer", "ups": "UPS",
               "generator": "Standby Generator", "pdu": "Busbar PDU",
               "chiller": "Chiller", "coolingtower": "Cooling Tower", "crac": "CRAH Unit",
               "ahu": "Air Handler"}


def _kind(eq) -> str:
    return classify_item(eq.get("label", ""), eq.get("assetType", ""))[1]


def _is_decor(eq) -> bool:
    return classify_item(eq.get("label", ""), eq.get("assetType", ""))[2]


def enrich_domain(bm: dict, facility: str) -> dict:
    """APPLY-TO-PLAN AUTO-WIRING (hospital / datacenter only).

    Turn a parsed/synthesized bim_model into a fully interrelated, runnable twin:
      1. re-type the plan's assets to the dedicated domain classes (CFP stays the
         common base for everything else);
      2. re-type rooms to domain spaces (DataHall / OperatingRoom / ICU / …);
      3. inject the infrastructure SPINE plans rarely draw (utility feed,
         transformer, central UPS, standby generator, main distribution, chiller
         plant + cooling tower) — with geometry so they render;
      4. wire the functional coupling the dynamics engine couples on:
         UtilityFeed→Transformer→UPS→Panel→loads, Generator backsUp UPS,
         CoolingTower→Chiller→AHU/CRAH, AHU/CRAH suppliesAirTo its room,
         Chiller→Imaging (chilled-water dependency);
      5. pre-set a few conditionIndex / event-rate params so the baked-in fault
         chains develop and the 3-D status indicators visibly change.

    Mutates and returns bm. A no-op for non hospital/datacenter facilities."""
    facility = (facility or "").lower()
    if facility not in ("hospital", "datacenter"):
        return bm
    is_dc = facility == "datacenter"

    eqs = [e for e in bm.get("equipment", []) if not _is_decor(e)]
    rooms = bm.get("rooms", [])
    b = bm.get("building", {})
    W = float(b.get("widthM") or 40.0)
    L = float(b.get("lengthM") or 30.0)

    # 1. re-type plan assets to domain classes
    cls_map = _DC_CLASS if is_dc else _HSP_CLASS
    for e in eqs:
        k = _kind(e)
        if k in cls_map:
            e["canonicalType"] = cls_map[k]

    # 2. re-type rooms to domain spaces
    kinds_in_room: dict[str, set] = {}
    for e in eqs:
        if e.get("room"):
            kinds_in_room.setdefault(e["room"], set()).add(_kind(e))
    for r in rooms:
        fn = (r.get("function") or "").lower()
        kinds = kinds_in_room.get(r["id"], set())
        if is_dc:
            if "rack" in kinds or "data" in fn or "hall" in fn:
                r["canonicalType"] = DC + "DataHall"
                r["setpoint"] = 24.0
        else:
            if "theatre" in fn or "operating" in fn or fn == "or":
                r["canonicalType"] = HSP + "OperatingRoom"; r["setpoint"] = 21.0
            elif "icu" in fn or "intensive" in fn:
                r["canonicalType"] = HSP + "ICU"; r["setpoint"] = 23.0
            elif "isolation" in fn:
                r["canonicalType"] = HSP + "IsolationRoom"; r["setpoint"] = 22.0

    # 3. inject the infrastructure spine (placed along the bottom plant strip)
    spine: list[dict] = []
    edge_x = [4.0]

    def add_spine(sid, ct, prop, params=None):
        x = min(edge_x[0], max(4.0, W - 4.0))
        edge_x[0] += max(4.0, W / 9.0)
        spine.append({"id": sid, "label": _PROP_LABEL.get(prop, prop.title()),
                      "assetType": prop, "canonicalType": ct, "room": None,
                      "level": 0, "x": round(x, 2), "y": round(L - 3.0, 2),
                      "params": params or {}})
        return sid

    def first_of(*kinds):
        return next((e["id"] for e in eqs if _kind(e) in kinds), None)

    # datacenter: a scheduled mains outage so the UPS visibly goes on battery
    util = add_spine("sp_util", CFP + "UtilityFeed", "switchgear",
                     {"outageAtMinute": 20, "outageMinutes": 30} if is_dc else {})
    xfmr = add_spine("sp_xfmr", CFP + "Transformer", "transformer",
                     {"ratedCapacity": 2000 if is_dc else 1500})
    ups = first_of("ups") or add_spine("sp_ups", CFP + "UPS", "ups",
                                       {"batteryEnergyKWh": 200 if is_dc else 120})
    gen = add_spine("sp_gen", CFP + "Generator", "generator", {"fuelCapacityL": 2000})
    if is_dc:
        panel = first_of("pdu") or add_spine("sp_pdu", DC + "BusbarPDU", "pdu", {})
    else:
        panel = add_spine("sp_panel", CFP + "PowerPanel", "switchgear", {})
    chiller = first_of("chiller") or add_spine(
        "sp_chiller", CFP + "Chiller", "chiller",
        {"ratedCapacity": 800 if is_dc else 500,
         **({} if is_dc else {"conditionIndex": 0.55})})   # hospital: fouled chiller
    ctower = add_spine("sp_ctower", CFP + "CoolingTower", "coolingtower", {})

    # ensure every populated room is cooled (coupling completeness + a healthy
    # baseline): inject a cooling unit into any room with heat-producing
    # equipment that the plan didn't draw an air mover for.
    cu_ct = (DC + "CRAHUnit") if is_dc else (CFP + "AirHandlingUnit")
    cu_prop = "crac" if is_dc else "ahu"
    cool_n = 0
    for r in rooms:
        kinds = kinds_in_room.get(r["id"], set())
        if not kinds or (kinds & _AIR_MOVERS):
            continue
        bb = r.get("bbox") or {}
        cid = f"sp_cool{cool_n}"; cool_n += 1
        spine.append({"id": cid, "label": _PROP_LABEL[cu_prop], "assetType": cu_prop,
                      "canonicalType": cu_ct, "room": r["id"],
                      "level": int(r.get("level", 0)),
                      "x": round(bb.get("x", 0) + bb.get("w", 6) * 0.5, 2),
                      "y": round(bb.get("y", 0) + bb.get("l", 6) * 0.85, 2), "params": {}})
        kinds_in_room[r["id"]].add(cu_prop)

    bm.setdefault("equipment", []).extend(spine)
    eqs_all = eqs + spine

    # 4. wire the coupling
    links = list(bm.get("_links", []))

    def link(s, p, t):
        if s and t and s != t:
            links.append({"source": s, "predicate": p, "target": t})

    link(util, "nxr:feeds", xfmr)
    link(xfmr, "nxr:feeds", ups)
    link(ups, "nxr:feeds", panel)
    link(util, "nxr:feeds", gen)        # generator senses mains presence
    link(gen, "cfp:backsUp", ups)       # and backs the UPS on loss
    link(ctower, "nxr:feeds", chiller)
    link(panel, "nxr:feeds", chiller)

    spine_sources = {util, xfmr, ups, gen, panel, ctower}
    for e in eqs_all:
        if e["id"] in spine_sources or e["id"] == chiller:
            continue
        if _kind(e) in _ELECTRICAL_LOADS:
            link(panel, "nxr:feeds", e["id"])               # electrical
        if _kind(e) in _AIR_MOVERS or _kind(e) in _IMAGING:
            link(chiller, "nxr:feeds", e["id"])             # chilled water
        if _kind(e) in _AIR_MOVERS and e.get("room"):
            link(e["id"], "cfp:suppliesAirTo", e["room"])   # conditioned air

    # dedupe (avoid double-counting downstream load on aggregators)
    seen, deduped = set(), []
    for lk in links:
        key = (lk["source"], lk["predicate"], lk["target"])
        if key not in seen:
            seen.add(key); deduped.append(lk)
    bm["_links"] = deduped

    # 5. bake in the chosen fault chains
    if is_dc:
        # degrade the CRAH serving a hall that actually has racks, so the
        # thermal->throttle chain is visible there.
        rack_rooms = {e.get("room") for e in eqs_all if _kind(e) == "rack" and e.get("room")}
        crac = next((e for e in eqs_all if _kind(e) == "crac" and e.get("room") in rack_rooms), None)
        crac = crac or next((e for e in eqs_all if _kind(e) == "crac"), None)
        if crac:
            crac.setdefault("params", {}).update(
                {"conditionIndex": 0.4, "coilEffectiveness": 0.5, "dustRate": 6e-6})
    else:
        fridge = next((e for e in eqs_all if _kind(e) == "fridge"), None)
        if fridge:
            fridge.setdefault("params", {}).update({"conditionIndex": 0.3})
        gasm = next((e for e in eqs_all if _kind(e) == "gas"), None)
        if gasm:
            gasm.setdefault("params", {}).update({"leakRatePerHour": 2.0})
    return bm


# ── bim_model → nxr-scene/1 (for the 3-D viewer) ─────────────────────────
def bim_model_to_scene(bm: dict, id_map: dict | None = None,
                       status_map: dict | None = None) -> dict:
    """Build the renderable scene graph. Geometry in metres, building centred
    on the world origin. id_map: {bim id → graph entityId}; status_map:
    {entityId → 'ok'|'warn'|'crit'}."""
    id_map = id_map or {}
    status_map = status_map or {}
    b = bm.get("building", {})
    W = float(b.get("widthM") or 40.0)
    L = float(b.get("lengthM") or 30.0)
    levels = bm.get("levels") or [{"index": 0, "elevationM": 0, "heightM": 3.5}]
    elev_of = {l.get("index", 0): l.get("elevationM", 0) for l in levels}

    def cx(x):  # plan x → world x
        return round(x - W / 2, 3)

    def cz(y):  # plan y → world z
        return round(y - L / 2, 3)

    def _status(eid):
        return status_map.get(eid) if eid else None

    nodes: list[dict] = []

    # Floor slabs
    for lvl in levels:
        idx = lvl.get("index", 0)
        e = lvl.get("elevationM", 0)
        nodes.append({
            "id": f"slab-{idx}", "entityId": id_map.get(f"floor-{idx}"),
            "kind": "slab", "type": CFP + "Floor", "label": f"Level {idx}",
            "level": idx,
            "transform": {"pos": [0, round(e - 0.15, 3), 0], "rotY": 0, "scale": [1, 1, 1]},
            "geometry": {"kind": "box", "size": [W, 0.3, L]},
            "status": None,
        })

    # Walls
    for i, w in enumerate(bm.get("walls", [])):
        s, en = w.get("start"), w.get("end")
        if not s or not en:
            continue
        dx, dy = en[0] - s[0], en[1] - s[1]
        length = math.hypot(dx, dy)
        if length < 0.05:
            continue
        idx = w.get("level", 0)
        e = elev_of.get(idx, 0)
        h = w.get("heightM", 3.5)
        t = w.get("thicknessM", 0.2)
        nodes.append({
            "id": f"wall-{idx}-{i}", "entityId": None, "kind": "wall",
            "type": CFP + "Floor", "label": "wall", "level": idx,
            "transform": {"pos": [cx((s[0] + en[0]) / 2), round(e + h / 2, 3),
                                  cz((s[1] + en[1]) / 2)],
                          "rotY": round(math.atan2(dy, dx), 4), "scale": [1, 1, 1]},
            "geometry": {"kind": "box", "size": [round(length, 3), h, t]},
            "status": None,
        })

    # Room floor pads (clickable + colourable surfaces)
    for room in bm.get("rooms", []):
        bb = room.get("bbox") or {}
        x, y = bb.get("x", 0), bb.get("y", 0)
        w, l = bb.get("w", 4), bb.get("l", 4)
        idx = room.get("level", 0)
        e = elev_of.get(idx, 0)
        eid = id_map.get(room["id"])
        nodes.append({
            "id": f"room-{room['id']}", "entityId": eid, "kind": "room",
            "type": CFP + "Room", "label": room.get("name") or room["id"],
            "level": idx,
            "transform": {"pos": [cx(x + w / 2), round(e + 0.05, 3), cz(y + l / 2)],
                          "rotY": 0, "scale": [1, 1, 1]},
            "geometry": {"kind": "box", "size": [round(w * 0.96, 3), 0.08, round(l * 0.96, 3)]},
            "status": _status(eid),
        })

    # Equipment + furniture props (both rendered; decor carries no entityId)
    for eq in bm.get("equipment", []):
        ct, prop, decor = classify_item(eq.get("label", ""), eq.get("assetType", ""))
        idx = eq.get("level", 0)
        e = elev_of.get(idx, 0)
        eid = None if decor else id_map.get(eq["id"])
        nodes.append({
            "id": f"eq-{eq['id']}", "entityId": eid,
            "kind": "decor" if decor else "equipment",
            "type": ct or "fit-out", "label": eq.get("label") or eq["id"], "level": idx,
            "transform": {"pos": [cx(eq.get("x", W / 2)), round(e, 3), cz(eq.get("y", L / 2))],
                          "rotY": round(math.radians(eq.get("rotationDeg", 0) or 0), 4),
                          "scale": [1, 1, 1]},
            "geometry": {"kind": "prop", "prop": prop},
            "decor": decor,
            "status": _status(eid),
        })

    top = max((l.get("elevationM", 0) + l.get("heightM", 3.5) for l in levels), default=3.5)
    return {
        "format": "nxr-scene/1", "units": "m",
        "bbox": {"min": [-W / 2, 0, -L / 2], "max": [W / 2, top, L / 2]},
        "levels": [{"index": l.get("index", 0), "elevationM": l.get("elevationM", 0),
                    "heightM": l.get("heightM", 3.5)} for l in levels],
        "building": {"name": b.get("name"), "widthM": W, "lengthM": L,
                     "floors": b.get("floors", len(levels))},
        "nodes": nodes,
        "synthesized": bool(bm.get("synthesized")),
    }
