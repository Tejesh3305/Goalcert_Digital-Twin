"""
network.py — fleet network specs: the Melbourne tram network dataset + the
generic normaliser that turns ANY fleet spec (tram, light rail, metro, bus)
into the canonical shape the physics / map / ontology layers consume.

A network spec is plain JSON-able data:

    {
      "id": "melbourne-tram", "name": "Melbourne Tram Network",
      "kind": "tram", "voltage_v": 600, "km_per_unit": 0.25,
      "nodes":  { id: {"name", "x", "y", "kind": stop|junction|interchange|terminus, "zone"} },
      "routes": [ {"id", "name", "color", "path": [node ids], "via", "night": bool} ],
      "depots": [ {"id", "name", "x", "y", "capacity"} ],
      "substations": [ {"id", "name", "x", "y", "capacity_mw"} ],
      "fleet":  [ {"class", "name", "count", "capacity", "sections", "low_floor": bool} ],
    }

`normalize_network()` validates it, computes derived geometry (route polylines,
lengths, shared segments with route-density), and returns the enriched spec.
Everything downstream (physics, behaviours, the live map API) reads only the
normalised form — feed in any fleet and you get a working twin.

Pure stdlib. Coordinates are schematic (0..100, y down), scaled to km by
`km_per_unit`.
"""
from __future__ import annotations

import math


