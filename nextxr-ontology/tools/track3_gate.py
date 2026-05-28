#!/usr/bin/env python3
"""
track3_gate.py — TRACK 3 EXIT TEST (the findings loop, closed).

Proves, end to end, through the SINGLE WRITE PATH:

  1. Seed an HVAC facility (Site, Space, AirHandler) via the Graph Writer.
  2. Feed simulated telemetry; a Tier-C threshold rule fires.
  3. The Finding lands as a :Finding node via the Graph Writer, FLAGS the
     air handler, and carries a Change Log event (changeLogRef -> event_id).
  4. The Tier-B learned baseline also fires (proving learned behaviours work).
  5. The Tier-A physics SLOT exists and the registry routes to it.
  6. GROUPED_INTO is available: the Finding is grouped into an Incident
     through the writer (same discipline), creating another Change Log event.
  7. The tenant's hash chain verifies.
  8. NEGATIVE: an invalid Finding (no flags) is rejected — the graph is
     untouched and NO orphan Change Log event is created.

Run AFTER:  docker compose up -d   and   python -m graph.schema
Usage:      python tools/track3_gate.py
"""

import contextlib
import io
import os
import sys
from pathlib import Path

# tools/ is sys.path[0]; add the package root so graph/, behaviors/, feed/,
# changelog/ import as packages.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from graph.connection import get_driver, close_driver          # noqa: E402
from graph.query import GraphQuery                              # noqa: E402
from graph.writer import GraphWriter, Rel                       # noqa: E402
from changelog.service import ChangeLog                         # noqa: E402
from behaviors.registry import BehaviorRegistry, Tier           # noqa: E402
from behaviors.hvac import (                                    # noqa: E402
    TemperatureThresholdRule, TemperatureZScoreBaseline,
    ThermalPhysicsBehavior,
)
from feed.simulate import simulate_temperature, FindingsLoop    # noqa: E402

CORE = "https://ontology.nextxr.io/v3/core#"
HVAC = "https://ontology.nextxr.io/v3/hvac#"
TENANT = "test-track3"

_passed = 0
_failed = 0


def check(label, cond, detail=""):
    global _passed, _failed
    mark = "PASS" if cond else "FAIL"
    if cond:
        _passed += 1
    else:
        _failed += 1
    extra = f"  ({detail})" if detail else ""
    print(f"  [{mark}] {label}{extra}")
    return cond


def clean_tenant(driver, tenant):
    with driver.session() as s:
        s.run("MATCH (n {tenantId:$t}) DETACH DELETE n", t=tenant)


