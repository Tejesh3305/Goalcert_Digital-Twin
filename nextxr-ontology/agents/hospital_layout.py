"""
hospital_layout.py — a purpose-built, sector-organised hospital-campus building
generator for the hospital-campus twin.

`synthesize_hospital_campus_bim()` returns a fully-enriched `bim_model` (same
shape `bim_support.synthesize_bim_model` returns) describing a realistic 2-floor
hospital: an acute/diagnostics ground floor (reception, emergency, imaging,
surgical suite, sterile supply, pharmacy, blood bank, bulk medical-gas store)
and an inpatient upper floor (ICU, general wards, isolation, nurse stations).

Unlike the generic `synthesize_bim_model` (a uniform grid of identical rooms),
this lays real, differently-sized rooms out in horizontal wings separated by
corridors, tags each with a clinical `sector`, and fills each with the right
equipment drawn from the shared 3-D catalog. It then runs the model through
`bim_support.enrich_domain(..., "hospital")` so the same infra spine + coupling
+ baked faults the plan→3D path uses are applied, and stamps plausible asset
metadata (manufacturer / warranty / condition) onto every monitored asset.

Coordinate convention matches bim_support: metres, origin at the building's min
corner, x/y in the floor plane, z up.
"""

from __future__ import annotations

from agents import bim_support as bs

# Building envelope (metres). Wide, shallow footprint → readable "wings".
_W = 58.0
_L = 42.0
_FH = 4.2                         # floor-to-floor height
_WALL_T = 0.22

# ── Per-room equipment recipes ───────────────────────────────────────────
# Each entry: (catalog prop key, human label, count). The key/label carry the
# keyword bim_support.classify_item routes on, so the right prop + CFP/HSP class
# is chosen automatically.
_RECIPE = {
    "reception":    [("reception", "Reception Desk", 1), ("chair", "Waiting Chair", 3),
                     ("plant", "Foliage", 2)],
    "ed_triage":    [("patientmonitor", "Triage Monitor", 1), ("bed", "Triage Bay", 2),
                     ("ivstand", "IV Stand", 1)],
    "ed_trauma":    [("hospitalbed", "Trauma Bay Bed", 1), ("patientmonitor", "Vitals Monitor", 1),
                     ("ivstand", "IV Stand", 1), ("infusionpump", "Infusion Pump", 1)],
    "ed_resus":     [("hospitalbed", "Resus Bed", 2), ("patientmonitor", "Vitals Monitor", 2),
                     ("ventilator", "Transport Ventilator", 1), ("crashcart", "Crash Cart", 1),
                     ("ivstand", "IV Stand", 2)],
    "mri":          [("mri", "MRI Scanner", 1), ("patientmonitor", "MR-safe Monitor", 1)],
    "ct":           [("ctscanner", "CT Scanner", 1), ("patientmonitor", "Vitals Monitor", 1)],
    "xray":         [("xray", "Digital X-Ray", 1)],
    "ultrasound":   [("ultrasound", "Ultrasound", 1), ("examtable", "Exam Couch", 1)],
    "lab":          [("fridge", "Reagent Fridge", 1), ("cabinet", "Analyser Bench", 2),
                     ("patientmonitor", "Bench Analyser", 1)],
    "or":           [("operatingtable", "Operating Table", 1), ("anesthesia", "Anaesthesia Machine", 1),
                     ("surgicallight", "Surgical Light", 1), ("laf", "Laminar Flow Canopy", 1),
                     ("mayostand", "Mayo Stand", 1), ("patientmonitor", "Anaesthetic Monitor", 1)],
    "scrub":        [("cabinet", "Sterile Store", 2)],
    "cssd":         [("autoclave", "Steam Autoclave", 2), ("cabinet", "Instrument Store", 1)],
    "pharmacy":     [("medcart", "Medication Cart", 1), ("fridge", "Cold-Chain Fridge", 1),
                     ("cabinet", "Drug Store", 1)],
    "bloodbank":    [("bloodbank", "Blood Bank Fridge", 2)],
    "gasstore":     [("gascylinderbank", "Medical Gas Manifold", 1)],
    "icu":          [("hospitalbed", "ICU Bed", 1), ("ventilator", "ICU Ventilator", 1),
                     ("patientmonitor", "Multiparameter Monitor", 1), ("ivstand", "IV Stand", 1),
                     ("infusionpump", "Infusion Pump", 1)],
    "ward":         [("bed", "Ward Bed", 1), ("patientmonitor", "Bedside Monitor", 1),
                     ("ivstand", "IV Stand", 1)],
    "isolation":    [("bed", "Isolation Bed", 1), ("patientmonitor", "Vitals Monitor", 1),
                     ("ventilator", "Ventilator", 1), ("ivstand", "IV Stand", 1)],
    "nurse":        [("nurse", "Nurse Station", 1), ("medcart", "Med Cart", 1)],
    "break":        [("sofa", "Staff Sofa", 1), ("table", "Table", 1), ("fridge", "Kitchenette Fridge", 1)],
}