# ── Melbourne tram network (from the PTV "Melbourne tram network" map, 2024) ──
# Schematic node coordinates eyeballed from the official + Mallis maps.
_N = {
    # north-west radials
    "airport-west":   ("Airport West", 8, 6, "terminus"),
    "essendon":       ("Essendon Depot Stn", 14, 11, "stop"),
    "moonee-ponds":   ("Moonee Ponds Junction", 17, 15, "interchange"),
    "west-maribyrnong": ("West Maribyrnong", 3, 13, "terminus"),
    "maribyrnong-rd": ("Maribyrnong Rd", 10, 17, "stop"),
    "footscray":      ("Footscray Station", 6, 27, "terminus"),
    "flemington":     ("Flemington Bridge", 22, 22, "stop"),
    "west-coburg":    ("West Coburg", 23, 5, "terminus"),
    "brunswick-west": ("Brunswick West", 25, 13, "stop"),
    "north-coburg":   ("North Coburg", 33, 4, "terminus"),
    "brunswick":      ("Brunswick (Sydney Rd)", 34, 14, "stop"),
    "moreland":       ("Moreland", 29, 9, "terminus"),
    "lygon":          ("Lygon St (Brunswick East)", 37, 18, "stop"),
    "east-coburg":    ("East Coburg", 43, 4, "terminus"),
    "east-brunswick": ("East Brunswick", 49, 11, "terminus"),
    "nicholson":      ("Nicholson St", 49, 20, "stop"),
    # north-east radials
    "west-preston":   ("West Preston", 56, 7, "terminus"),
    "st-georges-rd":  ("St Georges Rd (Northcote)", 55, 15, "stop"),
    "fitzroy":        ("Fitzroy (Brunswick St)", 52, 26, "stop"),
    "bundoora-rmit":  ("Bundoora RMIT", 73, 4, "terminus"),
    "preston":        ("Preston (High St)", 66, 10, "stop"),
    "northcote":      ("Northcote (High St)", 60, 18, "stop"),
    "smith-st":       ("Smith St (Collingwood)", 54, 24, "stop"),
    # university / inner north
    "melb-uni":       ("Melbourne University", 43, 25, "interchange"),
    "haymarket":      ("Haymarket Junction", 40, 29, "junction"),
    "st-vincents":    ("St Vincent's Plaza", 52, 31, "terminus"),
    # east radials
    "victoria-gardens": ("Victoria Gardens", 63, 33, "terminus"),
    "north-richmond": ("North Richmond Station", 59, 37, "terminus"),
    "bridge-rd":      ("Bridge Rd (Richmond)", 62, 40, "stop"),
    "richmond":       ("Richmond (Swan St)", 60, 43, "stop"),
    "kew-junction":   ("Kew Junction", 75, 29, "junction"),
    "kew":            ("Kew (Glenferrie Rd)", 79, 27, "terminus"),
    "north-balwyn":   ("North Balwyn", 87, 22, "terminus"),
    "box-hill":       ("Box Hill", 96, 27, "terminus"),
    "camberwell":     ("Camberwell Junction", 85, 32, "terminus"),
    "burke-rd":       ("Burke Rd", 82, 36, "stop"),
    "glenferrie":     ("Glenferrie Rd (Hawthorn)", 76, 40, "stop"),
    "hawthorn":       ("Hawthorn Bridge", 70, 42, "stop"),
    "wattle-park":    ("Wattle Park", 89, 40, "terminus"),
    "riversdale":     ("Riversdale Rd", 80, 43, "stop"),
    "vermont-south":  ("Vermont South", 98, 44, "terminus"),
    "burwood":        ("Burwood Hwy", 88, 46, "stop"),
    # CBD grid — Swanston (x44) / Elizabeth (x38) verticals; La Trobe (y33),
    # Bourke (y38), Collins (y42), Flinders (y46) horizontals.
    "latrobe-swan":   ("La Trobe / Swanston", 44, 33, "stop"),
    "bourke-swan":    ("Bourke / Swanston", 44, 38, "stop"),
    "collins-swan":   ("Collins / Swanston (Town Hall)", 44, 42, "interchange"),
    "flinders-swan":  ("Flinders / Swanston (Fed Sq)", 44, 46, "interchange"),
    "latrobe-eliz":   ("La Trobe / Elizabeth", 38, 33, "stop"),
    "bourke-eliz":    ("Bourke / Elizabeth", 38, 38, "stop"),
    "collins-eliz":   ("Collins / Elizabeth", 38, 42, "stop"),
    "flinders-stn":   ("Flinders Street Station", 38, 46, "interchange"),
    "spring-st":      ("Spring St (Parliament)", 50, 39, "junction"),
    "spencer-latrobe": ("La Trobe / Spencer", 31, 33, "stop"),
    "southern-cross": ("Southern Cross Station", 31, 44, "interchange"),
    # Docklands
    "docklands":      ("Docklands (Harbour Esp)", 24, 36, "junction"),
    "waterfront-city": ("Waterfront City", 15, 31, "terminus"),
    "central-pier":   ("Central Pier", 19, 37, "terminus"),
    "victoria-harbour": ("Victoria Harbour", 18, 43, "terminus"),
    # south / bayside
    "casino":         ("Casino / South Wharf", 36, 50, "stop"),
    "southbank":      ("Southbank", 41, 51, "stop"),
    "arts-precinct":  ("Arts Precinct", 44, 52, "stop"),
    "port-junction":  ("Port Junction (Clarendon)", 32, 55, "junction"),
    "south-melbourne": ("South Melbourne", 33, 60, "stop"),
    "south-melbourne-beach": ("South Melbourne Beach", 29, 67, "terminus"),
    "port-melbourne": ("Port Melbourne", 15, 59, "terminus"),
    "albert-park":    ("Albert Park", 38, 62, "stop"),
    "st-kilda-rd":    ("St Kilda Rd (Domain)", 46, 56, "stop"),
    "st-kilda-junction": ("St Kilda Junction", 46, 60, "junction"),
    "fitzroy-st":     ("Fitzroy St", 44, 66, "stop"),
    "st-kilda":       ("St Kilda (Esplanade)", 43, 70, "interchange"),
    "st-kilda-beach": ("St Kilda Beach", 41, 77, "terminus"),
    "balaclava":      ("Balaclava Station", 53, 70, "terminus"),
    "elsternwick":    ("Elsternwick", 57, 74, "stop"),
    # south-east radials
    "south-yarra":    ("South Yarra (Chapel St)", 58, 47, "stop"),
    "prahran":        ("Prahran (Commercial Rd)", 58, 55, "stop"),
    "windsor":        ("Windsor", 56, 60, "stop"),
    "toorak-rd":      ("Toorak Rd (South Yarra)", 66, 52, "stop"),
    "toorak":         ("Toorak Village", 79, 55, "terminus"),
    "glen-iris":      ("Glen Iris", 85, 55, "terminus"),
    "high-st-armadale": ("High St (Armadale)", 68, 58, "stop"),
    "malvern":        ("Malvern (Wattletree Rd)", 81, 61, "terminus"),
    "dandenong-rd":   ("Dandenong Rd", 62, 63, "stop"),
    "caulfield":      ("Caulfield", 74, 67, "stop"),
    "east-malvern":   ("East Malvern (Darling)", 86, 66, "terminus"),
    "glenhuntly":     ("Glen Huntly", 70, 72, "stop"),
    "carnegie":       ("Carnegie", 84, 73, "terminus"),
    "hawthorn-rd":    ("Hawthorn Rd (Caulfield Sth)", 63, 71, "stop"),
    "east-brighton":  ("East Brighton", 73, 81, "terminus"),
}

