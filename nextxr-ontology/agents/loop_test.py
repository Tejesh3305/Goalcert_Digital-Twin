#!/usr/bin/env python3
"""
loop_test.py — end-to-end proof of the closed loop, no UI.

    1. Bundle Author authors + publishes a NEW vertical (with a Tier-C rule).
    2. Twin-build flow classifies into that vertical, loads the authored bundle,
       validates, and commits a live twin to Neo4j.

Run AFTER the databases are up:  docker compose up -d neo4j redis
Usage:  python -m agents.loop_test
"""
import sys
import traceback
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.bundle_graph import build_bundle_graph
from agents.twin_graph import build_twin_graph
from agents.state import new_bundle_state, new_twin_state
from agents.registry import get_registry


def author_bundle(domain, bundle_name, expert_msg):
    ba = build_bundle_graph()
    sid = f"loop-ba-{domain}"
    ba.invoke(new_bundle_state("demo-loop", sid, domain=domain,
                               bundle_name=bundle_name), thread_id=sid)
    cur = ba.get_state(sid)
    cur["conversation"].append({"role": "user", "content": expert_msg})
    out = ba.invoke(cur, thread_id=sid, start_at="interviewer")
    print(f"  interview -> next={out.get('next_action')} "
          f"rules={len(out.get('rules', []))} "
          f"lint_ok={(out.get('lint_result') or {}).get('ok')}")
    if out.get("next_action") != "await_approval":
        print("  interviewer still gathering; sending a 'go ahead' nudge")
        cur = ba.get_state(sid)
        cur["conversation"].append({"role": "user", "content": "That's everything — build it now."})
        out = ba.invoke(cur, thread_id=sid, start_at="interviewer")
        print(f"  interview(2) -> next={out.get('next_action')} rules={len(out.get('rules', []))}")
    if out.get("next_action") == "await_approval":
        cur = ba.get_state(sid)
        cur["approved"] = True
        out = ba.invoke(cur, thread_id=sid, start_at="approval_gate")
    bid = out.get("published_bundle")
    if bid:
        b = get_registry().load(bid)
        print(f"  PUBLISHED {bid}: rules={[r.get('behavior_id') for r in b.get('rules', [])]}")
    return bid


def build_twin(tenant, twin_name, user_msg):
    tw = build_twin_graph()
    sid = f"loop-tw-{tenant}"
    tw.invoke(new_twin_state(tenant, sid, twin_name=twin_name), thread_id=sid)
    cur = tw.get_state(sid)
    cur["conversation"].append({"role": "user", "content": user_msg})
    out = tw.invoke(cur, thread_id=sid, start_at="concierge")
    print(f"  domain={out.get('domain')} bundle={out.get('loaded_bundles')} "
          f"validation_ok={(out.get('validation') or {}).get('ok')} "
          f"committed={out.get('committed')}")
    from graph.query import GraphQuery
    q = GraphQuery()
    for lbl in ("Location", "PhysicalAsset"):
        rows = q.list_by_label(tenant, lbl, limit=10)
        print(f"    {lbl}: {[(n.get('displayName'), n['canonicalType'].split('#')[-1]) for n in rows]}")
    return out.get("committed")


def main():
    print("=" * 66)
    print("  CLOSED-LOOP TEST — author a vertical, then build a twin from it")
    print("=" * 66)
    try:
        print("\n[1] Bundle Author publishes a new 'aquaculture' vertical:")
        bid = author_bundle(
            "aquaculture", "Aqua Pack",
            "Aquaculture fish tanks. Entities: FishTank. Measure water "
            "temperature in Celsius. Fault: water too warm. Build it now.")
        assert bid, "bundle was not published"

        print("\n[2] Twin-build flow builds a twin from the authored bundle:")
        ok = build_twin(
            "loop-aqua-twin", "Aqua Twin",
            "I run an aquaculture facility with fish tanks. Monitor water "
            "temperature. Go ahead and build it.")
        assert ok, "twin was not committed"

        print("\n" + "=" * 66)
        print("  CLOSED LOOP: CLOSED. Authored vertical -> live twin. [OK]")
        print("=" * 66)
        return 0
    except Exception:
        traceback.print_exc()
        print("\n  CLOSED LOOP: FAILED.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
