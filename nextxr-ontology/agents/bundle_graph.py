"""
bundle_graph.py — the Bundle Author sub-graph.

    interviewer ──(interview)──▶ END (yield to expert)
        │ (draft)
        ▼
    drafter ──▶ rule_author ──▶ linter ──(fail)──▶ interviewer
                                   │ (ok)
                                   ▼
                              approval_gate ──(wait)──▶ END (yield for approval)
                                   │ (publish)
                                   ▼
                               publisher ──▶ END

The two interrupts (interviewer yielding for expert input, and the approval
gate) make this resumable across turns and across a process restart — the human
approval gate is a real pause, not a flag check in a loop.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.engine import StateGraph, END, SqliteSaver
from agents.state import BundleAuthorState
from agents.bundle_agents import (
    interviewer, ontology_drafter, rule_author, linter, approval_gate, publisher,
    route_after_interview, route_after_lint, route_after_gate,
)

_checkpointer = SqliteSaver()


def build_bundle_graph():
    g = StateGraph(BundleAuthorState, name="bundle_author")
    g.add_node("interviewer", interviewer)
    g.add_node("drafter", ontology_drafter)
    g.add_node("rule_author", rule_author)
    g.add_node("linter", linter)
    g.add_node("approval_gate", approval_gate)
    g.add_node("publisher", publisher)

    g.set_entry_point("interviewer")

    g.add_conditional_edges("interviewer", route_after_interview, {
        "interview": END,        # yield turn to the expert
        "draft": "drafter",
    })
    g.add_edge("drafter", "rule_author")
    g.add_edge("rule_author", "linter")
    g.add_conditional_edges("linter", route_after_lint, {
        "fail": "interviewer",   # lint failed — go re-interview
        "ok": "approval_gate",
    })
    g.add_conditional_edges("approval_gate", route_after_gate, {
        "wait": END,             # yield for human approval
        "publish": "publisher",
    })
    g.add_edge("publisher", END)

    return g.compile(checkpointer=_checkpointer)


app = build_bundle_graph()
