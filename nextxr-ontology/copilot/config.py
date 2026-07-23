"""
config.py — configuration for the embedded Claude agents.

Everything is read from the environment (the repo-root .env is loaded
automatically, same as agents/gateway.py does). Nothing here is required: with
no key set every agent falls back to a deterministic stub.

  ANTHROPIC_API_KEY   the Claude key. Set it to light up real reasoning.
  NXR_CLAUDE_MODEL    override the model (default: claude-sonnet-5).
  NXR_COPILOT_EFFORT  override the default effort for the "deep" agents.

Values are read from os.environ on every access rather than snapshotted at
import, so a key can be injected by a test or a process manager after import.
"""
from __future__ import annotations

import os
from pathlib import Path

# Load the repo-root .env once so ANTHROPIC_API_KEY is available even when the
# process wasn't started with it exported.
try:
    from dotenv import load_dotenv
    # copilot/config.py -> nextxr-ontology/ -> nextxr-ontology-v3/
    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
    load_dotenv()  # cwd fallback
except Exception:  # noqa: BLE001 — dotenv is optional
    pass

# Sonnet 5 — near-Opus quality on this workload at $3/$15 per MTok instead of
# Opus 4.8's $5/$25. The trigger was the public demo deployment: with the API
# open, copilot traffic bills to our key from anyone who finds the URL, and the
# quality gap here does not justify paying Opus rates for it.
#
# The earlier default was Opus 4.8 with a "do not downgrade" note, on the
# grounds that these agents produce maintenance documentation a technician acts
# on. That reasoning still holds for the DEEP agents (diagnosis, work orders,
# procedures) — which is why they keep adaptive thinking at NXR_COPILOT_EFFORT
# (default "high"). Raise this back with NXR_CLAUDE_MODEL=claude-opus-4-8 for a
# deployment where those outputs matter more than the token bill.
DEFAULT_MODEL = "claude-sonnet-5"


class CopilotConfig:
    @property
    def ANTHROPIC_API_KEY(self) -> str:
        return (os.environ.get("ANTHROPIC_API_KEY") or "").strip()

    @property
    def CLAUDE_MODEL(self) -> str:
        return (os.environ.get("NXR_CLAUDE_MODEL")
                or os.environ.get("CLAUDE_MODEL")
                or DEFAULT_MODEL)

    @property
    def DEEP_EFFORT(self) -> str:
        """Effort for the heavy reasoning agents (diagnosis, work orders,
        procedures). low | medium | high | xhigh | max."""
        return os.environ.get("NXR_COPILOT_EFFORT") or "high"

    @property
    def claude_enabled(self) -> bool:
        return bool(self.ANTHROPIC_API_KEY)


config = CopilotConfig()


def copilot_status() -> dict:
    """What the embedded agent layer is actually configured to do. Surfaced on
    the health endpoint so a stub answer is never mistaken for a real one."""
    return {
        "claude_enabled": config.claude_enabled,
        "model": config.CLAUDE_MODEL if config.claude_enabled else None,
        "deep_effort": config.DEEP_EFFORT,
        "mode": "live" if config.claude_enabled else "stub",
        "hint": None if config.claude_enabled else
                "Set ANTHROPIC_API_KEY in .env to enable real agent reasoning. "
                "Until then every agent returns its deterministic fallback.",
    }