# Official Yarra Trams route colours (close approximations).
_ROUTES = [
    ("1",  "East Coburg – South Melbourne Beach", "#B5BD00",
     ["east-coburg", "lygon", "melb-uni", "latrobe-swan", "bourke-swan", "collins-swan",
      "flinders-swan", "arts-precinct", "southbank", "port-junction", "south-melbourne",
      "south-melbourne-beach"], "Lygon St · Swanston St · Sth Melbourne", True),
    ("3",  "Melbourne University – East Malvern", "#8DC8E8",
     ["melb-uni", "latrobe-swan", "bourke-swan", "collins-swan", "flinders-swan",
      "arts-precinct", "st-kilda-rd", "st-kilda-junction", "dandenong-rd", "caulfield",
      "east-malvern"], "St Kilda Rd · Balaclava · Caulfield", False),
    ("5",  "Melbourne University – Malvern", "#D50032",
     ["melb-uni", "latrobe-swan", "bourke-swan", "collins-swan", "flinders-swan",
      "arts-precinct", "st-kilda-rd", "st-kilda-junction", "dandenong-rd", "malvern"],
     "Dandenong Rd · Windsor · Armadale", False),
    ("6",  "Moreland – Glen Iris", "#01426A",
     ["moreland", "lygon", "melb-uni", "latrobe-swan", "bourke-swan", "collins-swan",
      "flinders-swan", "arts-precinct", "st-kilda-rd", "st-kilda-junction",
      "high-st-armadale", "glen-iris"], "Lygon St · High St · Armadale", True),
    ("11", "West Preston – Victoria Harbour", "#6ECEB2",
     ["west-preston", "st-georges-rd", "fitzroy", "spring-st", "collins-swan",
      "collins-eliz", "southern-cross", "docklands", "victoria-harbour"],
     "St Georges Rd · Fitzroy · Collins St", True),
    ("12", "Victoria Gardens – St Kilda", "#007E92",
     ["victoria-gardens", "st-vincents", "spring-st", "collins-swan", "collins-eliz",
      "southern-cross", "casino", "port-junction", "albert-park", "fitzroy-st",
      "st-kilda"], "Victoria St · Collins St · South Wharf", False),
    ("16", "Melbourne University – Kew", "#FBD872",
     ["melb-uni", "latrobe-swan", "bourke-swan", "collins-swan", "flinders-swan",
      "arts-precinct", "st-kilda-rd", "st-kilda-junction", "fitzroy-st", "st-kilda",
      "windsor", "prahran", "glenferrie", "kew-junction", "kew"],
     "St Kilda Beach · Glenferrie Rd · Malvern", True),
    ("19", "North Coburg – Flinders St Station", "#8F1A95",
     ["north-coburg", "brunswick", "haymarket", "latrobe-eliz", "bourke-eliz",
      "collins-eliz", "flinders-stn"], "Sydney Rd · Elizabeth St", True),
    ("30", "St Vincent's Plaza – Central Pier", "#524F94",
     ["st-vincents", "latrobe-swan", "latrobe-eliz", "spencer-latrobe", "docklands",
      "central-pier"], "La Trobe St", False),
    ("35", "City Circle", "#6B3529",
     ["flinders-stn", "flinders-swan", "spring-st", "latrobe-swan", "latrobe-eliz",
      "spencer-latrobe", "docklands", "victoria-harbour", "southern-cross",
      "flinders-stn"], "Free heritage loop — La Trobe · Spring · Flinders · Docklands", False),
    ("48", "North Balwyn – Victoria Harbour", "#45484D",
     ["north-balwyn", "kew-junction", "bridge-rd", "spring-st", "flinders-swan",
      "flinders-stn", "southern-cross", "docklands", "victoria-harbour"],
     "High St Kew · Bridge Rd · Flinders St", False),
    ("57", "West Maribyrnong – Flinders St Station", "#00C1D5",
     ["west-maribyrnong", "maribyrnong-rd", "flemington", "haymarket", "latrobe-eliz",
      "bourke-eliz", "collins-eliz", "flinders-stn"],
     "Racecourse Rd · Flemington · Nth Melbourne", False),
    ("58", "West Coburg – Toorak", "#969696",
     ["west-coburg", "brunswick-west", "haymarket", "latrobe-eliz", "southern-cross",
      "casino", "south-yarra", "toorak-rd", "toorak"],
     "William St · Toorak Rd · South Yarra", False),
    ("59", "Airport West – Flinders St Station", "#00653A",
     ["airport-west", "essendon", "moonee-ponds", "flemington", "haymarket",
      "latrobe-eliz", "bourke-eliz", "collins-eliz", "flinders-stn"],
     "Niddrie · Essendon · Moonee Ponds · Flemington", False),
    ("64", "Melbourne University – East Brighton", "#00AB8E",
     ["melb-uni", "latrobe-swan", "bourke-swan", "collins-swan", "flinders-swan",
      "arts-precinct", "st-kilda-rd", "st-kilda-junction", "dandenong-rd",
      "hawthorn-rd", "east-brighton"], "Dandenong Rd · Caulfield South", False),
    ("67", "Melbourne University – Carnegie", "#956C58",
     ["melb-uni", "latrobe-swan", "bourke-swan", "collins-swan", "flinders-swan",
      "arts-precinct", "st-kilda-rd", "st-kilda-junction", "windsor", "balaclava",
      "elsternwick", "glenhuntly", "carnegie"], "Balaclava · Elsternwick · Glen Huntly", False),
    ("70", "Wattle Park – Waterfront City", "#F59BBB",
     ["wattle-park", "riversdale", "hawthorn", "richmond", "flinders-swan",
      "flinders-stn", "southern-cross", "docklands", "waterfront-city"],
     "Riversdale Rd · Swan St · Flinders St", False),
    ("72", "Melbourne University – Camberwell", "#9ABEAA",
     ["melb-uni", "latrobe-swan", "bourke-swan", "collins-swan", "flinders-swan",
      "arts-precinct", "st-kilda-rd", "prahran", "glenferrie", "burke-rd",
      "camberwell"], "Commercial Rd · Prahran · Glen Iris", False),
    ("75", "Vermont South – Central Pier", "#00A9E0",
     ["vermont-south", "burwood", "hawthorn", "bridge-rd", "spring-st",
      "flinders-swan", "flinders-stn", "southern-cross", "docklands", "central-pier"],
     "Burwood Hwy · Hawthorn · Bridge Rd", True),
    ("78", "North Richmond – Balaclava", "#A0A0D6",
     ["north-richmond", "south-yarra", "prahran", "windsor", "balaclava"],
     "Chapel St · South Yarra · Prahran · Windsor", False),
    ("82", "Moonee Ponds – Footscray", "#D2D755",
     ["moonee-ponds", "maribyrnong-rd", "footscray"],
     "Maribyrnong Rd · Droop St", False),
    ("86", "Bundoora RMIT – Waterfront City", "#FFB500",
     ["bundoora-rmit", "preston", "northcote", "smith-st", "spring-st", "bourke-swan",
      "bourke-eliz", "docklands", "waterfront-city"],
     "Plenty Rd · High St · Smith St · Bourke St", True),
    ("96", "East Brunswick – St Kilda Beach", "#C6007E",
     ["east-brunswick", "nicholson", "spring-st", "bourke-swan", "bourke-eliz",
      "southern-cross", "casino", "port-junction", "albert-park", "fitzroy-st",
      "st-kilda-beach"], "Nicholson St · Bourke St · Southbank · Albert Park", True),
    ("109", "Box Hill – Port Melbourne", "#E87722",
     ["box-hill", "kew-junction", "victoria-gardens", "st-vincents", "spring-st",
      "collins-swan", "collins-eliz", "southern-cross", "casino", "port-junction",
      "port-melbourne"], "Whitehorse Rd · Victoria St · Collins St", True),
]

