"""
bundle_agents.py — the Bundle Author meta-agent's five nodes + human gate.

A compressed Team 3 running as its own sub-graph:

    Interviewer → Ontology Drafter → Rule Author → Linter → [HUMAN GATE] → Publisher

  * Interviewer    (LLM) — interviews the expert; builds entity/fault/measurement catalogues.
  * Ontology Drafter (LLM) — drafts a Turtle ontology fragment (classes subclassing the core).
  * Rule Author    (LLM) — authors at least one Tier-C rule (e.g. a threshold finding)
                           so the bundle can fire in the demo's incident beat.
  * Linter         (deterministic) — checks the fragment parses + the freeze rule
                           (pack classes subclass the platform, never edit it).
  * [gate]         (human) — approval required before Publisher. Non-negotiable.
  * Publisher      (deterministic) — emits a real, loadable bundle into the registry.

Everything an agent produces is a draft until the Publisher writes it — and the
Publisher cannot run without approved=true.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_TOOLS = ROOT / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from agents.gateway import get_gateway
from agents.registry import get_registry
from agents.engine import INTERRUPT_KEY

CORE = "https://ontology.nextxr.io/v3/core#"


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "bundle").strip().lower()).strip("-") or "bundle"


def _register_fragment(bundle_id: str, fragment: str, ns: str, primary_local: str):
    """Make an authored ontology fragment first-class: persist it to
    packs/published/ (restart-safe) AND inject it into the live cached ontology
    so the Validator/Graph Writer recognise the new class(es) right now.

    Guarantees the primary class declares nxr:taxonomyCategory "PhysicalAsset"
    and rdfs:subClassOf nxr:Equipment — the minimum the Graph Writer needs to
    resolve a Neo4j label for it."""
    from pathlib import Path as _P
    from rdflib import Graph as _G, URIRef, Literal, RDF, RDFS
    from rdflib.namespace import OWL

    NXR = "https://ontology.nextxr.io/v3/core#"
    prim = URIRef(ns + primary_local)

    # Parse the drafted fragment; fall back to an empty graph if it won't parse.
    g = _G()
    if fragment:
        try:
            g.parse(data=fragment, format="turtle")
        except Exception:
            g = _G()

    # Ensure the primary class is governed (category + a platform superclass).
    if (prim, RDF.type, OWL.Class) not in g:
        g.add((prim, RDF.type, OWL.Class))
    if not list(g.objects(prim, RDFS.subClassOf)):
        g.add((prim, RDFS.subClassOf, URIRef(NXR + "Equipment")))
    if not list(g.objects(prim, URIRef(NXR + "taxonomyCategory"))):
        g.add((prim, URIRef(NXR + "taxonomyCategory"), Literal("PhysicalAsset")))

    # 1. Persist for restart-safety.
    try:
        pub_dir = _P(__file__).resolve().parent.parent / "packs" / "published"
        pub_dir.mkdir(parents=True, exist_ok=True)
        g.serialize(destination=str(pub_dir / f"{bundle_id}.ttl"), format="turtle")
    except Exception:
        pass

    # 2. Inject into the live cached ontology so the next validate() /
    #    resolve_label() sees the new class immediately.
    try:
        import gate
        live = gate.ontology_graph()
        for triple in g:
            live.add(triple)
    except Exception:
        pass

    # 3. Clear the SchemaService cache so schema queries see the new class.
    try:
        from schema_service import SchemaService
        SchemaService.load.cache_clear()
    except Exception:
        pass


def _convo(state: dict) -> str:
    return "\n".join(f"{m.get('role')}: {m.get('content','')}"
                     for m in state.get("conversation", [])) or "(no messages)"


# ==========================================================================
#  Interviewer (LLM, user-facing) — builds the catalogues
# ==========================================================================
def interviewer(state: dict) -> dict:
    """Interview the domain expert to assemble the entity / fault / measurement
    catalogues. Yields back to the human until it has enough, then proceeds."""
    gw = get_gateway()
    domain = state.get("domain") or ""

    # First turn (no expert input yet): greet and yield. Never auto-proceed
    # with zero expert input — that would skip the interview AND risk a
    # draft→lint→re-interview loop. The expert must reply at least once.
    has_user = any(m.get("role") == "user" for m in state.get("conversation", []))
    if not has_user:
        greeting = (f"Let's author a '{domain}' capability bundle. Tell me its key "
                    f"assets, what each measures, and the main faults to watch for.")
        return {
            "conversation": list(state.get("conversation", [])) +
                            [{"role": "assistant", "content": greeting}],
            "reply_to_user": greeting,
            "next_action": "interview",
            INTERRUPT_KEY: "awaiting_expert_input",
        }

    def _stub() -> dict:
        # Deterministic seed catalogue for the no-key path / first pass.
        return {
            "reply_to_user": f"Thanks. I've drafted a starter catalogue for a "
                             f"'{domain or 'new'}' bundle. Review and approve, or "
                             f"tell me what to change.",
            "entity_catalogue": [f"{domain.title() or 'Custom'}Unit", "Sensor"],
            "measurement_catalogue": [
                {"name": "Temperature", "observable": "Temperature", "unit": "DEG_C"}],
            "fault_catalogue": [
                {"name": "Overheat", "description": "Temperature exceeds a safe threshold."}],
            "ready_to_draft": True,
        }

    result = gw.complete_json(
        tenant_id=state["tenant_id"], session_id=state["session_id"],
        system=("You interview a domain expert to define a new digital-twin "
                "capability bundle. Your job is to PROCEED quickly, not to chat. "
                "Extract three catalogues from the conversation. Return JSON "
                "{\"reply_to_user\": str, "
                "\"entity_catalogue\": [class names, PascalCase], "
                "\"measurement_catalogue\": [{\"name\":str,\"observable\":str,\"unit\":str}], "
                "\"fault_catalogue\": [{\"name\":str,\"description\":str}], "
                "\"ready_to_draft\": bool}. Set ready_to_draft=TRUE as soon as you "
                "have at least one entity AND one measurement — infer a reasonable "
                "fault if none is stated. If the expert says 'build it', 'go ahead', "
                "or similar, set ready_to_draft=true immediately. Do NOT keep asking "
                "for more detail. Keep reply_to_user to a short confirmation when "
                "proceeding."),
        user=f"Domain: {domain}\n\nConversation:\n{_convo(state)}\n\n"
             "If you already have at least one entity and one measurement, "
             "set ready_to_draft=true.",
        stub=_stub(),
    )
    # Safety net: if catalogues are populated, proceed even if the model dithered.
    if (result.get("entity_catalogue") and result.get("measurement_catalogue")
            and not result.get("ready_to_draft")):
        # Only force-proceed if the expert has actually replied at least once.
        if any(m.get("role") == "user" for m in state.get("conversation", [])):
            result["ready_to_draft"] = True

    reply = result.get("reply_to_user") or ""
    convo = list(state.get("conversation", []))
    if reply:
        convo = convo + [{"role": "assistant", "content": reply}]

    ready = bool(result.get("ready_to_draft"))
    update = {
        "conversation": convo,
        "reply_to_user": reply,
        "entity_catalogue": result.get("entity_catalogue") or state.get("entity_catalogue", []),
        "measurement_catalogue": result.get("measurement_catalogue") or state.get("measurement_catalogue", []),
        "fault_catalogue": result.get("fault_catalogue") or state.get("fault_catalogue", []),
        "next_action": "draft" if ready else "interview",
    }
    if not ready:
        update[INTERRUPT_KEY] = "awaiting_expert_input"
    return update


# ==========================================================================
#  Ontology Drafter (LLM) — Turtle fragment
# ==========================================================================
def ontology_drafter(state: dict) -> dict:
    """Draft a Turtle ontology fragment: one class per entity, subclassing a
    platform mid-level class (the freeze rule — add downward, never edit core)."""
    gw = get_gateway()
    domain = _slug(state.get("domain") or "custom")
    entities = state.get("entity_catalogue", []) or ["CustomUnit"]
    ns = f"https://ontology.nextxr.io/v3/{domain}#"

    def _stub() -> str:
        return _draft_fragment(domain, ns, entities)

    res = gw.complete(
        tenant_id=state["tenant_id"], session_id=state["session_id"],
        system=("You draft a NextXR capability-pack ontology fragment in Turtle. "
                "RULES: every class is `rdfs:subClassOf` a platform class "
                "(nxr:Equipment, nxr:Sensor, nxr:PhysicalAsset, nxr:Process, ...) "
                "and declares `nxr:taxonomyCategory` (usually \"PhysicalAsset\"). "
                "Never redefine nxr: classes. Use prefixes nxr: "
                "<https://ontology.nextxr.io/v3/core#> and a domain prefix. "
                "Output ONLY Turtle, no prose."),
        user=f"Domain prefix IRI: {ns}\nEntities: {entities}\n"
             f"Measurements: {state.get('measurement_catalogue')}",
        temperature=0.2, max_tokens=900,
        stub=_stub,
    )
    fragment = res.text.strip()
    # Guard: strip markdown fences if the model added them.
    fragment = re.sub(r"^```[a-z]*\n?|```$", "", fragment, flags=re.MULTILINE).strip()
    if "subClassOf" not in fragment:  # model produced prose -> use the stub
        fragment = _draft_fragment(domain, ns, entities)
    else:
        fragment = _ensure_prefixes(fragment, ns)
        # Final safety: must parse. If the LLM Turtle is still malformed, fall
        # back to the deterministic stub (which always parses + lints clean).
        try:
            from rdflib import Graph as _G
            _G().parse(data=fragment, format="turtle")
        except Exception:
            fragment = _draft_fragment(domain, ns, entities)
    return {"ontology_fragment": fragment, "next_action": "rules"}


# Standard prefixes every fragment needs. The LLM sometimes omits one (e.g.
# uses rdfs: without declaring it) — we prepend any that are missing so the
# fragment always parses.
_STD_PREFIXES = {
    "nxr": "https://ontology.nextxr.io/v3/core#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}


def _ensure_prefixes(fragment: str, ns: str) -> str:
    """Prepend any standard @prefix declarations the fragment uses but didn't
    declare, plus the domain prefix, so it always parses."""
    header = []
    used = set(re.findall(r"\b([a-zA-Z][\w-]*):", fragment))
    declared = set(re.findall(r"@prefix\s+([a-zA-Z][\w-]*):", fragment))
    for pfx, iri in _STD_PREFIXES.items():
        if pfx in used and pfx not in declared:
            header.append(f"@prefix {pfx}: <{iri}> .")
    # Domain prefixes (dom: or a slug:) the model may reference.
    for pfx in used - declared - set(_STD_PREFIXES):
        if pfx in ("dom",) or ns.endswith(f"/{pfx}#"):
            header.append(f"@prefix {pfx}: <{ns}> .")
    return ("\n".join(header) + "\n" + fragment) if header else fragment


def _draft_fragment(domain: str, ns: str, entities: list[str]) -> str:
    lines = [
        f"@prefix dom: <{ns}> .",
        "@prefix nxr: <https://ontology.nextxr.io/v3/core#> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .",
        "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .",
        "",
        f"<{ns.rstrip('#')}> a owl:Ontology ;",
        f'    owl:versionInfo "1.0.0" ;',
        f'    rdfs:label "{domain.title()} Capability Pack" ;',
        f"    owl:imports <https://ontology.nextxr.io/v3/core> .",
        "",
    ]
    for e in entities:
        local = re.sub(r"[^A-Za-z0-9]", "", e) or "Unit"
        parent = "nxr:Sensor" if "sensor" in e.lower() else "nxr:Equipment"
        lines += [
            f"dom:{local} a owl:Class ;",
            f"    rdfs:subClassOf {parent} ;",
            f'    rdfs:label "{e}" ;',
            f'    nxr:taxonomyCategory "PhysicalAsset" .',
            "",
        ]
    return "\n".join(lines)


# ==========================================================================
#  Rule Author (LLM) — at least one Tier-C rule
# ==========================================================================
def rule_author(state: dict) -> dict:
    """Author at least one Tier-C threshold rule so the bundle can fire a
    Finding in the demo's incident beat."""
    gw = get_gateway()
    domain = _slug(state.get("domain") or "custom")
    measurements = state.get("measurement_catalogue") or []
    signal = (measurements[0]["observable"] if measurements else "Temperature")

    def _stub() -> dict:
        return {"rules": [{
            "behavior_id": f"{domain}.threshold",
            "tier": "C", "kind": "threshold",
            "watches": f"{domain}:{signal}",
            "offset_c": 3.0, "duration_minutes": 3.0,
            "description": f"Critical Finding when {signal} exceeds setpoint+3 "
                           f"for >=3 min.",
        }]}

    result = gw.complete_json(
        tenant_id=state["tenant_id"], session_id=state["session_id"],
        system=("You author Tier-C (deterministic threshold) rules for a NextXR "
                "capability bundle. Return JSON {\"rules\": [{\"behavior_id\":str, "
                "\"tier\":\"C\", \"kind\":\"threshold\", \"watches\":str, "
                "\"offset_c\":number, \"duration_minutes\":number, "
                "\"description\":str}]}. Author at least ONE rule that fires a "
                "Finding when a measured value exceeds a setpoint offset for a "
                "sustained window."),
        user=f"Domain: {domain}\nMeasurements: {measurements}\n"
             f"Faults: {state.get('fault_catalogue')}",
        stub=_stub(),
    )
    rules = result.get("rules") or _stub()["rules"]
    return {"rules": rules, "next_action": "elicit"}


