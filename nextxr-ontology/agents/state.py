"""
state.py — the typed contracts every agent obeys.

One object flows through each graph. Every agent reads keys it needs and writes
keys it owns; nothing else is how they communicate ("State, not calls").

We use TypedDict for the shape (matching the spec) plus small factory helpers
that produce a fully-initialised dict, so a fresh session always has every key
present (the in-house engine merges partial updates onto this).
"""

from __future__ import annotations

from typing import TypedDict, Literal, Optional


# --------------------------------------------------------------------------
#  Twin-building flow
# --------------------------------------------------------------------------
class TwinBuildState(TypedDict):
    tenant_id: str                 # D7 lock — present on every op
    session_id: str
    conversation: list[dict]       # full dialogue, Concierge owns

    user_intent: Optional[str]     # Concierge →
    domain: Optional[str]          # Domain Classifier →
    domain_confidence: Optional[float]
    sub_type: Optional[str]

    loaded_bundles: list[str]      # Capability Composer →
    draft_entities: list[dict]     # from bundle templates (MVP)
    draft_relationships: list[dict]

    validation: Optional[dict]     # Validator → {ok: bool, errors: [...]}
    committed: bool                # Graph Writer →
    twin_id: Optional[str]

    # next_action drives routing; "ask" yields the turn back to the human.
    next_action: Literal["ask", "classify", "compose", "validate", "commit", "done"]
    errors: list[str]

    # --- presentation extras (not in the minimal spec, used by the UI) ---
    twin_name: Optional[str]       # display name for the committed twin
    reply_to_user: Optional[str]   # the Concierge's latest message to show


def new_twin_state(tenant_id: str, session_id: str,
                   twin_name: Optional[str] = None) -> TwinBuildState:
    """A fresh twin-build state with every key initialised."""
    return TwinBuildState(
        tenant_id=tenant_id,
        session_id=session_id,
        conversation=[],
        user_intent=None,
        domain=None,
        domain_confidence=None,
        sub_type=None,
        loaded_bundles=[],
        draft_entities=[],
        draft_relationships=[],
        validation=None,
        committed=False,
        twin_id=None,
        next_action="ask",
        errors=[],
        twin_name=twin_name,
        reply_to_user=None,
    )


# --------------------------------------------------------------------------
#  Bundle Author flow (the meta-agent's own state)
# --------------------------------------------------------------------------
class BundleAuthorState(TypedDict):
    tenant_id: str
    session_id: str
    conversation: list[dict]
    entity_catalogue: list[str]        # Interviewer builds these
    fault_catalogue: list[dict]
    measurement_catalogue: list[dict]
    ontology_fragment: Optional[str]   # Drafter → Turtle
    rules: list[dict]                  # Rule Author →
    lint_result: Optional[dict]        # Linter →
    approved: bool                     # HUMAN GATE
    published_bundle: Optional[str]    # Publisher →

    # --- presentation extras ---
    domain: Optional[str]              # the vertical being authored (e.g. "cooling")
    bundle_name: Optional[str]
    next_action: str                   # "interview" | "draft" | "rules" | "lint" | "await_approval" | "publish" | "done"
    reply_to_user: Optional[str]
    errors: list[str]


def new_bundle_state(tenant_id: str, session_id: str,
                     domain: Optional[str] = None,
                     bundle_name: Optional[str] = None) -> BundleAuthorState:
    return BundleAuthorState(
        tenant_id=tenant_id,
        session_id=session_id,
        conversation=[],
        entity_catalogue=[],
        fault_catalogue=[],
        measurement_catalogue=[],
        ontology_fragment=None,
        rules=[],
        lint_result=None,
        approved=False,
        published_bundle=None,
        domain=domain,
        bundle_name=bundle_name,
        next_action="interview",
        reply_to_user=None,
        errors=[],
    )
