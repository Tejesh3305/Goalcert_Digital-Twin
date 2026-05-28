"""
query.py — the Graph Query API (the READ path).

Thin, Cypher-backed, tenant-scoped reads for everything downstream: the
UI, the agents, and the behaviours. Reads BYPASS the Graph Writer — only
writes funnel through it. Every method requires a tenant_id; there is no
cross-tenant read.
"""

from __future__ import annotations

import re
from typing import Optional

from graph.connection import get_driver

# Closed set of legal Neo4j labels (taxonomy categories + ChangeLog).
# Any label not in this set is rejected before it reaches Cypher.
LEGAL_LABELS = frozenset({
    "Actor", "Capability", "Document", "Finding", "Incident",
    "Location", "MobileAsset", "Observation", "PhysicalAsset", "Process",
    "ChangeLog",
})

# Legal relationship types (UPPER_SNAKE from predicate IRIs).
_SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_label(label: str) -> str:
    """Reject labels not in the closed taxonomy set to prevent Cypher injection."""
    if label not in LEGAL_LABELS:
        raise ValueError(f"Unknown label {label!r}. Legal: {sorted(LEGAL_LABELS)}")
    return label


def _validate_rel_type(rel_type: str) -> str:
    """Reject relationship types that aren't safe identifiers."""
    if not _SAFE_NAME.match(rel_type):
        raise ValueError(f"Invalid relationship type {rel_type!r}")
    return rel_type


class GraphQuery:
    def __init__(self):
        self.driver = get_driver()

    def get_node(self, tenant_id: str, node_id: str) -> Optional[dict]:
        with self.driver.session() as s:
            rec = s.run(
                "MATCH (n {tenantId:$t, id:$i}) RETURN properties(n) AS p LIMIT 1",
                t=tenant_id, i=node_id,
            ).single()
            return dict(rec["p"]) if rec else None

    def get_property(self, tenant_id: str, node_id: str, key: str,
                     default=None):
        node = self.get_node(tenant_id, node_id)
        if not node:
            return default
        return node.get(key, default)

    def list_by_label(self, tenant_id: str, label: str, limit: int = 100):
        _validate_label(label)
        with self.driver.session() as s:
            recs = s.run(
                f"MATCH (n:{label} {{tenantId:$t}}) RETURN properties(n) AS p "
                f"ORDER BY n.updatedAt DESC LIMIT $lim",
                t=tenant_id, lim=limit,
            )
            return [dict(r["p"]) for r in recs]

    def neighbors(self, tenant_id: str, node_id: str,
                  rel_type: Optional[str] = None):
        rel = f":{_validate_rel_type(rel_type)}" if rel_type else ""
        with self.driver.session() as s:
            recs = s.run(
                f"MATCH (n {{tenantId:$t, id:$i}})-[r{rel}]->(m) "
                f"RETURN type(r) AS rel, properties(m) AS p",
                t=tenant_id, i=node_id,
            )
            return [{"rel": r["rel"], "node": dict(r["p"])} for r in recs]

    def get_findings(self, tenant_id: str, flagged_entity_id: Optional[str] = None):
        """List Finding nodes, optionally only those flagging a given entity."""
        with self.driver.session() as s:
            if flagged_entity_id:
                recs = s.run(
                    "MATCH (f:Finding {tenantId:$t})-[:FLAGS]->(e {id:$e}) "
                    "RETURN properties(f) AS p ORDER BY f.createdAt DESC",
                    t=tenant_id, e=flagged_entity_id,
                )
            else:
                recs = s.run(
                    "MATCH (f:Finding {tenantId:$t}) "
                    "RETURN properties(f) AS p ORDER BY f.createdAt DESC",
                    t=tenant_id,
                )
            return [dict(r["p"]) for r in recs]