# ==========================================================================
#  Behavior Modeler (LLM) — classifies faults into Tier A/B/C artefacts
# ==========================================================================
def behavior_modeler(state: dict) -> dict:
    """For each fault, classifies it as Tier A (physics — skeleton for human),
    Tier B (statistical — baseline config), or Tier C (rule — threshold). Produces
    the matching artefact. Goes between Drafter and Rule Author."""
    gw = get_gateway()
    domain = _slug(state.get("domain") or "custom")
    faults = state.get("fault_catalogue") or []
    measurements = state.get("measurement_catalogue") or []

    if not faults:
        return {"behavior_models": [], "next_action": "rules"}

    def _stub() -> dict:
        models = []
        for fault in faults:
            name = fault.get("name", "Unknown") if isinstance(fault, dict) else str(fault)
            signal = measurements[0].get("observable", "Temperature") if measurements else "Temperature"
            models.append({
                "fault": name, "tier": "C", "artefact_type": "threshold_rule",
                "artefact": {
                    "behavior_id": f"{domain}.{_slug(name)}",
                    "watches": f"{domain}:{signal}", "offset_c": 3.0,
                    "duration_minutes": 3.0,
                    "description": f"Tier-C rule for {name}.",
                },
            })
        return {"models": models}

    fault_text = []
    for f in faults:
        if isinstance(f, dict):
            fault_text.append(f"{f.get('name', '?')}: {f.get('description', '')}")
        else:
            fault_text.append(str(f))

    result = gw.complete_json(
        tenant_id=state["tenant_id"], session_id=state["session_id"],
        system=("You classify facility faults into behaviour tiers for a digital-twin "
                "platform. Tier A: physics-based (needs first-principles equation, "
                "produce a skeleton). Tier B: statistical baseline (produce config "
                "with signal, warmup, z_threshold). Tier C: simple threshold rule "
                "(produce rule with offset, duration). Return JSON "
                "{\"models\": [{\"fault\": str, \"tier\": \"A\"|\"B\"|\"C\", "
                "\"artefact_type\": str, \"artefact\": dict}]}."),
        user=f"Domain: {domain}\nFaults:\n" + "\n".join(fault_text) +
             f"\nMeasurements: {measurements}",
        stub=_stub(),
        max_tokens=900,
    )
    models = result.get("models") or _stub()["models"]
    return {"behavior_models": models, "next_action": "rules"}


