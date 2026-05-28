"""
crud.py — Tenant-scoped create and read operations for the NextXR graph.

Every operation requires a tenant_id. There is no way to read or write
across tenants — that's the isolation contract.

This is a thin layer for Day 1. The Graph Writer (Day 3-4) will wrap
this with validate() + Change Log emit in a single transaction.
"""

import os
import time
import uuid
from datetime import datetime, timezone

from graph.connection import get_driver


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _uuid7():
    """
    Generate a time-ordered UUIDv7 per RFC 9562.

    Layout: 48-bit Unix-ms timestamp | version (7) | rand_a | variant | rand_b.
    The leading timestamp makes ids lexically/byte sortable by creation time,
    which is the "time-ordered" property the change-log and feed ordering rely on.
    """
    ms = int(time.time() * 1000)
    b = bytearray(os.urandom(16))
    # First 48 bits = big-endian millisecond timestamp.
    b[0:6] = ms.to_bytes(6, "big")
    # High nibble of byte 6 = version 7.
    b[6] = (b[6] & 0x0F) | 0x70
    # Top two bits of byte 8 = variant (10xx).
    b[8] = (b[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(b)))


def _new_id():
    """Generate a time-ordered UUIDv7 id.

    Uses the stdlib uuid.uuid7() on Python 3.14+, otherwise the RFC 9562
    implementation above. Either way the id is time-ordered — never a
    random v4, which would break created-time sortability.
    """
    native = getattr(uuid, "uuid7", None)
    return str(native()) if native else _uuid7()


def create_node(tenant_id, label, properties, created_by="system"):
    """
    Create a single node with the given Neo4j label and properties.

    Automatically stamps the 10 base properties. Any extra properties
    in the dict are set alongside them.

    Returns the full node properties dict as stored.
    """
    now = _now_iso()
    node_id = properties.get("id") or _new_id()
    canonical_type = properties.get("canonicalType", f"https://ontology.nextxr.io/v3/core#{label}")

    base = {
        "id": node_id,
        "tenantId": tenant_id,
        "canonicalType": canonical_type,
        "displayName": properties.get("displayName", ""),
        "createdAt": now,
        "updatedAt": now,
        "createdBy": created_by,
        "changeLogRef": properties.get("changeLogRef", ""),
        "status": properties.get("status", ""),
    }

    # Merge any extra properties the caller passed (excluding base keys
    # we already handled, and tags which is a list)
    extra_keys = set(properties.keys()) - set(base.keys()) - {"tags"}
    for k in extra_keys:
        base[k] = properties[k]

    # Tags stored as a list property
    base["tags"] = properties.get("tags", [])

    driver = get_driver()
    with driver.session() as session:
        result = session.run(
            f"CREATE (n:{label} $props) RETURN properties(n) AS props",
            props=base,
        )
        record = result.single()
        return dict(record["props"])


def read_node(tenant_id, node_id, label=None):
    """
    Read a single node by id, scoped to a tenant.

    If label is given, matches on that label too (faster with the index).
    Returns the node properties dict, or None if not found.
    """
    driver = get_driver()
    label_clause = f":{label}" if label else ""
    with driver.session() as session:
        result = session.run(
            f"MATCH (n{label_clause} {{tenantId: $tid, id: $nid}}) "
            f"RETURN properties(n) AS props LIMIT 1",
            tid=tenant_id,
            nid=node_id,
        )
        record = result.single()
        return dict(record["props"]) if record else None


def list_nodes(tenant_id, label, limit=50):
    """
    List nodes of a given label for a tenant, ordered by updatedAt desc.
    """
    driver = get_driver()
    with driver.session() as session:
        result = session.run(
            f"MATCH (n:{label} {{tenantId: $tid}}) "
            f"RETURN properties(n) AS props "
            f"ORDER BY n.updatedAt DESC LIMIT $lim",
            tid=tenant_id,
            lim=limit,
        )
        return [dict(r["props"]) for r in result]


def delete_node(tenant_id, node_id, label=None):
    """
    Delete a single node by id, scoped to a tenant.
    Returns True if a node was deleted, False otherwise.
    """
    driver = get_driver()
    label_clause = f":{label}" if label else ""
    with driver.session() as session:
        result = session.run(
            f"MATCH (n{label_clause} {{tenantId: $tid, id: $nid}}) "
            f"DETACH DELETE n RETURN count(n) AS deleted",
            tid=tenant_id,
            nid=node_id,
        )
        record = result.single()
        return record["deleted"] > 0
