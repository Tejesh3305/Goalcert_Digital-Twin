"""
copilot — the embedded agentic layer of the digital twin.

This package is the platform-native home of the agents that were prototyped in
the standalone agentic engine (collins-demo/orchestrator). They now run INSIDE
the twin, reading the live machine-twin runtime (twins/runtime.py) directly
rather than over HTTP from a separate service.

  config.py     Claude/Anthropic configuration (key, model, effort profiles)
  knowledge.py  the fault-library / compliance / incident-memory RAG store
  agents.py     the agents themselves

Every agent is keyless-safe: with no ANTHROPIC_API_KEY set, each one falls back
to a deterministic stub so the whole product still runs. Use `agent_trace()` to
find out which path a call actually took — a stub answer must never be mistaken
for a real one.
"""
from __future__ import annotations

from .config import config, copilot_status
from .agents import agent_trace

__all__ = ["config", "copilot_status", "agent_trace"]
