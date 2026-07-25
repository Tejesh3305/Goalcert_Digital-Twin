"""
NextXR Agents — the agentic core.

Six MVP agents across two flows, one closed loop:

  Twin-Building chain (twin_graph.py):
    Concierge → Domain Classifier → Capability Composer → Validator → Graph Writer

  Bundle Author sub-graph (bundle_graph.py), the meta-agent:
    Interviewer → Ontology Drafter → Rule Author → Linter → [human gate] → Publisher

Five disciplines keep the seams invisible:
  * State, not calls — agents only read/write the shared State; the graph owns
    control flow. No agent imports or invokes another.
  * One writer — only Graph Writer / Publisher mutate persistent stores.
  * tenant_id everywhere — threaded through State from entry.
  * Idempotent commits — UUIDv7 keys + upserts, safe under checkpoint retries.
  * Human gate before publish — Bundle Author can't sign without approved=true.

Built on an in-house, LangGraph-compatible engine (engine.py) so the code reads
exactly like the spec and can swap to real LangGraph by changing one import.
The LLM layer (gateway.py) uses Claude when ANTHROPIC_API_KEY is present, else
deterministic stubs — so the whole flow and demo run with or without a key.
That is the platform's ONLY LLM: these agent graphs and the embedded agent layer
(copilot/) share one key, one model policy, one failure mode.
"""

from .state import TwinBuildState, BundleAuthorState
from .engine import StateGraph, END, CheckpointSaver, SqliteSaver

__all__ = ["TwinBuildState", "BundleAuthorState", "StateGraph", "END",
           "CheckpointSaver", "SqliteSaver"]
