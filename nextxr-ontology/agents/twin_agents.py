"""
twin_agents.py — the five twin-building agents + their routers.

Each agent is a pure function `(state) -> partial_update`. They read keys they
need and write keys they own; they NEVER call each other (the graph owns flow).
Routers are pure `(state) -> key` functions feeding the conditional edges.

Build order mirrors the spec (most fundamental first):
    Graph Writer · Validator · Capability Composer · Domain Classifier · Concierge
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_TOOLS = ROOT / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))  # so `import gate` (the SHACL write-gate) works

from agents.gateway import get_gateway
from agents.registry import get_registry

CORE = "https://ontology.nextxr.io/v3/core#"

# Known demo verticals the Domain Classifier chooses from.
KNOWN_DOMAINS = ["hvac", "cooling", "facility", "building", "maritime",
                 "energy", "manufacturing"]
CONFIDENCE_THRESHOLD = 0.7


def _new_id_safe() -> str:
    """Time-ordered UUIDv7 id (reuses the platform generator)."""
    from graph.crud import _new_id
    return _new_id()


# ==========================================================================
# 5 · Graph Writer   (deterministic — the only node that mutates the graph)
# ==========================================================================
def graph_writer(state: dict) -> dict:
    """Commit validated drafts via the platform's single write path
    (GraphWriter → SHACL gate → Neo4j → Change Log → event bus). Idempotent on
    UUIDv7 keys. Resolves template `key` references to real node ids.

    On a connection error it RAISES (per spec) so the workflow can retry the
    same idempotent op — it does NOT loop back to Concierge."""
    from graph.writer import GraphWriter, Rel
    from changelog.service import ChangeLog
    from twins import TwinRegistry

    tenant_id = state["tenant_id"]
    drafts = state.get("draft_entities", [])
    rel_drafts = state.get("draft_relationships", [])

    writer = GraphWriter(changelog=ChangeLog())

    # Ensure schema exists (idempotent); keep the shared driver open.
    try:
        from graph import schema
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            schema.apply_schema(dry_run=False, close=False)
    except Exception:
        pass

    # Pre-resolve the draft ids the Validator assigned (idempotent + consistent
    # with what was validated). Map template key -> id up front so an entity can
    # be created WITH its outgoing relationships in one atomic create (some SHACL
    # shapes require the relationship at creation, e.g. AirHandler servesSpace).
    key_to_id: dict[str, str] = {}
    for ent in drafts:
        ent.setdefault("_draft_id", _new_id_safe())
        if ent.get("key"):
            key_to_id[ent["key"]] = ent["_draft_id"]

    # Group outgoing relationships by their source entity's draft id.
    out_rels: dict[str, list] = {}
    for rel in rel_drafts:
        src = key_to_id.get(rel.get("source_key"), rel.get("source_id"))
        tgt = key_to_id.get(rel.get("target_key"), rel.get("target_id"))
        if src and tgt:
            out_rels.setdefault(src, []).append(Rel(predicate=rel["predicate"], target_id=tgt))

    # Create targets-before-sources: entities that are pure targets (no outgoing
    # rels) first, so a source's relationship target already exists.
    def _has_outgoing(ent):
        return bool(out_rels.get(ent["_draft_id"]))
    ordered = sorted(drafts, key=_has_outgoing)  # False (no rels) first

    primary_id = None
    errors = []
    for ent in ordered:
        nid = ent["_draft_id"]
        res = writer.create(
            tenant_id=tenant_id,
            canonical_type=ent["canonical_type"],
            actor="agent:graph_writer",
            properties=dict(ent.get("properties", {})),
            relationships=out_rels.get(nid) or None,
            node_id=nid,
        )
        if not res.ok:
            errors.append(f"{ent.get('canonical_type','?').split('#')[-1]}: {res.error}")
            continue
        if primary_id is None and res.label == "PhysicalAsset":
            primary_id = res.node_id

    committed_count = len(drafts) - len(errors)
    if errors and committed_count == 0:
        # Nothing committed — surface as a validation-style failure, not a crash.
        return {"committed": False, "errors": state.get("errors", []) + errors,
                "next_action": "ask",
                "reply_to_user": "I couldn't commit the twin: " + "; ".join(errors)}

    twin_id = tenant_id  # a twin == a tenant
    # Register the twin so it appears in the Twins list + the feed can target it.
    try:
        reg = TwinRegistry()
        if reg.get(tenant_id) is None:
            from twins.service import Twin, _now_iso
            reg._insert(Twin(
                tenant_id=tenant_id,
                name=state.get("twin_name") or state.get("domain", "Twin").title(),
                domain=state.get("domain") or "hvac",
                description=f"Authored by the agent flow from "
                            f"{', '.join(state.get('loaded_bundles', [])) or 'a bundle'}.",
                created_at=_now_iso(), seed_asset_id=primary_id,
            ))
        elif primary_id:
            reg._set_seed_asset(tenant_id, primary_id)
    except Exception:
        pass

    msg = f"Your twin is live ({committed_count} entities committed)."
    if errors:
        msg += f" ({len(errors)} entity/-ies were skipped: {'; '.join(errors)})"
    return {"committed": True, "twin_id": twin_id, "next_action": "done",
            "errors": state.get("errors", []) + errors,
            "reply_to_user": msg + " You can watch it on the dashboard."}


# ==========================================================================
# 4 · Validator   (deterministic — pure code, SHACL + completeness)
# ==========================================================================
def validator(state: dict) -> dict:
    """Validate every draft entity through the SHACL write gate, exactly as the
    Graph Writer will. We reuse the writer's OWN Turtle renderer and assign each
    draft a real UUIDv7 id + the tenant, so what passes here is what commits.
    Relationship cardinality (e.g. "an air handler must serve a space") is
    checked by rendering each entity WITH its outgoing draft relationships.

    Errors are human-readable so the Concierge can voice them. Pure code."""
    import gate  # tools/gate.py
    from graph.writer import GraphWriter, Rel
    from graph.crud import _new_id

    drafts = state.get("draft_entities", [])
    rel_drafts = state.get("draft_relationships", [])
    errors = []

    if not drafts:
        return {"validation": {"ok": False, "errors": [
            {"path": "draft_entities", "reason": "No entities were composed."}]},
            "next_action": "ask"}

    # Assign each draft a real id (keyed by template key) so relationships and
    # the UUIDv7 base-shape check resolve like a real commit.
    key_to_id = {}
    for ent in drafts:
        ent_id = _new_id()
        ent["_draft_id"] = ent_id
        if ent.get("key"):
            key_to_id[ent["key"]] = ent_id

    # Build a writer purely to reuse its renderer (no DB writes happen here).
    w = GraphWriter.__new__(GraphWriter)  # bypass __init__ (no driver needed)

    for ent in drafts:
        node_id = ent["_draft_id"]
        ct = ent["canonical_type"]
        props = {
            "id": node_id, "tenantId": state["tenant_id"],
            "canonicalType": ct,
            "createdAt": _now_iso_z(), "updatedAt": _now_iso_z(),
            "createdBy": "agent:validator",
            **{k: v for k, v in ent.get("properties", {}).items()},
        }
        # Outgoing relationships for THIS entity (resolve template keys to ids).
        rels = []
        for r in rel_drafts:
            if key_to_id.get(r.get("source_key")) == node_id:
                tgt = key_to_id.get(r.get("target_key"), r.get("target_id"))
                if tgt:
                    rels.append(Rel(predicate=r["predicate"], target_id=tgt))
        ttl = w._render_node_ttl(node_id, ct, props, rels)
        try:
            result = gate.validate(ttl)
            if not result.ok:
                for v in result.violations:
                    errors.append({"path": ct.split("#")[-1],
                                   "reason": _humanize(str(v))})
        except Exception as e:
            errors.append({"path": ct.split("#")[-1],
                           "reason": f"validation error: {e}"})

    ok = len(errors) == 0
    return {"validation": {"ok": ok, "errors": errors},
            "next_action": "commit" if ok else "ask"}


def _now_iso_z() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _humanize(violation: str) -> str:
    """Trim SHACL violation noise into something the Concierge can speak."""
    for sep in (" — ", " - ", ": "):
        if sep in violation:
            tail = violation.split(sep)[-1].strip()
            if tail and "conform to Shape" not in tail:
                return tail
    return violation.strip()


# ==========================================================================
# 3 · Capability Composer   (hybrid — deterministic load, templates → drafts)
# ==========================================================================
def capability_composer(state: dict) -> dict:
    """Query the registry for a bundle matching the domain, load it, and turn
    its templates into draft entities/relationships (MVP: no free-text mapping).
    The demo's plug-and-play moment — surfaces which bundle loaded."""
    registry = get_registry()
    domain = state.get("domain") or ""
    matches = registry.query(domain)

    if not matches:
        return {"loaded_bundles": [], "draft_entities": [], "draft_relationships": [],
                "next_action": "ask",
                "errors": state.get("errors", []) + [f"No bundle for domain '{domain}'."],
                "reply_to_user": f"We don't support '{domain}' yet — no capability "
                                 f"bundle is available. Want to author one?"}

    bundle = registry.load(matches[0]["bundle_id"])
    twin_name = state.get("twin_name") or f"{domain.title()} Twin"

    # Templates → drafts. Personalise the root display name.
    entities = []
    for t in bundle.get("entity_templates", []):
        props = dict(t.get("properties", {}))
        if t.get("key") == "site":
            props["displayName"] = twin_name
        entities.append({"key": t.get("key"),
                         "canonical_type": t["canonical_type"],
                         "properties": props})
    rels = [dict(r) for r in bundle.get("relationship_templates", [])]

    return {"loaded_bundles": [bundle["bundle_id"]],
            "draft_entities": entities, "draft_relationships": rels,
            "next_action": "validate",
            "reply_to_user": f"Loaded the **{bundle['name']}** bundle "
                             f"({len(entities)} entities, {len(bundle.get('rules', []))} rule(s)). "
                             f"Validating before commit…"}


