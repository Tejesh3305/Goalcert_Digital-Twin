"""
writer.py — THE GRAPH WRITER. The single, non-negotiable write path.

Its contract is fixed (from the architecture doc):

    receive a mutation request
      -> resolve the Neo4j label from the ontology (taxonomyCategory)
      -> call ontology validate()                     [tools/gate.validate]
      -> if INVALID: reject. Nothing touches the graph. No Change Log event.
      -> if VALID:   write to Neo4j in a transaction
      -> emit a Change Log event                       [changelog.ChangeLog]
      -> stamp changeLogRef on the node
      -> return the result

Everything else in the system — adapters, agents, behaviours, the simulated
feed — calls THIS. They never issue Cypher mutations directly. (graph/crud.py
remains only as the Day-1 low-level read helper; mutations go through here.)

Every method takes a tenant_id. There is no tenant-less write path.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Make the ontology gate (tools/) importable regardless of how we're launched.
_TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import gate  # noqa: E402  — tools/gate.py: the single validate() function
from rdflib import URIRef, RDF  # noqa: E402
from rdflib.namespace import Namespace  # noqa: E402

from graph.connection import get_driver  # noqa: E402
from changelog.service import ChangeLog  # noqa: E402
from graph.state_machine import validate_transition  # noqa: E402
from bus import BusEvent, get_event_bus  # noqa: E402

NXR = "https://ontology.nextxr.io/v3/core#"
TAXONOMY_PRED = URIRef(NXR + "taxonomyCategory")
SUBCLASS = URIRef("http://www.w3.org/2000/01/rdf-schema#subClassOf")

# CURIE prefixes the writer understands for predicates/relationships.
PREFIXES = {
    "nxr": NXR,
    "cfp": "https://ontology.nextxr.io/v3/cfp#",
    "hvac": "https://ontology.nextxr.io/v3/hvac#",
    "office": "https://ontology.nextxr.io/v3/office#",
    "sosa": "http://www.w3.org/ns/sosa/",
}

# The 10 base property keys the writer stamps / manages itself.
_BASE_KEYS = {
    "id", "tenantId", "canonicalType", "displayName", "createdAt",
    "updatedAt", "createdBy", "changeLogRef", "tags", "status",
}


# --------------------------------------------------------------------------
#  Request / result types
# --------------------------------------------------------------------------
@dataclass
class Rel:
    """An outgoing relationship to an existing node in the same tenant.
    predicate is a CURIE ('nxr:flags', 'hvac:servesSpace') or a full IRI.
    If ontology_ref=True, target_id is an ontology IRI (e.g. a sosa:ObservableProperty)
    rather than a graph entity — skips Neo4j referential integrity and renders
    the raw IRI in validation TTL. Not persisted as a Neo4j edge."""
    predicate: str
    target_id: str
    ontology_ref: bool = False


@dataclass
class WriteResult:
    """Outcome of a mutation. Truthy iff the write was committed."""
    ok: bool
    node_id: Optional[str] = None
    label: Optional[str] = None
    canonical_type: Optional[str] = None
    event_id: Optional[str] = None
    violations: list = field(default_factory=list)
    error: Optional[str] = None

    def __bool__(self) -> bool:
        return self.ok


# --------------------------------------------------------------------------
#  Small helpers
# --------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_uuid7() -> str:
    from graph.crud import _new_id  # reuse the time-ordered UUIDv7 generator
    return _new_id()


def _camel_to_upper_snake(name: str) -> str:
    out = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0 and not name[i - 1].isupper():
            out.append("_")
        out.append(ch.upper())
    return "".join(out)


def _resolve_predicate(curie_or_iri: str) -> tuple[str, str]:
    """Return (full_iri, neo4j_rel_type) for a predicate CURIE or IRI."""
    if curie_or_iri.startswith("http"):
        iri = curie_or_iri
        local = iri.split("#")[-1].split("/")[-1]
    else:
        pfx, local = curie_or_iri.split(":", 1)
        if pfx not in PREFIXES:
            raise ValueError(f"Unknown predicate prefix '{pfx}:'")
        iri = PREFIXES[pfx] + local
    return iri, _camel_to_upper_snake(local)


def _ttl_literal(value) -> str:
    """Render a Python value as a typed Turtle literal."""
    if isinstance(value, bool):
        return f'"{str(value).lower()}"^^xsd:boolean'
    if isinstance(value, float):
        return f'"{value}"^^xsd:double'
    if isinstance(value, int):
        return f'"{value}"^^xsd:integer'
    s = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def _subject_iri(node_id: str) -> str:
    return f"<urn:nxr:{node_id}>"


# --------------------------------------------------------------------------
#  The writer
# --------------------------------------------------------------------------
class GraphWriter:
    """The one component allowed to mutate the graph."""

    def __init__(self, changelog: Optional[ChangeLog] = None, bus=None):
        self.changelog = changelog or ChangeLog()
        # The live fan-out bus. Defaults to the process-wide singleton (Redis if
        # reachable, else in-memory). Publishing is best-effort and happens AFTER
        # the Change Log append — the bus can never affect a write.
        self.bus = bus if bus is not None else get_event_bus()
        self._label_cache: dict[str, Optional[str]] = {}

    @property
    def driver(self):
        """Always resolve the live singleton driver. Never cache it at init —
        the shared driver can be closed/recreated (e.g. when schema is applied),
        and a stale reference would raise 'Driver closed'."""
        return get_driver()

    # ---- event-bus fan-out -------------------------------------------
    def _publish_event(self, *, event_id, tenant_id, entity_id, entity_type,
                       label, action, actor, ts, field_changes=None) -> None:
        """Publish one committed mutation to the event bus. BEST-EFFORT: this
        mirrors the Change Log event the write just produced, and must never
        raise or affect the write. The Change Log remains the durable record."""
        try:
            self.bus.publish(BusEvent(
                event_id=event_id, tenant_id=tenant_id, entity_id=entity_id,
                entity_type=entity_type, label=label, action=action,
                actor=actor, ts=ts, field_changes=field_changes,
            ))
        except Exception:
            # Defensive: bus implementations already swallow their own errors,
            # but the write path must be bulletproof regardless.
            pass

    # ---- ontology label resolution -----------------------------------
    def resolve_label(self, canonical_type: str) -> Optional[str]:
        """Look up the closed-taxonomy category (= Neo4j label) for a class
        IRI, walking up rdfs:subClassOf if the class doesn't declare its own.
        Returns None for an unknown / ungoverned type — which the writer
        treats as a rejection (you cannot persist an entity the ontology
        doesn't recognise)."""
        if canonical_type in self._label_cache:
            return self._label_cache[canonical_type]

        g = gate.ontology_graph()
        cls = URIRef(canonical_type)
        seen = set()
        frontier = [cls]
        label = None
        while frontier:
            cur = frontier.pop()
            if cur in seen:
                continue
            seen.add(cur)
            cat = g.value(cur, TAXONOMY_PRED)
            if cat is not None:
                label = str(cat)
                break
            frontier.extend(g.objects(cur, SUBCLASS))
        self._label_cache[canonical_type] = label
        return label

    # ---- validation turtle assembly ----------------------------------
    def _render_node_ttl(self, node_id, canonical_type, props, rels) -> str:
        """Build the Turtle subgraph handed to validate(): JUST this node's
        full intended state (base props + extras + outgoing relationships).

        Referenced target nodes are deliberately NOT included as typed nodes:
        each was already validated when it was created, and asserting a bare
        `<target> a SomeClass` here would make SHACL re-validate it against the
        base shape and fail (it has no properties in this subgraph). Target
        existence is enforced separately as a referential-integrity check
        against Neo4j; relationship cardinality is enforced here."""
        subj = _subject_iri(node_id)
        lines = [
            "@prefix nxr:  <https://ontology.nextxr.io/v3/core#> .",
            "@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .",
            "",
            f"{subj} a <{canonical_type}> ;",
        ]
        body = []
        # The 10 base properties + any extra datatype props.
        for key, val in props.items():
            if val is None or val == "" or (isinstance(val, list) and not val):
                continue
            if key == "canonicalType":
                body.append(f'    nxr:canonicalType "{val}"^^xsd:anyURI')
            elif key in ("createdAt", "updatedAt"):
                body.append(f'    nxr:{key} "{val}"^^xsd:dateTime')
            elif key == "tags":
                for t in (val if isinstance(val, list) else [val]):
                    body.append(f'    nxr:tags {_ttl_literal(t)}')
            else:
                body.append(f"    nxr:{key} {_ttl_literal(val)}")
        # Outgoing relationships (cardinality is validated; range existence is
        # checked separately against the graph).
        for r in rels:
            iri, _ = _resolve_predicate(r.predicate)
            if getattr(r, "ontology_ref", False):
                # Target is an ontology IRI, not a graph entity — render raw.
                target_iri = r.target_id
                if not target_iri.startswith("http"):
                    pfx, local = target_iri.split(":", 1)
                    target_iri = PREFIXES.get(pfx, pfx + ":") + local
                body.append(f"    <{iri}> <{target_iri}>")
            else:
                body.append(f"    <{iri}> {_subject_iri(r.target_id)}")
        lines.append(" ;\n".join(body) + " .")
        return "\n".join(lines)

    # ---- neo4j helpers ------------------------------------------------
    def _fetch_node(self, tenant_id, node_id):
        with self.driver.session() as s:
            rec = s.run(
                "MATCH (n {tenantId:$t, id:$i}) RETURN properties(n) AS p, "
                "labels(n) AS labels LIMIT 1",
                t=tenant_id, i=node_id,
            ).single()
            if not rec:
                return None
            return {"props": dict(rec["p"]), "labels": list(rec["labels"])}

    def _fetch_outgoing(self, tenant_id, node_id):
        """Return existing outgoing rels as [Rel(predicateIri, target_id)]."""
        with self.driver.session() as s:
            recs = s.run(
                "MATCH (n {tenantId:$t, id:$i})-[r]->(m) "
                "RETURN r.predicateIri AS pred, m.id AS target",
                t=tenant_id, i=node_id,
            )
            out = []
            for rec in recs:
                if rec["pred"] and rec["target"]:
                    out.append(Rel(predicate=rec["pred"], target_id=rec["target"]))
            return out

    def _target_canonical_types(self, tenant_id, rels) -> dict:
        """Map each rel target_id -> its stored canonicalType. Missing targets
        map to None (caller rejects on referential-integrity grounds)."""
        out = {}
        with self.driver.session() as s:
            for r in rels:
                rec = s.run(
                    "MATCH (m {tenantId:$t, id:$i}) RETURN m.canonicalType AS c "
                    "LIMIT 1",
                    t=tenant_id, i=r.target_id,
                ).single()
                out[r.target_id] = rec["c"] if rec else None
        return out

    # ---- the single write path ---------------------------------------
    def create(self, *, tenant_id: str, canonical_type: str, actor: str,
               properties: Optional[dict] = None,
               relationships: Optional[list] = None,
               node_id: Optional[str] = None) -> WriteResult:
        """Create one node. validate -> commit -> emit -> stamp, or reject."""
        properties = dict(properties or {})
        rels = list(relationships or [])

        label = self.resolve_label(canonical_type)
        if label is None:
            return WriteResult(ok=False, canonical_type=canonical_type,
                               error=f"Unknown/ungoverned type: {canonical_type}")

        node_id = node_id or _new_uuid7()
        now = _now_iso()
        props = {
            "id": node_id,
            "tenantId": tenant_id,
            "canonicalType": canonical_type,
            "createdAt": now,
            "updatedAt": now,
            "createdBy": actor,
        }
        # caller-supplied extras (displayName, status, setpoint, severity, ...)
        for k, v in properties.items():
            if k not in ("id", "tenantId", "canonicalType", "createdAt",
                         "createdBy"):
                props[k] = v

        # Split ontology-ref rels from graph rels. Ontology refs (e.g.
        # sosa:observes pointing at an ObservableProperty IRI) are included in
        # validation TTL but skip Neo4j referential integrity and persistence.
        graph_rels = [r for r in rels if not getattr(r, "ontology_ref", False)]
        all_rels = rels  # validation needs both kinds

        # Referential integrity: every graph relationship target must exist.
        target_types = self._target_canonical_types(tenant_id, graph_rels)
        missing = [tid for tid, c in target_types.items() if c is None]
        if missing:
            return WriteResult(ok=False, canonical_type=canonical_type,
                               error=f"Relationship target(s) not found in "
                                     f"tenant '{tenant_id}': {missing}")

        # ---- VALIDATE (before anything touches the graph) ----
        ttl = self._render_node_ttl(node_id, canonical_type, props, all_rels)
        result = gate.validate(ttl)
        if not result.ok:
            return WriteResult(
                ok=False, node_id=node_id, label=label,
                canonical_type=canonical_type,
                violations=[str(v) for v in result.violations],
                error="Validation failed; nothing written.",
            )

        # ---- COMMIT (single transaction: node + graph relationships) ----
        self._commit_create(tenant_id, label, props, graph_rels)

        # ---- EMIT change-log event ----
        field_changes = {k: {"old": None, "new": v} for k, v in props.items()}
        for r in rels:
            iri, _ = _resolve_predicate(r.predicate)
            field_changes[iri] = {"old": None, "new": r.target_id}
        ev = self.changelog.append(
            tenant_id=tenant_id, entity_id=node_id,
            entity_type=canonical_type, actor=actor, action="create",
            field_changes=field_changes,
        )

        # ---- STAMP changeLogRef back onto the node ----
        self._stamp_change_log_ref(tenant_id, node_id, ev.event_id)

        # ---- PUBLISH to the live event bus (best-effort) ----
        self._publish_event(
            event_id=ev.event_id, tenant_id=tenant_id, entity_id=node_id,
            entity_type=canonical_type, label=label, action="create",
            actor=actor, ts=ev.ts, field_changes=field_changes,
        )

        return WriteResult(ok=True, node_id=node_id, label=label,
                           canonical_type=canonical_type, event_id=ev.event_id)

    def relate(self, *, tenant_id: str, actor: str, source_id: str,
               predicate: str, target_id: str) -> WriteResult:
        """Add one outgoing relationship to an existing node, re-validating
        the source node's FULL state (existing props + all rels + the new
        one) through the same gate. This is how a Finding is GROUPED_INTO an
        Incident — proving the relationship path also honours the discipline.
        """
        src = self._fetch_node(tenant_id, source_id)
        if src is None:
            return WriteResult(ok=False, node_id=source_id,
                               error="Source node not found in tenant.")
        canonical_type = src["props"].get("canonicalType")
        label = self.resolve_label(canonical_type)

        existing = self._fetch_outgoing(tenant_id, source_id)
        new_rel = Rel(predicate=predicate, target_id=target_id)
        all_rels = existing + [new_rel]

        target_types = self._target_canonical_types(tenant_id, all_rels)
        if target_types.get(target_id) is None:
            return WriteResult(ok=False, node_id=source_id,
                               error=f"Relationship target '{target_id}' not "
                                     f"found in tenant.")

        props = dict(src["props"])
        props["updatedAt"] = _now_iso()
        props.pop("changeLogRef", None)  # re-stamped after this mutation

        ttl = self._render_node_ttl(source_id, canonical_type, props, all_rels)
        result = gate.validate(ttl)
        if not result.ok:
            return WriteResult(
                ok=False, node_id=source_id, label=label,
                canonical_type=canonical_type,
                violations=[str(v) for v in result.violations],
                error="Validation failed; relationship not written.",
            )

        iri, rel_type = _resolve_predicate(predicate)
        with self.driver.session() as s:
            s.execute_write(self._tx_relate, tenant_id, source_id, target_id,
                            rel_type, iri, props["updatedAt"])

        rel_changes = {iri: {"old": None, "new": target_id}}
        ev = self.changelog.append(
            tenant_id=tenant_id, entity_id=source_id,
            entity_type=canonical_type, actor=actor, action="update",
            field_changes=rel_changes,
        )
        self._stamp_change_log_ref(tenant_id, source_id, ev.event_id)
        self._publish_event(
            event_id=ev.event_id, tenant_id=tenant_id, entity_id=source_id,
            entity_type=canonical_type, label=label, action="update",
            actor=actor, ts=ev.ts, field_changes=rel_changes,
        )
        return WriteResult(ok=True, node_id=source_id, label=label,
                           canonical_type=canonical_type, event_id=ev.event_id)

    # ---- transaction functions ---------------------------------------
    def _commit_create(self, tenant_id, label, props, rels):
        with self.driver.session() as s:
            s.execute_write(self._tx_create, label, props, rels)

    @staticmethod
    def _tx_create(tx, label, props, rels):
        tx.run(f"CREATE (n:{label} $props)", props=props)
        for r in rels:
            iri, rel_type = _resolve_predicate(r.predicate)
            tx.run(
                f"MATCH (n {{tenantId:$t, id:$sid}}) "
                f"MATCH (m {{tenantId:$t, id:$tid}}) "
                f"CREATE (n)-[:{rel_type} {{predicateIri:$iri}}]->(m)",
                t=props["tenantId"], sid=props["id"], tid=r.target_id, iri=iri,
            )

    @staticmethod
    def _tx_relate(tx, tenant_id, source_id, target_id, rel_type, iri, updated):
        tx.run(
            f"MATCH (n {{tenantId:$t, id:$sid}}) "
            f"MATCH (m {{tenantId:$t, id:$tid}}) "
            f"MERGE (n)-[r:{rel_type}]->(m) "
            f"SET r.predicateIri = $iri, n.updatedAt = $u",
            t=tenant_id, sid=source_id, tid=target_id, iri=iri, u=updated,
        )

    def _stamp_change_log_ref(self, tenant_id, node_id, event_id):
        with self.driver.session() as s:
            s.run(
                "MATCH (n {tenantId:$t, id:$i}) SET n.changeLogRef = $e",
                t=tenant_id, i=node_id, e=event_id,
            )

    # ---- update path (validate new state → patch → emit) -------------
    def update(self, *, tenant_id: str, node_id: str, actor: str,
               properties: dict) -> WriteResult:
        """Update properties on an existing node. The full post-update state
        is validated through the gate before anything is written."""
        src = self._fetch_node(tenant_id, node_id)
        if src is None:
            return WriteResult(ok=False, node_id=node_id,
                               error=f"Node {node_id} not found in tenant {tenant_id}")

        canonical_type = src["props"].get("canonicalType")
        label = self.resolve_label(canonical_type)
        if label is None:
            return WriteResult(ok=False, node_id=node_id,
                               error=f"Unknown type: {canonical_type}")

        # State machine enforcement: validate status transitions
        if "status" in properties and canonical_type:
            old_status = src["props"].get("status")
            sm_error = validate_transition(canonical_type, old_status, properties["status"])
            if sm_error:
                return WriteResult(
                    ok=False, node_id=node_id, label=label,
                    canonical_type=canonical_type,
                    error=f"State machine violation: {sm_error}",
                )

        # Merge new properties into existing
        merged = dict(src["props"])
        for k, v in properties.items():
            if k not in ("id", "tenantId", "canonicalType", "createdAt", "createdBy"):
                merged[k] = v
        merged["updatedAt"] = _now_iso()
        merged.pop("changeLogRef", None)

        # Fetch existing relationships for full-state validation
        existing_rels = self._fetch_outgoing(tenant_id, node_id)

        # Validate the full post-update state
        ttl = self._render_node_ttl(node_id, canonical_type, merged, existing_rels)
        result = gate.validate(ttl)
        if not result.ok:
            return WriteResult(
                ok=False, node_id=node_id, label=label,
                canonical_type=canonical_type,
                violations=[str(v) for v in result.violations],
                error="Validation failed; nothing updated.",
            )

        # Build the field_changes diff for the changelog
        old_props = src["props"]
        field_changes = {}
        for k, v in properties.items():
            if k not in ("id", "tenantId", "canonicalType", "createdAt", "createdBy"):
                field_changes[k] = {"old": old_props.get(k), "new": v}

        # Apply in Neo4j
        update_props = {k: v for k, v in properties.items()
                        if k not in ("id", "tenantId", "canonicalType",
                                     "createdAt", "createdBy")}
        update_props["updatedAt"] = merged["updatedAt"]

        with self.driver.session() as s:
            set_clauses = ", ".join(f"n.`{k}` = ${k}" for k in update_props)
            s.run(
                f"MATCH (n {{tenantId:$t, id:$i}}) SET {set_clauses}",
                t=tenant_id, i=node_id, **update_props,
            )

        # Emit changelog
        ev = self.changelog.append(
            tenant_id=tenant_id, entity_id=node_id,
            entity_type=canonical_type, actor=actor, action="update",
            field_changes=field_changes,
        )
        self._stamp_change_log_ref(tenant_id, node_id, ev.event_id)
        self._publish_event(
            event_id=ev.event_id, tenant_id=tenant_id, entity_id=node_id,
            entity_type=canonical_type, label=label, action="update",
            actor=actor, ts=ev.ts, field_changes=field_changes,
        )

        return WriteResult(ok=True, node_id=node_id, label=label,
                           canonical_type=canonical_type, event_id=ev.event_id)

    # ---- delete path (no validation needed, but logged) --------------
    def delete(self, *, tenant_id: str, node_id: str, actor: str) -> WriteResult:
        """Delete a node. No SHACL validation needed for deletions, but the
        event IS logged in the Change Log for auditability."""
        src = self._fetch_node(tenant_id, node_id)
        if src is None:
            return WriteResult(ok=False, node_id=node_id,
                               error=f"Node {node_id} not found in tenant {tenant_id}")

        canonical_type = src["props"].get("canonicalType")
        label = self.resolve_label(canonical_type) or "Unknown"

        # Snapshot the node before deleting (for the changelog payload)
        snapshot = dict(src["props"])

        # Delete from Neo4j (detach removes relationships too)
        with self.driver.session() as s:
            s.run(
                "MATCH (n {tenantId:$t, id:$i}) DETACH DELETE n",
                t=tenant_id, i=node_id,
            )

        # Emit changelog
        field_changes = {k: {"old": v, "new": None} for k, v in snapshot.items()}
        ev = self.changelog.append(
            tenant_id=tenant_id, entity_id=node_id,
            entity_type=canonical_type, actor=actor, action="delete",
            field_changes=field_changes,
        )
        self._publish_event(
            event_id=ev.event_id, tenant_id=tenant_id, entity_id=node_id,
            entity_type=canonical_type, label=label, action="delete",
            actor=actor, ts=ev.ts, field_changes=field_changes,
        )

        return WriteResult(ok=True, node_id=node_id, label=label,
                           canonical_type=canonical_type)
