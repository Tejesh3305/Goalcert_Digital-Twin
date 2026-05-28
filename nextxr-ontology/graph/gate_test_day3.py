#!/usr/bin/env python3
"""
gate_test_day3.py — Day 3 gate: "The Graph Writer — validate → commit → emit."

This script proves six things:
  1. A valid write succeeds: node lands in Neo4j + Change Log entry created.
  2. An invalid write is rejected: no node in DB, no log entry, violations returned.
  3. Update works: node updated in DB + Change Log entry for UPDATE.
  4. Delete works: node removed from DB + Change Log entry for DELETE.
  5. Change Log chain is still intact after all operations.
  6. Tenant isolation: writer enforces the tenant wall.

Run AFTER docker-compose up and python -m graph.schema.

Usage:
    python -m graph.gate_test_day3
"""

import sys
from pathlib import Path

# Ensure tools/ is on the path for gate.py
_TOOLS_DIR = str(Path(__file__).resolve().parent.parent / "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

from graph.connection import get_driver, close_driver
from graph.writer import write_node, update_node, delete_node
from graph.crud import read_node, list_nodes
from graph.changelog import get_entries, verify_chain


TENANT = "test-writer-alpha"
TENANT_B = "test-writer-beta"


def clean():
    """Wipe test data so the gate is idempotent."""
    driver = get_driver()
    with driver.session() as session:
        for t in [TENANT, TENANT_B]:
            session.run("MATCH (n {tenantId: $tid}) DETACH DELETE n", tid=t)
            session.run("MATCH (e:ChangeLog {tenantId: $tid}) DETACH DELETE e", tid=t)


def _space_turtle(urn, name):
    """Helper: build a valid Space entity in Turtle (needed as related context)."""
    import uuid
    return (
        f'{urn} a nxr:Space ;\n'
        f'    nxr:id "{uuid.uuid4()}" ;\n'
        f'    nxr:tenantId "{TENANT}" ;\n'
        f'    nxr:canonicalType "https://ontology.nextxr.io/v3/core#Space"^^xsd:anyURI ;\n'
        f'    nxr:createdAt "2026-05-28T10:00:00Z"^^xsd:dateTime ;\n'
        f'    nxr:updatedAt "2026-05-28T10:00:00Z"^^xsd:dateTime ;\n'
        f'    nxr:createdBy "gate-test" ;\n'
        f'    nxr:displayName "{name}" .\n'
    )


def test_valid_write():
    """Gate check 1: valid AirHandler write succeeds end to end."""
    print("--- Test 1: Valid Write (validate → commit → emit) ---")

    space_ttl = _space_turtle("<urn:space:lobby>", "Main Lobby")

    result = write_node(
        tenant_id=TENANT,
        class_name="AirHandler",
        properties={
            "displayName": "AHU-01",
            "status": "running",
            "_rdf": {"hvac:servesSpace": "<urn:space:lobby>"},
        },
        related_turtle=space_ttl,
        actor="gate-test",
    )

    assert result.ok, f"FAIL: valid write rejected — {result.violations}"
    assert result.node is not None, "FAIL: no node returned"
    assert result.log_entry is not None, "FAIL: no log entry returned"
    assert result.node["displayName"] == "AHU-01"
    assert result.node["tenantId"] == TENANT
    assert result.log_entry["action"] == "CREATE"
    assert result.log_entry["entityId"] == result.node["id"]

    # Verify it's actually in Neo4j
    db_node = read_node(TENANT, result.node["id"], label="PhysicalAsset")
    assert db_node is not None, "FAIL: node not found in Neo4j"
    assert db_node["displayName"] == "AHU-01"

    # Verify changeLogRef is set
    assert result.node.get("changeLogRef") == result.log_entry["id"], \
        "FAIL: changeLogRef not linked to log entry"

    print(f"  Created: {result.node['id'][:8]}… → logged as {result.log_entry['id'][:8]}…")
    print("  PASS: valid write succeeded with node + log entry.\n")
    return result.node["id"]


def test_invalid_write():
    """Gate check 2: invalid write is blocked — nothing in DB, no log."""
    print("--- Test 2: Invalid Write Rejected ---")

    # AirHandler WITHOUT servesSpace and without displayName — should fail SHACL
    result = write_node(
        tenant_id=TENANT,
        class_name="AirHandler",
        properties={
            "id": "00000000-0000-0000-0000-invalid00001",
        },
        actor="gate-test",
    )

    assert not result.ok, "FAIL: invalid write was accepted!"
    assert len(result.violations) > 0, "FAIL: no violations returned"
    assert result.node is None, "FAIL: a node was created for invalid write"
    assert result.log_entry is None, "FAIL: a log entry was created for invalid write"

    # Verify nothing in Neo4j
    db_node = read_node(TENANT, "00000000-0000-0000-0000-invalid00001", label="PhysicalAsset")
    assert db_node is None, "FAIL: invalid node found in Neo4j!"

    print(f"  Rejected with {len(result.violations)} violation(s):")
    for v in result.violations[:2]:
        print(f"    → {v[:100]}")
    print("  PASS: invalid write blocked — no node, no log.\n")


def test_update():
    """Gate check 3: update a node through the writer."""
    print("--- Test 3: Update via Writer ---")

    # First create a Site (simpler, fewer required props)
    create_result = write_node(
        tenant_id=TENANT,
        class_name="Site",
        properties={"displayName": "HQ Campus", "status": "active"},
        actor="gate-test",
    )
    assert create_result.ok, f"FAIL: setup create failed — {create_result.violations}"
    node_id = create_result.node["id"]

    # Now update it
    update_result = update_node(
        tenant_id=TENANT,
        node_id=node_id,
        class_name="Site",
        properties={"displayName": "HQ Campus — Renovated", "status": "maintenance"},
        actor="gate-test",
    )

    assert update_result.ok, f"FAIL: update rejected — {update_result.violations}"
    assert update_result.node["displayName"] == "HQ Campus — Renovated"
    assert update_result.log_entry["action"] == "UPDATE"

    # Verify in DB
    db_node = read_node(TENANT, node_id, label="Location")
    assert db_node["displayName"] == "HQ Campus — Renovated"

    print(f"  Updated: {node_id[:8]}… displayName → 'HQ Campus — Renovated'")
    print("  PASS: update succeeded with node + UPDATE log entry.\n")
    return node_id


def test_delete(node_id):
    """Gate check 4: delete a node through the writer."""
    print("--- Test 4: Delete via Writer ---")

    result = delete_node(
        tenant_id=TENANT,
        node_id=node_id,
        class_name="Site",
        actor="gate-test",
    )

    assert result.ok, f"FAIL: delete failed — {result.violations}"
    assert result.log_entry["action"] == "DELETE"
    assert result.log_entry["entityId"] == node_id

    # Verify gone from DB
    db_node = read_node(TENANT, node_id, label="Location")
    assert db_node is None, "FAIL: deleted node still in Neo4j!"

    print(f"  Deleted: {node_id[:8]}… → DELETE logged")
    print("  PASS: delete succeeded — node gone, deletion logged.\n")


def test_chain_intact():
    """Gate check 5: the Change Log chain is still valid after all operations."""
    print("--- Test 5: Chain Integrity ---")

    ok, details = verify_chain(TENANT)
    assert ok, f"FAIL: chain broken — {details}"

    entries = get_entries(TENANT)
    actions = [e["action"] for e in entries]
    assert "CREATE" in actions, "FAIL: no CREATE entries"
    assert "UPDATE" in actions, "FAIL: no UPDATE entries"
    assert "DELETE" in actions, "FAIL: no DELETE entries"

    print(f"  {details}")
    print(f"  Actions in chain: {actions}")
    print("  PASS: chain intact with CREATE, UPDATE, and DELETE entries.\n")


def test_tenant_isolation():
    """Gate check 6: writer enforces tenant isolation."""
    print("--- Test 6: Tenant Isolation via Writer ---")

    # Create a Site for tenant B
    result_b = write_node(
        tenant_id=TENANT_B,
        class_name="Site",
        properties={"displayName": "Beta Campus"},
        actor="gate-test",
    )
    assert result_b.ok, f"FAIL: tenant B write failed — {result_b.violations}"

    # Tenant A should not see tenant B's node
    a_nodes = list_nodes(TENANT, "Location")
    b_ids = {n["id"] for n in a_nodes}
    assert result_b.node["id"] not in b_ids, "FAIL: tenant A can see tenant B's node!"

    # Tenant A's log should not have tenant B's entries
    a_log = get_entries(TENANT)
    a_log_entities = {e["entityId"] for e in a_log}
    assert result_b.node["id"] not in a_log_entities, "FAIL: tenant A's log has tenant B's entry!"

    print("  PASS: tenant B's data invisible to tenant A (nodes + log).\n")


def main():
    print("=" * 60)
    print("  DAY 3 GATE TEST")
    print("  The Graph Writer — validate → commit → emit")
    print("=" * 60 + "\n")

    try:
        driver = get_driver()
        driver.verify_connectivity()
        print("Neo4j connection: OK\n")
    except Exception as e:
        print(f"Neo4j connection FAILED: {e}")
        print("Make sure Neo4j is running: docker compose up -d")
        sys.exit(1)

    clean()

    passed = 0
    total = 6

    try:
        ahu_id = test_valid_write()
        passed += 1
    except AssertionError as e:
        print(f"  FAIL: {e}\n")
        ahu_id = None

    try:
        test_invalid_write()
        passed += 1
    except AssertionError as e:
        print(f"  FAIL: {e}\n")

    try:
        site_id = test_update()
        passed += 1
    except AssertionError as e:
        print(f"  FAIL: {e}\n")
        site_id = None

    try:
        if site_id:
            test_delete(site_id)
            passed += 1
        else:
            print("--- Test 4: Delete via Writer ---\n  SKIP: no node to delete\n")
    except AssertionError as e:
        print(f"  FAIL: {e}\n")

    try:
        test_chain_intact()
        passed += 1
    except AssertionError as e:
        print(f"  FAIL: {e}\n")

    try:
        test_tenant_isolation()
        passed += 1
    except AssertionError as e:
        print(f"  FAIL: {e}\n")

    # Cleanup
    clean()
    close_driver()

    print("=" * 60)
    print(f"  GATE RESULT: {passed}/{total} passed")
    if passed == total:
        print("  DAY 3 GATE: CLOSED. Ready for Day 4.")
    else:
        print("  DAY 3 GATE: OPEN. Fix failures before proceeding.")
    print("=" * 60)

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