# Plausible per-kind asset metadata for the info panel / asset management.
# (manufacturer, model prefix, warranty years, base conditionIndex)
_ASSET_META = {
    "mri":          ("Siemens Healthineers", "MAGNETOM", 7, 0.93),
    "ctscanner":    ("GE HealthCare", "Revolution", 6, 0.9),
    "xray":         ("Philips", "DigitalDiagnost", 6, 0.94),
    "ultrasound":   ("Canon Medical", "Aplio", 5, 0.95),
    "ventilator":   ("Dräger", "Evita", 5, 0.9),
    "infusionpump": ("B. Braun", "Infusomat", 4, 0.92),
    "patientmonitor": ("Philips", "IntelliVue", 5, 0.94),
    "autoclave":    ("Getinge", "HS66", 6, 0.9),
    "bloodbank":    ("Helmer Scientific", "iB", 8, 0.95),
    "fridge":       ("Follett", "REF", 6, 0.93),
    "gascylinderbank": ("BeaconMedaes", "Manifold", 10, 0.96),
    "gas":          ("BeaconMedaes", "Outlet", 10, 0.96),
    "nurse":        ("Hillrom", "NaviCare", 6, 0.95),
    "hospitalbed":  ("Hillrom", "Progressa", 8, 0.92),
    "bed":          ("Stryker", "SV2", 8, 0.94),
    "operatingtable": ("Maquet", "Magnus", 10, 0.93),
}

# ── Floor templates ──────────────────────────────────────────────────────
# A floor is a list of horizontal bands (front→back). Each band is either a
# corridor {"corridor": True, "h": depth} or a wing:
#   {"h": depth, "rooms": [(name, function, sector, weight, recipe_key), ...]}
# weights are relative room widths across the full building width.
_GROUND = [
    {"h": 10.5, "rooms": [
        ("Reception & Admin", "reception admin", "Admin", 1.5, "reception"),
        ("ED — Triage", "emergency triage", "Emergency", 1.1, "ed_triage"),
        ("ED — Trauma Bay", "emergency trauma bay", "Emergency", 1.1, "ed_trauma"),
        ("ED — Resuscitation", "emergency resus", "Emergency", 1.5, "ed_resus"),
    ]},
    {"corridor": True, "h": 3.6},
    {"h": 12.5, "rooms": [
        ("MRI Suite", "mri imaging suite", "Diagnostic Imaging", 1.15, "mri"),
        ("CT Suite", "ct imaging suite", "Diagnostic Imaging", 1.15, "ct"),
        ("X-Ray Room", "x-ray imaging room", "Diagnostic Imaging", 0.95, "xray"),
        ("Ultrasound", "ultrasound imaging room", "Diagnostic Imaging", 0.85, "ultrasound"),
        ("Laboratory", "laboratory", "Support Services", 1.1, "lab"),
    ]},
    {"corridor": True, "h": 3.6},
    {"h": 11.8, "rooms": [
        ("Operating Room 1", "operating theatre", "Surgical", 1.2, "or"),
        ("Operating Room 2", "operating theatre", "Surgical", 1.2, "or"),
        ("Scrub & Prep", "scrub prep", "Surgical", 0.6, "scrub"),
        ("Central Sterile Supply", "cssd sterile supply", "Support Services", 0.95, "cssd"),
        ("Pharmacy", "pharmacy", "Support Services", 0.85, "pharmacy"),
        ("Blood Bank", "blood bank cold storage", "Support Services", 0.6, "bloodbank"),
        ("Medical Gas Store", "medical gas manifold store", "Support Services", 0.6, "gasstore"),
    ]},
]

