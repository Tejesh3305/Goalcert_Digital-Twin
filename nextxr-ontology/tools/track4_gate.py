#!/usr/bin/env python3
"""
track4_gate.py — Track 4 gate: REST API + Dashboard + Schema integration.

This script proves ten things:
  1. Server starts and /health returns healthy
  2. Schema API returns ontology version and types
  3. Write API creates an entity via POST
  4. Read API retrieves the created entity
  5. Write API updates the entity via PATCH
  6. Write API adds a relationship via POST /rel
  7. Write API rejects invalid entity (SHACL gate works over REST)
  8. Write API deletes an entity via DELETE
  9. Stats endpoint reflects all mutations
 10. Dashboard HTML loads at /

Prerequisites:
    - Neo4j must be running (docker compose up -d)
    - python -m graph.schema must have been run
    - python -m server.main must be running on port 8000

Usage:
    python tools/track4_gate.py
"""

from __future__ import annotations

import json
import sys
import urllib.request
import urllib.error
from urllib.parse import urlencode

API = "http://localhost:8000/api/v1"
TENANT = "track4-test"
CORE = "https://ontology.nextxr.io/v3/core#"
HVAC = "https://ontology.nextxr.io/v3/hvac#"

passed = 0
failed = 0


def _get(path: str, params: dict = None) -> dict:
    url = f"{API}{path}"
    if params:
        url += "?" + urlencode(params)
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def _post(path: str, body: dict = None, params: dict = None) -> tuple[int, dict]:
    url = f"{API}{path}"
    if params:
        url += "?" + urlencode(params)
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method="POST")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _patch(path: str, body: dict) -> tuple[int, dict]:
    url = f"{API}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="PATCH")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _delete(path: str, params: dict) -> tuple[int, dict]:
    url = f"{API}{path}" + "?" + urlencode(params)
    req = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  — {detail}")


def main():
    global passed, failed

    print("=" * 66)
    print("  TRACK 4 GATE TEST")
    print("  REST API + Dashboard + Schema — full server stack")
    print("=" * 66)
    print()

    # 1. Health
    try:
        d = _get("/health")
        check("1. Server healthy", d.get("status") == "healthy", f"got: {d}")
    except Exception as e:
        check("1. Server healthy", False, f"Server unreachable: {e}")
        print("\n  Server must be running: python -m server.main\n")
        sys.exit(1)

    # 2. Schema API
    try:
        d = _get("/schema/version")
        check("2a. Schema version", "ontologyVersion" in d, f"got: {d}")
    except Exception as e:
        check("2a. Schema version", False, str(e))

    try:
        d = _get("/schema/categories")
        check("2b. Schema categories", isinstance(d, list) and len(d) >= 10,
              f"got {len(d) if isinstance(d, list) else d}")
    except Exception as e:
        check("2b. Schema categories", False, str(e))

    # 3. Create entity via POST
    site_id = None
    try:
        code, d = _post("/entities", {
            "tenant": TENANT,
            "canonical_type": CORE + "Site",
            "actor": "track4-test",
            "properties": {"displayName": "Track 4 Test Site"},
        })
        check("3. Create entity (POST)", code == 200 and d.get("status") == "created",
              f"code={code}, body={d}")
        site_id = d.get("node_id")
    except Exception as e:
        check("3. Create entity (POST)", False, str(e))

    # 4. Read it back via GET
    if site_id:
        try:
            d = _get(f"/entities/{site_id}", {"tenant": TENANT})
            node = d.get("node", {})
            check("4. Read entity (GET)", node.get("displayName") == "Track 4 Test Site",
                  f"got: {node.get('displayName')}")
        except Exception as e:
            check("4. Read entity (GET)", False, str(e))
    else:
        check("4. Read entity (GET)", False, "no site_id from create")

    # 5. Update via PATCH
    if site_id:
        try:
            code, d = _patch(f"/entities/{site_id}", {
                "tenant": TENANT,
                "actor": "track4-test",
                "properties": {"displayName": "Track 4 Updated Site", "status": "active"},
            })
            check("5. Update entity (PATCH)", code == 200 and d.get("status") == "updated",
                  f"code={code}, body={d}")
        except Exception as e:
            check("5. Update entity (PATCH)", False, str(e))

    # 6. Create a second entity + relate
    space_id = None
    if site_id:
        try:
            code, d = _post("/entities", {
                "tenant": TENANT,
                "canonical_type": CORE + "Space",
                "actor": "track4-test",
                "properties": {"displayName": "Test Room"},
            })
            space_id = d.get("node_id")

            code2, d2 = _post(f"/entities/{site_id}/rel", {
                "tenant": TENANT,
                "actor": "track4-test",
                "predicate": "nxr:hasPart",
                "target_id": space_id,
            })
            check("6. Relate entities (POST /rel)",
                  code2 == 200 and d2.get("status") == "related",
                  f"code={code2}, body={d2}")
        except Exception as e:
            check("6. Relate entities (POST /rel)", False, str(e))

    # 7. Reject invalid entity (missing required tenant in body)
    try:
        code, d = _post("/entities", {
            "tenant": TENANT,
            "canonical_type": "http://bogus.example/FakeClass",
            "actor": "track4-test",
            "properties": {"displayName": "This should fail"},
        })
        check("7. Reject invalid entity", code == 422,
              f"expected 422, got {code}")
    except Exception as e:
        check("7. Reject invalid entity", False, str(e))

    # 8. Delete entity
    if space_id:
        try:
            code, d = _delete(f"/entities/{space_id}",
                              {"tenant": TENANT, "actor": "track4-test"})
            check("8. Delete entity (DELETE)", code == 200 and d.get("status") == "deleted",
                  f"code={code}, body={d}")
        except Exception as e:
            check("8. Delete entity (DELETE)", False, str(e))
    else:
        check("8. Delete entity (DELETE)", False, "no space_id")

    # 9. Stats reflect mutations
    try:
        d = _get("/stats", {"tenant": TENANT})
        check("9. Stats reflect mutations",
              d.get("total_entities", 0) >= 1 and d.get("changelog_events", 0) >= 3,
              f"entities={d.get('total_entities')}, events={d.get('changelog_events')}")
    except Exception as e:
        check("9. Stats reflect mutations", False, str(e))

    # 10. Dashboard loads
    try:
        req = urllib.request.Request("http://localhost:8000/")
        with urllib.request.urlopen(req) as r:
            html = r.read().decode()
            check("10. Dashboard HTML loads",
                  "nextxr" in html.lower() and "chart" in html,
                  f"length={len(html)}")
    except Exception as e:
        check("10. Dashboard HTML loads", False, str(e))

    # Cleanup: delete the test site
    if site_id:
        try:
            _delete(f"/entities/{site_id}", {"tenant": TENANT, "actor": "cleanup"})
        except Exception:
            pass

    # Summary
    total = passed + failed
    print()
    print(f"  {passed}/{total} passed", end="")
    if failed:
        print(f"  ({failed} FAILED)")
    else:
        print("  ALL CLOSED")
    print("=" * 66)

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
