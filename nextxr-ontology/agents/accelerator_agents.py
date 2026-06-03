"""
accelerator_agents.py — Team 4: Accelerator Pack Composer agents.

Assembles multiple capability bundles, adapters, compliance docs, and reference
deployment topologies into a single Solution Accelerator Pack.

    Pack Interviewer → Bundle Selector → Pack Assembler → END
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.gateway import get_gateway
from agents.registry import get_registry
from agents.engine import INTERRUPT_KEY


def _convo(state: dict) -> str:
    return "\n".join(f"{m.get('role')}: {m.get('content', '')}"
                     for m in state.get("conversation", [])) or "(no messages)"


# ==========================================================================
#  Pack Interviewer (LLM, user-facing)
# ==========================================================================
def pack_interviewer(state: dict) -> dict:
    """Interview about target domain, which bundles to include, compliance needs."""
    gw = get_gateway()

    has_user = any(m.get("role") == "user" for m in state.get("conversation", []))
    if not has_user:
        # List available bundles.
        registry = get_registry()
        available = [{"id": b["bundle_id"], "name": b["name"],
                      "domains": b.get("domains", [])}
                     for b in registry.all_bundles()]
        greeting = (f"Let's assemble a Solution Accelerator Pack. "
                    f"Available bundles: {json.dumps(available)}. "
                    f"What domain and bundles should it include?")
        return {
            "conversation": list(state.get("conversation", [])) +
                            [{"role": "assistant", "content": greeting}],
            "reply_to_user": greeting,
            "next_action": "interview",
            INTERRUPT_KEY: "awaiting_input",
        }

    def _stub() -> dict:
        text = _convo(state).lower()
        domain = None
        for d in ["hvac", "maritime", "energy", "facility", "cooling"]:
            if d in text:
                domain = d
                break
        return {
            "reply_to_user": f"Got it — assembling a '{domain or 'facility'}' "
                             f"accelerator pack.",
            "target_domain": domain or "facility",
            "pack_name": f"{(domain or 'facility').title()} Accelerator Pack",
            "ready_to_select": True,
        }

    result = gw.complete_json(
        tenant_id=state["tenant_id"], session_id=state["session_id"],
        system=("You interview a user to assemble a NextXR Solution Accelerator Pack. "
                "Identify the target domain, a pack name, and what compliance "
                "standards apply. Return JSON "
                "{\"reply_to_user\": str, \"target_domain\": str|null, "
                "\"pack_name\": str|null, \"ready_to_select\": bool}. "
                "Set ready_to_select=true once you know the domain."),
        user=f"Conversation:\n{_convo(state)}",
        stub=_stub(),
    )

    reply = result.get("reply_to_user") or ""
    convo = list(state.get("conversation", []))
    if reply:
        convo = convo + [{"role": "assistant", "content": reply}]

    ready = bool(result.get("ready_to_select"))
    update = {
        "conversation": convo,
        "reply_to_user": reply,
        "target_domain": result.get("target_domain") or state.get("target_domain"),
        "pack_name": result.get("pack_name") or state.get("pack_name"),
        "next_action": "select" if ready else "interview",
    }
    if not ready:
        update[INTERRUPT_KEY] = "awaiting_input"
    return update


# ==========================================================================
#  Bundle Selector (Hybrid — registry query + LLM matching)
# ==========================================================================
def bundle_selector(state: dict) -> dict:
    """Select relevant bundles from the registry and identify adapter/compliance needs."""
    gw = get_gateway()
    domain = state.get("target_domain") or "facility"
    registry = get_registry()
    all_bundles = registry.all_bundles()

    def _stub() -> dict:
        # Select bundles matching the domain.
        matches = registry.query(domain)
        selected = [m["bundle_id"] for m in matches]
        if not selected:
            selected = [b["bundle_id"] for b in all_bundles[:1]]
        return {
            "selected_bundles": selected,
            "adapters": [{"name": "MQTT Adapter", "type": "mqtt",
                          "config": {"topic": f"{domain}/#"}}],
            "compliance_docs": [{"title": "ISO 55000 Asset Management",
                                 "content": "Compliance mapping placeholder.",
                                 "standard": "ISO 55000"}],
        }

    bundle_summary = [{"id": b["bundle_id"], "name": b["name"],
                       "domains": b.get("domains", []),
                       "rules": len(b.get("rules", []))}
                      for b in all_bundles]

    result = gw.complete_json(
        tenant_id=state["tenant_id"], session_id=state["session_id"],
        system=("You select capability bundles for a NextXR Solution Accelerator Pack. "
                "Given a target domain and available bundles, select the relevant ones "
                "and identify needed adapters and compliance standards. Return JSON "
                "{\"selected_bundles\": [bundle_id], "
                "\"adapters\": [{\"name\": str, \"type\": str, \"config\": dict}], "
                "\"compliance_docs\": [{\"title\": str, \"content\": str, \"standard\": str}]}."),
        user=f"Target domain: {domain}\n"
             f"Available bundles: {json.dumps(bundle_summary)}\n"
             f"Conversation: {_convo(state)}",
        stub=_stub(),
        max_tokens=900,
    )

    return {
        "selected_bundles": result.get("selected_bundles") or _stub()["selected_bundles"],
        "adapters": result.get("adapters") or _stub()["adapters"],
        "compliance_docs": result.get("compliance_docs") or _stub()["compliance_docs"],
        "next_action": "assemble",
    }


# ==========================================================================
#  Pack Assembler (deterministic — combines selected artifacts)
# ==========================================================================
def pack_assembler(state: dict) -> dict:
    """Assemble the selected bundles, adapters, and compliance docs into a
    Solution Accelerator Pack manifest."""
    registry = get_registry()
    selected = state.get("selected_bundles") or []
    pack_name = state.get("pack_name") or "Accelerator Pack"
    domain = state.get("target_domain") or "facility"

    # Load each selected bundle.
    bundles = []
    for bid in selected:
        b = registry.load(bid)
        if b:
            bundles.append({
                "bundle_id": b["bundle_id"],
                "name": b["name"],
                "domains": b.get("domains", []),
                "entity_count": len(b.get("entity_templates", [])),
                "rule_count": len(b.get("rules", [])),
            })

    manifest = {
        "pack_name": pack_name,
        "domain": domain,
        "version": "1.0.0",
        "bundles": bundles,
        "bundle_ids": selected,
        "adapters": state.get("adapters") or [],
        "compliance_docs": state.get("compliance_docs") or [],
        "metadata": {
            "total_bundles": len(bundles),
            "total_adapters": len(state.get("adapters") or []),
            "total_compliance_docs": len(state.get("compliance_docs") or []),
        },
    }

    return {"pack_manifest": manifest, "next_action": "done",
            "reply_to_user": f"Assembled **{pack_name}** — "
                             f"{len(bundles)} bundle(s), "
                             f"{len(state.get('adapters') or [])} adapter(s), "
                             f"{len(state.get('compliance_docs') or [])} compliance doc(s)."}


# ==========================================================================
#  Routers
# ==========================================================================
def route_after_pack_interview(state: dict) -> str:
    return "select" if state.get("next_action") == "select" else "interview"
