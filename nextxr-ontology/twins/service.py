"""
service.py — the Twin registry + seeding logic.

A Twin is one isolated platform instance, keyed by tenant_id. The registry
table (the shared relational store — RDS Postgres in production, SQLite
locally) holds only metadata:
which twins exist, their name, domain template, and seed asset. The entities
themselves live in Neo4j under the tenant_id and are created EXCLUSIVELY through
the Graph Writer (validate -> commit -> changelog -> bus), so a seeded twin
honours every platform guarantee the moment it is born.

Templates
---------
A template describes what to seed. The shipped "hvac" template builds a
Site -> Space <- AirHandler facility (the exact shape the simulated feed and
the three-tier behaviours already understand), so a new HVAC twin is alive on
creation. "blank" seeds just a root Site, for building by hand via Add Asset.
"""

from __future__ import annotations

import db

import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

CORE = "https://ontology.nextxr.io/v3/core#"
HVAC = "https://ontology.nextxr.io/v3/hvac#"
CFP  = "https://ontology.nextxr.io/v3/cfp#"
RAIL = "https://ontology.nextxr.io/v3/railway#"
HSP  = "https://ontology.nextxr.io/v3/hospital#"
EV   = "https://ontology.nextxr.io/v3/ev#"
DEF  = "https://ontology.nextxr.io/v3/defence#"
FLEET = "https://ontology.nextxr.io/v3/fleet#"

_STORE = "twins"

