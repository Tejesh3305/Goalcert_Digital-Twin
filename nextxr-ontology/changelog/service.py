"""
service.py — the Change Log service.

Contract (from the LLD event schema):
  * event_id     : ULID (time-ordered, lexically sortable)
  * tenant_id    : isolation key — chains are PER TENANT
  * entity_id    : the node this event is about
  * entity_type  : its canonical class IRI
  * actor        : who/what caused the mutation
  * action       : "create" | "update" | "delete"
  * field_changes: { field: {"old": ..., "new": ...} }
  * ts           : ISO-8601 UTC
  * prev_event_hash : wm_hash of the previous event in this tenant's chain
  * wm_hash      : sha256 over the canonical content (incl. prev_event_hash)

The prev_event_hash -> wm_hash linkage is the tamper-evident chain: change
any stored field of an old event and every wm_hash after it stops matching.

Storage for the backbone is the shared relational store (db/) — RDS Postgres in
production, SQLite locally. The WORM/S3-object-lock ledger and Merkle
summarisation from the LLD are hardening layered on top later; this is the
spine they wrap.

CONCURRENCY. Appending reads the tenant's last wm_hash and then inserts the next
event. Two writers interleaving there would produce two events claiming the same
predecessor and break verify_chain(). On one SQLite file that could not happen —
the file lock serialised everything. On Postgres, with the API running several
ECS tasks, it can, so append() takes a per-tenant advisory lock for the length of
its transaction. Chains stay per-tenant, so tenants never block each other.
"""

from __future__ import annotations

import db

import hashlib
import json
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

GENESIS_HASH = "0" * 64  # prev_event_hash of the first event in any chain

_STORE = "changelog"

# Crockford base32 alphabet (ULID spec).
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid() -> str:
    """Generate a 26-char ULID: 48-bit millisecond timestamp + 80-bit
    randomness, Crockford base32. Time-ordered and lexically sortable, which
    is exactly the property changeLogRef relies on for history traversal."""
    ts = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10), "big")  # 80 bits
    val = (ts << 80) | rand                       # 128-bit value
    out = []
    for _ in range(26):
        out.append(_CROCKFORD[val & 0x1F])
        val >>= 5
    return "".join(reversed(out))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Event:
    """One Change Log event, exactly as persisted."""
    event_id: str
    tenant_id: str
    entity_id: str
    entity_type: str
    actor: str
    action: str
    field_changes: dict
    ts: str
    prev_event_hash: str
    wm_hash: str

    def content_dict(self) -> dict:
        """The canonical payload that gets hashed — everything except wm_hash
        itself. prev_event_hash IS included, which is what links the chain."""
        return {
            "event_id": self.event_id,
            "tenant_id": self.tenant_id,
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "actor": self.actor,
            "action": self.action,
            "field_changes": self.field_changes,
            "ts": self.ts,
            "prev_event_hash": self.prev_event_hash,
        }