# ==========================================================================
# 2 · Domain Classifier   (LLM — structured, low temperature)
# ==========================================================================
def domain_classifier(state: dict) -> dict:
    """Pick a vertical + confidence from the conversation. Never guesses past
    the threshold — low confidence routes back to the Concierge.

    The vertical list is dynamic: it includes the domains of any PUBLISHED
    bundles, so a vertical the Bundle Author just authored is immediately
    classifiable (this is what makes the closed loop land on the new bundle)."""
    gw = get_gateway()
    convo = _convo_text(state)
    intent = state.get("user_intent") or ""

    # Domains available right now = built-in known + every published bundle's domains.
    published_domains = []
    try:
        for b in get_registry().list_published():
            published_domains += [d for d in b.get("domains", [])]
    except Exception:
        pass
    # Published domains first so they win on a keyword match.
    available = list(dict.fromkeys(published_domains + KNOWN_DOMAINS))

    def _stub() -> dict:
        text = (convo + " " + intent).lower()
        best, conf = None, 0.0
        for d in available:
            if d and d.lower() in text:
                best, conf = d, 0.9
                break
        if best is None:
            for kw, d in [("refriger", None), ("cold storage", None), ("freezer", None),
                          ("cool", "cooling"), ("air", "hvac"), ("temperature", "hvac"),
                          ("ship", "maritime"), ("port", "maritime"), ("power", "energy"),
                          ("server", "hvac"), ("room", "hvac"), ("plant", "facility")]:
                if kw in text:
                    # If a published bundle covers this keyword, prefer it.
                    match = d
                    for pd in published_domains:
                        if kw.split()[0] in pd.lower() or pd.lower() in text:
                            match = pd
                            break
                    if match:
                        best, conf = match, 0.8
                        break
        return {"domain": best or "hvac", "sub_type": None,
                "confidence": conf if best else 0.4}

    result = gw.complete_json(
        tenant_id=state["tenant_id"], session_id=state["session_id"],
        system=("You classify a facility-twin request into ONE vertical. "
                f"Choose `domain` from this list (earlier entries are preferred "
                f"when they fit): {available}. Return JSON "
                '{"domain": str, "sub_type": str|null, "confidence": 0.0-1.0}. '
                "Set confidence below 0.7 only if the conversation is too vague."),
        user=f"Conversation:\n{convo}\n\nStated intent: {intent}",
        stub=_stub(),
    )
    domain = (result.get("domain") or "hvac").lower()
    conf = float(result.get("confidence", 0.0) or 0.0)
    return {"domain": domain, "sub_type": result.get("sub_type"),
            "domain_confidence": conf,
            "next_action": "compose" if conf >= CONFIDENCE_THRESHOLD else "ask"}


