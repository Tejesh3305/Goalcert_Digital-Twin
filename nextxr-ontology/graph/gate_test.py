#!/usr/bin/env python3
"""
gate_test.py — Day 1 gate: "Create a node + read it back, tenant-scoped."

This script proves three things:
  1. A node can be created and read back with all base properties intact.
  2. Tenant isolation works: tenant A cannot see tenant B's nodes.
  3. The uniqueness constraint rejects duplicate (tenantId, id) pairs.

Run AFTER docker-compose up and python -m graph.schema.

Usage:
    python -m graph.gate_test
"""

import sys
from graph.connection import get_driver, close_driver
from graph.crud import create_node, read_node, list_nodes, delete_node


def clean(tenant_id, label="PhysicalAsset"):
    """Remove test nodes so the gate is idempotent."""
    driver = get_driver()
    with driver.session() as session:
        session.run(
            f"MATCH (n:{label} {{tenantId: $tid}}) DETACH DELETE n",
            tid=tenant_id,
        )


def test_create_and_read():
    """Gate check 1: create a node, read it back, verify properties."""
    print("--- Test 1: Create + Read ---")
    tenant = "test-tenant-alpha"
    clean(tenant)

    props = {
        "displayName": "AHU-01",
        "canonicalType": "https://ontology.nextxr.io/v3/hvac#AirHandler",
        "status": "running",
    }

    created = create_node(tenant, "PhysicalAsset", props, created_by="gate-test")
    node_id = created["id"]
    print(f"  Created node {node_id}")

    read_back = read_node(tenant, node_id, label="PhysicalAsset")
    assert read_back is not None, "FAIL: node not found on read-back"
    assert read_back["tenantId"] == tenant, "FAIL: tenantId mismatch"
    assert read_back["displayName"] == "AHU-01", "FAIL: displayName mismatch"
    assert read_back["canonicalType"] == props["canonicalType"], "FAIL: canonicalType mismatch"
    assert read_back["createdBy"] == "gate-test", "FAIL: createdBy mismatch"
    assert read_back["createdAt"] != "", "FAIL: createdAt not stamped"
    assert read_back["updatedAt"] != "", "FAIL: updatedAt not stamped"
    print("  PASS: node created and read back with all base properties.\n")
    return node_id


def test_tenant_isolation(node_id_alpha):
    """Gate check 2: a different tenant cannot see tenant alpha's node."""
    print("--- Test 2: Tenant Isolation ---")
    other_tenant = "test-tenant-beta"

    result = read_node(other_tenant, node_id_alpha, label="PhysicalAsset")
    assert result is None, "FAIL: tenant-beta could read tenant-alpha's node!"
    print("  PASS: tenant-beta cannot see tenant-alpha's node.\n")

    # Also verify list_nodes is isolated
    beta_nodes = list_nodes(other_tenant, "PhysicalAsset")
    assert len(beta_nodes) == 0, "FAIL: tenant-beta sees nodes in list!"
    print("  PASS: list_nodes returns empty for wrong tenant.\n")


def test_uniqueness_constraint():
    """Gate check 3: duplicate (tenantId, id) is rejected."""
    print("--- Test 3: Uniqueness Constraint ---")
    tenant = "test-tenant-alpha"
    fixed_id = "00000000-0000-0000-0000-gate00test01"

    # Clean any leftover from previous runs
    delete_node(tenant, fixed_id, label="PhysicalAsset")

    create_node(tenant, "PhysicalAsset", {
        "id": fixed_id,
        "displayName": "Duplicate Test",
    })

    try:
        create_node(tenant, "PhysicalAsset", {
            "id": fixed_id,
            "displayName": "Should Fail",
        })
        print("  FAIL: duplicate was allowed!\n")
        return False
    except Exception as e:
        if "Constraint" in str(type(e).__name__) or "constraint" in str(e).lower():
            print("  PASS: duplicate (tenantId, id) correctly rejected.\n")
            return True
        else:
            print(f"  FAIL: unexpected error: {e}\n")
            return False


def main():
    print("=" * 60)
    print("  DAY 1 GATE TEST")
    print("  Create a node + read it back, tenant-scoped")
    print("=" * 60 + "\n")

    try:
        # Verify connection first
        driver = get_driver()
        driver.verify_connectivity()
        print("Neo4j connection: OK\n")
    except Exception as e:
        print(f"Neo4j connection FAILED: {e}")
        print("Make sure Neo4j is running: docker compose up -d")
        sys.exit(1)

    passed = 0
    total = 3

    node_id = test_create_and_read()
    passed += 1

    test_tenant_isolation(node_id)
    passed += 1

    if test_uniqueness_constraint():
        passed += 1

    # Cleanup
    clean("test-tenant-alpha")
    clean("test-tenant-beta")
    close_driver()

    print("=" * 60)
    print(f"  GATE RESULT: {passed}/{total} passed")
    if passed == total:
        print("  DAY 1 GATE: CLOSED. Ready for Day 2.")
    else:
        print("  DAY 1 GATE: OPEN. Fix failures before proceeding.")
    print("=" * 60)

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