MELBOURNE_TRAM = {
    "id": "melbourne-tram",
    "name": "Melbourne Tram Network",
    "operator": "Yarra Trams (demo twin)",
    "kind": "tram",
    "voltage_v": 600,              # 600 V DC overhead
    "km_per_unit": 0.25,           # schematic unit -> km
    "nodes": {k: {"name": n, "x": x, "y": y, "kind": kind}
              for k, (n, x, y, kind) in _N.items()},
    "routes": [{"id": rid, "name": name, "color": color, "path": path,
                "via": via, "night": night}
               for rid, name, color, path, via, night in _ROUTES],
    "depots": [
        {"id": "dep-essendon",   "name": "Essendon Depot",   "x": 15, "y": 13, "capacity": 65},
        {"id": "dep-brunswick",  "name": "Brunswick Depot",  "x": 33, "y": 12, "capacity": 60},
        {"id": "dep-preston",    "name": "Preston Workshops","x": 63, "y": 11, "capacity": 75},
        {"id": "dep-kew",        "name": "Kew Depot",        "x": 77, "y": 28, "capacity": 60},
        {"id": "dep-camberwell", "name": "Camberwell Depot", "x": 84, "y": 34, "capacity": 55},
        {"id": "dep-glenhuntly", "name": "Glenhuntly Depot", "x": 70, "y": 73, "capacity": 50},
        {"id": "dep-malvern",    "name": "Malvern Depot",    "x": 72, "y": 56, "capacity": 60},
        {"id": "dep-southbank",  "name": "Southbank Depot",  "x": 40, "y": 52, "capacity": 55},
    ],
    "substations": [
        {"id": "sub-cbd-e",      "name": "CBD East TSS",     "x": 47, "y": 40, "capacity_mw": 6.0},
        {"id": "sub-cbd-w",      "name": "CBD West TSS",     "x": 34, "y": 40, "capacity_mw": 6.0},
        {"id": "sub-docklands",  "name": "Docklands TSS",    "x": 21, "y": 35, "capacity_mw": 4.5},
        {"id": "sub-stkilda",    "name": "St Kilda Rd TSS",  "x": 45, "y": 61, "capacity_mw": 5.0},
        {"id": "sub-brunswick",  "name": "Brunswick TSS",    "x": 34, "y": 13, "capacity_mw": 4.0},
        {"id": "sub-preston",    "name": "Preston TSS",      "x": 62, "y": 12, "capacity_mw": 4.0},
        {"id": "sub-kew",        "name": "Kew TSS",          "x": 76, "y": 31, "capacity_mw": 4.0},
        {"id": "sub-camberwell", "name": "Camberwell TSS",   "x": 84, "y": 37, "capacity_mw": 4.0},
        {"id": "sub-glenhuntly", "name": "Glen Huntly TSS",  "x": 69, "y": 70, "capacity_mw": 3.5},
        {"id": "sub-footscray",  "name": "Footscray TSS",    "x": 10, "y": 24, "capacity_mw": 3.0},
    ],
    # Real Melbourne fleet composition (approximate 2024 counts).
    "fleet": [
        {"class": "E",  "name": "E-Class (Bombardier Flexity Swift)", "count": 100,
         "capacity": 210, "sections": 3, "low_floor": True},
        {"class": "D2", "name": "D2-Class (Siemens Combino, 5-section)", "count": 21,
         "capacity": 180, "sections": 5, "low_floor": True},
        {"class": "D1", "name": "D1-Class (Siemens Combino, 3-section)", "count": 38,
         "capacity": 110, "sections": 3, "low_floor": True},
        {"class": "C2", "name": "C2-Class (Alstom Citadis 'Bumblebee')", "count": 5,
         "capacity": 190, "sections": 5, "low_floor": True},
        {"class": "C1", "name": "C1-Class (Alstom Citadis)", "count": 36,
         "capacity": 140, "sections": 3, "low_floor": True},
        {"class": "B2", "name": "B2-Class (Comeng)", "count": 122,
         "capacity": 145, "sections": 2, "low_floor": False},
        {"class": "A",  "name": "A1/A2-Class (Comeng)", "count": 69,
         "capacity": 105, "sections": 1, "low_floor": False},
        {"class": "W8", "name": "W8-Class (heritage, City Circle)", "count": 12,
         "capacity": 90, "sections": 1, "low_floor": False},
    ],
}