_WARD_FLOOR = [
    {"h": 12.5, "rooms": [
        ("Intensive Care Unit", "icu critical care", "Critical Care", 2.4, ("icu", 5)),
        ("ICU Nurse Station", "icu nurse station", "Critical Care", 0.8, "nurse"),
        ("Isolation Room 1", "isolation room", "Critical Care", 0.9, "isolation"),
        ("Isolation Room 2", "isolation room", "Critical Care", 0.9, "isolation"),
    ]},
    {"corridor": True, "h": 3.8},
    {"h": 11.5, "rooms": [
        ("General Ward A", "general ward", "Wards", 2.6, ("ward", 6)),
        ("Ward A Nurse Station", "ward nurse station", "Wards", 0.8, "nurse"),
        ("Staff Break Room", "break room", "Support Services", 0.9, "break"),
    ]},
    {"corridor": True, "h": 3.8},
    {"h": 10.6, "rooms": [
        ("General Ward B", "general ward", "Wards", 2.6, ("ward", 6)),
        ("Ward B Nurse Station", "ward nurse station", "Wards", 0.8, "nurse"),
        ("Day Room", "day room lounge", "Wards", 0.9, "break"),
    ]},
]


def _place_in_room(bb: dict, n: int) -> list[tuple[float, float]]:
    """Spread n items over a room's bbox on a centred grid, inset from walls."""
    import math
    x0, y0, w, l = bb["x"], bb["y"], bb["w"], bb["l"]
    if n <= 0:
        return []
    cols = max(1, min(n, round(math.sqrt(n * w / max(l, 0.1)))))
    rows = max(1, math.ceil(n / cols))
    pts = []
    for i in range(n):
        r, c = divmod(i, cols)
        fx = (c + 0.5) / cols
        fy = (r + 0.5) / rows
        # inset 12% so nothing sits inside a wall
        fx = 0.12 + fx * 0.76
        fy = 0.12 + fy * 0.76
        pts.append((round(x0 + fx * w, 2), round(y0 + fy * l, 2)))
    return pts


def _build_floor(level: int, template: list, eq_counter: list) -> tuple[list, list, list]:
    """Return (rooms, walls, equipment) for one floor from its band template."""
    rooms, walls, equipment = [], [], []
    round(level * (_FH + 0.4), 3)

    # perimeter walls
    walls.extend(bs._perimeter_walls(level, _W, _L, _FH, _WALL_T))

    y = 0.0
    bands = list(template)
    for bi, band in enumerate(bands):
        h = band["h"]
        # horizontal separator wall at the top edge of this band (skip building edge)
        if bi > 0:
            walls.append({"level": level, "start": [0.0, round(y, 2)], "end": [_W, round(y, 2)],
                          "heightM": _FH, "thicknessM": 0.16})
        if band.get("corridor"):
            rooms.append({
                "id": f"r{level}-cor{bi}", "level": level, "name": f"Corridor {level}-{bi}",
                "function": "corridor", "type": "corridor", "sector": "Circulation",
                "footprint": _rect(0.0, y, _W, h), "bbox": {"x": 0.0, "y": round(y, 2), "w": _W, "l": h},
                "areaM2": round(_W * h, 1),
            })
            y += h
            continue

        specs = band["rooms"]
        total_w = sum(s[3] for s in specs)
        x = 0.0
        for si, (name, function, sector, weight, recipe) in enumerate(specs):
            rw = round(_W * weight / total_w, 2)
            # vertical partition between rooms in this band (skip the left edge)
            if si > 0:
                walls.append({"level": level, "start": [round(x, 2), round(y, 2)],
                              "end": [round(x, 2), round(y + h, 2)],
                              "heightM": _FH, "thicknessM": 0.16})
            bb = {"x": round(x, 2), "y": round(y, 2), "w": rw, "l": h}
            rid = f"r{level}-{bi}-{si}"
            rooms.append({
                "id": rid, "level": level, "name": name, "function": function,
                "sector": sector, "footprint": _rect(bb["x"], bb["y"], rw, h),
                "bbox": bb, "areaM2": round(rw * h, 1),
            })
            # equipment: a recipe key, or (key, per_bay_count) to scale by beds
            recipe_key, bays = (recipe if isinstance(recipe, tuple) else (recipe, 1))
            items = _expand_recipe(recipe_key, bays)
            pts = _place_in_room(bb, len(items))
            for (key, label), (ex, ey) in zip(items, pts, strict=False):
                equipment.append({
                    "id": f"eq{eq_counter[0]}", "label": label, "assetType": key,
                    "room": rid, "level": level, "x": ex, "y": ey, "rotationDeg": 0,
                })
                eq_counter[0] += 1
            x += rw
        y += h

    # a few exterior windows + a ground-floor entrance door (visual richness)
    if level == 0:
        walls_openings = [("door", _W * 0.28, 0.0), ("window", _W * 0.55, 0.0),
                          ("window", _W * 0.75, 0.0)]
    else:
        walls_openings = [("window", _W * 0.3, 0.0), ("window", _W * 0.6, 0.0),
                          ("window", _W * 0.85, 0.0)]
    openings = [{"type": t, "level": level, "at": [round(px, 2), py], "widthM": 1.4}
                for t, px, py in walls_openings]
    return rooms, walls, equipment, openings


