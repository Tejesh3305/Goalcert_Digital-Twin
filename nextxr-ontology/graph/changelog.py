"""
changelog.py — Append-only, hash-chained Change Log for the NextXR graph.

Every mutation (create, update, delete) produces a log entry. Each entry
stores the SHA-256 hash of the previous entry so the chain is tamper-evident:
if someone edits or deletes a past entry, verify_chain() will catch it.

Design choices for Day 2 (per sprint plan):
  - Entries stored as :ChangeLog nodes in Neo4j (same DB, same tenant wall).
  - Hash chain is per-tenant — each tenant has an independent chain.
  - WORM storage and Merkle-tree hardening are deferred to post-v1.
  - No entry may be updated or deleted through this module.

Usage:
    from graph.changelog import append_entry, get_entries, verify_chain
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone

from graph.connection import get_driver


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _new_id():
    try:
        return str(uuid.uuid7())
    except AttributeError:
        return str(uuid.uuid4())


def _compute_hash(prev_hash, tenant_id, entity_id, action, timestamp, payload_json):
    """
    SHA-256 of the concatenation of the key fields.
    This is what makes the chain tamper-evident — changing any field
    in any past entry will break all downstream hashes.
    """
    raw = f"{prev_hash}|{tenant_id}|{entity_id}|{action}|{timestamp}|{payload_json}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── writes ──────────────────────────────────────────────────────────

def append_entry(tenant_id, entity_id, entity_label, action, payload=None, actor="system"):
    """
    Append one entry to the tenant's change log.

    Parameters
    ----------
    tenant_id    : str   — which tenant
    entity_id    : str   — the id of the node that changed
    entity_label : str   — the Neo4j label (taxonomy category) of that node
    action       : str   — one of CREATE, UPDATE, DELETE
    payload      : dict  — snapshot or diff of the change (stored as JSON string)
    actor        : str   — who or what triggered this change

    Returns the full entry dict as stored.
    """
    if action not in ("CREATE", "UPDATE", "DELETE"):
        raise ValueError(f"action must be CREATE, UPDATE, or DELETE — got {action!r}")

    driver = get_driver()
    now = _now_iso()
    payload_json = json.dumps(payload or {}, sort_keys=True, default=str)

    with driver.session() as session:
        # Find the most recent entry for this tenant to get prevHash.
        # ORDER BY e.seq DESC gives us the latest one.
        prev = session.run(
            "MATCH (e:ChangeLog {tenantId: $tid}) "
            "RETURN e.hash AS hash, e.seq AS seq "
            "ORDER BY e.seq DESC LIMIT 1",
            tid=tenant_id,
        ).single()

        prev_hash = prev["hash"] if prev else "GENESIS"
        seq = (prev["seq"] + 1) if prev else 0

        entry_hash = _compute_hash(prev_hash, tenant_id, entity_id, action, now, payload_json)

        entry = {
            "id": _new_id(),
            "tenantId": tenant_id,
            "seq": seq,
            "entityId": entity_id,
            "entityLabel": entity_label,
            "action": action,
            "payload": payload_json,
            "actor": actor,
            "timestamp": now,
            "prevHash": prev_hash,
            "hash": entry_hash,
        }

        result = session.run(
            "CREATE (e:ChangeLog $props) RETURN properties(e) AS props",
            props=entry,
        )
        return dict(result.single()["props"])


# ── reads ───────────────────────────────────────────────────────────

def get_entries(tenant_id, entity_id=None, limit=100):
    """
    Fetch change log entries for a tenant, ordered oldest-first (by seq).

    If entity_id is given, only entries for that entity are returned.
    """
    driver = get_driver()
    with driver.session() as session:
        if entity_id:
            result = session.run(
                "MATCH (e:ChangeLog {tenantId: $tid, entityId: $eid}) "
                "RETURN properties(e) AS props ORDER BY e.seq ASC LIMIT $lim",
                tid=tenant_id, eid=entity_id, lim=limit,
            )
        else:
            result = session.run(
                "MATCH (e:ChangeLog {tenantId: $tid}) "
                "RETURN properties(e) AS props ORDER BY e.seq ASC LIMIT $lim",
                tid=tenant_id, lim=limit,
            )
        return [dict(r["props"]) for r in result]


def get_entry_count(tenant_id):
    """Return the number of change log entries for a tenant."""
    driver = get_driver()
    with driver.session() as session:
        result = session.run(
            "MATCH (e:ChangeLog {tenantId: $tid}) RETURN count(e) AS cnt",
            tid=tenant_id,
        )
        return result.single()["cnt"]


# ── verification ────────────────────────────────────────────────────

def verify_chain(tenant_id):
    """
    Walk the entire chain for a tenant and recompute every hash.

    Returns (ok: bool, details: str).
      - ok=True  means every entry's hash matches and prevHash links are correct.
      - ok=False means tampering or corruption was detected; details says where.
    """
    entries = get_entries(tenant_id, limit=100_000)
    if not entries:
        return True, "empty chain"

    for i, entry in enumerate(entries):
        expected_seq = i
        if entry["seq"] != expected_seq:
            return False, f"seq gap at position {i}: expected {expected_seq}, got {entry['seq']}"

        expected_prev = entries[i - 1]["hash"] if i > 0 else "GENESIS"
        if entry["prevHash"] != expected_prev:
            return False, (
                f"prevHash mismatch at seq {entry['seq']}: "
                f"expected {expected_prev[:16]}…, got {entry['prevHash'][:16]}…"
            )

        recomputed = _compute_hash(
            entry["prevHash"],
            entry["tenantId"],
            entry["entityId"],
            entry["action"],
            entry["timestamp"],
            entry["payload"],
        )
        if entry["hash"] != recomputed:
            return False, (
                f"hash mismatch at seq {entry['seq']}: "
                f"stored {entry['hash'][:16]}…, recomputed {recomputed[:16]}…"
            )

    return True, f"chain intact — {len(entries)} entries verified"