# ── Normaliser: any fleet spec → canonical enriched network ───────────

def _dist(a: dict, b: dict) -> float:
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def normalize_network(spec: dict | None = None) -> dict:
    """Validate + enrich a fleet network spec. Returns a NEW dict with:
       routes[i].length_km, routes[i].points [(x,y)...], segments (unique
       undirected edges with the routes that share them), fleet_size,
       total_capacity. Raises ValueError on a malformed spec."""
    src = spec or MELBOURNE_TRAM
    nodes = src.get("nodes") or {}
    routes = src.get("routes") or []
    if not nodes or not routes:
        raise ValueError("network spec needs non-empty 'nodes' and 'routes'")
    kmu = float(src.get("km_per_unit", 0.25))

    out = {k: v for k, v in src.items() if k not in ("routes",)}
    out["nodes"] = {k: dict(v) for k, v in nodes.items()}

    segments: dict[tuple, dict] = {}
    norm_routes = []
    for r in routes:
        path = [p for p in r.get("path", []) if p in nodes]
        if len(path) < 2:
            raise ValueError(f"route {r.get('id')} has fewer than 2 known nodes")
        pts = [(nodes[p]["x"], nodes[p]["y"]) for p in path]
        length = sum(_dist(nodes[path[i]], nodes[path[i + 1]])
                     for i in range(len(path) - 1)) * kmu
        rr = dict(r)
        rr["path"] = path
        rr["points"] = pts
        rr["length_km"] = round(length, 2)
        rr["loop"] = path[0] == path[-1]
        norm_routes.append(rr)
        for i in range(len(path) - 1):
            key = tuple(sorted((path[i], path[i + 1])))
            seg = segments.setdefault(key, {"a": key[0], "b": key[1],
                                            "routes": [], "length_km": round(
                                                _dist(nodes[key[0]], nodes[key[1]]) * kmu, 2)})
            if rr["id"] not in seg["routes"]:
                seg["routes"].append(rr["id"])

    out["routes"] = norm_routes
    out["segments"] = [dict(v, id=f"{v['a']}~{v['b']}") for v in segments.values()]
    fleet = src.get("fleet") or [{"class": "GEN", "name": "Generic vehicle",
                                  "count": 40, "capacity": 120}]
    out["fleet"] = [dict(f) for f in fleet]
    out["fleet_size"] = sum(int(f.get("count", 0)) for f in fleet)
    out["total_capacity"] = sum(int(f.get("count", 0)) * int(f.get("capacity", 0))
                                for f in fleet)
    out["depots"] = [dict(d) for d in src.get("depots", [])]
    out["substations"] = [dict(s) for s in src.get("substations", [])]
    out["route_km"] = round(sum(r["length_km"] for r in norm_routes), 1)
    return out


# Named registry so twins can be built from a spec id ("melbourne-tram") or a
# full custom dict. Extend by registering more networks here or passing specs.
NETWORKS = {"melbourne-tram": MELBOURNE_TRAM}


def get_network(ref=None) -> dict:
    """ref: None (default Melbourne) | spec-id string | full spec dict."""
    if ref is None:
        return normalize_network(MELBOURNE_TRAM)
    if isinstance(ref, str):
        if ref not in NETWORKS:
            raise ValueError(f"unknown network '{ref}' — have {list(NETWORKS)}")
        return normalize_network(NETWORKS[ref])
    return normalize_network(ref)