def _expand_recipe(recipe_key: str, bays: int) -> list[tuple[str, str]]:
    """Flatten a recipe (optionally repeated per bed/bay) into (key, label) items."""
    base = _RECIPE.get(recipe_key, [])
    out = []
    for rep in range(max(1, bays)):
        suffix = f" {rep + 1}" if bays > 1 else ""
        for key, label, count in base:
            for c in range(count):
                tag = f"{label}{suffix}"
                if count > 1 and bays == 1:
                    tag = f"{label} {c + 1}"
                out.append((key, tag))
    return out


def _rect(x: float, y: float, w: float, l: float) -> list:
    return [[round(x, 2), round(y, 2)], [round(x + w, 2), round(y, 2)],
            [round(x + w, 2), round(y + l, 2)], [round(x, 2), round(y + l, 2)]]


def attach_asset_metadata(bm: dict) -> dict:
    """Stamp plausible manufacturer / model / serial / install + warranty dates /
    criticality / conditionIndex onto every monitored (non-decor) asset — the
    asset-management fields the equipment info panel surfaces. Uses setdefault so
    any fault params baked in by enrich_domain (e.g. a degraded conditionIndex)
    are preserved."""
    import datetime
    base_year = datetime.date.today().year
    n = 0
    for eq in bm.get("equipment", []):
        _, prop, decor = bs.classify_item(eq.get("label", ""), eq.get("assetType", ""))
        if decor:
            continue
        meta = _ASSET_META.get(prop)
        if not meta:
            continue
        mfr, model_prefix, warranty_years, cond = meta
        params = eq.setdefault("params", {})
        age = n % 6                                   # 0..5 years old, deterministic
        install_year = base_year - age
        params.setdefault("manufacturer", mfr)
        params.setdefault("modelNumber", f"{model_prefix}-{100 + (n % 900)}")
        params.setdefault("serialNumber", f"{prop[:3].upper()}{install_year}{1000 + n:04d}")
        params.setdefault("installDate", f"{install_year}-{1 + (n % 12):02d}-15")
        params.setdefault("warrantyExpiry", f"{install_year + warranty_years}-{1 + (n % 12):02d}-15")
        params.setdefault("runtimeHours", 1800 * age + (n % 1800))
        params.setdefault("criticality", "high" if prop in (
            "ventilator", "mri", "ctscanner", "gascylinderbank", "bloodbank",
            "operatingtable", "patientmonitor") else "medium")
        params.setdefault("conditionIndex", round(cond - 0.02 * age, 3))
        n += 1
    return bm


def synthesize_hospital_campus_bim(floors: int = 2, name: str | None = None) -> dict:
    """Build a ready-to-commit, sector-organised hospital `bim_model`."""
    floors = max(1, min(int(floors or 2), 4))
    levels, rooms, walls, equipment, openings = [], [], [], [], []
    eq_counter = [0]
    for lvl in range(floors):
        template = _GROUND if lvl == 0 else _WARD_FLOOR
        levels.append({"index": lvl, "elevationM": round(lvl * (_FH + 0.4), 3), "heightM": _FH})
        r, w, e, o = _build_floor(lvl, template, eq_counter)
        rooms.extend(r); walls.extend(w); equipment.extend(e); openings.extend(o)

    bm = {
        "building": {"name": name or "NextXR General Hospital", "widthM": _W,
                     "lengthM": _L, "floors": floors},
        "levels": levels, "rooms": rooms, "walls": walls,
        "openings": openings, "equipment": equipment,
        "facility": "hospital", "synthesized": True,
    }
    # apply the domain functional layer (infra spine + coupling + baked faults +
    # room/asset re-typing to HSP classes) then stamp asset-management metadata.
    bm = bs.enrich_domain(bm, "hospital")
    bm = attach_asset_metadata(bm)
    return bm
