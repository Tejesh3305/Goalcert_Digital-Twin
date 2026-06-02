"""
twin_graph.py — the twin-building orchestration graph.

The entire twin-building flow as nodes + edges. Straight edges where flow is
unconditional; two conditional edges carry all the branching (the confidence
gate and the validation feedback loop). Reads exactly like the spec.

    concierge ──(ask)──▶ END (yield to human)
        │ (classify)
        ▼
    classifier ──(low)──▶ concierge
        │ (ok)
        ▼
    composer ──▶ validator ──(fail)──▶ concierge
                     │ (ok)
                     ▼
                graph_writer ──▶ END
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.engine import StateGraph, END, SqliteSaver
from agents.state import TwinBuildState
from agents.twin_agents import (
    concierge_agent, domain_classifier, capability_composer, validator,
    graph_writer, route_after_concierge, route_after_classify, route_after_validate,
)

_checkpointer = SqliteSaver()


def build_twin_graph():
    g = StateGraph(TwinBuildState, name="twin_build")
    g.add_node("concierge", concierge_agent)
    g.add_node("classifier", domain_classifier)
    g.add_node("composer", capability_composer)
    g.add_node("validator", validator)
    g.add_node("graph_writer", graph_writer)

    g.set_entry_point("concierge")

    # Concierge decides: keep talking (yield), or proceed to classify.
    g.add_conditional_edges("concierge", route_after_concierge, {
        "ask": END,
        "classify": "classifier",
    })
    # Confidence gate.
    g.add_conditional_edges("classifier", route_after_classify, {
        "low": "concierge",
        "ok": "composer",
    })
    g.add_edge("composer", "validator")
    # Validation gate — the feedback loop.
    g.add_conditional_edges("validator", route_after_validate, {
        "fail": "concierge",
        "ok": "graph_writer",
    })
    g.add_edge("graph_writer", END)

    return g.compile(checkpointer=_checkpointer)


# Process-wide compiled app (state persistence via the SQLite checkpointer).
app = build_twin_graph()