# ==========================================================================
# 1 · Concierge   (LLM — user-facing guide; never writes the graph)
# ==========================================================================
def concierge_agent(state: dict) -> dict:
    """Jargon-free guide. Learns facility type + rough scope, then proceeds.
    Voices any Validator/Classifier loop-back reason and re-asks. Sets
    ready_to_classify only when type + rough scope are known.

    Emits an interrupt to yield the turn back to the human when more input is
    needed (next_action='ask')."""
    from agents.engine import INTERRUPT_KEY
    gw = get_gateway()
    convo = _convo_text(state)

    # If we just looped back from Validator/Classifier, lead with the reason.
    loopback = _loopback_reason(state)

    def _stub() -> dict:
        # Deterministic: if the user has said anything substantive, proceed.
        last_user = _last_user_msg(state)
        if loopback:
            return {"reply_to_user": loopback + " Could you clarify the facility "
                    "type and what you want to monitor?", "ready_to_classify": False}
        if last_user and len(last_user.split()) >= 3:
            return {"reply_to_user": "Got it — let me set that up.",
                    "ready_to_classify": True}
        return {"reply_to_user": "Tell me about the facility you want a digital "
                "twin for — what kind of site is it, and what do you want to keep "
                "an eye on?", "ready_to_classify": False}

    result = gw.complete_json(
        tenant_id=state["tenant_id"], session_id=state["session_id"],
        system=("You are a friendly, jargon-free guide helping a facility "
                "operator create a digital twin. Your goal is to PROCEED quickly, "
                "not to chat. Return JSON "
                "{\"reply_to_user\": str, \"ready_to_classify\": bool}. "
                "Set ready_to_classify=TRUE as soon as you can name the facility "
                "TYPE and roughly what they want to monitor — do NOT keep asking "
                "for more detail once you have those two things. If the user says "
                "anything like 'build it', 'go ahead', or 'create it', set "
                "ready_to_classify=true immediately. Only set it false when the "
                "facility type is genuinely unknown. When proceeding, keep "
                "reply_to_user to a short confirmation like 'Got it — building "
                "your <type> twin now.' If a reason for re-asking is provided, "
                "voice it plainly first, then ask ONE focused question."),
        user=(f"{('Reason to re-ask: ' + loopback) if loopback else ''}\n\n"
              f"Conversation so far:\n{convo}\n\n"
              "Decide: do you already know the facility type and rough scope? "
              "If yes, set ready_to_classify=true."),
        stub=_stub(),
    )

    reply = result.get("reply_to_user") or ""
    ready = bool(result.get("ready_to_classify"))

    convo_list = list(state.get("conversation", []))
    if reply:
        convo_list = convo_list + [{"role": "assistant", "content": reply}]

    update = {
        "conversation": convo_list,
        "reply_to_user": reply,
        "user_intent": _last_user_msg(state) or state.get("user_intent"),
        "next_action": "classify" if ready else "ask",
        # clear stale loopback signal once voiced
        "validation": None if ready else state.get("validation"),
    }
    if not ready:
        # Yield the turn back to the human; the graph pauses here.
        update[INTERRUPT_KEY] = "awaiting_user_input"
    return update


