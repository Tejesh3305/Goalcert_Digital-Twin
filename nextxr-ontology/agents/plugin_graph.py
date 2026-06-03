"""
plugin_graph.py — Team 4: Plugin Scaffolder flow.

    plugin_interviewer ──(interview)──▶ END (yield for developer input)
        │ (scaffold)
        ▼
    plugin_scaffolder ──▶ END
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.engine import StateGraph, END, SqliteSaver
from agents.state import PluginScaffoldState
from agents.plugin_agents import (
    plugin_interviewer, plugin_scaffolder,
    route_after_plugin_interview,
)

_checkpointer = SqliteSaver()


def build_plugin_graph():
    g = StateGraph(PluginScaffoldState, name="plugin_scaffolder")
    g.add_node("interviewer", plugin_interviewer)
    g.add_node("scaffolder", plugin_scaffolder)

    g.set_entry_point("interviewer")

    g.add_conditional_edges("interviewer", route_after_plugin_interview, {
        "interview": END,
        "scaffold": "scaffolder",
    })
    g.add_edge("scaffolder", END)

    return g.compile(checkpointer=_checkpointer)


app = build_plugin_graph()