# Domain templates: what gets seeded when a twin of this kind is created.
# Each is a pure description; service.seed() interprets it via the Graph Writer.
TEMPLATES = {
    "hvac": {
        "label": "HVAC Facility",
        "description": "A site with a server room cooled by an air handler — "
                       "ready for the live temperature feed and Tier A/B/C rules.",
        "primary_signal": "hvac:AirTemperature",
        "seeds_feed": True,
    },
    "generic-facility": {
        "label": "Generic Facility",
        "description": "A 3-floor building with HVAC, power, fire, security, "
                       "water, and network systems — the CFP demo twin. "
                       "All systems produce telemetry and fire rules.",
        "primary_signal": "cfp:upsSoC",
        "seeds_feed": True,
    },
    # ── Machine-twin domains (single-asset / network physics twins) ──
    # These seed a Site + one machine asset; the live physics + findings run in
    # the machine-twin runtime (twins/runtime.py), not the HVAC/CFP feed.
    "turbine-engine": {
        "label": "Gas Turbine Engine",
        "description": "A gas-turbine engine twin — EGT, shaft speeds, fuel, "
                       "vibration, EPR and oil signals with health + RUL.",
        "primary_signal": "turbine:egt",
        "seeds_feed": False,
        "machine": True,
        "class_iri": "https://ontology.nextxr.io/v3/turbine#GasTurbine",
    },
    "edm-machine": {
        "label": "Wire EDM Machine",
        "description": "A wire electrical-discharge-machining twin — discharge, "
                       "dielectric, wire-transport and axis signals.",
        "primary_signal": "edm:sparkFrequency",
        "seeds_feed": False,
        "machine": True,
        "class_iri": "https://ontology.nextxr.io/v3/edm#WireEDM",
    },
    "railway-metro": {
        "label": "Metro Rail Network",
        "description": "A full MRT / metro twin — lines, stations, platforms, "
                       "permanent way, third-rail traction power, CBTC "
                       "signalling and station services, with a live network "
                       "map, per-station KPIs and a depot board.",
        "primary_signal": "rail:onTimePerformance",
        "seeds_feed": False,
        "machine": True,
        "class_iri": RAIL + "RailNetwork",
    },
    "tram-network": {
        "label": "Tram / Light-Rail Network",
        "description": "A whole tram / light-rail network twin — rolling stock, "
                       "traction power (overhead line + substations), track & "
                       "points, signalling and service operations, with a live "
                       "network map showing per-vehicle positions and per-route "
                       "status.",
        "primary_signal": "fleet:onTimePerformance",
        "seeds_feed": False,
        "machine": True,
        # The fleet domain has no ontology class of its own; seed the network
        # node as a governed rail network (the runtime picks the fleet physics
        # from the twin's `domain`, not from this node class).
        "class_iri": RAIL + "RailNetwork",
    },
    "railway-trainset": {
        "label": "Rolling Stock (Train Set)",
        "description": "A rolling-stock twin — one train set at the vehicle "
                       "level: traction, bogies, braking, doors and auxiliaries "
                       "with health + remaining-useful-life.",
        "primary_signal": "rail:trainSpeed",
        "seeds_feed": False,
        "machine": True,
        "class_iri": RAIL + "RollingStock",
    },
    "hospital-campus": {
        "label": "Hospital Campus",
        "description": "A full hospital-campus twin — theatres, ICU, ED, pharmacy, "
                       "wards, medical gas, water safety, power resilience, "
                       "sterilisation, infection control and patient flow, with a "
                       "bed board, OR calendar, patient-flow funnel, infection map "
                       "and medical-gas schematic.",
        "primary_signal": "hsp:orPressure",
        "seeds_feed": False,
        "machine": True,
        "class_iri": HSP + "Hospital",
    },
    "ev-charging-network": {
        "label": "EV Charging Network",
        "description": "An EV charging-network twin — stations, chargers, grid "
                       "connection, transformer, solar and V2G, with a charging "
                       "geo map, grid load curve and a V2G trading view.",
        "primary_signal": "ev:networkLoad",
        "seeds_feed": False,
        "machine": True,
        "class_iri": EV + "ChargingNetwork",
    },
    "ev-battery-pack": {
        "label": "EV Battery Pack",
        "description": "A battery-pack twin at cell level — modules of cells with "
                       "a Thevenin ECM, thermal coupling and degradation, with a "
                       "cell-health heatmap and imbalance / thermal-runaway / SoH "
                       "monitoring.",
        "primary_signal": "ev:cellVoltageDelta",
        "seeds_feed": False,
        "machine": True,
        "class_iri": EV + "BatteryPack",
    },
    "defence-base": {
        "label": "Military Base (C4ISR)",
        "description": "A military-base twin — perimeter, C4ISR command centre, "
                       "radar, hangars, runways, fuel and ammunition storage and "
                       "NBC, with a NATO APP-6 tactical map and a mission board.",
        "primary_signal": "def:radarCoverage",
        "seeds_feed": False,
        "machine": True,
        "class_iri": DEF + "MilitaryBase",
    },
    "defence-warship": {
        "label": "Warship",
        "description": "A naval surface-combatant twin — gas-turbine propulsion, "
                       "stability under progressive flooding, hull structural "
                       "fatigue and a damage-control compartment diagram.",
        "primary_signal": "def:listAngle",
        "seeds_feed": False,
        "machine": True,
        "class_iri": DEF + "Vessel",
    },
    "scanned-object": {
        "label": "Scanned Object",
        "description": "A 3-D object reconstructed from a photo with TRELLIS "
                       "(RunPod). The generated mesh is the twin's model.",
        "primary_signal": None,
        "seeds_feed": False,
    },
    "blank": {
        "label": "Blank Twin",
        "description": "An empty twin with just a root site. Build it by hand "
                       "via Add Asset.",
        "primary_signal": None,
        "seeds_feed": False,
    },
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(name: str) -> str:
    """Turn a display name into a safe tenant id slug."""
    base = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    base = base or "twin"
    # Keep it short but unique enough; append a millisecond suffix.
    return f"{base[:32]}-{int(time.time() * 1000) % 100000}"


@dataclass
class Twin:
    tenant_id: str
    name: str
    domain: str            # template key, e.g. "hvac"
    description: str
    created_at: str
    seed_asset_id: Optional[str] = None   # the primary asset the feed targets

    def to_dict(self) -> dict:
        return asdict(self)


# Rehydrate the registry from the graph at most once per process.
_REHYDRATED = False


def _graph_session():
    """A Neo4j session via the shared driver. Imported lazily so the twins
    package keeps its hard dependencies one-way — the graph mirror below is
    best-effort and every caller swallows failures."""
    from graph.connection import get_driver  # local import by design
    return get_driver().session()


class TwinRegistry:
    """Registry of twins, in the shared relational store. Seeding goes through
    the Graph Writer.

    The graph mirror below predates RDS: with the registry in a local SQLite
    file, a redeploy wiped it while the twins' Neo4j entities survived. Every
    registry row is therefore mirrored to a `(:NxrTwinRegistry)` node, and an
    empty registry rehydrates from the graph on first use. On RDS the registry
    is itself durable, so this is now a belt-and-braces recovery path rather
    than the thing that saves the data — but it costs nothing and still covers
    a restore into an empty database."""

    def __init__(self, db_path: Optional[Path] = None):
        """`db_path` forces a private SQLite file (offline tools only)."""
        self.db_path = Path(db_path) if db_path else None
        self._init_db()
        self._rehydrate_from_graph()

    # ---- graph mirror (survives redeploys) ---------------------------
    def _mirror_upsert(self, twin: Twin) -> None:
        try:
            with _graph_session() as s:
                s.run(
                    "MERGE (t:NxrTwinRegistry {tenant_id: $tenant_id}) "
                    "SET t.name = $name, t.domain = $domain, "
                    "    t.description = $description, "
                    "    t.created_at = $created_at, "
                    "    t.seed_asset_id = $seed_asset_id",
                    **twin.to_dict(),
                )
        except Exception:
            pass  # mirror is best-effort; the local registry stays the truth

    def _mirror_delete(self, tenant_id: str) -> None:
        try:
            with _graph_session() as s:
                s.run("MATCH (t:NxrTwinRegistry {tenant_id: $tid}) DELETE t",
                      tid=tenant_id)
        except Exception:
            pass

    def _rehydrate_from_graph(self) -> None:
        global _REHYDRATED
        if _REHYDRATED:
            return
        _REHYDRATED = True
        try:
            with self._connect() as conn:
                if conn.execute("SELECT 1 FROM twins LIMIT 1").fetchone():
                    return  # registry already has rows — nothing to recover
            with _graph_session() as s:
                rows = s.run("MATCH (t:NxrTwinRegistry) RETURN t").data()
            if not rows:
                return
            with self._connect() as conn:
                for r in rows:
                    t = r["t"]
                    # ON CONFLICT DO NOTHING, not INSERT OR IGNORE: the latter
                    # is SQLite-only. Two tasks can rehydrate concurrently.
                    conn.execute(
                        "INSERT INTO twins (tenant_id, name, domain, "
                        "description, created_at, seed_asset_id) "
                        "VALUES (?,?,?,?,?,?) "
                        "ON CONFLICT (tenant_id) DO NOTHING",
                        (t.get("tenant_id"), t.get("name", ""),
                         t.get("domain", "blank"), t.get("description", ""),
                         t.get("created_at", ""), t.get("seed_asset_id")),
                    )
        except Exception:
            pass  # graph offline — start empty, exactly as before

    def _connect(self):
        return db.connect(_STORE, path=self.db_path)

    def _init_db(self):
        if self.db_path is None:
            db.schema.ensure(_STORE)
            return
        with self._connect() as conn:
            for stmt in db.schema.render(_STORE, db.SQLITE):
                conn.execute(stmt)

    # ---- registry CRUD ------------------------------------------------
    def _row_to_twin(self, row) -> Twin:
        return Twin(
            tenant_id=row["tenant_id"], name=row["name"], domain=row["domain"],
            description=row["description"], created_at=row["created_at"],
            seed_asset_id=row["seed_asset_id"],
        )

    def list(self) -> list[Twin]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM twins ORDER BY created_at DESC"
            ).fetchall()
            return [self._row_to_twin(r) for r in rows]

    def get(self, tenant_id: str) -> Optional[Twin]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM twins WHERE tenant_id = ?", (tenant_id,)
            ).fetchone()
            return self._row_to_twin(row) if row else None

    def _insert(self, twin: Twin) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO twins (tenant_id, name, domain, description, "
                "created_at, seed_asset_id) VALUES (?,?,?,?,?,?)",
                (twin.tenant_id, twin.name, twin.domain, twin.description,
                 twin.created_at, twin.seed_asset_id),
            )

    def _set_seed_asset(self, tenant_id: str, asset_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE twins SET seed_asset_id = ? WHERE tenant_id = ?",
                (asset_id, tenant_id),
            )

    def delete(self, tenant_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM twins WHERE tenant_id = ?", (tenant_id,)
            )
            deleted = cur.rowcount > 0
        if deleted:
            self._mirror_delete(tenant_id)
        return deleted

    # ---- creation + seeding ------------------------------------------
    def create(self, *, name: str, domain: str, writer, actor: str = "twin-factory",
               tenant_id: Optional[str] = None) -> Twin:
        """Register a twin and seed its initial graph through the Graph Writer.

        `writer` is a GraphWriter instance (injected so this package never
        imports the graph layer directly — keeps the dependency one-way)."""
        if domain not in TEMPLATES:
            raise ValueError(f"Unknown template '{domain}'. "
                             f"Known: {sorted(TEMPLATES)}")

        tenant_id = tenant_id or _slugify(name)
        if self.get(tenant_id) is not None:
            raise ValueError(f"Twin '{tenant_id}' already exists.")

        tpl = TEMPLATES[domain]
        twin = Twin(
            tenant_id=tenant_id, name=name, domain=domain,
            description=tpl["description"], created_at=_now_iso(),
            seed_asset_id=None,
        )
        self._insert(twin)

        # Seed the graph. If seeding fails (e.g. DB drops mid-create), roll back
        # the registry row so we never leave an empty orphan twin behind.
        try:
            seed_asset_id = self._seed(twin, writer, actor)
        except Exception:
            self.delete(tenant_id)
            raise

        if seed_asset_id:
            self._set_seed_asset(tenant_id, seed_asset_id)
            twin.seed_asset_id = seed_asset_id

        self._mirror_upsert(twin)
        return twin

    def _seed(self, twin: Twin, writer, actor: str) -> Optional[str]:
        """Seed the twin's initial entities. Returns the primary asset id (the
        one the feed should target), or None for a blank twin."""
        from graph.writer import Rel  # local import: one-way dependency

        if twin.domain == "blank":
            writer.create(
                tenant_id=twin.tenant_id, canonical_type=CORE + "Site",
                actor=actor, properties={"displayName": f"{twin.name} — Site"},
            )
            return None

        if twin.domain == "scanned-object":
            # A photo-reconstructed object: a root Site + the object as one
            # PhysicalAsset. The generated GLB is the twin's renderable model,
            # cached separately (bim_support scene cache) and shown by the viewer.
            writer.create(
                tenant_id=twin.tenant_id, canonical_type=CORE + "Site",
                actor=actor, properties={"displayName": f"{twin.name} — Scan"},
            )
            asset = writer.create(
                tenant_id=twin.tenant_id, canonical_type=CORE + "PhysicalAsset",
                actor=actor,
                properties={"displayName": twin.name, "status": "modelled"},
            )
            return asset.node_id if asset.ok else None

        tpl = TEMPLATES.get(twin.domain, {})
        if tpl.get("machine"):
            if twin.domain == "railway-metro":
                return self._seed_railway_metro(twin, writer, actor)
            if twin.domain == "railway-trainset":
                return self._seed_railway_trainset(twin, writer, actor)
            if twin.domain == "hospital-campus":
                return self._seed_hospital_campus(twin, writer, actor)
            if twin.domain == "ev-charging-network":
                return self._seed_charging_network(twin, writer, actor)
            if twin.domain == "ev-battery-pack":
                return self._seed_battery_pack(twin, writer, actor)
            if twin.domain == "defence-base":
                return self._seed_military_base(twin, writer, actor)
            if twin.domain == "defence-warship":
                return self._seed_warship(twin, writer, actor)
            return self._seed_machine(twin, writer, actor, tpl["class_iri"])

        if twin.domain == "generic-facility":
            return self._seed_generic_facility(twin, writer, actor)

        # hvac template: Site, Space, AirHandler (servesSpace).
        writer.create(
            tenant_id=twin.tenant_id, canonical_type=CORE + "Site",
            actor=actor, properties={"displayName": f"{twin.name} — Plant"},
        )
        space = writer.create(
            tenant_id=twin.tenant_id, canonical_type=CORE + "Space",
            actor=actor, properties={"displayName": "Server Room 1"},
        )
        ahu = writer.create(
            tenant_id=twin.tenant_id, canonical_type=HVAC + "AirHandler",
            actor=actor,
            properties={"displayName": "AHU-01", "status": "running",
                        "setpoint": 22.0},
            relationships=[Rel("hvac:servesSpace", space.node_id)] if space.ok else None,
        )
        return ahu.node_id if ahu.ok else None

    def _seed_machine(self, twin: Twin, writer, actor: str,
                      class_iri: str) -> Optional[str]:
        """Seed a machine-domain twin: a root Site + the single machine asset the
        live physics runtime targets. Returns the machine asset's node id."""
        writer.create(
            tenant_id=twin.tenant_id, canonical_type=CORE + "Site",
            actor=actor, properties={"displayName": f"{twin.name} — Facility"},
        )
        machine = writer.create(
            tenant_id=twin.tenant_id, canonical_type=class_iri, actor=actor,
            properties={"displayName": twin.name, "status": "running"},
        )
        return machine.node_id if machine.ok else None

    def _seed_generic_facility(self, twin: Twin, writer, actor: str) -> Optional[str]:
        """Seed a 3-floor generic facility with multi-system assets.
        Returns the UPS entity id (the primary asset the CFP feed targets)."""
        from graph.writer import Rel  # local import: one-way dependency
        t = twin.tenant_id

        # --- Spatial backbone ---
        building = writer.create(
            tenant_id=t, canonical_type=CFP + "Building", actor=actor,
            properties={"displayName": f"{twin.name} — Main Building",
                        "status": "active"},
        )
        floors = {}
        for idx, name in [(0, "Ground Floor"), (1, "First Floor"), (2, "Second Floor")]:
            f = writer.create(
                tenant_id=t, canonical_type=CFP + "Floor", actor=actor,
                properties={"displayName": name, "levelIndex": idx},
            )
            floors[idx] = f

        zone = writer.create(
            tenant_id=t, canonical_type=CFP + "Zone", actor=actor,
            properties={"displayName": "HVAC Zone A", "zoneType": "hvac"},
        )

        # --- HVAC ---
        ahu = writer.create(
            tenant_id=t, canonical_type=CFP + "AirHandlingUnit", actor=actor,
            properties={"displayName": "AHU-01", "status": "running",
                        "setpoint": 22.0},
            relationships=[Rel("cfp:suppliesAirTo", zone.node_id)] if zone.ok else None,
        )
        chiller = writer.create(
            tenant_id=t, canonical_type=CFP + "Chiller", actor=actor,
            properties={"displayName": "Chiller-01", "status": "running"},
            relationships=[Rel("nxr:feeds", ahu.node_id)] if ahu.ok else None,
        )
        air_filter = writer.create(
            tenant_id=t, canonical_type=CFP + "AirFilter", actor=actor,
            properties={"displayName": "Filter-AHU01", "status": "running"},
        )
        pump = writer.create(
            tenant_id=t, canonical_type=CFP + "Pump", actor=actor,
            properties={"displayName": "CHW Pump-01", "status": "running"},
        )

        # --- Power ---
        ups = writer.create(
            tenant_id=t, canonical_type=CFP + "UPS", actor=actor,
            properties={"displayName": "UPS-01", "status": "running"},
            relationships=[Rel("cfp:backsUp", chiller.node_id)] if chiller.ok else None,
        )
        transformer = writer.create(
            tenant_id=t, canonical_type=CFP + "Transformer", actor=actor,
            properties={"displayName": "TX-01", "status": "running"},
        )
        generator = writer.create(
            tenant_id=t, canonical_type=CFP + "Generator", actor=actor,
            properties={"displayName": "GenSet-01", "status": "off"},
            relationships=[Rel("cfp:backsUp", ups.node_id)] if ups.ok else None,
        )

        # --- Fire ---
        smoke_rels = []
        if zone.ok:
            smoke_rels.append(Rel("nxr:monitors", zone.node_id))
        smoke_rels.append(Rel("sosa:observes", "cfp:smokeObscuration", ontology_ref=True))
        smoke = writer.create(
            tenant_id=t, canonical_type=CFP + "SmokeDetector", actor=actor,
            properties={"displayName": "Smoke-GF-01"},
            relationships=smoke_rels,
        )
        facp = writer.create(
            tenant_id=t, canonical_type=CFP + "FireAlarmPanel", actor=actor,
            properties={"displayName": "FACP-01", "status": "running"},
            relationships=[Rel("cfp:controls", smoke.node_id)] if smoke.ok else None,
        )

        # --- Security ---
        door = writer.create(
            tenant_id=t, canonical_type=CFP + "AccessDoor", actor=actor,
            properties={"displayName": "Main Entry", "status": "running"},
        )

        # --- Water ---
        tank = writer.create(
            tenant_id=t, canonical_type=CFP + "WaterTank", actor=actor,
            properties={"displayName": "Fire Reserve Tank", "status": "running"},
        )

        # --- Network ---
        edge = writer.create(
            tenant_id=t, canonical_type=CFP + "EdgeNode", actor=actor,
            properties={"displayName": "Edge-01", "status": "running"},
        )

        return ups.node_id if ups.ok else None

    # ------------------------------------------------------------------
    #  Railway / metro seeds
    #
    #  These build the railway entity-relationship graph the ontology
    #  describes (P1-015 … P1-020). The physics + live network map run in
    #  the machine-twin runtime (railway/); the seed is the ONTOLOGICAL
    #  representation the platform reasons over (topology, findings targets,
    #  compliance). _seed_railway_metro composes the six named builders.
    # ------------------------------------------------------------------
    def _rail_new(self, t, writer, actor, cls, name, props=None, rels=None):
        """Create one railway node; returns its node id (or None)."""
        r = writer.create(
            tenant_id=t, canonical_type=RAIL + cls, actor=actor,
            properties={"displayName": name, **(props or {})},
            relationships=rels,
        )
        return r.node_id if r.ok else None

    def _rail_link(self, t, writer, actor, src, predicate, tgt):
        """Wire a railway relationship between two existing nodes."""
        if src and tgt:
            writer.relate(tenant_id=t, actor=actor, source_id=src,
                          predicate=predicate, target_id=tgt)

    def _seed_occ(self, t, writer, actor, twin_name):
        """P1-020 — the Operations Control Centre (top-level supervisory node)."""
        return self._rail_new(t, writer, actor, "OperationsControlCentre",
                              f"{twin_name} — OCC", {"status": "running"})

    def _seed_signalling(self, t, writer, actor):
        """P1-019 — a CBTC signalling system aggregating train-detection devices."""
        sig = self._rail_new(t, writer, actor, "SignalSystem",
                            "CBTC Signalling System", {"status": "running"})
        ac = self._rail_new(t, writer, actor, "AxleCounter", "Axle Counter AC-01")
        tc = self._rail_new(t, writer, actor, "TrackCircuit", "Track Circuit TC-01")
        self._rail_link(t, writer, actor, sig, "rail:hasDetector", ac)
        self._rail_link(t, writer, actor, sig, "rail:hasDetector", tc)
        return sig

    def _seed_depot(self, t, writer, actor, name="Main Depot", berths=8):
        """P1-017 — a stabling + maintenance depot."""
        return self._rail_new(t, writer, actor, "Depot", name,
                             {"status": "running", "berthCount": int(berths)})

    def _seed_rolling_stock(self, t, writer, actor, name, depot_id=None, cars=6):
        """P1-018 — a train set with bogies + traction motors, stabled at a depot."""
        from graph.writer import Rel  # local import: one-way dependency
        rels = [Rel("rail:stabledAt", depot_id)] if depot_id else None
        rs = self._rail_new(t, writer, actor, "RollingStock", name,
                           {"status": "in_service", "formationLength": int(cars)}, rels)
        for b in range(2):
            bogie = self._rail_new(t, writer, actor, "Bogie", f"{name} — Bogie {b + 1}")
            motor = self._rail_new(t, writer, actor, "TractionMotor",
                                 f"{name} — TM {b + 1}")
            self._rail_link(t, writer, actor, bogie, "rail:hasTractionMotor", motor)
            self._rail_link(t, writer, actor, rs, "rail:hasBogie", bogie)
        return rs

    def _seed_station(self, t, writer, actor, name, seq=0):
        """P1-016 — a station with a platform (+PSD), escalator and ACMV plant."""
        station = self._rail_new(t, writer, actor, "Station", name,
                               {"status": "open", "sequenceIndex": int(seq)})
        platform = self._rail_new(t, writer, actor, "Platform", f"{name} — Platform 1")
        psd = self._rail_new(t, writer, actor, "PSD", f"{name} — PSD Array")
        esc = self._rail_new(t, writer, actor, "Escalator", f"{name} — Escalator 1")
        acmv = self._rail_new(t, writer, actor, "ACMV", f"{name} — ACMV Plant")
        self._rail_link(t, writer, actor, platform, "rail:hasPSD", psd)
        self._rail_link(t, writer, actor, station, "rail:hasPlatform", platform)
        self._rail_link(t, writer, actor, station, "rail:hasEscalator", esc)
        self._rail_link(t, writer, actor, station, "rail:hasACMV", acmv)
        return station

    def _seed_line(self, t, writer, actor, name, station_ids):
        """P1-015 — a metro line calling at an ordered list of stations."""
        line = self._rail_new(t, writer, actor, "Line", name, {"status": "running"})
        for sid in station_ids:
            self._rail_link(t, writer, actor, line, "rail:servesStation", sid)
        return line

    def _seed_railway_metro(self, twin, writer, actor) -> Optional[str]:
        """P1-015…020 — the whole metro: network head node supervised by an OCC,
        3 lines over shared interchange stations, permanent way + traction power,
        signalling and a depot with rolling stock. Returns the RailNetwork id (the
        node the live findings flag)."""
        t = twin.tenant_id

        network = self._rail_new(t, writer, actor, "RailNetwork",
                               f"{twin.name} — Metro Network", {"status": "running"})
        occ = self._seed_occ(t, writer, actor, twin.name)
        signalsys = self._seed_signalling(t, writer, actor)
        depot = self._seed_depot(t, writer, actor, "Central Depot", berths=8)

        # Rolling stock stabled at the depot
        for i in range(2):
            self._seed_rolling_stock(t, writer, actor, f"Train Set {i + 1:02d}", depot)

        # Traction power: substations feeding energised third-rail sections
        for nm in ("TSS Riverside", "TSS Expo"):
            third = self._rail_new(t, writer, actor, "ThirdRail", f"{nm} — Third Rail",
                                 {"status": "energised", "nominalVoltage": 750.0})
            sub = self._rail_new(t, writer, actor, "TractionSubstation", nm,
                               {"status": "running"})
            self._rail_link(t, writer, actor, sub, "rail:feedsSection", third)
        self._rail_new(t, writer, actor, "Track", "Running Track (CWR)",
                     {"status": "in_service"})

        # Shared stations (interchanges reused across lines)
        stations = {}
        for seq, (sid, sname) in enumerate(
                [("CEN", "Central"), ("RIV", "Riverside"),
                 ("MKT", "Market"), ("EXPO", "Expo")]):
            stations[sid] = self._seed_station(t, writer, actor, sname, seq)

        # Lines over those stations, each operated by the network + supervised
        line_defs = [
            ("Line 1 · North–South", ["RIV", "CEN", "MKT"]),
            ("Line 2 · East–West", ["CEN", "EXPO"]),
            ("Line 3 · Circle", ["RIV", "EXPO", "MKT"]),
        ]
        for lname, stops in line_defs:
            line = self._seed_line(t, writer, actor, lname,
                                 [stations[s] for s in stops])
            self._rail_link(t, writer, actor, network, "rail:operatesLine", line)
            self._rail_link(t, writer, actor, occ, "rail:supervises", line)
            self._rail_link(t, writer, actor, signalsys, "rail:signalsLine", line)

        return network

    def _seed_railway_trainset(self, twin, writer, actor) -> Optional[str]:
        """P1-018 — a stand-alone rolling-stock twin: a depot + one train set with
        bogies and traction motors. Returns the RollingStock id."""
        t = twin.tenant_id
        writer.create(
            tenant_id=t, canonical_type=CORE + "Site", actor=actor,
            properties={"displayName": f"{twin.name} — Depot Site"},
        )
        depot = self._seed_depot(t, writer, actor, f"{twin.name} — Home Depot", berths=4)
        return self._seed_rolling_stock(t, writer, actor, twin.name, depot, cars=6)

    # ------------------------------------------------------------------
    #  Hospital campus seeds
    #
    #  Build the hospital entity-relationship graph the ontology describes
    #  (P3-017 … P3-022). The physics + live clinical views run in the
    #  machine-twin runtime (hospital/); the seed is the ONTOLOGICAL
    #  representation. _seed_hospital_campus composes the department builders.
    #  Reuses existing hsp:/cfp: classes (ICU, MedicalGasManifold,
    #  PatientMonitor, NurseCallSystem, AirHandlingUnit, UPS, Generator).
    # ------------------------------------------------------------------
    def _hsp_new(self, t, writer, actor, iri, name, props=None):
        r = writer.create(
            tenant_id=t, canonical_type=iri, actor=actor,
            properties={"displayName": name, **(props or {})},
        )
        return r.node_id if r.ok else None

    def _hsp_link(self, t, writer, actor, src, predicate, tgt):
        if src and tgt:
            writer.relate(tenant_id=t, actor=actor, source_id=src,
                          predicate=predicate, target_id=tgt)

    def _seed_operating_theatre(self, t, writer, actor, name):
        """P3-018 — an OR twin: AHU pressure, medical-gas pendant, anaesthetic
        machine (patient monitor) and an RTLS tag, pre-wired with hsp:* sensors."""
        theatre = self._hsp_new(t, writer, actor, HSP + "OperatingTheatre", name,
                              {"status": "active", "pressureSetpoint": 15.0})
        ahu = self._hsp_new(t, writer, actor, CFP + "AirHandlingUnit", f"{name} — AHU",
                          {"status": "running", "setpoint": 20.0})
        gas = self._hsp_new(t, writer, actor, HSP + "MedicalGasManifold", f"{name} — O2 Pendant",
                          {"status": "running", "gasType": "O2"})
        mon = self._hsp_new(t, writer, actor, HSP + "PatientMonitor", f"{name} — Anaesthetic Monitor")
        tag = self._hsp_new(t, writer, actor, HSP + "RTLSTag", f"{name} — RTLS Tag")
        self._hsp_link(t, writer, actor, ahu, "cfp:suppliesAirTo", theatre)
        self._hsp_link(t, writer, actor, tag, "hsp:tracks", mon)
        return theatre

    def _seed_icu(self, t, writer, actor, name="ICU", beds=8):
        """P3-019 — an ICU twin: bed-level ventilator + patient monitor, RTLS tags
        and a nurse-call system."""
        icu = self._hsp_new(t, writer, actor, HSP + "ICU", name,
                          {"status": "open", "bedCount": int(beds)})
        nurse = self._hsp_new(t, writer, actor, HSP + "NurseCallSystem", f"{name} — Nurse Call")
        self._hsp_link(t, writer, actor, icu, "nxr:hasPart", nurse)
        for i in range(min(beds, 4)):     # a representative subset of bays
            bed = self._hsp_new(t, writer, actor, HSP + "Bed", f"{name} — Bed {i + 1}",
                              {"status": "occupied"})
            vent = self._hsp_new(t, writer, actor, HSP + "Ventilator", f"{name} — Ventilator {i + 1}")
            mon = self._hsp_new(t, writer, actor, HSP + "PatientMonitor", f"{name} — Monitor {i + 1}")
            tag = self._hsp_new(t, writer, actor, HSP + "RTLSTag", f"{name} — Tag {i + 1}")
            self._hsp_link(t, writer, actor, icu, "hsp:hasBed", bed)
            self._hsp_link(t, writer, actor, bed, "nxr:hasPart", vent)
            self._hsp_link(t, writer, actor, tag, "hsp:tracks", bed)
            self._hsp_link(t, writer, actor, mon, "nxr:monitors", bed)
        return icu

    def _seed_pharmacy(self, t, writer, actor, name="Pharmacy"):
        """P3-020 — a pharmacy twin: cold storage, blood bank and a pneumatic-tube
        station."""
        pharm = self._hsp_new(t, writer, actor, HSP + "Pharmacy", name, {"status": "open"})
        cold = self._hsp_new(t, writer, actor, HSP + "ColdStorage", f"{name} — Cold Storage",
                           {"status": "running"})
        blood = self._hsp_new(t, writer, actor, HSP + "BloodBank", f"{name} — Blood Bank",
                            {"status": "running"})
        tube = self._hsp_new(t, writer, actor, HSP + "PneumaticTube", f"{name} — Tube Station")
        for a in (cold, blood, tube):
            self._hsp_link(t, writer, actor, pharm, "nxr:hasPart", a)
        return pharm

    def _seed_emergency_dept(self, t, writer, actor, name="Emergency Department", bays=6):
        """P3-021 — an ED twin: triage/resus bays with beds, monitors and imaging
        feeding the patient-flow model."""
        ed = self._hsp_new(t, writer, actor, HSP + "EmergencyDept", name, {"status": "open"})
        rad = self._hsp_new(t, writer, actor, HSP + "RadiologyRoom", f"{name} — Imaging",
                          {"status": "running"})
        self._hsp_link(t, writer, actor, ed, "nxr:hasPart", rad)
        for i in range(min(bays, 4)):
            bed = self._hsp_new(t, writer, actor, HSP + "Bed", f"{name} — Bay {i + 1}",
                              {"status": "occupied" if i < 2 else "available"})
            mon = self._hsp_new(t, writer, actor, HSP + "PatientMonitor", f"{name} — Monitor {i + 1}")
            self._hsp_link(t, writer, actor, ed, "hsp:hasBed", bed)
            self._hsp_link(t, writer, actor, mon, "nxr:monitors", bed)
        return ed

    def _seed_medical_gas(self, t, writer, actor):
        """P3-022 — the medical-gas system: O2 + N2O manifolds feeding pipeline
        pressure zones (the schematic + alarm bindings)."""
        o2 = self._hsp_new(t, writer, actor, HSP + "MedicalGasManifold", "O2 Manifold",
                         {"status": "running", "gasType": "O2"})
        n2o = self._hsp_new(t, writer, actor, HSP + "MedicalGasManifold", "N2O Manifold",
                          {"status": "running", "gasType": "N2O"})
        for zname, gas, manifold in [("Theatres Zone", "O2", o2), ("ICU Zone", "O2", o2),
                                     ("Emergency Zone", "O2", o2), ("Wards Zone", "O2", o2)]:
            zone = self._hsp_new(t, writer, actor, HSP + "MedicalGasZone", zname,
                               {"status": "running", "gasType": gas})
            self._hsp_link(t, writer, actor, manifold, "hsp:servesZone", zone)
        return o2

    def _seed_hospital_campus(self, twin, writer, actor) -> Optional[str]:
        """P3-017…022 — the whole campus: a Hospital head node with theatres, ICU,
        ED, pharmacy, wards, medical gas, water and power. Returns the Hospital id
        (the node the live findings flag)."""
        t = twin.tenant_id
        hospital = self._hsp_new(t, writer, actor, HSP + "Hospital",
                               f"{twin.name} — Campus", {"status": "running"})

        departments = [
            self._seed_operating_theatre(t, writer, actor, "Theatre 1"),
            self._seed_operating_theatre(t, writer, actor, "Theatre 2"),
            self._seed_icu(t, writer, actor, "ICU"),
            self._seed_emergency_dept(t, writer, actor, "Emergency Department"),
            self._seed_pharmacy(t, writer, actor, "Pharmacy"),
            self._hsp_new(t, writer, actor, HSP + "Laboratory", "Laboratory", {"status": "open"}),
        ]
        # Wards with beds
        for wname, n in [("Ward A", 4), ("Ward B", 4)]:
            ward = self._hsp_new(t, writer, actor, HSP + "Ward", wname,
                               {"status": "open", "bedCount": n})
            for i in range(n):
                bed = self._hsp_new(t, writer, actor, HSP + "Bed", f"{wname} — Bed {i + 1}",
                                  {"status": "occupied" if i < 3 else "available"})
                self._hsp_link(t, writer, actor, ward, "hsp:hasBed", bed)
            departments.append(ward)

        # Campus-wide infrastructure
        self._seed_medical_gas(t, writer, actor)
        self._hsp_new(t, writer, actor, HSP + "WaterSystem", "Domestic Water System",
                    {"status": "running"})
        self._hsp_new(t, writer, actor, HSP + "Autoclave", "CSSD Autoclave", {"status": "running"})
        self._hsp_new(t, writer, actor, CFP + "UPS", "Critical UPS", {"status": "running"})
        self._hsp_new(t, writer, actor, CFP + "Generator", "Standby Generator", {"status": "standby"})

        for dep in departments:
            self._hsp_link(t, writer, actor, hospital, "hsp:hasDepartment", dep)

        # Also commit a real, sector-organised 3-D BIM building (rooms + walls +
        # placed equipment) so the twin's 3-D viewer renders an actual hospital.
        self._seed_hospital_campus_building(twin, writer, actor)
        return hospital

    def _seed_hospital_campus_building(self, twin, writer, actor) -> None:
        """Commit a sector-organised, 2-floor BIM building for the hospital-campus
        twin: real room + wall geometry, equipment placed by specialty and stamped
        with asset-management metadata (manufacturer / warranty / condition), plus
        the infra spine + functional coupling + baked faults from enrich_domain.

        This makes graph_entities_to_bim_model() succeed for the tenant, so
        GET /twin/scene/{tenant} returns the real building instead of the generic
        office fallback. Additive alongside the ontological department graph and
        best-effort — a failure here never blocks twin creation."""
        try:
            from agents import hospital_layout
            from agents import bim_support as bs
        except Exception:
            return
        t = twin.tenant_id
        try:
            bm = hospital_layout.synthesize_hospital_campus_bim(floors=2, name=twin.name)
            entities, rels = bs.bim_model_to_drafts(bm, twin_name=twin.name)
        except Exception:
            return

        # Pass 1: create every node (properties only). None of these classes require
        # an outgoing relationship at creation, so mapping bim key -> real id first
        # lets pass 2 add every relationship with its target already present.
        key_to_id: dict[str, str] = {}
        for ent in entities:
            try:
                res = writer.create(
                    tenant_id=t, canonical_type=ent["canonical_type"], actor=actor,
                    properties=dict(ent.get("properties", {})))
            except Exception:
                continue
            if res.ok and ent.get("key"):
                key_to_id[ent["key"]] = res.node_id

        # Pass 2: apply the containment + functional-coupling relationships.
        for rel in rels:
            s = key_to_id.get(rel.get("source_key"))
            tgt = key_to_id.get(rel.get("target_key"))
            if s and tgt:
                try:
                    writer.relate(tenant_id=t, actor=actor, source_id=s,
                                  predicate=rel["predicate"], target_id=tgt)
                except Exception:
                    pass

        # Cache the renderable scene so the first 3-D load is instant and carries
        # entityIds (for live status colouring) and the furniture layer.
        try:
            scene = bs.bim_model_to_scene(bm, id_map=dict(key_to_id))
            scene["status"] = "ok"
            scene["twin_id"] = t
            bs.save_scene_cache(t, scene)
        except Exception:
            pass

    # ------------------------------------------------------------------
    #  EV / e-mobility seeds
    #
    #  Build the EV entity-relationship graph the ontology describes
    #  (P2-015…019). Physics + live views run in the machine-twin runtime
    #  (ev/). Reuses the _hsp_new/_hsp_link helpers (full-IRI create + relate).
    # ------------------------------------------------------------------
    def _seed_grid_node(self, t, writer, actor, name="Grid Node"):
        """P2-018 — a grid-connection node with a transformer and solar."""
        grid = self._hsp_new(t, writer, actor, EV + "GridConnection", name,
                           {"status": "connected", "nominalVoltage": 400.0})
        tx = self._hsp_new(t, writer, actor, EV + "Transformer", f"{name} — Transformer",
                         {"status": "running", "ratedPowerKW": 2500.0})
        self._hsp_link(t, writer, actor, tx, "nxr:feeds", grid)
        return grid

    def _seed_solar_farm(self, t, writer, actor, grid_id=None, name="Solar Array"):
        """P2-019 — a solar array with an inverter feeding the grid connection."""
        solar = self._hsp_new(t, writer, actor, EV + "SolarPanel", name,
                            {"status": "generating", "ratedPowerKW": 800.0})
        if grid_id:
            self._hsp_link(t, writer, actor, solar, "ev:feedsGrid", grid_id)
        return solar

    def _seed_ev_fleet(self, t, writer, actor, name="EV Fleet", vehicles=3):
        """P2-017 — an EV fleet aggregating electric vehicles (each with a pack)."""
        fleet = self._hsp_new(t, writer, actor, EV + "EVFleet", name, {"status": "active"})
        for i in range(vehicles):
            ev = self._hsp_new(t, writer, actor, EV + "ElectricVehicle", f"{name} — EV {i + 1}",
                             {"status": "idle", "stateOfChargePct": 65.0, "stateOfHealthPct": 92.0})
            pack = self._hsp_new(t, writer, actor, EV + "BatteryPack", f"{name} — EV {i + 1} Pack",
                               {"status": "ok", "stateOfHealthPct": 92.0, "moduleCount": 8})
            self._hsp_link(t, writer, actor, ev, "nxr:hasPart", pack)
            self._hsp_link(t, writer, actor, fleet, "ev:hasVehicle", ev)
        return fleet

    def _seed_battery_pack(self, twin, writer, actor) -> Optional[str]:
        """P2-016 — a battery pack of N modules of cells with a cooling loop.
        Returns the BatteryPack id (the node the live findings flag)."""
        t = twin.tenant_id
        writer.create(tenant_id=t, canonical_type=CORE + "Site", actor=actor,
                      properties={"displayName": f"{twin.name} — Lab"})
        pack = self._hsp_new(t, writer, actor, EV + "BatteryPack", twin.name,
                           {"status": "ok", "stateOfChargePct": 62.0,
                            "stateOfHealthPct": 93.0, "moduleCount": 8})
        cooling = self._hsp_new(t, writer, actor, EV + "CoolingSystem", f"{twin.name} — Coolant Loop",
                              {"status": "running"})
        self._hsp_link(t, writer, actor, cooling, "ev:cools", pack)
        for m in range(4):     # a representative subset of modules
            module = self._hsp_new(t, writer, actor, EV + "BatteryModule", f"{twin.name} — Module {m + 1}",
                                 {"status": "ok", "cellCount": 12})
            self._hsp_link(t, writer, actor, pack, "ev:hasModule", module)
            for k in range(3):
                cell = self._hsp_new(t, writer, actor, EV + "BatteryCell",
                                   f"{twin.name} — M{m + 1}C{k + 1}",
                                   {"status": "ok", "nominalVoltage": 3.7})
                self._hsp_link(t, writer, actor, module, "ev:hasCell", cell)
        return pack

    def _seed_charging_network(self, twin, writer, actor) -> Optional[str]:
        """P2-015 — a charging network: stations with chargers + connectors, a grid
        connection with a transformer, solar, and a small EV fleet. Returns the
        ChargingNetwork id."""
        t = twin.tenant_id
        network = self._hsp_new(t, writer, actor, EV + "ChargingNetwork",
                              f"{twin.name} — Network", {"status": "running"})
        grid = self._seed_grid_node(t, writer, actor, "Grid PCC")
        self._seed_solar_farm(t, writer, actor, grid, "Rooftop Solar")

        for sname, chargers, kw in [("City Hub", 2, 150.0), ("Airport", 2, 350.0),
                                    ("Riverside Mall", 2, 75.0), ("Highway Rapid", 2, 350.0)]:
            station = self._hsp_new(t, writer, actor, EV + "ChargingStation", sname, {"status": "online"})
            self._hsp_link(t, writer, actor, network, "ev:hasStation", station)
            self._hsp_link(t, writer, actor, station, "ev:poweredBy", grid)
            for c in range(chargers):
                charger = self._hsp_new(t, writer, actor, EV + "Charger", f"{sname} — Charger {c + 1}",
                                      {"status": "available", "ratedPowerKW": kw})
                connector = self._hsp_new(t, writer, actor, EV + "Connector", f"{sname} — CCS {c + 1}",
                                        {"status": "idle"})
                self._hsp_link(t, writer, actor, charger, "ev:hasConnector", connector)
                self._hsp_link(t, writer, actor, station, "ev:hasCharger", charger)

        self._seed_ev_fleet(t, writer, actor, "Depot Fleet", vehicles=3)
        return network

    # ------------------------------------------------------------------
    #  Defence seeds
    #
    #  Build the defence entity-relationship graph the ontology describes
    #  (P4-012…016). Physics + live views run in the machine-twin runtime
    #  (packs/defence/). Reuses the _hsp_new/_hsp_link helpers.
    # ------------------------------------------------------------------
    def _seed_command_center(self, t, writer, actor, name="Command Center"):
        """P4-016 — a C4ISR command centre aggregating asset feeds."""
        return self._hsp_new(t, writer, actor, DEF + "CommandCenter", name,
                           {"status": "operational", "classification": "UNCLASSIFIED"})

    def _seed_radar_station(self, t, writer, actor, name, arc=360.0):
        """P4-015 — a radar with a coverage arc + jamming status."""
        return self._hsp_new(t, writer, actor, DEF + "Radar", name,
                           {"status": "radiating", "coverageArcDeg": float(arc)})

    def _seed_aircraft(self, t, writer, actor, name, hours=42.0):
        """P4-014 — an aircraft with engine, avionics, weapons + flight-hour counter."""
        ac = self._hsp_new(t, writer, actor, DEF + "Aircraft", name,
                         {"status": "ready", "flightHours": float(hours),
                          "classification": "UNCLASSIFIED"})
        eng = self._hsp_new(t, writer, actor, DEF + "GasTurbine", f"{name} — Engine")
        wpn = self._hsp_new(t, writer, actor, DEF + "WeaponSystem", f"{name} — Weapons")
        self._hsp_link(t, writer, actor, ac, "def:hasEngine", eng)
        self._hsp_link(t, writer, actor, ac, "nxr:hasPart", wpn)
        return ac

    def _seed_warship(self, twin, writer, actor) -> Optional[str]:
        """P4-013 — a warship with a gas turbine, radar mast, weapons and watertight
        compartments (damage-control). Returns the Vessel id."""
        t = twin.tenant_id
        writer.create(tenant_id=t, canonical_type=CORE + "Site", actor=actor,
                      properties={"displayName": f"{twin.name} — Naval Port"})
        vessel = self._hsp_new(t, writer, actor, DEF + "Vessel", twin.name,
                             {"status": "underway", "classification": "UNCLASSIFIED"})
        gt = self._hsp_new(t, writer, actor, DEF + "GasTurbine", f"{twin.name} — GT Propulsion")
        radar = self._seed_radar_station(t, writer, actor, f"{twin.name} — Mast Radar", 360.0)
        wpn = self._hsp_new(t, writer, actor, DEF + "WeaponSystem", f"{twin.name} — CIWS")
        self._hsp_link(t, writer, actor, vessel, "def:hasEngine", gt)
        self._hsp_link(t, writer, actor, vessel, "def:hasRadar", radar)
        self._hsp_link(t, writer, actor, vessel, "nxr:hasPart", wpn)
        for cname in ("Fwd Store", "Magazine", "Aux Machinery", "Main Machinery",
                      "Shaft Alley", "Ops Room"):
            comp = self._hsp_new(t, writer, actor, DEF + "Compartment", f"{twin.name} — {cname}",
                               {"status": "dry"})
            self._hsp_link(t, writer, actor, vessel, "def:hasCompartment", comp)
        return vessel

    def _seed_military_base(self, twin, writer, actor) -> Optional[str]:
        """P4-012 — the whole base: perimeter + sectors, C4ISR, radar, hangars,
        runways, fuel + ammunition storage and NBC, with an air fleet. Returns the
        MilitaryBase id."""
        t = twin.tenant_id
        base = self._hsp_new(t, writer, actor, DEF + "MilitaryBase",
                           f"{twin.name} — Base", {"status": "operational",
                                                   "classification": "UNCLASSIFIED"})
        # perimeter + sectors
        perimeter = self._hsp_new(t, writer, actor, DEF + "Perimeter", "Base Perimeter",
                                {"status": "secure"})
        self._hsp_link(t, writer, actor, perimeter, "def:defends", base)
        for s in ("North", "East", "South", "West"):
            sector = self._hsp_new(t, writer, actor, DEF + "Sector", f"Sector {s}", {"status": "clear"})
            self._hsp_link(t, writer, actor, perimeter, "def:hasSector", sector)

        cc = self._seed_command_center(t, writer, actor, f"{twin.name} — C4ISR")
        assets = [cc, perimeter]
        radars = [self._seed_radar_station(t, writer, actor, "Search Radar", 360.0),
                  self._seed_radar_station(t, writer, actor, "Fire-Control Radar", 120.0)]
        assets += radars
        for cls, nm, props in [
            (DEF + "Hangar", "Aircraft Hangar", {"status": "active"}),
            (DEF + "Runway", "Main Runway", {"status": "open"}),
            (DEF + "FuelStorage", "Fuel Farm", {"status": "nominal"}),
            (DEF + "Ammunition", "Ammunition Store", {"status": "secure"}),
            (DEF + "NBCSystem", "NBC Detection", {"status": "monitoring"}),
            (DEF + "CommunicationNode", "SATCOM Node", {"status": "online"}),
        ]:
            assets.append(self._hsp_new(t, writer, actor, cls, nm, props))
        aircraft = [self._seed_aircraft(t, writer, actor, f"Falcon 0{i + 1}", 42.0 - 8.0 * i)
                    for i in range(2)]
        assets += aircraft

        for a in assets:
            self._hsp_link(t, writer, actor, base, "def:hasAsset", a)
        for a in radars + aircraft:
            self._hsp_link(t, writer, actor, cc, "def:aggregates", a)
        return base