# ==========================================================================
# V · Vision Agent   (multimodal — image/CAD analysis)
# ==========================================================================
def vision_agent(state: dict) -> dict:
    """Reads uploaded images and extracts structured facility findings (asset
    counts, layout observations, equipment types). Feeds the Schema Mapper.
    If no files are uploaded, the graph skips this node entirely."""
    gw = get_gateway()
    files = state.get("uploaded_files") or []
    domain = state.get("domain") or ""

    if not files:
        return {"vision_findings": []}

    image_urls = [f["url"] for f in files if f.get("url")]
    if not image_urls:
        return {"vision_findings": []}

    def _stub() -> dict:
        return {"findings": [
            {"label": f"{(domain or 'Facility').title()} Unit",
             "count": 1, "location": "Zone 1", "confidence": 0.8,
             "type": "asset"},
        ]}

    result = gw.complete_json_vision(
        tenant_id=state["tenant_id"], session_id=state["session_id"],
        system=("You analyze facility images for a digital-twin platform. "
                "Extract structured findings: asset counts, equipment types, "
                "layout observations. Return JSON "
                "{\"findings\": [{\"label\": str, \"count\": int, "
                "\"location\": str|null, \"confidence\": float, "
                "\"type\": \"asset\"|\"layout\"|\"measurement\"}]}."),
        user_text=f"Domain: {domain}. Analyze these facility images and "
                  f"extract all visible assets, equipment, and layout details.",
        image_urls=image_urls,
        stub=_stub(),
    )
    findings = result.get("findings") or _stub()["findings"]
    return {"vision_findings": findings}


