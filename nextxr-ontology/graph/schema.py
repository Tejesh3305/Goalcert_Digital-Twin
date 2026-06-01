#!/usr/bin/env python3
"""
schema.py — Set up Neo4j constraints and indexes for the NextXR graph.

Reads the 10 taxonomy categories from the ontology TTL files (via
ontology_graph.py's canonical file list) so the graph schema always
matches the ontology. Then applies:

  1. One uniqueness constraint per category on (tenantId, id).
  2. An index on updatedAt per category (for feed queries).

Usage:
    cd nextxr-ontology
    python -m graph.schema            # apply schema to running Neo4j
    python -m graph.schema --check    # dry-run: print what would be created
"""

import argparse
import sys
from pathlib import Path

try:
    from rdflib import Graph as RDFGraph, Literal
except ImportError:
    sys.exit("pip install rdflib")

from graph.connection import get_driver, close_driver

ROOT = Path(__file__).resolve().parent.parent
NXR = "https://ontology.nextxr.io/v3/core#"
NXR_TAXONOMY = NXR + "taxonomyCategory"

# Use the same file list as ontology_graph.py (the single source of truth).
# Kept as a fallback list here so the graph layer can run independently,
# but kept in sync with tools/ontology_graph.py's PLATFORM_FILES + PACK_FILES.
CLASS_FILES = [
    "imports/bfo.ttl",
    "imports/sosa.ttl",
    "platform/nxr-classes.ttl",
    "platform/nxr-properties.ttl",
    "platform/nxr-base-shape.ttl",
    "platform/nxr-units.ttl",
    "platform/nxr-taxonomy.ttl",
    "platform/nxr-shapes.ttl",
    "packs/hvac/hvac-classes.ttl",
    "packs/hvac/hvac-shapes.ttl",
]


def discover_taxonomy_categories():
    """Parse the ontology and return the set of taxonomy category strings."""
    g = RDFGraph()
    for f in CLASS_FILES:
        path = ROOT / f
        if path.exists():
            g.parse(path, format="turtle")

    categories = set()
    from rdflib import URIRef
    tax_pred = URIRef(NXR_TAXONOMY)
    for _cls, _pred, cat in g.triples((None, tax_pred, None)):
        if isinstance(cat, Literal):
            categories.add(str(cat))
    return sorted(categories)


def apply_schema(dry_run=False, close=True):
    """Create uniqueness constraints and indexes for every taxonomy category.

    `close` controls whether the shared Neo4j driver is closed when done. The
    CLI (`python -m graph.schema`) wants it closed; in-process callers (the
    server, twin seeding) must pass close=False, otherwise they'd close the
    singleton driver out from under cached GraphWriter/GraphQuery instances
    ("Driver closed" errors)."""
    categories = discover_taxonomy_categories()
    print(f"Discovered {len(categories)} taxonomy categories from ontology:")
    for c in categories:
        print(f"  - {c}")
    print()

    if dry_run:
        print("[dry-run] Would create the following constraints + indexes:\n")
        for cat in categories:
            print(f"  CONSTRAINT uniq_{cat.lower()}_tenant_id ON (n:{cat})"
                  f" ASSERT (n.tenantId, n.id) IS UNIQUE")
            print(f"  INDEX idx_{cat.lower()}_updated_at FOR (n:{cat})"
                  f" ON (n.updatedAt)")
        return

    driver = get_driver()
    with driver.session() as session:
        for cat in categories:
            constraint_name = f"uniq_{cat.lower()}_tenant_id"
            index_name = f"idx_{cat.lower()}_updated_at"

            # Uniqueness constraint on (tenantId, id) per label
            try:
                session.run(
                    f"CREATE CONSTRAINT {constraint_name} IF NOT EXISTS "
                    f"FOR (n:{cat}) REQUIRE (n.tenantId, n.id) IS UNIQUE"
                )
                print(f"  [ok] constraint {constraint_name}")
            except Exception as e:
                print(f"  [!!] constraint {constraint_name}: {e}")

            # Index on updatedAt for feed/query performance
            try:
                session.run(
                    f"CREATE INDEX {index_name} IF NOT EXISTS "
                    f"FOR (n:{cat}) ON (n.updatedAt)"
                )
                print(f"  [ok] index {index_name}")
            except Exception as e:
                print(f"  [!!] index {index_name}: {e}")

        # ── ChangeLog label (Day 2) ──────────────────────────────────
        # Uniqueness on (tenantId, id) so no duplicate log entries.
        # Index on (tenantId, seq) for fast chain walks.
        # Index on (tenantId, entityId) for per-entity history queries.
        for name, cypher in [
            (
                "uniq_changelog_tenant_id",
                "CREATE CONSTRAINT uniq_changelog_tenant_id IF NOT EXISTS "
                "FOR (e:ChangeLog) REQUIRE (e.tenantId, e.id) IS UNIQUE",
            ),
            (
                "idx_changelog_tenant_seq",
                "CREATE INDEX idx_changelog_tenant_seq IF NOT EXISTS "
                "FOR (e:ChangeLog) ON (e.tenantId, e.seq)",
            ),
            (
                "idx_changelog_tenant_entity",
                "CREATE INDEX idx_changelog_tenant_entity IF NOT EXISTS "
                "FOR (e:ChangeLog) ON (e.tenantId, e.entityId)",
            ),
        ]:
            try:
                session.run(cypher)
                print(f"  [ok] {name}")
            except Exception as e:
                print(f"  [!!] {name}: {e}")

    print("\nSchema applied.")
    if close:
        close_driver()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="dry-run only")
    args = ap.parse_args()
    apply_schema(dry_run=args.check)
