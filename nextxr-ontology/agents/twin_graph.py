"""
twin_graph.py — the twin-building orchestration graph (full product).

Eight nodes. The three Phase-1 additions (Vision Agent, Schema Mapper, Scene
Generator) are backwards compatible: without uploaded files the Vision Agent is
skipped; the Schema Mapper degrades to bundle templates in stub mode; the Scene
Generator only runs if the twin was built from uploaded files.

    concierge ──(ask)──▶ END (yield to human)
        │ (classify)                  ┐
        │ (vision) ──▶ vision_agent ──┘──▶ classifier
        ▼
    classifier ──(low)──▶ concierge
        │ (ok)
        ▼
    composer ──▶ schema_mapper ──▶ validator ──(fail)──▶ concierge
                                      │ (ok)
                                      ▼
                                graph_writer ──(scene)──▶ scene_generator ──▶ END
                                      │ (done)
                                      ▼
                                     END
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
    graph_writer, vision_agent, schema_mapper, scene_generator,
    route_after_concierge_v2, route_after_classify, route_after_validate,
    route_after_graph_writer,
)

_checkpointer = SqliteSaver()


def build_twin_graph():
    g = StateGraph(TwinBuildState, name="twin_build")

    # --- existing nodes ---
    g.add_node("concierge", concierge_agent)
    g.add_node("classifier", domain_classifier)
    g.add_node("composer", capability_composer)
    g.add_node("validator", validator)
    g.add_node("graph_writer", graph_writer)

    # --- Phase 1 additions ---
    g.add_node("vision", vision_agent)
    g.add_node("schema_mapper", schema_mapper)
    g.add_node("scene_generator", scene_generator)

    g.set_entry_point("concierge")

    # Concierge: yield / classify / branch to vision if files uploaded.
    g.add_conditional_edges("concierge", route_after_concierge_v2, {
        "ask": END,
        "classify": "classifier",
        "vision": "vision",
    })
    # Vision feeds into the classifier (findings enrich classification).
    g.add_edge("vision", "classifier")

    # Confidence gate (unchanged from MVP).
    g.add_conditional_edges("classifier", route_after_classify, {
        "low": "concierge",
        "ok": "composer",
    })

    # Composer loads bundle; Schema Mapper refines into ontology entities.
    g.add_edge("composer", "schema_mapper")
    g.add_edge("schema_mapper", "validator")

    # Validation gate — the feedback loop (unchanged).
    g.add_conditional_edges("validator", route_after_validate, {
        "fail": "concierge",
        "ok": "graph_writer",
    })

    # Graph Writer → optional Scene Generator → END.
    g.add_conditional_edges("graph_writer", route_after_graph_writer, {
        "scene": "scene_generator",
        "done": END,
    })
    g.add_edge("scene_generator", END)

    return g.compile(checkpointer=_checkpointer)


# Process-wide compiled app.
app = build_twin_graph()