# ==========================================================================
# 3b · Schema Mapper   (LLM — maps free-text + findings to ontology entities)
# ==========================================================================
def schema_mapper(state: dict) -> dict:
    """Translates plain-language descriptions + Vision findings + bundle templates
    into concrete ontology entities. When no LLM is available, degrades to the
    Capability Composer's bundle-template behaviour (zero regression)."""
    gw = get_gateway()
    domain = state.get("domain") or ""
    bundles = state.get("loaded_bundles") or []
    vision = state.get("vision_findings") or []
    convo = _convo_text(state)

    # If the Composer already drafted from templates and there's no vision
    # input requiring richer mapping, pass through.
    existing_drafts = state.get("draft_entities") or []
    if existing_drafts and not vision and gw.backend == "stub":
        return {"mapping_source": "bundle", "next_action": "validate"}

    # Load the bundle's templates as base vocabulary.
    registry = get_registry()
    bundle_templates = []
    for bid in bundles:
        b = registry.load(bid)
        if b:
            bundle_templates = b.get("entity_templates", [])
            break

    # Build a legal-type vocabulary from SchemaService for the LLM.
    legal_types = []
    try:
        import sys
        _tools = str(ROOT / "tools")
        if _tools not in sys.path:
            sys.path.insert(0, _tools)
        from schema_service import SchemaService
        svc = SchemaService.load()
        legal_types = [{"iri": t["iri"], "label": t.get("label", ""),
                        "category": t.get("category", "")}
                       for t in svc.legal_types(instantiable_only=True)]
    except Exception:
        pass

    def _stub() -> dict:
        # Stub: use bundle templates directly (MVP behaviour).
        return {"entities": [{"key": t.get("key"), "canonical_type": t["canonical_type"],
                              "properties": t.get("properties", {})}
                             for t in bundle_templates],
                "relationships": [dict(r) for r in (registry.load(bundles[0]) or {}).get(
                    "relationship_templates", [])] if bundles else []}

    vision_text = ""
    if vision:
        items = [f"- {f.get('label', '?')} (x{f.get('count', 1)}, "
                 f"location: {f.get('location', 'unknown')})" for f in vision]
        vision_text = "\nVision findings:\n" + "\n".join(items)

    result = gw.complete_json(
        tenant_id=state["tenant_id"], session_id=state["session_id"],
        system=("You map facility descriptions to concrete NextXR ontology entities. "
                "Each entity needs: \"key\" (short local ref like 'site', 'ahu-01'), "
                "\"canonical_type\" (full IRI from the legal types list), "
                "and \"properties\" (dict with at least displayName). "
                "Also produce relationships: [{\"source_key\", \"predicate\", \"target_key\"}]. "
                "Return JSON {\"entities\": [...], \"relationships\": [...]}. "
                "Use ONLY canonical_type IRIs from the legal types provided."),
        user=f"Domain: {domain}\n"
             f"Conversation:\n{convo}\n{vision_text}\n\n"
             f"Legal types (use these IRIs): {legal_types[:30]}\n\n"
             f"Bundle templates (for reference): {bundle_templates}",
        stub=_stub(),
        max_tokens=1200,
    )

    entities = result.get("entities") or _stub()["entities"]
    rels = result.get("relationships") or _stub()["relationships"]

    return {"draft_entities": entities, "draft_relationships": rels,
            "mapping_source": "mapper" if gw.backend != "stub" else "bundle",
            "next_action": "validate"}