# ==========================================================================
#  Elicitation Designer (LLM) — generates Concierge question sets
# ==========================================================================
def elicitation_designer(state: dict) -> dict:
    """Generates the elicitation question set the Twin-Building Concierge will
    later use when a customer instantiates a twin in this new domain. This is the
    elegant bit: Team 3's output reconfigures Team 1's behaviour."""
    gw = get_gateway()
    domain = state.get("domain") or "custom"
    entities = state.get("entity_catalogue") or []
    faults = state.get("fault_catalogue") or []
    measurements = state.get("measurement_catalogue") or []

    def _stub() -> dict:
        questions = []
        for e in entities[:3]:
            name = e if isinstance(e, str) else str(e)
            questions.append({
                "question": f"How many {name} units does your facility have?",
                "purpose": f"Determine {name} asset count for twin scaffold.",
                "domain_anchor": name,
            })
        if measurements:
            m = measurements[0]
            mname = m.get("name", "key measurement") if isinstance(m, dict) else str(m)
            questions.append({
                "question": f"What is the normal operating range for {mname}?",
                "purpose": f"Establish baseline setpoint for {mname}.",
                "domain_anchor": mname,
            })
        if not questions:
            questions.append({
                "question": f"What are the main assets in your {domain} facility?",
                "purpose": "Discover primary equipment.",
                "domain_anchor": domain,
            })
        return {"questions": questions}

    result = gw.complete_json(
        tenant_id=state["tenant_id"], session_id=state["session_id"],
        system=("You design elicitation questions for a digital-twin concierge. "
                "Given a domain's entity/fault/measurement catalogues, produce "
                "questions that help the concierge extract the user's facility "
                "configuration. Return JSON "
                "{\"questions\": [{\"question\": str, \"purpose\": str, "
                "\"domain_anchor\": str}]}. 5-10 questions. "
                "Questions should be jargon-free and focus on what to monitor."),
        user=f"Domain: {domain}\nEntities: {entities}\n"
             f"Faults: {faults}\nMeasurements: {measurements}",
        stub=_stub(),
        max_tokens=700,
    )
    questions = result.get("questions") or _stub()["questions"]
    return {"elicitation_questions": questions, "next_action": "curate"}