def _hash_content(content: dict) -> str:
    """Deterministic sha256 of a content dict (sorted keys, compact)."""
    blob = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class ChangeLog:
    """Append-only, per-tenant hash-chained event store."""

    def __init__(self, db_path: Optional[Path] = None):
        """`db_path` forces a private SQLite file even when Postgres is
        configured. Only offline tools that want a reproducible, throwaway
        ledger should pass it; the service always uses the shared store."""
        self.db_path = Path(db_path) if db_path else None
        self._init_db()

    # ---- schema -------------------------------------------------------
    def _connect(self):
        return db.connect(_STORE, path=self.db_path)

    def _init_db(self):
        if self.db_path is None:
            db.schema.ensure(_STORE)
            return
        # Private file: provision it directly, bypassing the shared-store cache.
        with self._connect() as conn:
            for stmt in db.schema.render(_STORE, db.SQLITE):
                conn.execute(stmt)

    # ---- append -------------------------------------------------------
    def _last_hash(self, conn, tenant_id: str) -> str:
        row = conn.execute(
            "SELECT wm_hash FROM events WHERE tenant_id = ? "
            "ORDER BY seq DESC LIMIT 1",
            (tenant_id,),
        ).fetchone()
        return row["wm_hash"] if row else GENESIS_HASH

    def append(self, *, tenant_id: str, entity_id: str, entity_type: str,
               actor: str, action: str, field_changes: dict) -> Event:
        """Append one event to the tenant's chain and return it (with its
        ULID event_id and computed wm_hash). This is the ONLY write method."""
        with self._connect() as conn:
            # Hold the tenant's chain for this transaction: read-last-hash and
            # insert must be atomic across every task, or two concurrent writes
            # both claim the same predecessor and verify_chain() fails forever.
            conn.lock(f"changelog:{tenant_id}")
            prev_hash = self._last_hash(conn, tenant_id)
            ev = Event(
                event_id=ulid(),
                tenant_id=tenant_id,
                entity_id=entity_id,
                entity_type=entity_type,
                actor=actor,
                action=action,
                field_changes=field_changes,
                ts=_now_iso(),
                prev_event_hash=prev_hash,
                wm_hash="",  # filled next
            )
            ev.wm_hash = _hash_content(ev.content_dict())
            conn.execute(
                "INSERT INTO events (event_id, tenant_id, entity_id, "
                "entity_type, actor, action, field_changes, ts, "
                "prev_event_hash, wm_hash) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ev.event_id, ev.tenant_id, ev.entity_id, ev.entity_type,
                 ev.actor, ev.action, db.Json(ev.field_changes),
                 ev.ts, ev.prev_event_hash, ev.wm_hash),
            )
        return ev

    # ---- read ---------------------------------------------------------
    def _row_to_event(self, row) -> Event:
        return Event(
            event_id=row["event_id"],
            tenant_id=row["tenant_id"],
            entity_id=row["entity_id"],
            entity_type=row["entity_type"],
            actor=row["actor"],
            action=row["action"],
            field_changes=db.json_load(row["field_changes"]),
            ts=row["ts"],
            prev_event_hash=row["prev_event_hash"],
            wm_hash=row["wm_hash"],
        )

    def get(self, event_id: str) -> Optional[Event]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM events WHERE event_id = ?", (event_id,)
            ).fetchone()
            return self._row_to_event(row) if row else None

    def list_for_entity(self, tenant_id: str, entity_id: str) -> list[Event]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE tenant_id = ? AND entity_id = ? "
                "ORDER BY seq ASC",
                (tenant_id, entity_id),
            ).fetchall()
            return [self._row_to_event(r) for r in rows]

    def list_for_tenant(self, tenant_id: str) -> list[Event]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE tenant_id = ? ORDER BY seq ASC",
                (tenant_id,),
            ).fetchall()
            return [self._row_to_event(r) for r in rows]

    # ---- verify -------------------------------------------------------
    def verify_chain(self, tenant_id: str) -> tuple[bool, Optional[str]]:
        """Walk the tenant's chain from genesis. Returns (ok, first_bad).
        first_bad is the event_id where verification failed, or None if the
        whole chain is intact. Two checks per event:
          1. its prev_event_hash equals the previous event's wm_hash, and
          2. recomputing its content hash reproduces its stored wm_hash.
        """
        expected_prev = GENESIS_HASH
        for ev in self.list_for_tenant(tenant_id):
            if ev.prev_event_hash != expected_prev:
                return (False, ev.event_id)            # broken linkage
            if _hash_content(ev.content_dict()) != ev.wm_hash:
                return (False, ev.event_id)            # altered content
            expected_prev = ev.wm_hash
        return (True, None)

    # ---- maintenance --------------------------------------------------
    def purge_tenant(self, tenant_id: str) -> int:
        """Drop a tenant's whole chain and return how many events went.

        The ledger is append-only by contract, so this exists for exactly two
        callers: the exit-test gates, which need a chain that starts at genesis
        on every run, and deleting a twin. It used to be done by deleting the
        SQLite file — impossible once every tenant shares one Postgres table.
        """
        with self._connect() as conn:
            conn.lock(f"changelog:{tenant_id}")
            cur = conn.execute("DELETE FROM events WHERE tenant_id = ?",
                               (tenant_id,))
            return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    def count(self, tenant_id: Optional[str] = None) -> int:
        with self._connect() as conn:
            if tenant_id:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM events WHERE tenant_id = ?",
                    (tenant_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM events"
                ).fetchone()
            return row["n"]
