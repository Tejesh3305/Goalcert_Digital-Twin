"""
engine.py — a tiny, zero-dependency, LangGraph-compatible state-graph executor.

Implements the slice of the LangGraph API the spec uses, with the SAME names and
shapes, so agent/graph code reads exactly like the spec and can be swapped for
the real `langgraph` package by changing imports only:

    from agents.engine import StateGraph, END, SqliteSaver   # in-house
    # later:
    from langgraph.graph import StateGraph, END
    from langgraph.checkpoint.sqlite import SqliteSaver

Semantics
---------
* Nodes are callables `(state: dict) -> dict`. The returned dict is MERGED onto
  the running state (partial updates, like LangGraph reducers but shallow).
* Unconditional edges: `add_edge(src, dst)`.
* Conditional edges: `add_conditional_edges(src, router, mapping)` where
  `router(state) -> key` and `mapping[key]` is the next node (or END).
* `END` terminates the run.
* `set_entry_point(node)` sets the start.
* `compile(checkpointer=...)` returns an app with `.invoke()` / `.step()`.

Checkpointing
-------------
`SqliteSaver` persists the full state after every node, keyed by a thread_id
(we use session_id). This makes runs RESUMABLE across process restarts — the
"yield turn to the human, come back later" pattern the Concierge needs, and the
human-approval gate the Bundle Author needs. Swap to a Temporal/Postgres saver
later without touching graph code.

Human-in-the-loop
-----------------
A node may return the sentinel {"__interrupt__": "<reason>"} to pause the run
(e.g. Concierge yielding for user input, or the Bundle Author's approval gate).
`invoke()` stops, persists, and returns; a later `invoke(resume_input=...)`
continues from the saved checkpoint.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Callable, Optional

# Sentinel marking a graph terminus (matches LangGraph's END).
END = "__end__"

# A node returns this to pause the graph for external input.
INTERRUPT_KEY = "__interrupt__"


# --------------------------------------------------------------------------
#  Checkpointer
# --------------------------------------------------------------------------
class SqliteSaver:
    """Persists graph state per thread_id (we key on session_id). Stores the
    full state JSON plus the node to resume at."""

    def __init__(self, db_path: Optional[Path] = None):
        default = Path(__file__).resolve().parent.parent / "data" / "agent_checkpoints.db"
        self.db_path = Path(db_path) if db_path else default
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS checkpoints (
                    thread_id   TEXT PRIMARY KEY,
                    graph_name  TEXT NOT NULL,
                    state       TEXT NOT NULL,
                    resume_at   TEXT,
                    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
                )"""
            )

    def save(self, thread_id: str, graph_name: str, state: dict,
             resume_at: Optional[str]):
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO checkpoints (thread_id, graph_name, state, resume_at) "
                "VALUES (?,?,?,?) ON CONFLICT(thread_id) DO UPDATE SET "
                "state=excluded.state, resume_at=excluded.resume_at, "
                "graph_name=excluded.graph_name, updated_at=datetime('now')",
                (thread_id, graph_name, json.dumps(state), resume_at),
            )

    def load(self, thread_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT state, resume_at FROM checkpoints WHERE thread_id=?",
                (thread_id,),
            ).fetchone()
            if not row:
                return None
            return {"state": json.loads(row["state"]), "resume_at": row["resume_at"]}

    def delete(self, thread_id: str):
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM checkpoints WHERE thread_id=?", (thread_id,))


# --------------------------------------------------------------------------
#  Graph definition
# --------------------------------------------------------------------------
class StateGraph:
    """Build a graph of nodes + edges, then compile() to run it."""

    def __init__(self, state_type=dict, name: str = "graph"):
        self.state_type = state_type
        self.name = name
        self._nodes: dict[str, Callable] = {}
        self._edges: dict[str, str] = {}                 # src -> dst (unconditional)
        self._cond: dict[str, tuple[Callable, dict]] = {}  # src -> (router, mapping)
        self._entry: Optional[str] = None

    def add_node(self, name: str, fn: Callable):
        if name in (END, INTERRUPT_KEY):
            raise ValueError(f"'{name}' is reserved")
        self._nodes[name] = fn
        return self

    def add_edge(self, src: str, dst: str):
        self._edges[src] = dst
        return self

    def add_conditional_edges(self, src: str, router: Callable, mapping: dict):
        self._cond[src] = (router, mapping)
        return self

    def set_entry_point(self, name: str):
        self._entry = name
        return self

    def compile(self, checkpointer: Optional[SqliteSaver] = None) -> "CompiledGraph":
        if self._entry is None:
            raise ValueError("entry point not set")
        return CompiledGraph(self, checkpointer)


class CompiledGraph:
    """A runnable graph. invoke() runs from entry (or resumes a checkpoint)
    until END or an interrupt."""

    def __init__(self, g: StateGraph, checkpointer: Optional[SqliteSaver]):
        self.g = g
        self.checkpointer = checkpointer
        self.max_steps = 100  # cycle guard

    def _next(self, node: str, state: dict) -> str:
        if node in self.g._cond:
            router, mapping = self.g._cond[node]
            key = router(state)
            if key not in mapping:
                raise KeyError(f"router for '{node}' returned '{key}' "
                               f"not in {list(mapping)}")
            return mapping[key]
        return self.g._edges.get(node, END)

    def invoke(self, state: Optional[dict] = None, *, thread_id: str,
               start_at: Optional[str] = None) -> dict:
        """Run the graph. If `state` is None, resume from the checkpoint for
        `thread_id`. Returns the state at END or at an interrupt (which carries
        an `__interrupt__` key naming why it paused)."""
        resume_at = None
        if state is None:
            ckpt = self.checkpointer.load(thread_id) if self.checkpointer else None
            if not ckpt:
                raise ValueError(f"no checkpoint for thread '{thread_id}'")
            state = ckpt["state"]
            resume_at = ckpt["resume_at"]

        node = start_at or resume_at or self.g._entry
        steps = 0
        while node != END and steps < self.max_steps:
            steps += 1
            fn = self.g._nodes[node]
            update = fn(state) or {}

            # Interrupt: persist and pause, recording where to resume.
            if INTERRUPT_KEY in update:
                reason = update.pop(INTERRUPT_KEY)
                state.update(update)
                resume = self._next(node, state)
                if self.checkpointer:
                    self.checkpointer.save(thread_id, self.g.name, state, resume)
                state = dict(state)
                state[INTERRUPT_KEY] = reason
                return state

            state.update(update)
            nxt = self._next(node, state)
            if self.checkpointer:
                self.checkpointer.save(thread_id, self.g.name, state, nxt)
            node = nxt

        state.pop(INTERRUPT_KEY, None)
        if self.checkpointer:
            self.checkpointer.save(thread_id, self.g.name, state, END)
        return state

    def get_state(self, thread_id: str) -> Optional[dict]:
        if not self.checkpointer:
            return None
        ckpt = self.checkpointer.load(thread_id)
        return ckpt["state"] if ckpt else None
