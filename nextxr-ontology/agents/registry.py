"""
registry.py — the Capability Bundle registry.

A "bundle" packages a vertical: which ontology classes it provides, a set of
ready-made ENTITY TEMPLATES (so the MVP builds twins from templates rather than
free-text mapping — no Schema Mapper yet), and the Tier-C rule(s) it ships.

Two sources, unified behind one API:
  * built-in bundles (e.g. the HVAC pack already in the platform), and
  * PUBLISHED bundles authored by the Bundle Author meta-agent and persisted to
    SQLite. This is what closes the loop: the Composer can load the very bundle
    the Bundle Author just published.

API the Capability Composer uses:
    registry.query(domain)      -> [bundle summaries] matching a domain
    registry.load(bundle_id)    -> full bundle dict (templates + rules)
    registry.publish(bundle)    -> persist a Bundle-Author bundle (Publisher)
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional

CORE = "https://ontology.nextxr.io/v3/core#"
HVAC = "https://ontology.nextxr.io/v3/hvac#"

_DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "bundles.db"


# --------------------------------------------------------------------------
#  Built-in bundles — the platform ships HVAC; others are templates the demo
#  can use. Each template entity is {canonical_type, properties, key?} and a
#  relationship is {source_key, predicate, target_key}. `key` lets templates
#  reference each other before real UUIDs exist (the Graph Writer resolves them).
# --------------------------------------------------------------------------
BUILTIN_BUNDLES: dict[str, dict] = {
    "hvac-core": {
        "bundle_id": "hvac-core",
        "name": "HVAC Facility Pack",
        "domains": ["hvac", "cooling", "climate", "facility", "building"],
        "source": "builtin",
        "entity_templates": [
            {"key": "site", "canonical_type": CORE + "Site",
             "properties": {"displayName": "Facility"}},
            {"key": "space", "canonical_type": CORE + "Space",
             "properties": {"displayName": "Server Room 1"}},
            {"key": "ahu", "canonical_type": HVAC + "AirHandler",
             "properties": {"displayName": "AHU-01", "status": "running",
                            "setpoint": 22.0}},
        ],
        "relationship_templates": [
            {"source_key": "ahu", "predicate": "hvac:servesSpace", "target_key": "space"},
        ],
        "rules": [
            {"behavior_id": "hvac.temp_threshold", "tier": "C",
             "watches": "hvac:AirTemperature", "kind": "threshold",
             "offset_c": 3.0, "duration_minutes": 3.0,
             "description": "Critical Finding when air temp exceeds setpoint+3°C for ≥3 min."},
        ],
        "primary_signal": "hvac:AirTemperature",
    },
}


class BundleRegistry:
    """Unified query/load over built-in + published bundles."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else _DEFAULT_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS published_bundles (
                    bundle_id   TEXT PRIMARY KEY,
                    name        TEXT NOT NULL,
                    domains     TEXT NOT NULL,   -- json list
                    payload     TEXT NOT NULL,   -- full bundle json
                    tenant_id   TEXT,
                    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
                )"""
            )

    # ---- query / load -------------------------------------------------
    def _published(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT payload FROM published_bundles "
                                "ORDER BY created_at DESC").fetchall()
            return [json.loads(r["payload"]) for r in rows]

    def all_bundles(self) -> list[dict]:
        """Every bundle (published first, so a freshly-authored one wins)."""
        return self._published() + list(BUILTIN_BUNDLES.values())

    def query(self, domain: str) -> list[dict]:
        """Bundle summaries whose `domains` include (or fuzzy-match) the domain.
        Published bundles rank first so the closed loop picks the new one."""
        d = (domain or "").strip().lower()
        out = []
        for b in self.all_bundles():
            domains = [x.lower() for x in b.get("domains", [])]
            if d and (d in domains or any(d in x or x in d for x in domains)):
                out.append({"bundle_id": b["bundle_id"], "name": b["name"],
                            "domains": b.get("domains", []),
                            "source": b.get("source", "published"),
                            "rule_count": len(b.get("rules", [])),
                            "entity_count": len(b.get("entity_templates", []))})
        return out

    def load(self, bundle_id: str) -> Optional[dict]:
        """Full bundle by id (published store first, then built-ins)."""
        for b in self._published():
            if b["bundle_id"] == bundle_id:
                return b
        return BUILTIN_BUNDLES.get(bundle_id)

    # ---- publish (the Bundle Author's Publisher writes here) ----------
    def publish(self, bundle: dict, tenant_id: Optional[str] = None) -> str:
        """Persist a published bundle. Idempotent on bundle_id (upsert)."""
        bid = bundle["bundle_id"]
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO published_bundles (bundle_id, name, domains, payload, tenant_id) "
                "VALUES (?,?,?,?,?) ON CONFLICT(bundle_id) DO UPDATE SET "
                "name=excluded.name, domains=excluded.domains, payload=excluded.payload",
                (bid, bundle.get("name", bid),
                 json.dumps(bundle.get("domains", [])),
                 json.dumps(bundle), tenant_id),
            )
        return bid

    def list_published(self) -> list[dict]:
        return [{"bundle_id": b["bundle_id"], "name": b["name"],
                 "domains": b.get("domains", []), "rules": b.get("rules", [])}
                for b in self._published()]


_registry: Optional[BundleRegistry] = None
_lock = threading.Lock()


def get_registry() -> BundleRegistry:
    global _registry
    if _registry is None:
        with _lock:
            if _registry is None:
                _registry = BundleRegistry()
    return _registry
