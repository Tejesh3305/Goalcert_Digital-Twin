"""
plugin_agents.py — Team 4: Plugin Scaffolder agents.

Helps platform engineers extend the platform via the Plugin SDK. Two nodes:
    Plugin Interviewer → Plugin Scaffolder → END

Six extension points:
    adapter   — ingest external telemetry into TelemetrySample
    behavior  — Behavior subclass implementing evaluate()
    view      — frontend panel component skeleton
    webhook   — FastAPI route for external event handling
    transform — data transform pipeline
    auth      — authentication middleware
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.gateway import get_gateway
from agents.engine import INTERRUPT_KEY

EXTENSION_POINTS = ["adapter", "behavior", "view", "webhook", "transform", "auth"]


def _convo(state: dict) -> str:
    return "\n".join(f"{m.get('role')}: {m.get('content', '')}"
                     for m in state.get("conversation", [])) or "(no messages)"


# ==========================================================================
#  Plugin Interviewer (LLM, user-facing)
# ==========================================================================
def plugin_interviewer(state: dict) -> dict:
    """Interview the developer about which extension point, what their plugin
    does, and its inputs/outputs. Yields back for input."""
    gw = get_gateway()

    has_user = any(m.get("role") == "user" for m in state.get("conversation", []))
    if not has_user:
        greeting = ("What kind of plugin do you want to build? Extension points: "
                    f"{', '.join(EXTENSION_POINTS)}. Tell me what it should do.")
        return {
            "conversation": list(state.get("conversation", [])) +
                            [{"role": "assistant", "content": greeting}],
            "reply_to_user": greeting,
            "next_action": "interview",
            INTERRUPT_KEY: "awaiting_developer_input",
        }

    def _stub() -> dict:
        text = _convo(state).lower()
        ext = None
        for ep in EXTENSION_POINTS:
            if ep in text:
                ext = ep
                break
        return {
            "reply_to_user": f"Got it — scaffolding a '{ext or 'adapter'}' plugin.",
            "extension_point": ext or "adapter",
            "plugin_name": "my-plugin",
            "plugin_config": {"description": "Custom plugin", "inputs": [], "outputs": []},
            "ready_to_scaffold": True,
        }

    result = gw.complete_json(
        tenant_id=state["tenant_id"], session_id=state["session_id"],
        system=("You interview a developer to scaffold a NextXR Plugin SDK extension. "
                f"Extension points: {EXTENSION_POINTS}. Return JSON "
                "{\"reply_to_user\": str, \"extension_point\": str|null, "
                "\"plugin_name\": str|null, "
                "\"plugin_config\": {\"description\": str, \"inputs\": [str], \"outputs\": [str]}|null, "
                "\"ready_to_scaffold\": bool}. "
                "Set ready_to_scaffold=true once you know the extension point "
                "and what the plugin does."),
        user=f"Conversation:\n{_convo(state)}",
        stub=_stub(),
    )

    reply = result.get("reply_to_user") or ""
    convo = list(state.get("conversation", []))
    if reply:
        convo = convo + [{"role": "assistant", "content": reply}]

    ready = bool(result.get("ready_to_scaffold"))
    update = {
        "conversation": convo,
        "reply_to_user": reply,
        "extension_point": result.get("extension_point") or state.get("extension_point"),
        "plugin_name": result.get("plugin_name") or state.get("plugin_name"),
        "plugin_config": result.get("plugin_config") or state.get("plugin_config"),
        "next_action": "scaffold" if ready else "interview",
    }
    if not ready:
        update[INTERRUPT_KEY] = "awaiting_developer_input"
    return update


# ==========================================================================
#  Plugin Scaffolder (Hybrid — templates + optional LLM refinement)
# ==========================================================================
def plugin_scaffolder(state: dict) -> dict:
    """Generate Plugin SDK boilerplate for the chosen extension point.
    The developer writes the real logic; we scaffold the contract."""
    gw = get_gateway()
    ext = state.get("extension_point") or "adapter"
    name = state.get("plugin_name") or "my-plugin"
    config = state.get("plugin_config") or {}
    desc = config.get("description", "Custom plugin")

    # Deterministic templates per extension point.
    templates = {
        "adapter": {
            "files": [{
                "path": f"{name}/adapter.py",
                "language": "python",
                "content": (
                    f'"""{name} — Data Adapter Plugin"""\n\n'
                    "from behaviors.registry import TelemetrySample\n"
                    "from datetime import datetime, timezone\n\n\n"
                    "class Adapter:\n"
                    f'    """Adapter: {desc}"""\n\n'
                    "    def __init__(self, config: dict):\n"
                    "        self.config = config\n\n"
                    "    def connect(self) -> None:\n"
                    '        """Establish connection to the external source."""\n'
                    "        raise NotImplementedError\n\n"
                    "    def poll(self) -> list[TelemetrySample]:\n"
                    '        """Poll for new samples. Return a list of TelemetrySample."""\n'
                    "        raise NotImplementedError\n\n"
                    "    def close(self) -> None:\n"
                    '        """Clean up resources."""\n'
                    "        pass\n"
                ),
            }],
            "readme": f"# {name}\n\nData adapter plugin. Implement `connect()` and `poll()`.",
        },
        "behavior": {
            "files": [{
                "path": f"{name}/behavior.py",
                "language": "python",
                "content": (
                    f'"""{name} — Behavior Plugin"""\n\n'
                    "from behaviors.registry import Behavior, Tier, Finding, TelemetrySample\n"
                    "from graph.query import GraphQuery\n\n\n"
                    f"class {name.replace('-', '_').title().replace('_', '')}Behavior(Behavior):\n"
                    f'    behavior_id = "{name}"\n'
                    "    tier = Tier.C\n"
                    '    watches = []  # signals this behavior reacts to\n'
                    f'    emits = "{desc}"\n\n'
                    "    def evaluate(self, sample: TelemetrySample, "
                    "query: GraphQuery) -> list[Finding]:\n"
                    '        """Evaluate a sample and return 0+ Findings."""\n'
                    "        raise NotImplementedError\n"
                ),
            }],
            "readme": f"# {name}\n\nBehavior plugin. Implement `evaluate()`.",
        },
        "view": {
            "files": [{
                "path": f"{name}/Panel.jsx",
                "language": "jsx",
                "content": (
                    f"/* {name} — View Plugin */\n"
                    "import React from 'react';\n"
                    "import { useTwin } from '../context/TwinContext';\n"
                    "import { usePolling } from '../hooks/useApi';\n"
                    "import api from '../api/client';\n\n"
                    f"export default function {name.replace('-', '_').title().replace('_', '')}Panel() {{\n"
                    "  const { activeTenant } = useTwin();\n"
                    "  // Add your panel logic here\n"
                    "  return (\n"
                    f'    <div className="panel">\n'
                    f"      <h2>{name}</h2>\n"
                    f"      <p>{desc}</p>\n"
                    "    </div>\n"
                    "  );\n"
                    "}\n"
                ),
            }],
            "readme": f"# {name}\n\nView plugin. Register the panel in nav.js and App.jsx.",
        },
        "webhook": {
            "files": [{
                "path": f"{name}/routes.py",
                "language": "python",
                "content": (
                    f'"""{name} — Webhook Plugin"""\n\n'
                    "from fastapi import APIRouter, Request\n\n"
                    f'router = APIRouter(prefix="/api/v1/plugins/{name}", tags=["{name}"])\n\n\n'
                    f'@router.post("/event")\n'
                    "async def handle_event(request: Request):\n"
                    f'    """{desc}"""\n'
                    "    body = await request.json()\n"
                    "    # Process the incoming event\n"
                    '    return {"status": "received"}\n'
                ),
            }],
            "readme": f"# {name}\n\nWebhook plugin. Mount the router in server/main.py.",
        },
        "transform": {
            "files": [{
                "path": f"{name}/transform.py",
                "language": "python",
                "content": (
                    f'"""{name} — Transform Plugin"""\n\n\n'
                    "class Transform:\n"
                    f'    """{desc}"""\n\n'
                    "    def __init__(self, config: dict):\n"
                    "        self.config = config\n\n"
                    "    def process(self, record: dict) -> dict:\n"
                    '        """Transform a single record. Return the transformed dict."""\n'
                    "        raise NotImplementedError\n\n"
                    "    def batch(self, records: list[dict]) -> list[dict]:\n"
                    "        return [self.process(r) for r in records]\n"
                ),
            }],
            "readme": f"# {name}\n\nTransform plugin. Implement `process()`.",
        },
        "auth": {
            "files": [{
                "path": f"{name}/middleware.py",
                "language": "python",
                "content": (
                    f'"""{name} — Auth Middleware Plugin"""\n\n'
                    "from fastapi import Request, HTTPException\n"
                    "from starlette.middleware.base import BaseHTTPMiddleware\n\n\n"
                    f"class {name.replace('-', '_').title().replace('_', '')}Auth(BaseHTTPMiddleware):\n"
                    f'    """{desc}"""\n\n'
                    "    async def dispatch(self, request: Request, call_next):\n"
                    "        # Implement your auth logic here\n"
                    "        # Example: check token, validate session, etc.\n"
                    "        response = await call_next(request)\n"
                    "        return response\n"
                ),
            }],
            "readme": f"# {name}\n\nAuth middleware. Add to the FastAPI app middleware stack.",
        },
    }

    scaffold = templates.get(ext, templates["adapter"])

    return {"scaffold": scaffold, "next_action": "done",
            "reply_to_user": f"Scaffolded a **{ext}** plugin: `{name}`. "
                             f"{len(scaffold['files'])} file(s) generated."}


# ==========================================================================
#  Routers
# ==========================================================================
def route_after_plugin_interview(state: dict) -> str:
    return "scaffold" if state.get("next_action") == "scaffold" else "interview"