# ==========================================================================
#  Asset Curator (Hybrid) — sources 3D assets for domain entities
# ==========================================================================
def asset_curator(state: dict) -> dict:
    """Matches catalogue entities to an asset library, flags gaps where no
    matching 3D asset exists. For the MVP, the asset library is a static
    catalogue; gaps are reported to the expert."""
    gw = get_gateway()
    domain = _slug(state.get("domain") or "custom")
    entities = state.get("entity_catalogue") or []

    # Static asset catalogue — in production this would be a real asset DB.
    KNOWN_ASSETS = {
        "airhandler": "asset_ahu_generic", "chiller": "asset_chiller_generic",
        "pump": "asset_pump_generic", "fan": "asset_fan_generic",
        "sensor": "asset_sensor_generic", "valve": "asset_valve_generic",
        "filter": "asset_filter_generic", "coil": "asset_coil_generic",
        "duct": "asset_duct_generic", "diffuser": "asset_diffuser_generic",
        "transformer": "asset_transformer_generic", "ups": "asset_ups_generic",
        "generator": "asset_generator_generic", "tank": "asset_tank_generic",
    }

    manifest = []
    gaps = []
    for e in entities:
        name = e if isinstance(e, str) else str(e)
        name_lower = name.lower().replace(" ", "")
        # Try exact match, then partial.
        asset_id = KNOWN_ASSETS.get(name_lower)
        if not asset_id:
            for key, aid in KNOWN_ASSETS.items():
                if key in name_lower or name_lower in key:
                    asset_id = aid
                    break
        if asset_id:
            manifest.append({"entity": name, "asset_id": asset_id,
                             "source": "library", "status": "matched"})
        else:
            manifest.append({"entity": name, "asset_id": None,
                             "source": None, "status": "gap"})
            gaps.append(name)

    return {"asset_manifest": manifest, "asset_gaps": gaps,
            "next_action": "lint"}


