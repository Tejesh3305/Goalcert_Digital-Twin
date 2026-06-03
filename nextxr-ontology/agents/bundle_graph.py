"""
bundle_graph.py — the Bundle Author sub-graph (full product).

Nine nodes. The three Phase-3 additions (Behavior Modeler, Elicitation Designer,
Asset Curator) slot into the chain between Drafter and Linter. The existing
Interviewer → Drafter → Rule Author → Linter → Gate → Publisher flow is preserved;
the new nodes add richer artifacts that the Publisher includes in the bundle.

    interviewer ──(interview)──▶ END (yield to expert)
        │ (draft)
        ▼
    drafter ──▶ behavior_modeler ──▶ rule_author ──▶ elicitation_designer ──▶ asset_curator ──▶ linter
                                                                                                │
                                                               ┌──(fail)────────────────────────┘
                                                               ▼
                                                          interviewer
                                                          (ok) ──▶ approval_gate ──(wait)──▶ END
                                                                          │ (publish)
                                                                          ▼
                                                                      publisher ──▶ END
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
    behavior_modeler, elicitation_designer, asset_curator,
    route_after_interview, route_after_lint, route_after_gate,
)

_checkpointer = SqliteSaver()


def build_bundle_graph():
    g = StateGraph(BundleAuthorState, name="bundle_author")

    # --- existing nodes ---
    g.add_node("interviewer", interviewer)
    g.add_node("drafter", ontology_drafter)
    g.add_node("rule_author", rule_author)
    g.add_node("linter", linter)
    g.add_node("approval_gate", approval_gate)
    g.add_node("publisher", publisher)

    # --- Phase 3 additions ---
    g.add_node("behavior_modeler", behavior_modeler)
    g.add_node("elicitation_designer", elicitation_designer)
    g.add_node("asset_curator", asset_curator)

    g.set_entry_point("interviewer")

    g.add_conditional_edges("interviewer", route_after_interview, {
        "interview": END,
        "draft": "drafter",
    })

    # Drafter → Behavior Modeler → Rule Author → Elicitation → Asset Curator → Linter
    g.add_edge("drafter", "behavior_modeler")
    g.add_edge("behavior_modeler", "rule_author")
    g.add_edge("rule_author", "elicitation_designer")
    g.add_edge("elicitation_designer", "asset_curator")
    g.add_edge("asset_curator", "linter")

    g.add_conditional_edges("linter", route_after_lint, {
        "fail": "interviewer",
        "ok": "approval_gate",
    })
    g.add_conditional_edges("approval_gate", route_after_gate, {
        "wait": END,
        "publish": "publisher",
    })
    g.add_edge("publisher", END)

    return g.compile(checkpointer=_checkpointer)


app = build_bundle_graph()
