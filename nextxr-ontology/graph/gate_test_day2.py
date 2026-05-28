#!/usr/bin/env python3
"""
gate_test_day2.py — Day 2 gate: "Append-only hash-chained Change Log."

This script proves five things:
  1. Entries can be appended and read back.
  2. The hash chain is valid after multiple appends.
  3. Tampering with a past entry is detected by verify_chain().
  4. Tenant isolation: tenant A cannot see tenant B's log entries.
  5. Querying by entity_id returns only that entity's history.

Run AFTER docker-compose up and python -m graph.schema.

Usage:
    python -m graph.gate_test_day2
"""

import sys
from graph.connection import get_driver, close_driver
from graph.changelog import append_entry, get_entries, get_entry_count, verify_chain


TENANT_A = "test-changelog-alpha"
TENANT_B = "test-changelog-beta"


def clean():
    """Wipe test log entries so the gate is idempotent."""
    driver = get_driver()
    with driver.session() as session:
        session.run("MATCH (e:ChangeLog {tenantId: $t}) DETACH DELETE e", t=TENANT_A)
        session.run("MATCH (e:ChangeLog {tenantId: $t}) DETACH DELETE e", t=TENANT_B)


def test_append_and_read():
    """Gate check 1: append entries, read them back."""
    print("--- Test 1: Append + Read ---")

    e1 = append_entry(
        TENANT_A, "sensor-001", "PhysicalAsset", "CREATE",
        payload={"displayName": "Temp Sensor 1", "status": "active"},
        actor="gate-test",
    )
    assert e1["seq"] == 0, f"FAIL: first entry seq should be 0, got {e1['seq']}"
    assert e1["prevHash"] == "GENESIS", f"FAIL: first entry prevHash should be GENESIS"
    assert e1["action"] == "CREATE"

    e2 = append_entry(
        TENANT_A, "sensor-001", "PhysicalAsset", "UPDATE",
        payload={"status": "calibrating"},
        actor="gate-test",
    )
    assert e2["seq"] == 1, f"FAIL: second entry seq should be 1, got {e2['seq']}"
    assert e2["prevHash"] == e1["hash"], "FAIL: second entry prevHash should be first entry's hash"

    e3 = append_entry(
        TENANT_A, "ahu-001", "PhysicalAsset", "CREATE",
        payload={"displayName": "AHU-01"},
        actor="gate-test",
    )
    assert e3["seq"] == 2

    entries = get_entries(TENANT_A)
    assert len(entries) == 3, f"FAIL: expected 3 entries, got {len(entries)}"
    assert entries[0]["seq"] == 0 and entries[2]["seq"] == 2, "FAIL: ordering wrong"

    count = get_entry_count(TENANT_A)
    assert count == 3, f"FAIL: count should be 3, got {count}"

    print("  PASS: 3 entries appended and read back correctly.\n")
    return e1, e2, e3


def test_chain_valid():
    """Gate check 2: verify the hash chain is intact."""
    print("--- Test 2: Chain Verification ---")

    ok, details = verify_chain(TENANT_A)
    assert ok, f"FAIL: chain should be valid — {details}"
    print(f"  PASS: {details}\n")


def test_tamper_detection():
    """Gate check 3: mutate a past entry and confirm verify_chain catches it."""
    print("--- Test 3: Tamper Detection ---")

    driver = get_driver()
    with driver.session() as session:
        # Tamper: change the action of seq 0 from CREATE to DELETE
        session.run(
            "MATCH (e:ChangeLog {tenantId: $tid, seq: 0}) SET e.action = 'DELETE'",
            tid=TENANT_A,
        )

    ok, details = verify_chain(TENANT_A)
    assert not ok, "FAIL: tampered chain should NOT verify!"
    print(f"  Tamper detected: {details}")

    # Restore the original value so subsequent tests work
    with driver.session() as session:
        session.run(
            "MATCH (e:ChangeLog {tenantId: $tid, seq: 0}) SET e.action = 'CREATE'",
            tid=TENANT_A,
        )

    # Confirm it verifies again after restoring
    ok, details = verify_chain(TENANT_A)
    assert ok, f"FAIL: restored chain should verify — {details}"
    print("  PASS: tampering detected and chain restored.\n")


def test_tenant_isolation():
    """Gate check 4: tenant B cannot see tenant A's log entries."""
    print("--- Test 4: Tenant Isolation ---")

    # Append one entry for tenant B
    append_entry(
        TENANT_B, "pump-001", "PhysicalAsset", "CREATE",
        payload={"displayName": "Pump-01"},
        actor="gate-test",
    )

    b_entries = get_entries(TENANT_B)
    assert len(b_entries) == 1, f"FAIL: tenant B should have 1 entry, got {len(b_entries)}"

    a_entries = get_entries(TENANT_A)
    a_entity_ids = {e["entityId"] for e in a_entries}
    assert "pump-001" not in a_entity_ids, "FAIL: tenant A can see tenant B's entity!"

    print("  PASS: tenant B's entries are invisible to tenant A.\n")


def test_query_by_entity():
    """Gate check 5: filtering by entity_id returns only that entity's history."""
    print("--- Test 5: Query by Entity ---")

    sensor_entries = get_entries(TENANT_A, entity_id="sensor-001")
    assert len(sensor_entries) == 2, f"FAIL: sensor-001 should have 2 entries, got {len(sensor_entries)}"
    assert all(e["entityId"] == "sensor-001" for e in sensor_entries)

    ahu_entries = get_entries(TENANT_A, entity_id="ahu-001")
    assert len(ahu_entries) == 1, f"FAIL: ahu-001 should have 1 entry, got {len(ahu_entries)}"

    print("  PASS: entity-scoped queries return correct subsets.\n")


def main():
    print("=" * 60)
    print("  DAY 2 GATE TEST")
    print("  Append-only hash-chained Change Log")
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
    total = 5

    try:
        test_append_and_read()
        passed += 1
    except AssertionError as e:
        print(f"  FAIL: {e}\n")

    try:
        test_chain_valid()
        passed += 1
    except AssertionError as e:
        print(f"  FAIL: {e}\n")

    try:
        test_tamper_detection()
        passed += 1
    except AssertionError as e:
        print(f"  FAIL: {e}\n")

    try:
        test_tenant_isolation()
        passed += 1
    except AssertionError as e:
        print(f"  FAIL: {e}\n")

    try:
        test_query_by_entity()
        passed += 1
    except AssertionError as e:
        print(f"  FAIL: {e}\n")

    # Cleanup
    clean()
    close_driver()

    print("=" * 60)
    print(f"  GATE RESULT: {passed}/{total} passed")
    if passed == total:
        print("  DAY 2 GATE: CLOSED. Ready for Day 3.")
    else:
        print("  DAY 2 GATE: OPEN. Fix failures before proceeding.")
    print("=" * 60)

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