# ==========================================================================
#  Linter (deterministic) — parse + freeze-rule check
# ==========================================================================
def linter(state: dict) -> dict:
    """Deterministic checks: the Turtle parses, declares at least one class, every
    class subclasses the platform (freeze rule), and at least one Tier-C rule
    exists. Produces lint_result; routes to the human gate on success."""
    from rdflib import Graph as RDFGraph
    issues = []
    fragment = state.get("ontology_fragment") or ""

    # 1. Parses?
    g = None
    try:
        g = RDFGraph().parse(data=fragment, format="turtle")
    except Exception as e:
        issues.append({"severity": "error", "reason": f"Turtle does not parse: {e}"})

    if g is not None:
        from rdflib import RDF, URIRef
        from rdflib.namespace import OWL, RDFS
        subclass = URIRef("http://www.w3.org/2000/01/rdf-schema#subClassOf")
        # Accept owl:Class OR rdfs:Class — LLMs use both; they're equivalent for
        # our purposes (the publisher normalises to owl:Class on registration).
        classes = list(g.subjects(RDF.type, OWL.Class)) + list(g.subjects(RDF.type, RDFS.Class))
        classes = list(dict.fromkeys(classes))
        if not classes:
            issues.append({"severity": "error", "reason": "No class declared."})
        # 2. Freeze rule: every domain class must subclass something, and must
        #    NOT redefine a core nxr: class.
        for c in classes:
            ciri = str(c)
            if ciri.startswith(CORE):
                issues.append({"severity": "error",
                               "reason": f"Pack must not redefine core class {ciri}."})
            if not list(g.objects(c, subclass)):
                issues.append({"severity": "warning",
                               "reason": f"{ciri.split('#')[-1]} has no rdfs:subClassOf "
                                         f"(should subclass a platform class)."})

    # 3. At least one Tier-C rule.
    tierc = [r for r in state.get("rules", []) if str(r.get("tier")).upper() == "C"]
    if not tierc:
        issues.append({"severity": "error",
                       "reason": "Bundle has no Tier-C rule; it can't fire a Finding."})

    # 4. Elicitation questions (warning only, not blocking).
    if not state.get("elicitation_questions"):
        issues.append({"severity": "warning",
                       "reason": "No elicitation questions generated; Concierge "
                                 "will use defaults for this domain."})

    # 5. Asset gaps (warning only).
    gaps = state.get("asset_gaps") or []
    if gaps:
        preview = ", ".join(gaps[:3])
        suffix = f" (+{len(gaps) - 3} more)" if len(gaps) > 3 else ""
        issues.append({"severity": "warning",
                       "reason": f"{len(gaps)} entity/-ies have no matching 3D "
                                 f"asset: {preview}{suffix}"})

    errors = [i for i in issues if i["severity"] == "error"]
    ok = len(errors) == 0
    update = {"lint_result": {"ok": ok, "issues": issues},
              "next_action": "await_approval" if ok else "interview",
              "reply_to_user": ("Lint passed — ready for your approval."
                                if ok else
                                "Lint found issues, let's refine: " +
                                "; ".join(i["reason"] for i in errors))}
    if not ok:
        # Yield to the expert to refine — do NOT silently re-draft in a loop.
        update[INTERRUPT_KEY] = "awaiting_expert_input"
    return update


