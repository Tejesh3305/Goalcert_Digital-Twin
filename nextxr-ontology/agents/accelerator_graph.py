"""
accelerator_graph.py — Team 4: Accelerator Pack Composer flow.

    pack_interviewer ──(interview)──▶ END (yield for input)
        │ (select)
        ▼
    bundle_selector ──▶ pack_assembler ──▶ END
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.engine import StateGraph, END, SqliteSaver
from agents.state import AcceleratorPackState
from agents.accelerator_agents import (
    pack_interviewer, bundle_selector, pack_assembler,
    route_after_pack_interview,
)

_checkpointer = SqliteSaver()


def build_accelerator_graph():
    g = StateGraph(AcceleratorPackState, name="accelerator_pack")
    g.add_node("interviewer", pack_interviewer)
    g.add_node("selector", bundle_selector)
    g.add_node("assembler", pack_assembler)

    g.set_entry_point("interviewer")

    g.add_conditional_edges("interviewer", route_after_pack_interview, {
        "interview": END,
        "select": "selector",
    })
    g.add_edge("selector", "assembler")
    g.add_edge("assembler", END)

    return g.compile(checkpointer=_checkpointer)


app = build_accelerator_graph()
