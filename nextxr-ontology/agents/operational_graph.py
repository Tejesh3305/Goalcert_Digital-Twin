"""
operational_graph.py — Team 2: Runtime / Operational flow.

Triggered by the feed loop (or manually via API) when an Incident is created.
Not conversational — no human drives it; the operator reads the output on a
dashboard.

    context_gatherer ──▶ diagnosis_agent ──▶ recommender_agent ──▶ END

All three nodes are pure functions — no interrupts, no human-in-the-loop.
The Context Gatherer populates graph context; the Diagnosis and Recommender
agents use LLM reasoning with deterministic stubs.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.engine import StateGraph, END, SqliteSaver
from agents.state import OperationalState
from agents.operational_agents import (
    context_gatherer, diagnosis_agent, recommender_agent,
    route_after_gather, route_after_diagnose,
)

_checkpointer = SqliteSaver()


def build_operational_graph():
    g = StateGraph(OperationalState, name="operational")
    g.add_node("gather", context_gatherer)
    g.add_node("diagnose", diagnosis_agent)
    g.add_node("recommend", recommender_agent)

    g.set_entry_point("gather")

    g.add_conditional_edges("gather", route_after_gather, {
        "diagnose": "diagnose",
        "done": END,
    })
    g.add_conditional_edges("diagnose", route_after_diagnose, {
        "recommend": "recommend",
        "done": END,
    })
    g.add_edge("recommend", END)

    return g.compile(checkpointer=_checkpointer)


app = build_operational_graph()
