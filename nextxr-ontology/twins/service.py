"""
service.py — the Twin registry + seeding logic.

A Twin is one isolated platform instance, keyed by tenant_id. The registry
table (SQLite, same zero-dep pattern as the Change Log) holds only metadata:
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

import re
import sqlite3
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

CORE = "https://ontology.nextxr.io/v3/core#"
HVAC = "https://ontology.nextxr.io/v3/hvac#"
CFP  = "https://ontology.nextxr.io/v3/cfp#"

_DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "twins.db"

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
    "tram-network": {
        "label": "Tram Fleet Network",
        "description": "A tram fleet-network twin — live vehicles, per-route "
                       "status, traction power and service KPIs.",
        "primary_signal": "fleet:otp",
        "seeds_feed": False,
        "machine": True,
        "class_iri": "https://ontology.nextxr.io/v3/fleet#TramNetwork",
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


class TwinRegistry:
    """SQLite-backed registry of twins. Seeding goes through the Graph Writer."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else _DEFAULT_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS twins (
                    tenant_id     TEXT PRIMARY KEY,
                    name          TEXT NOT NULL,
                    domain        TEXT NOT NULL,
                    description   TEXT NOT NULL DEFAULT '',
                    created_at    TEXT NOT NULL,
                    seed_asset_id TEXT
                )
                """
            )

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
            return cur.rowcount > 0

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

        tpl = TEMPLATES.get(twin.domain, {})
        if tpl.get("machine"):
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
