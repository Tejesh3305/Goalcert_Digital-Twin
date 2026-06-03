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
    next_action: Literal["ask", "classify", "compose", "map", "validate",
                         "commit", "scene", "done"]
    errors: list[str]

    # --- presentation extras (not in the minimal spec, used by the UI) ---
    twin_name: Optional[str]       # display name for the committed twin
    reply_to_user: Optional[str]   # the Concierge's latest message to show

    # --- Phase 1 expansion: Vision · Schema Mapper · Scene Generator ------
    uploaded_files: list[dict]     # [{url, type, filename}] for Vision Agent
    vision_findings: list[dict]    # [{label, count, location, confidence}]
    mapping_source: Optional[str]  # "bundle" | "mapper" — who wrote drafts
    scene_result: Optional[dict]   # {status, topology, message}


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
        uploaded_files=[],
        vision_findings=[],
        mapping_source=None,
        scene_result=None,
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

    # --- Phase 3 expansion: Behavior Modeler · Elicitation Designer · Asset Curator
    behavior_models: list[dict]        # [{fault, tier, artefact_type, artefact}]
    elicitation_questions: list[dict]  # [{question, purpose, domain_anchor}]
    asset_manifest: list[dict]         # [{entity, asset_id, source, status}]
    asset_gaps: list[str]              # entities with no matching 3D asset

    # --- presentation extras ---
    domain: Optional[str]              # the vertical being authored (e.g. "cooling")
    bundle_name: Optional[str]
    next_action: str                   # "interview"|"draft"|"model"|"rules"|"elicit"|"curate"|"lint"|"await_approval"|"publish"|"done"
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
        behavior_models=[],
        elicitation_questions=[],
        asset_manifest=[],
        asset_gaps=[],
        domain=domain,
        bundle_name=bundle_name,
        next_action="interview",
        reply_to_user=None,
        errors=[],
    )


# --------------------------------------------------------------------------
#  Operational flow (Diagnosis + Recommender agents — Team 2)
# --------------------------------------------------------------------------
class OperationalState(TypedDict):
    tenant_id: str
    session_id: str
    incident_id: str                       # the Incident to analyse
    finding_ids: list[str]                 # Findings grouped into it
    affected_entity_id: str                # the entity the incident affects

    # context (populated by Context Gatherer)
    graph_dependents: list[dict]
    finding_details: list[dict]
    past_incidents: list[dict]

    # Diagnosis Agent outputs
    diagnosis: Optional[dict]              # {hypotheses: [{cause, confidence, evidence, rank}]}
    diagnosis_cached: bool

    # Recommender Agent outputs
    recommendations: list[dict]            # [{action, priority, mode, rationale}]
    advisory_lock: bool                    # always True (recommender never actuates)

    next_action: str                       # "gather" | "diagnose" | "recommend" | "done"
    errors: list[str]


def new_operational_state(tenant_id: str, session_id: str,
                          incident_id: str, finding_ids: list[str],
                          affected_entity_id: str) -> OperationalState:
    return OperationalState(
        tenant_id=tenant_id,
        session_id=session_id,
        incident_id=incident_id,
        finding_ids=finding_ids,
        affected_entity_id=affected_entity_id,
        graph_dependents=[],
        finding_details=[],
        past_incidents=[],
        diagnosis=None,
        diagnosis_cached=False,
        recommendations=[],
        advisory_lock=True,
        next_action="gather",
        errors=[],
    )


# --------------------------------------------------------------------------
#  Plugin Scaffolder flow (Team 4)
# --------------------------------------------------------------------------
class PluginScaffoldState(TypedDict):
    tenant_id: str
    session_id: str
    conversation: list[dict]

    extension_point: Optional[str]       # "adapter"|"behavior"|"view"|"webhook"|"transform"|"auth"
    plugin_name: Optional[str]
    plugin_config: Optional[dict]        # {description, inputs, outputs}

    scaffold: Optional[dict]             # {files: [{path, content, language}], readme: str}

    next_action: str                     # "interview" | "scaffold" | "done"
    reply_to_user: Optional[str]
    errors: list[str]


def new_plugin_state(tenant_id: str, session_id: str) -> PluginScaffoldState:
    return PluginScaffoldState(
        tenant_id=tenant_id, session_id=session_id,
        conversation=[], extension_point=None, plugin_name=None,
        plugin_config=None, scaffold=None,
        next_action="interview", reply_to_user=None, errors=[],
    )


# --------------------------------------------------------------------------
#  Accelerator Pack Composer flow (Team 4)
# --------------------------------------------------------------------------
class AcceleratorPackState(TypedDict):
    tenant_id: str
    session_id: str
    conversation: list[dict]

    pack_name: Optional[str]
    target_domain: Optional[str]
    selected_bundles: list[str]          # bundle_ids to include
    adapters: list[dict]                 # [{name, type, config}]
    compliance_docs: list[dict]          # [{title, content, standard}]

    pack_manifest: Optional[dict]        # {bundles, adapters, docs, metadata}

    next_action: str                     # "interview" | "select" | "assemble" | "done"
    reply_to_user: Optional[str]
    errors: list[str]


def new_accelerator_state(tenant_id: str, session_id: str) -> AcceleratorPackState:
    return AcceleratorPackState(
        tenant_id=tenant_id, session_id=session_id,
        conversation=[], pack_name=None, target_domain=None,
        selected_bundles=[], adapters=[], compliance_docs=[],
        pack_manifest=None,
        next_action="interview", reply_to_user=None, errors=[],
    )
