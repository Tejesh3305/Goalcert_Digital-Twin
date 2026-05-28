"""
query_api.py — Read-only REST API for the NextXR graph.

Provides tenant-scoped endpoints for:
  - Listing entities by label
  - Getting a single entity + its relationships
  - Listing findings (optionally filtered by entity)
  - Change Log history for an entity
  - KPI stats (counts, latest findings)

All reads — no mutations. Mutations go through the Graph Writer only.

Usage (standalone):
    uvicorn server.query_api:router --reload --port 8001
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure parent packages are importable
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, HTTPException, Query
from graph.connection import get_driver
from graph.query import GraphQuery, LEGAL_LABELS
from changelog.service import ChangeLog

router = APIRouter(prefix="/api/v1", tags=["graph"])

_query = None
_changelog = None


def _get_query() -> GraphQuery:
    global _query
    if _query is None:
        _query = GraphQuery()
    return _query


def _get_changelog() -> ChangeLog:
    global _changelog
    if _changelog is None:
        _changelog = ChangeLog()
    return _changelog


# ── Entities ────────────────────────────────────────────────────────

@router.get("/entities")
def list_entities(tenant: str, label: str = "PhysicalAsset", limit: int = 100):
    """List entities of a given label for a tenant."""
    if label not in LEGAL_LABELS:
        raise HTTPException(status_code=400,
                            detail=f"Unknown label {label!r}. Legal: {sorted(LEGAL_LABELS)}")
    q = _get_query()
    nodes = q.list_by_label(tenant, label, limit=limit)
    return {"tenant": tenant, "label": label, "count": len(nodes), "nodes": nodes}


@router.get("/entities/{node_id}")
def get_entity(node_id: str, tenant: str):
    """Get a single entity by ID, with its outgoing relationships."""
    q = _get_query()
    node = q.get_node(tenant, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
    neighbors = q.neighbors(tenant, node_id)
    return {"node": node, "relationships": neighbors}


# ── Findings ────────────────────────────────────────────────────────

@router.get("/findings")
def list_findings(tenant: str, entity_id: str = None, limit: int = 50):
    """List Finding nodes, optionally filtered by the entity they flag."""
    q = _get_query()
    if entity_id:
        findings = q.get_findings(tenant, flagged_entity_id=entity_id)
    else:
        findings = q.get_findings(tenant)
    return {"tenant": tenant, "count": len(findings[:limit]), "findings": findings[:limit]}


# ── Change Log ──────────────────────────────────────────────────────

@router.get("/changelog/{entity_id}")
def entity_changelog(entity_id: str, tenant: str):
    """Get the Change Log history for a specific entity."""
    cl = _get_changelog()
    events = cl.list_for_entity(tenant, entity_id)
    return {
        "tenant": tenant,
        "entity_id": entity_id,
        "count": len(events),
        "events": [
            {
                "event_id": e.event_id,
                "action": e.action,
                "actor": e.actor,
                "ts": e.ts,
                "field_changes": e.field_changes,
            }
            for e in events
        ],
    }


@router.get("/changelog")
def tenant_changelog(tenant: str, limit: int = 50):
    """Get the latest Change Log events for a tenant."""
    cl = _get_changelog()
    events = cl.list_for_tenant(tenant)
    # Return most recent first
    recent = list(reversed(events[-limit:]))
    return {"tenant": tenant, "count": len(recent), "events": [
        {
            "event_id": e.event_id,
            "entity_id": e.entity_id,
            "action": e.action,
            "actor": e.actor,
            "ts": e.ts,
        }
        for e in recent
    ]}


# ── Stats / KPIs ───────────────────────────────────────────────────

@router.get("/stats")
def stats(tenant: str):
    """Quick KPI summary: entity counts by label, finding counts by severity."""
    q = _get_query()
    driver = get_driver()

    with driver.session() as s:
        # Count entities per label
        label_counts = {}
        for label in ["PhysicalAsset", "Location", "Finding", "Incident",
                      "Process", "Observation", "Capability", "Document",
                      "Actor", "MobileAsset"]:
            rec = s.run(
                f"MATCH (n:{label} {{tenantId:$t}}) RETURN count(n) AS c",
                t=tenant,
            ).single()
            cnt = rec["c"] if rec else 0
            if cnt > 0:
                label_counts[label] = cnt

        # Findings by severity
        severity_counts = {}
        recs = s.run(
            "MATCH (f:Finding {tenantId:$t}) RETURN f.severity AS sev, count(f) AS c",
            t=tenant,
        )
        for r in recs:
            if r["sev"]:
                severity_counts[r["sev"]] = r["c"]

        # Latest findings (top 5)
        latest_recs = s.run(
            "MATCH (f:Finding {tenantId:$t}) "
            "RETURN properties(f) AS p ORDER BY f.createdAt DESC LIMIT 5",
            t=tenant,
        )
        latest_findings = [dict(r["p"]) for r in latest_recs]

    cl = _get_changelog()
    event_count = cl.count(tenant)

    return {
        "tenant": tenant,
        "entity_counts": label_counts,
        "total_entities": sum(label_counts.values()),
        "finding_severity": severity_counts,
        "total_findings": sum(severity_counts.values()),
        "changelog_events": event_count,
        "latest_findings": latest_findings,
    }


# ── Health ──────────────────────────────────────────────────────────

@router.get("/health")
def health():
    """Basic health check — verifies Neo4j connectivity."""
    try:
        driver = get_driver()
        driver.verify_connectivity()
        return {"status": "healthy", "neo4j": "connected"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Neo4j unreachable: {e}")