# ==========================================================================
#  Human gate — pause until approved=true
# ==========================================================================
def approval_gate(state: dict) -> dict:
    """The non-negotiable human gate. If not approved, interrupt and wait. The
    UI shows a review-and-approve screen; setting approved=true and resuming
    continues to the Publisher."""
    if state.get("approved"):
        return {"next_action": "publish"}
    return {"next_action": "await_approval",
            "reply_to_user": "Review the drafted ontology + rule, then approve to "
                             "publish the bundle.",
            INTERRUPT_KEY: "awaiting_human_approval"}


# ==========================================================================
#  Publisher (deterministic) — emit a real, loadable bundle
# ==========================================================================
def publisher(state: dict) -> dict:
    """Publish the bundle into the registry so the Capability Composer can load
    it. Idempotent on bundle_id. Cannot run unless approved=true (the gate
    guarantees it, but we re-check — safety-critical)."""
    if not state.get("approved"):
        return {"published_bundle": None, "next_action": "await_approval",
                "errors": state.get("errors", []) + ["Publish blocked: not approved."]}

    registry = get_registry()
    domain = _slug(state.get("domain") or "custom")
    bundle_id = state.get("bundle_name") and _slug(state["bundle_name"]) or f"{domain}-pack"

    # Build entity templates from the catalogue so the Composer can instantiate.
    ns = f"https://ontology.nextxr.io/v3/{domain}#"
    entities = state.get("entity_catalogue", []) or ["CustomUnit"]
    # First non-sensor entity is the primary asset; give it a setpoint so the
    # Tier-C threshold rule has something to compare against.
    templates = [
        {"key": "site", "canonical_type": CORE + "Site",
         "properties": {"displayName": f"{domain.title()} Facility"}},
        {"key": "space", "canonical_type": CORE + "Space",
         "properties": {"displayName": "Zone 1"}},
    ]
    primary = next((e for e in entities if "sensor" not in e.lower()), entities[0])
    local = re.sub(r"[^A-Za-z0-9]", "", primary) or "Unit"
    templates.append({
        "key": "primary",
        "canonical_type": ns + local,
        "properties": {"displayName": f"{local}-01", "status": "running",
                       "setpoint": 22.0},
    })
    # No authored relationship template for the MVP: a bare PhysicalAsset
    # subclass satisfies the base shape, whereas an arbitrary predicate could
    # trip domain/range SHACL. Relationships come from curated bundles.
    rel_templates = []

    # Register the authored ontology fragment into the LIVE ontology so the
    # Validator/Graph Writer recognise the new class(es) immediately (this is
    # what closes the loop). Persist to packs/published/ for restart-safety.
    _register_fragment(bundle_id, state.get("ontology_fragment") or "", ns, local)

    bundle = {
        "bundle_id": bundle_id,
        "name": state.get("bundle_name") or f"{domain.title()} Capability Pack",
        "domains": [domain, state.get("domain") or domain],
        "source": "authored",
        "ontology_fragment": state.get("ontology_fragment"),
        "entity_templates": templates,
        "relationship_templates": rel_templates,
        "rules": state.get("rules", []),
        "primary_signal": (state.get("measurement_catalogue") or [{}])[0].get("observable"),
        # Phase 3 artifacts
        "behavior_models": state.get("behavior_models", []),
        "elicitation_questions": state.get("elicitation_questions", []),
        "asset_manifest": state.get("asset_manifest", []),
        "asset_gaps": state.get("asset_gaps", []),
    }
    registry.publish(bundle, tenant_id=state.get("tenant_id"))

    return {"published_bundle": bundle_id, "next_action": "done",
            "reply_to_user": f"Published **{bundle['name']}** ({bundle_id}). "
                             f"It's now in the registry — the Concierge flow can "
                             f"build a twin from it."}


# ==========================================================================
#  Routers
# ==========================================================================
def route_after_interview(state: dict) -> str:
    return "draft" if state.get("next_action") == "draft" else "interview"


def route_after_lint(state: dict) -> str:
    return "ok" if (state.get("lint_result") or {}).get("ok") else "fail"


def route_after_gate(state: dict) -> str:
    return "publish" if state.get("approved") else "wait"