def main():
    print("=" * 66)
    print("  TRACK 3 EXIT TEST — behaviour registry + the findings loop")
    print("=" * 66 + "\n")

    try:
        get_driver().verify_connectivity()
    except Exception as e:
        print(f"Neo4j connection FAILED: {e}\n  docker compose up -d")
        sys.exit(1)

    # Ensure schema (idempotent); silence its chatty output. Note: apply_schema
    # closes the shared driver when it finishes, so we re-acquire it afterwards.
    from graph import schema
    with contextlib.redirect_stdout(io.StringIO()):
        schema.apply_schema(dry_run=False)
    driver = get_driver()

    # Fresh per-run change log so the gate is reproducible.
    gate_db = ROOT / "data" / "track3_gate.db"
    if gate_db.exists():
        os.remove(gate_db)
    cl = ChangeLog(db_path=gate_db)

    clean_tenant(driver, TENANT)

    writer = GraphWriter(changelog=cl)
    query = GraphQuery()

    # ---------------------------------------------------------------
    print("--- 1. Seed the HVAC facility through the Graph Writer ---")
    site = writer.create(tenant_id=TENANT, canonical_type=CORE + "Site",
                         actor="seed", properties={"displayName": "Plant A"})
    space = writer.create(tenant_id=TENANT, canonical_type=CORE + "Space",
                          actor="seed", properties={"displayName": "Server Room 1"})
    check("Site created via writer", site.ok, site.error or site.event_id)
    check("Space created via writer", space.ok, space.error or space.event_id)

    ahu = writer.create(
        tenant_id=TENANT, canonical_type=HVAC + "AirHandler", actor="seed",
        properties={"displayName": "AHU-01", "status": "running", "setpoint": 22.0},
        relationships=[Rel("hvac:servesSpace", space.node_id)],
    )
    check("AirHandler created via writer (label from ontology)",
          ahu.ok and ahu.label == "PhysicalAsset",
          f"label={ahu.label} {ahu.error or ''}")
    ahu_id = ahu.node_id

    # ---------------------------------------------------------------
    print("\n--- 2. Build the registry: one behaviour per tier ---")
    registry = BehaviorRegistry()
    registry.register(TemperatureThresholdRule(offset_c=3.0, duration_minutes=3.0))
    registry.register(TemperatureZScoreBaseline(warmup=12, z_threshold=3.0))
    registry.register(ThermalPhysicsBehavior())
    check("3 behaviours registered (A/B/C)", len(registry.all()) == 3)
    check("Tier-C rule present", len(registry.by_tier(Tier.C)) == 1)
    check("Tier-B learned behaviour present", len(registry.by_tier(Tier.B)) == 1)
    check("Tier-A physics SLOT present", len(registry.by_tier(Tier.A)) == 1)

    # ---------------------------------------------------------------
    print("\n--- 3. Feed telemetry; behaviours fire; Findings written via writer ---")
    loop = FindingsLoop(registry, writer, query)
    samples = list(simulate_temperature(TENANT, ahu_id, setpoint=22.0, minutes=30))
    outcomes = loop.run(samples)

    tier_c = [o for o in outcomes if o.finding.tier == Tier.C]
    tier_b = [o for o in outcomes if o.finding.tier == Tier.B]
    check("Tier-C rule fired and Finding written", len(tier_c) >= 1,
          f"{len(tier_c)} finding(s)")
    check("Tier-B baseline fired and Finding written", len(tier_b) >= 1,
          f"{len(tier_b)} finding(s)")
    check("Every emitted Finding committed via writer",
          all(o.result.ok for o in outcomes), f"{len(outcomes)} total")

    # ---------------------------------------------------------------
    print("\n--- 4. The Tier-C Finding node, in the graph, carrying its event ---")
    if tier_c:
        c_res = tier_c[0].result
        node = query.get_node(TENANT, c_res.node_id)
        check("Finding node exists in Neo4j", node is not None)
        check("Finding is labelled :Finding", c_res.label == "Finding")
        check("Finding flags the air handler",
              any(n["node"]["id"] == ahu_id
                  for n in query.neighbors(TENANT, c_res.node_id, "FLAGS")))
        check("Finding.changeLogRef points to its event",
              node and node.get("changeLogRef") == c_res.event_id,
              f"ref={node.get('changeLogRef') if node else None}")
        ev = cl.get(c_res.event_id)
        check("Change Log event exists for the Finding", ev is not None)
        check("Event is action=create on the Finding type",
              ev and ev.action == "create" and ev.entity_type == CORE + "Finding")

    # ---------------------------------------------------------------
    print("\n--- 5. GROUPED_INTO is available: group the Finding into an Incident ---")
    incident = writer.create(
        tenant_id=TENANT, canonical_type=CORE + "Incident", actor="correlator",
        properties={"displayName": "High-temp incident", "status": "open"},
        relationships=[Rel("nxr:affects", ahu_id)],
    )
    check("Incident created via writer", incident.ok, incident.error or "")
    if tier_c and incident.ok:
        grouped = writer.relate(
            tenant_id=TENANT, actor="correlator",
            source_id=tier_c[0].result.node_id,
            predicate="nxr:groupedInto", target_id=incident.node_id,
        )
        check("Finding GROUPED_INTO Incident via writer", grouped.ok,
              grouped.error or "")
        rels = query.neighbors(TENANT, tier_c[0].result.node_id, "GROUPED_INTO")
        check("(:Finding)-[:GROUPED_INTO]->(:Incident) exists in graph",
              any(n["node"]["id"] == incident.node_id for n in rels))

    # ---------------------------------------------------------------
    print("\n--- 6. The tenant's Change Log hash chain verifies ---")
    ok_chain, bad = cl.verify_chain(TENANT)
    check("Hash chain intact", ok_chain, f"events={cl.count(TENANT)}")

    # ---------------------------------------------------------------
    print("\n--- 7. NEGATIVE: invalid Finding rejected, graph + log untouched ---")
    findings_before = len(query.list_by_label(TENANT, "Finding"))
    events_before = cl.count(TENANT)
    bad_res = writer.create(
        tenant_id=TENANT, canonical_type=CORE + "Finding", actor="behavior:bad",
        properties={"displayName": "orphan finding", "status": "open"},
        relationships=[],   # NO flags -> violates FindingShape (minCount 1)
    )
    findings_after = len(query.list_by_label(TENANT, "Finding"))
    events_after = cl.count(TENANT)
    check("Invalid Finding REJECTED by the gate", not bad_res.ok,
          (bad_res.violations[0] if bad_res.violations else bad_res.error))
    check("Graph untouched (no Finding node added)",
          findings_after == findings_before,
          f"{findings_before}->{findings_after}")
    check("No orphan Change Log event",
          events_after == events_before, f"{events_before}->{events_after}")

    # ---------------------------------------------------------------
    close_driver()
    print("\n" + "=" * 66)
    print(f"  TRACK 3 GATE RESULT: {_passed} passed, {_failed} failed")
    if _failed == 0:
        print("  TRACK 3 GATE: CLOSED. The findings loop is sound.")
    else:
        print("  TRACK 3 GATE: OPEN. Fix failures before proceeding.")
    print("=" * 66)
    sys.exit(0 if _failed == 0 else 1)


if __name__ == "__main__":
    main()