# ==========================================================================
# 6 · Scene Generator   (stub — needs 3D engine infrastructure)
# ==========================================================================
def scene_generator(state: dict) -> dict:
    """Turns a committed graph into a procedural 3D scene descriptor.
    STUB: queries graph topology and returns an entity list with placeholder
    positions. Real glTF generation requires a 3D engine (future work)."""
    from graph.query import GraphQuery

    tenant_id = state.get("tenant_id") or ""
    twin_id = state.get("twin_id")
    if not twin_id:
        return {"scene_result": {"status": "skipped",
                                 "message": "No committed twin to visualize."},
                "next_action": "done"}

    try:
        q = GraphQuery()
        assets = q.list_by_label(tenant_id, "PhysicalAsset", limit=50)
        locations = q.list_by_label(tenant_id, "Location", limit=50)
    except Exception:
        assets, locations = [], []

    topology = []
    for e in assets + locations:
        topology.append({
            "id": e.get("id"), "type": e.get("canonicalType", ""),
            "name": e.get("displayName", ""), "label": "PhysicalAsset"
            if e in assets else "Location",
        })

    return {"scene_result": {
        "format": "gltf", "status": "stub",
        "node_count": len(topology),
        "message": "Scene generation requires 3D engine. Topology extracted.",
        "topology": topology,
    }, "next_action": "done"}


# ==========================================================================
#  Routers (pure functions feeding the conditional edges)
# ==========================================================================
def route_after_concierge(state: dict) -> str:
    """Original MVP router (kept for backwards compat reference)."""
    return "classify" if state.get("next_action") == "classify" else "ask"


def route_after_concierge_v2(state: dict) -> str:
    """Extended router: branches to Vision Agent when files are uploaded."""
    if state.get("next_action") == "classify":
        if state.get("uploaded_files"):
            return "vision"
        return "classify"
    return "ask"


def route_after_classify(state: dict) -> str:
    conf = state.get("domain_confidence") or 0.0
    return "ok" if conf >= CONFIDENCE_THRESHOLD else "low"


def route_after_validate(state: dict) -> str:
    v = state.get("validation") or {}
    return "ok" if v.get("ok") else "fail"


def route_after_graph_writer(state: dict) -> str:
    """After commit: optionally generate a scene, or done."""
    if state.get("scene_result") is not None:
        return "done"  # already has a scene result, skip
    if state.get("committed") and state.get("uploaded_files"):
        return "scene"  # uploaded files hint that visualization is wanted
    return "done"


# ==========================================================================
#  Helpers
# ==========================================================================
def _convo_text(state: dict) -> str:
    lines = []
    for m in state.get("conversation", []):
        role = m.get("role", "user")
        lines.append(f"{role}: {m.get('content', '')}")
    return "\n".join(lines) or "(no messages yet)"


def _last_user_msg(state: dict):
    for m in reversed(state.get("conversation", [])):
        if m.get("role") == "user":
            return m.get("content")
    return None


def _loopback_reason(state: dict):
    """If the Validator failed or the Classifier was unsure, produce a short
    human reason for the Concierge to voice."""
    v = state.get("validation")
    if v and not v.get("ok") and v.get("errors"):
        reasons = "; ".join(e.get("reason", "") for e in v["errors"][:3])
        return f"The twin didn't pass validation: {reasons}."
    if state.get("domain_confidence") is not None and \
       state["domain_confidence"] < CONFIDENCE_THRESHOLD and \
       state.get("conversation"):
        return "I'm not yet sure what kind of facility this is."
    return None
